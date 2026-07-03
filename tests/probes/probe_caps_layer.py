#!/usr/bin/env python3
"""
tests/probes/probe_caps_layer.py  (run on macOS)

Validates the CAPS-layer planes -- never exercised before. Prior probes only
compared the four base planes (plain/shift/option/shift+option, modifier bytes
0x00/0x02/0x08/0x0A). The caps-lock bit is 0x04, giving four more planes:

    caps           byte 0x04   (index 0x06 under byte+2)
    caps+shift     byte 0x06   (index 0x08)
    caps+option    byte 0x0C   (index 0x0E)
    caps+shift+opt byte 0x0E   (index 0x10)

The modifier map points these at real, often distinct char tables (e.g. US caps
-> table 2, the uppercase-letters layer that the four-plane port currently drops
-- the source of the "printable output on no plane" warnings). This probe drives
the real UCKeyTranslate at the caps bytes and compares against our file-only
decode of the same planes, exactly as the base-plane validation does, to find
out whether the caps layer decodes correctly with the same byte+2 + output-
reference machinery.

It reports, per layout, the caps-plane match rate and any mismatching cells, and
an aggregate. A high match rate means the caps layer is already decodable and
only needs to be surfaced into the model; mismatches show what differs.

Usage (from repo root or anywhere):
  python3 tests/probes/probe_caps_layer.py
  python3 tests/probes/probe_caps_layer.py german czech
  python3 tests/probes/probe_caps_layer.py --max-miss 6
"""

import os
import sys
import struct
import ctypes


def _bootstrap_src_on_path():
    """Locate the package 'src' dir relative to this file and add it to sys.path.

    Lets the probe run from the repo root, from tests/probes/, or from any
    nested probe folder without the caller setting PYTHONPATH. Walks upward from
    this file looking for a 'src/keylayout_to_xkb' package.
    """

    here = os.path.dirname(os.path.abspath(__file__))
    cursor = here
    for _ in range(8):
        candidate = os.path.join(cursor, 'src')
        if os.path.isdir(os.path.join(candidate, 'keylayout_to_xkb')):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return candidate
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    raise RuntimeError(
        'could not locate src/keylayout_to_xkb above '
        f'{here}; run from within the repo tree'
    )


_bootstrap_src_on_path()

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uckeytranslate import _load_uckeytranslate, _translate
from keylayout_to_xkb.extract import uchr_parse as up
from keylayout_to_xkb.common.models import (
    ModifierState,
    PLANE_MODIFIER_BYTE,
)


__version__ = '20260703'


# Caps-layer planes: human label -> modifier byte, derived from the SHARED
# PLANE_MODIFIER_BYTE constant. This probe originally carried its own byte
# list; keeping a private copy is how the production resolver's list drifted
# out of sync with the validated bytes without anything noticing.
_CAPS_PLANES = {
    'caps':            PLANE_MODIFIER_BYTE[ModifierState.CAPS],
    'caps+shift':      PLANE_MODIFIER_BYTE[ModifierState.CAPS_SHIFT],
    'caps+option':     PLANE_MODIFIER_BYTE[ModifierState.CAPS_OPTION],
    'caps+shift+opt':  PLANE_MODIFIER_BYTE[ModifierState.CAPS_SHIFT_OPTION],
}


def _prep(data):
    _hf, _dv, fi, ktc = struct.unpack_from('<HHII', data, 0)
    f, l, mod, ci, sro, sto, seqo = struct.unpack_from('<IIIIIII', data, 12)
    maxout = up._parse_max_output_char_length(data, fi)
    cmarker, csize = struct.unpack_from('<HH', data, ci)
    ccount = struct.unpack_from('<I', data, ci + 4)[0]
    toffs = [struct.unpack_from('<I', data, ci + 8 + 4 * j)[0] for j in range(ccount)]
    m, default, mc = struct.unpack_from('<HHH', data, mod)
    arr = [data[mod + 6 + k] for k in range(mc)]
    seqs = up._parse_sequence_table(data, seqo)
    sr = up._parse_state_records(data, sro)
    return {
        'data': data, 'toffs': toffs, 'csize': csize, 'ccount': ccount,
        'arr': arr, 'default': default, 'seqs': seqs, 'sr': sr,
        'state_active': len(sr) > 0, 'seq_active': maxout >= 2,
    }


def _our_cell(p, byte, vk):
    idx = byte        # direct index: the map decode is struct-aligned now
    ti = p['arr'][idx] if idx < len(p['arr']) else p['default']
    if ti >= p['ccount'] or vk >= p['csize']:
        return ('NONE', None)
    entry = struct.unpack_from('<H', p['data'], p['toffs'][ti] + 2 * vk)[0]
    ko = up._entry_to_key_output(
        entry, p['sr'], p['seqs'], p['state_active'], p['seq_active'], vk
    )
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


def main(argv):
    max_miss = 4
    wants = []
    i = 0
    while i < len(argv):
        if argv[i] == '--max-miss':
            max_miss = int(argv[i + 1]); i += 2
        else:
            wants.append(argv[i].lower()); i += 1

    try:
        payloads = extract_all_layouts()
    except Exception as error:
        print(f'extraction failed (need macOS): {error}')
        return 2
    handle, kbd_type = _load_uckeytranslate()
    print(f'kbd_type={kbd_type}  validating caps-layer planes '
          f'{[hex(b) for b in _CAPS_PLANES.values()]}\n')

    grand_match = grand_total = 0
    layouts_tested = layouts_clean = 0
    worst = []

    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data:
            continue
        if wants and not any(tok in name.lower() for tok in wants):
            continue
        try:
            p = _prep(data)
        except Exception as error:
            print(f'{name}: prep failed ({error})')
            continue
        buf = ctypes.create_string_buffer(data, len(data))
        ptr = ctypes.cast(buf, ctypes.c_void_p)

        match = total = 0
        misses = []
        for plane, byte in _CAPS_PLANES.items():
            for vk in range(p['csize']):
                ours = _our_cell(p, byte, vk)
                os_ = _os_cell(handle, ptr, kbd_type, byte, vk)
                if ours == ('NONE', None) and os_ == ('CHARS', ''):
                    continue
                total += 1
                if ours == os_:
                    match += 1
                elif len(misses) < max_miss:
                    misses.append((plane, vk, ours, os_))

        if total == 0:
            continue
        grand_match += match
        grand_total += total
        layouts_tested += 1
        rate = 100.0 * match / total
        if match == total:
            layouts_clean += 1
        else:
            worst.append((rate, name, match, total))
            print(f'{name[:34]:34} {match}/{total} ({rate:.1f}%)')
            for plane, vk, ours, os_ in misses:
                print(f'    {plane:14} vk{vk:<3} ours={ours} os={os_}')

    print('\n' + '=' * 60)
    print(f'layouts tested:      {layouts_tested}')
    print(f'layouts 100% clean:  {layouts_clean}')
    if grand_total:
        print(f'caps-plane cell match: {grand_match}/{grand_total} '
              f'({100.0 * grand_match / grand_total:.2f}%)')
    if worst:
        worst.sort()
        print('\nlowest-scoring caps layers:')
        for rate, name, m, t in worst[:20]:
            print(f'  {name}: {m}/{t} ({rate:.1f}%)')
    print('\nIf the caps planes match at ~100%, the caps layer decodes with the')
    print('existing machinery and only needs surfacing into the model/emitter.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
