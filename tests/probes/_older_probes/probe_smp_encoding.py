#!/usr/bin/env python3
"""
probe_smp_encoding.py  (repo root, run on macOS)

Diagnostic for the supplementary-plane (SMP) codepoint decode bug. For the
layouts whose output codepoints exceed U+FFFF (Wancho, Adlam, Pahawh, Osage,
Hanifi Rohingya), it dumps -- for the plain-plane keys that mismatch the OS --
the RAW char-table entry, its flag bits, the layout's maxOutputCharLength, the
sequence-table contents, and what the OS actually produces. This shows exactly
how the format encodes an SMP codepoint so the decode can be fixed from fact,
not assumption.

No fix is applied; this only reports structure.

Usage:
  python3 probe_smp_encoding.py
  python3 probe_smp_encoding.py wancho adlam
"""

import sys
import struct
import ctypes

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uckeytranslate import _load_uckeytranslate, _translate
from keylayout_to_xkb.extract import uchr_parse as up


__version__ = '20260626'

_FLAG_SEQUENCE = 0x8000
_FLAG_STATE    = 0x4000
_FLAG_TEST     = 0xC000


def _prep(data):
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
    return {
        'data': data, 'toffs': toffs, 'csize': csize, 'arr': arr,
        'default': default, 'seqs': seqs, 'sr': sr, 'ccount': ccount,
        'maxout': maxout, 'state_active': len(sr) > 0, 'seq_active': maxout >= 2,
    }


def _flag_name(entry):
    masked = entry & _FLAG_TEST
    if masked == _FLAG_TEST:
        return 'both-bits (0xC000)'
    if masked == _FLAG_SEQUENCE:
        return 'sequence (0x8000)'
    if masked == _FLAG_STATE:
        return 'state (0x4000)'
    return 'literal'


def main(argv):
    wants = [a.lower() for a in argv] or [
        'wancho', 'adlam', 'pahawh', 'osage', 'hanifi', 'rohingya']

    payloads = extract_all_layouts()
    handle, kbd_type = _load_uckeytranslate()

    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data or not any(tok in name.lower() for tok in wants):
            continue
        p = _prep(data)
        buf = ctypes.create_string_buffer(data, len(data))
        ptr = ctypes.cast(buf, ctypes.c_void_p)

        print(f'=== {name} ===')
        print(f'  maxOutputCharLength={p["maxout"]}  seq_active={p["seq_active"]} '
              f'sequences={len(p["seqs"])}  char_tables={p["ccount"]}')
        # show a few sequences (these may hold the SMP surrogate pairs)
        for si, s in enumerate(p['seqs'][:8]):
            cps = ' '.join(f'U+{ord(c):04X}' for c in s)
            print(f'    seq[{si}] = {s!r}  ({cps})')
        # plain plane table = arr[0x02] (byte+2)
        plain_table = p['arr'][0x02] if 0x02 < len(p['arr']) else p['default']
        base = p['toffs'][plain_table]
        print(f'  plain table index = {plain_table}')
        # dump first ~12 keys: raw entry, flags, what OS produces
        for vk in range(12):
            if vk >= p['csize']:
                break
            entry = struct.unpack_from('<H', data, base + 2 * vk)[0]
            os_out = _translate(handle, ptr, kbd_type, vk, 0x00)
            os_cps = (' '.join(f'U+{ord(c):04X}' for c in os_out)
                      if os_out else ('DEAD' if os_out is None else 'empty'))
            print(f'    vk{vk:<3} entry=0x{entry:04X} [{_flag_name(entry)}]  '
                  f'index={entry & 0x3FFF}  OS={os_out!r} ({os_cps})')
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
