#!/usr/bin/env python3
"""
probe_kind_variants.py  (repo root, run on macOS)

Validate per-keyboard-kind variant resolution against the native tool. For each
layout and each kind (ANSI/ISO/JIS), this:

  1. Finds a representative gestalt type of that kind that the layout actually
     advertises (its records' [first,last] ranges cover it).
  2. Resolves the char table that type's record points at, and decodes every
     plane key from it (our file-only decode).
  3. Drives the REAL UCKeyTranslate with that SAME representative type as
     kbd_type, for every key/plane.
  4. Compares. A high match rate per kind confirms that building an independent,
     self-contained layout per kind reproduces what that physical keyboard does.

It also reports which kinds collapse to the same table (so the emitter can
dedupe output while keeping all labels), and an 'unlabeled' default built from
the lowest generic type.

Kind facts (type number -> kind) are derived from Apple's Gestalt.h keyboard
constants (the ...ANSIKbd/...ISOKbd/...JISKbd names); only the numeric
classification is used, not Apple's text.

Usage:
  python3 probe_kind_variants.py
  python3 probe_kind_variants.py turkish us german
"""

import sys
import struct
import ctypes

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uckeytranslate import _load_uckeytranslate, _translate
from keylayout_to_xkb.extract import uchr_parse as up
from keylayout_to_xkb.common.models import OutputKind


__version__ = '20260626'


# type number -> kind, from Gestalt.h keyboard constants (numeric facts only).
_KIND_BY_TYPE = {
    7: 'ISO', 8: 'ISO', 9: 'ISO', 11: 'ISO', 13: 'ISO', 16: 'ISO', 20: 'ISO',
    29: 'ISO', 32: 'ISO', 35: 'ISO', 38: 'ISO', 41: 'ISO', 196: 'ISO',
    199: 'ISO', 203: 'ISO', 205: 'ISO',
    17: 'JIS', 21: 'JIS', 30: 'JIS', 33: 'JIS', 36: 'JIS', 39: 'JIS', 42: 'JIS',
    197: 'JIS', 200: 'JIS', 201: 'JIS', 206: 'JIS', 207: 'JIS',
    28: 'ANSI', 31: 'ANSI', 34: 'ANSI', 37: 'ANSI', 40: 'ANSI', 195: 'ANSI',
    198: 'ANSI', 202: 'ANSI', 204: 'ANSI',
}

# Preferred representative type per kind (modern USB first, then older).
_REPRESENTATIVE = {
    'ANSI': [40, 37, 34, 31, 198, 204, 202, 195, 28],
    'ISO':  [41, 38, 35, 32, 199, 205, 203, 196, 29, 20, 16, 13, 11, 9, 8, 7],
    'JIS':  [42, 39, 36, 33, 200, 206, 207, 201, 197, 30, 21, 17],
}

_PLANE_BYTES = {'plain': 0x00, 'shift': 0x02, 'option': 0x08, 'shift_option': 0x0A}


def _records(data):
    """Yield (index, first, last, offsets-tuple) for each keyboard-type record."""

    _hf, _dv, _fi, ktc = struct.unpack_from('<HHII', data, 0)
    out = []
    for i in range(ktc):
        base = 12 + i * 28
        first, last, mod, ci, sr, st, seq = struct.unpack_from('<IIIIIII', data, base)
        out.append((i, first, last, (mod, ci, sr, st, seq)))
    return out


def _advertised_type_for_kind(records, kind):
    """First representative type of 'kind' that some record's range covers."""

    for t in _REPRESENTATIVE[kind]:
        for (_i, first, last, _off) in records:
            if first <= t <= last:
                return t
    return None


def _lowest_generic_type(records):
    """A generic (kind-less) representative type for the 'unlabeled' default."""

    for (_i, first, last, _off) in records:
        for t in range(first, last + 1):
            if t not in _KIND_BY_TYPE:
                return t
    return records[0][1] if records else 0


def _record_for_type(records, t):
    for (i, first, last, off) in records:
        if first <= t <= last:
            return i, off
    return None, None


