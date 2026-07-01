#!/usr/bin/env python3
"""
probe_type_table_exact.py  (repo root, run on macOS)

Deterministic, exhaustive check -- no scoring, no "best", no "tie". For EVERY
installed layout and EVERY keyboard type the layout advertises, it asks one
exact question:

  Does UCKeyTranslate driven at type T produce EXACTLY what the char table that
  type's record points at decodes to -- across all four planes and all keys?

If yes for a type, that type's record-range -> table mapping is honored by the
OS, so resolving that type from the file is correct. If no, the OS does NOT use
the record's table for that type, and the per-cell differences are printed so
the real selection logic can be identified.

This covers the full space: every layout (single- and multi-record), every
advertised type (the union of all records' [first,last] ranges), every plane,
every key. The output is per (layout, type): EXACT or a list of differing cells.

Aggregates at the end: for each type number, how many layouts it was exact on
vs not -- which reveals whether specific type numbers (e.g. obsolete pre-USB
ISO types) are systematically not honored by the OS.

Usage:
  python3 probe_type_table_exact.py
  python3 probe_type_table_exact.py --show-cells 6
  python3 probe_type_table_exact.py manipuri korean      # name filter
"""

import sys
import struct
import ctypes
from collections import defaultdict

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uckeytranslate import _load_uckeytranslate, _translate
from keylayout_to_xkb.extract import uchr_parse as up
from keylayout_to_xkb.common.gestalt_keyboard import kind_of_type


__version__ = '20260626'

_PLANE_BYTES = {'plain': 0x00, 'shift': 0x02, 'option': 0x08, 'shift_option': 0x0A}


def _records(data):
    _hf, _dv, _fi, ktc = struct.unpack_from('<HHII', data, 0)
    out = []
    for i in range(ktc):
        f, l, mod, ci, sr, st, seq = struct.unpack_from('<IIIIIII', data, 12 + i * 28)
        out.append({'i': i, 'first': f, 'last': l, 'mod': mod, 'ci': ci,
                    'sr': sr, 'st': st, 'seq': seq})
    return out


def _record_for_type(records, t):
    for r in records:
        if r['first'] <= t <= r['last']:
            return r
    return None


def _decode_record_cell(data, rec, maxout, byte, vk):
    """Decode one plane/key from a record's table (file-only, byte+2)."""

    mod, ci, sr_off, seq_off = rec['mod'], rec['ci'], rec['sr'], rec['seq']
    cmarker, csize = struct.unpack_from('<HH', data, ci)
    if vk >= csize:
        return ('NONE', None)
    ccount = struct.unpack_from('<I', data, ci + 4)[0]
    idx = byte + 2
    m, default, mc = struct.unpack_from('<HHH', data, mod)
    arr = [data[mod + 6 + k] for k in range(mc)]
    ti = arr[idx] if idx < len(arr) else default
    if ti >= ccount:
        return ('NONE', None)
    toff = struct.unpack_from('<I', data, ci + 8 + 4 * ti)[0]
    entry = struct.unpack_from('<H', data, toff + 2 * vk)[0]
    seqs = up._parse_sequence_table(data, seq_off)
    sr = up._parse_state_records(data, sr_off)
    ko = up._entry_to_key_output(entry, sr, seqs, len(sr) > 0, maxout >= 2, vk)
    if ko is None:
        return ('NONE', None)
    if ko.kind.name == 'DEAD':
        return ('DEAD', None)
    return ('CHARS', ko.output)


def _os_cell(handle, ptr, kbd_type, byte, vk):
    produced = _translate(handle, ptr, kbd_type, vk, byte)
    if produced is None:
        return ('DEAD', None)
    return ('CHARS', produced)


def _csize(data, rec):
    return struct.unpack_from('<H', data, rec['ci'] + 2)[0]


def main(argv):
    show_cells = 4
    wants = []
    i = 0
    while i < len(argv):
        if argv[i] == '--show-cells':
            show_cells = int(argv[i + 1]); i += 2
        else:
            wants.append(argv[i].lower()); i += 1

    payloads = extract_all_layouts()
    handle, _build_type = _load_uckeytranslate()

    # per-type aggregate: type -> [exact_count, mismatch_count]
    type_exact = defaultdict(int)
    type_miss = defaultdict(int)
    layouts_done = 0

    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data:
            continue
        if wants and not any(tok in name.lower() for tok in wants):
            continue
        records = _records(data)
        _hf, _dv, fi, _k = struct.unpack_from('<HHII', data, 0)
        maxout = up._parse_max_output_char_length(data, fi)
        buf = ctypes.create_string_buffer(data, len(data))
        ptr = ctypes.cast(buf, ctypes.c_void_p)

        # the set of types to test = union of all advertised ranges
        types = []
        for r in records:
            types.extend(range(r['first'], r['last'] + 1))
        types = sorted(set(types))

        layout_lines = []
        for t in types:
            rec = _record_for_type(records, t)
            if rec is None:
                continue
            csize = _csize(data, rec)
            diffs = []
            for plane, byte in _PLANE_BYTES.items():
                for vk in range(csize):
                    ours = _decode_record_cell(data, rec, maxout, byte, vk)
                    os_ = _os_cell(handle, ptr, t, byte, vk)
                    if ours == os_:
                        continue
                    if ours[0] == 'NONE' and os_ == ('CHARS', ''):
                        continue
                    diffs.append((plane, vk, ours, os_))
            if diffs:
                type_miss[t] += 1
                kind = kind_of_type(t) or 'generic'
                layout_lines.append(
                    (t, kind, rec['ci'], len(diffs), diffs[:show_cells]))
            else:
                type_exact[t] += 1

        layouts_done += 1
        if layout_lines:
            print(f'=== {name} ===')
            for (t, kind, ci, ndiff, sample) in layout_lines:
                print(f'  type {t:3} ({kind:7}) record-table@{ci:5}: '
                      f'{ndiff} cells differ from OS')
                for (plane, vk, ours, os_) in sample:
                    print(f'      {plane:12} vk{vk:<3} record={ours} os={os_}')

    print('\n' + '=' * 60)
    print(f'layouts checked: {layouts_done}')
    print('\nper-type: layouts where the record-table EXACTLY equals OS output')
    print('vs where it does NOT (the deterministic signal):')
    all_types = sorted(set(type_exact) | set(type_miss))
    for t in all_types:
        e, m = type_exact.get(t, 0), type_miss.get(t, 0)
        kind = kind_of_type(t) or 'generic'
        flag = '' if m == 0 else '   <-- NOT honored on some layouts'
        print(f'  type {t:3} ({kind:7}): exact={e:3}  mismatch={m:3}{flag}')
    print('\nTypes with mismatch>0 are not reliably resolvable from their record')
    print('range alone; the per-layout cell diffs above show what the OS does')
    print('instead, which is the lead for the real selection rule.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
