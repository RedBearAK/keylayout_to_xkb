#!/usr/bin/env python3
"""
probe_decode_validation.py  (repo root, run on macOS)

INFORMATION-GATHERING probe. Does NOT decide a transformation. For every
installed layout, every key, at the four plane bytes, it compares our file-only
decode against the real UCKeyTranslate output, and categorizes every mismatch
by the KIND of char-table entry involved.

The question this answers: are the mismatches concentrated on cells that need
STATE-RECORD resolution (keys that represent more than a single literal char --
accent bases, dead keys, sequence outputs), or are they spread across simple
literal-codepoint cells too?

  - If mismatches cluster on state/sequence/dead entries -> the divergence is
    about state-table resolution, as hypothesized.
  - If simple literal cells also mismatch in bulk -> the divergence is about
    table SELECTION (the modifier->table indexing), not state resolution.

It prints, per layout: match count, and a breakdown of mismatches by entry kind
(literal / state-index / sequence-index / dead / empty), plus a few example
mismatch cells of each kind with the raw entry value. Pure data.

Baseline decode here uses the RAW array index (arr[byte]) deliberately, so the
mismatch pattern is informative about both selection and resolution at once.
The entry-kind tag is read from the raw 16-bit entry's flag bits.

Usage:
  python3 probe_decode_validation.py
  python3 probe_decode_validation.py greek russian
  python3 probe_decode_validation.py --examples 6
"""

import sys
import struct
import ctypes

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uckeytranslate import _load_uckeytranslate, _translate
from keylayout_to_xkb.extract import uchr_parse as up
from keylayout_to_xkb.common.models import OutputKind


__version__ = '20260625'


_PLANE_BYTES = {'plain': 0x00, 'shift': 0x02, 'option': 0x08, 'shift_option': 0x0A}

_FLAG_SEQUENCE = 0x8000
_FLAG_STATE    = 0x4000
_FLAG_TEST     = 0xC000
_EMPTY_A       = 0xFFFE
_EMPTY_B       = 0xFFFF


def _entry_kind(entry, state_active, seq_active):
    """Classify a raw 16-bit char-table entry by its flag bits."""

    if entry in (_EMPTY_A, _EMPTY_B):
        return 'empty'
    masked = entry & _FLAG_TEST
    if masked == _FLAG_SEQUENCE and seq_active:
        return 'sequence'
    if masked == _FLAG_STATE and state_active:
        return 'state'
    if masked == _FLAG_TEST:
        return 'literal-high'   # both bits: literal high codepoint
    return 'literal'


def _prep(data):
    if len(data) < 40:
        return None
    try:
        fmt, ver, fi, count = struct.unpack_from('<HHII', data, 0)
        (_first, _last, modoff, cti, sri, _sti, seq) = struct.unpack_from('<IIIIIII', data, 12)
        cmarker, csize = struct.unpack_from('<HH', data, cti)
        ccount = struct.unpack_from('<I', data, cti + 4)[0]
        toffs = [struct.unpack_from('<I', data, cti + 8 + 4 * i)[0] for i in range(ccount)]
        m, default, mc = struct.unpack_from('<HHH', data, modoff)
        arr = [data[modoff + 6 + k] for k in range(mc)]
        seqs = up._parse_sequence_table(data, seq)
        sr = up._parse_state_records(data, sri)
        maxout = up._parse_max_output_char_length(data, fi)
    except Exception as error:
        print(f'    (prep failed: {error})')
        return None
    return {
        'data': data, 'toffs': toffs, 'csize': csize, 'arr': arr,
        'default': default, 'seqs': seqs, 'sr': sr,
        'state_active': len(sr) > 0, 'seq_active': maxout >= 2,
    }


def _raw_entry(p, byte, vk):
    """Raw 16-bit entry our naive decode would read (arr[byte] selection)."""

    ti = p['arr'][byte] if byte < len(p['arr']) else p['default']
    if ti >= len(p['toffs']) or vk >= p['csize']:
        return None, None
    entry = struct.unpack_from('<H', p['data'], p['toffs'][ti] + 2 * vk)[0]
    return ti, entry


def _our_decode(p, byte, vk):
    ti, entry = _raw_entry(p, byte, vk)
    if entry is None:
        return ('NONE', None)
    ko = up._entry_to_key_output(
        entry, p['sr'], p['seqs'], p['state_active'], p['seq_active'], vk
    )
    if ko is None:
        return ('NONE', None)
    if ko.kind is OutputKind.DEAD:
        return ('DEAD', None)
    return ('CHARS', ko.output)