def _decode_table(data, off, maxout):
    """Decode every plane/key from the record whose offsets are 'off'."""

    mod, ci, sr_off, st, seq = off
    cmarker, csize = struct.unpack_from('<HH', data, ci)
    ccount = struct.unpack_from('<I', data, ci + 4)[0]
    toffs = [struct.unpack_from('<I', data, ci + 8 + 4 * j)[0] for j in range(ccount)]
    m, default, mc = struct.unpack_from('<HHH', data, mod)
    arr = [data[mod + 6 + k] for k in range(mc)]
    seqs = up._parse_sequence_table(data, seq)
    sr = up._parse_state_records(data, sr_off)
    sia, qia = len(sr) > 0, maxout >= 2
    cells = {}
    for plane, byte in _PLANE_BYTES.items():
        idx = byte + 2
        ti = arr[idx] if idx < len(arr) else default
        for vk in range(csize):
            if ti >= len(toffs):
                continue
            entry = struct.unpack_from('<H', data, toffs[ti] + 2 * vk)[0]
            ko = up._entry_to_key_output(entry, sr, seqs, sia, qia, vk)
            if ko is None:
                cells[(vk, plane)] = ('NONE', None)
            elif ko.kind is OutputKind.DEAD:
                cells[(vk, plane)] = ('DEAD', None)
            else:
                cells[(vk, plane)] = ('CHARS', ko.output)
    return cells, csize


def _os_cell(handle, ptr, kbd_type, byte, vk):
    produced = _translate(handle, ptr, kbd_type, vk, byte)
    if produced is None:
        return ('DEAD', None)
    return ('CHARS', produced)


def main(argv):
    wants = [a.lower() for a in argv]
    payloads = extract_all_layouts()
    handle, _default_type = _load_uckeytranslate()

    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data:
            continue
        if wants and not any(tok in name.lower() for tok in wants):
            continue
        records = _records(data)
        if len(records) <= 1:
            continue  # single-type layouts already validated elsewhere
        _hf, _dv, fi, _ktc = struct.unpack_from('<HHII', data, 0)
        maxout = up._parse_max_output_char_length(data, fi)
        buf = ctypes.create_string_buffer(data, len(data))
        ptr = ctypes.cast(buf, ctypes.c_void_p)

        print(f'\n=== {name} ({len(records)} records) ===')
        # build the set: unlabeled + each kind that is advertised
        targets = []
        gen_t = _lowest_generic_type(records)
        targets.append(('unlabeled', gen_t))
        for kind in ('ANSI', 'ISO', 'JIS'):
            t = _advertised_type_for_kind(records, kind)
            if t is not None:
                targets.append((kind, t))

        table_for = {}
        for label, t in targets:
            idx, off = _record_for_type(records, t)
            if off is None:
                print(f'  {label:9} type {t}: no record')
                continue
            table_for[label] = off[1]  # char-index offset = table identity
            cells, csize = _decode_table(data, off, maxout)
            match = total = 0
            misses = []
            for (vk, plane), ours in cells.items():
                os_ = _os_cell(handle, ptr, t, _PLANE_BYTES[plane], vk)
                total += 1
                ok = (ours == os_) or (ours[0] == 'NONE' and os_ == ('CHARS', ''))
                if ok:
                    match += 1
                elif len(misses) < 3:
                    misses.append((vk, plane, ours, os_))
            rate = 100.0 * match / total if total else 0.0
            tag = '' if rate >= 99.0 else '  <-- mismatch'
            print(f'  {label:9} type {t:3} table@{off[1]:5} '
                  f'{match}/{total} ({rate:.1f}%){tag}')
            for vk, plane, ours, os_ in misses:
                print(f'        vk{vk} {plane}: ours={ours} os={os_}')

        # which labels share a table (dedupe candidates)
        from collections import defaultdict
        shared = defaultdict(list)
        for label, tbl in table_for.items():
            shared[tbl].append(label)
        dupes = {t: ls for t, ls in shared.items() if len(ls) > 1}
        if dupes:
            print(f'  shared tables (emit once, keep labels): '
                  + '; '.join(f'{ls}' for ls in dupes.values()))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
