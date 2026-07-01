#!/usr/bin/env python3
"""
fuzz_confirm_byteplus2.py  (repo root, run on macOS)

Targeted confirmation of ONE hypothesis: the modifier-byte -> table-index
transform UCKeyTranslate uses is  index = byte + 2  (for the character planes).

This fixes the brittle-matching defect of the previous extraction probe. It does
NOT compare against our own table decode (which diverges on state/dead cells).
Instead it tests the hypothesis DIRECTLY against the OS, per key:

  For each plane byte B in {0x00, 0x02, 0x08, 0x0A}:
    predicted_table = tableNum[B + 2]
    For each key vk:
      our_char = decode(predicted_table, vk) via _entry_to_key_output
      os_char  = UCKeyTranslate(vk, B)        (the real OS output at byte B)
      compare. Dead/empty handled leniently.

If byte+2 is the true transform, our_char (read from the predicted table) will
match os_char for (nearly) every key on every layout -- INCLUDING the layouts
that came back empty in the identity-matching probe, because here we resolve the
predicted table's entry the same way for both and compare characters, not whole
vectors.

It reports, per layout and per plane, the match rate, and lists the first few
mismatches with detail. The summary gives the overall match rate and flags any
layout where byte+2 fails badly (which would refute it for that layout/script).

A near-100% overall match PROVES byte+2 is the transform -> off-Mac decode is
solvable. A layout/script with systematic byte+2 failure REFUTES it -> abandon
file-only extraction.

Usage:
  python3 fuzz_confirm_byteplus2.py
  python3 fuzz_confirm_byteplus2.py thai hebrew hindi turkish   # the empties
  python3 fuzz_confirm_byteplus2.py --offset 2                  # test other offsets
"""

import sys
import struct
import ctypes

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uckeytranslate import _load_uckeytranslate, _translate
from keylayout_to_xkb.extract import uchr_parse as up
from keylayout_to_xkb.common.models import OutputKind


__version__ = '20260626'


_PLANE_BYTES = {'plain': 0x00, 'shift': 0x02, 'option': 0x08, 'shift_option': 0x0A}


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
        'default': default, 'mc': mc, 'ccount': ccount, 'seqs': seqs, 'sr': sr,
        'state_active': len(sr) > 0, 'seq_active': maxout >= 2,
    }


def _decode_cell(p, table_index, vk):
    """Decode one key in a given table to ('CHARS',s)/('DEAD',None)/('NONE',None)."""

    if table_index >= p['ccount'] or vk >= p['csize']:
        return ('NONE', None)
    entry = struct.unpack_from('<H', p['data'], p['toffs'][table_index] + 2 * vk)[0]
    ko = up._entry_to_key_output(
        entry, p['sr'], p['seqs'], p['state_active'], p['seq_active'], vk
    )
    if ko is None:
        return ('NONE', None)
    if ko.kind is OutputKind.DEAD:
        return ('DEAD', None)
    return ('CHARS', ko.output)


def _os_cell(handle, ptr, kbd_type, byte, vk):
    produced = _translate(handle, ptr, kbd_type, vk, byte)
    if produced is None:
        return ('DEAD', None)
    return ('CHARS', produced)


def _match(ours, os_):
    if ours == os_:
        return True
    # lenient equivalences for "nothing produced"
    if ours[0] == 'NONE' and os_ == ('CHARS', ''):
        return True
    if ours == ('CHARS', '') and os_[0] == 'NONE':
        return True
    return False


def main(argv):
    offset = 2
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == '--offset':
            offset = int(argv[i + 1]); i += 2
        else:
            args.append(argv[i].lower()); i += 1

    try:
        payloads = extract_all_layouts()
    except Exception as error:
        print(f'extraction failed (need macOS): {error}')
        return 2
    handle, kbd_type = _load_uckeytranslate()
    print(f'kbd_type={kbd_type}  testing transform: index = byte + {offset}\n')

    grand_match = 0
    grand_total = 0
    layouts_tested = 0
    bad_layouts = []

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

        lay_match = 0
        lay_total = 0
        plane_rates = {}
        examples = []
        for plane, byte in _PLANE_BYTES.items():
            idx = byte + offset
            predicted_table = p['arr'][idx] if idx < len(p['arr']) else p['default']
            pm = 0
            pt = 0
            for vk in range(p['csize']):
                ours = _decode_cell(p, predicted_table, vk)
                os_ = _os_cell(handle, ptr, kbd_type, byte, vk)
                pt += 1
                if _match(ours, os_):
                    pm += 1
                elif len(examples) < 8:
                    examples.append((plane, byte, idx, predicted_table, vk, ours, os_))
            plane_rates[plane] = (pm, pt)
            lay_match += pm
            lay_total += pt

        grand_match += lay_match
        grand_total += lay_total
        layouts_tested += 1
        rate = 100.0 * lay_match / lay_total if lay_total else 100.0
        tag = '' if rate >= 99.0 else '   <-- byte+2 FAILS here'
        if rate < 99.0:
            bad_layouts.append((name, rate))
        pr = '  '.join(f'{pl}:{m}/{t}' for pl, (m, t) in plane_rates.items())
        print(f'{name[:34]:34} {lay_match}/{lay_total} ({rate:.1f}%)  [{pr}]{tag}')
        if rate < 99.0:
            for (plane, byte, idx, ptable, vk, ours, os_) in examples:
                print(f'      {plane:12} byte0x{byte:02x} idx{idx} table{ptable} '
                      f'vk{vk:<3} ours={ours} os={os_}')

    print('\n' + '=' * 64)
    print(f'layouts tested: {layouts_tested}')
    if grand_total:
        print(f'overall match: {grand_match}/{grand_total} '
              f'({100.0 * grand_match / grand_total:.2f}%)')
    print(f'\ntransform under test: index = byte + {offset}')
    if not bad_layouts:
        print('VERDICT: byte+%d holds on EVERY tested layout (>=99%%).' % offset)
        print('  The transform is CONFIRMED and file-derivable. Off-Mac decode')
        print('  can read tableNum[byte+%d] for each plane.' % offset)
    else:
        print(f'VERDICT: byte+{offset} FAILS on {len(bad_layouts)} layout(s):')
        for name, rate in bad_layouts:
            print(f'  {name}: {rate:.1f}%')
        print('  If these are a few stragglers with a DIFFERENT clean offset,')
        print('  the transform may be conditional. If they fail at all offsets,')
        print('  file-only extraction is refuted for those scripts.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