def _os_decode(handle, ptr, kbd_type, byte, vk):
    produced = _translate(handle, ptr, kbd_type, vk, byte)
    if produced is None:
        return ('DEAD', None)
    return ('CHARS', produced)


def main(argv):
    examples = 4
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == '--examples':
            examples = int(argv[i + 1]); i += 2
        else:
            args.append(argv[i].lower()); i += 1

    try:
        payloads = extract_all_layouts()
    except Exception as error:
        print(f'extraction failed (need macOS): {error}')
        return 2
    handle, kbd_type = _load_uckeytranslate()
    print(f'kbd_type={kbd_type}\n')

    # overall tallies of mismatch-by-kind
    from collections import defaultdict
    grand_miss_by_kind = defaultdict(int)
    grand_match_by_kind = defaultdict(int)
    grand_total = 0
    grand_match = 0
    layouts_tested = 0

    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data:
            continue
        if args and not any(tok in name.lower() for tok in args):
            continue
        p = _prep(data)
        if p is None:
            continue
        buf = ctypes.create_string_buffer(data, len(data))
        ptr = ctypes.cast(buf, ctypes.c_void_p)

        miss_by_kind = defaultdict(int)
        match_by_kind = defaultdict(int)
        examples_by_kind = defaultdict(list)
        total = 0
        match = 0
        for plane, byte in _PLANE_BYTES.items():
            for vk in range(p['csize']):
                ti, entry = _raw_entry(p, byte, vk)
                kind = _entry_kind(entry, p['state_active'], p['seq_active']) if entry is not None else 'none'
                ours = _our_decode(p, byte, vk)
                os_ = _os_decode(handle, ptr, kbd_type, byte, vk)
                ok = (ours == os_)
                if not ok:
                    if ours[0] == 'NONE' and os_ == ('CHARS', ''):
                        ok = True
                    if ours == ('CHARS', '') and os_[0] == 'NONE':
                        ok = True
                total += 1
                if ok:
                    match += 1
                    match_by_kind[kind] += 1
                else:
                    miss_by_kind[kind] += 1
                    if len(examples_by_kind[kind]) < examples:
                        examples_by_kind[kind].append(
                            (plane, byte, vk, ti, entry, ours, os_))

        layouts_tested += 1
        grand_total += total
        grand_match += match
        for k, v in miss_by_kind.items():
            grand_miss_by_kind[k] += v
        for k, v in match_by_kind.items():
            grand_match_by_kind[k] += v

        total_miss = total - match
        rate = 100.0 * match / total if total else 100.0
        flag = '' if total_miss == 0 else '   <-- has mismatches'
        print(f'{name[:38]:38} {match}/{total} ({rate:.1f}%){flag}')
        if total_miss:
            # mismatch breakdown by entry kind
            kinds = sorted(miss_by_kind, key=lambda k: -miss_by_kind[k])
            for k in kinds:
                mm = miss_by_kind[k]
                mt = match_by_kind.get(k, 0)
                print(f'     mismatch on {k:13}: {mm:4d}  (matched same-kind: {mt})')
            # a few example cells per kind
            for k in kinds:
                for (plane, byte, vk, ti, entry, ours, os_) in examples_by_kind[k][:examples]:
                    es = f'0x{entry:04x}' if entry is not None else 'none'
                    print(f'        [{k:12}] {plane:12} byte0x{byte:02x} vk{vk:<3} '
                          f'table{ti} entry{es}  ours={ours} os={os_}')
            print()

    print('\n' + '=' * 64)
    print(f'layouts tested: {layouts_tested}')
    if grand_total:
        print(f'overall: {grand_match}/{grand_total} '
              f'({100.0 * grand_match / grand_total:.2f}%) cells match\n')
    print('mismatches by entry kind (the key question):')
    allkinds = set(grand_miss_by_kind) | set(grand_match_by_kind)
    for k in sorted(allkinds, key=lambda k: -grand_miss_by_kind.get(k, 0)):
        mm = grand_miss_by_kind.get(k, 0)
        mt = grand_match_by_kind.get(k, 0)
        tot = mm + mt
        pct = 100.0 * mm / tot if tot else 0.0
        print(f'  {k:14}: {mm:6d} mismatched / {tot:6d} total  ({pct:.1f}% of this kind wrong)')
    print('\nReading the result:')
    print('  If "state"/"sequence"/"dead" rows have high wrong%, and "literal"')
    print('  is near 0% wrong, the divergence is state-resolution as hypothesized.')
    print('  If "literal" is also badly wrong, it is table SELECTION (modifier')
    print('  indexing), not state resolution.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
