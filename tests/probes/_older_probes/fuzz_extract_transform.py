#!/usr/bin/env python3
"""
fuzz_extract_transform.py  (repo root, run on macOS)

FORCED EXTRACTION of the modifier-byte -> table-index transformation that
UCKeyTranslate applies. This does NOT guess bit positions. It mechanically
inverts the OS output against the raw tableNum array and tests whether a
consistent transformation function f(byte) -> index exists.

THE LOGIC
For each layout:
  1. Read the raw tableNum array (index -> table number) from the file.
  2. Build each char-table's full output vector (the string every key produces),
     so tables can be identified by content, unambiguously.
  3. For each modifier byte 0..255, ask the REAL UCKeyTranslate for its full
     output vector across all keys. Match that vector to the char-table(s) whose
     content equals it. That set of tables = "the table the OS selected for this
     byte" (T_os(byte)).
  4. Invert through the raw array: which array indices N have tableNum[N] in
     T_os(byte)? That candidate set is f(byte) -- the indices that, read from
     the raw array, would reproduce the OS's choice.
  5. f is CONSISTENT for this layout if every byte has a nonempty candidate set
     AND we can pick one index per byte such that the byte->index relation is a
     clean function (ideally identity, or a fixed permutation).

CROSS-LAYOUT TEST
The transformation is FILE-DERIVABLE only if the SAME byte->index relation holds
across all layouts. We compute, for the four planes (and all 32 low bytes), the
candidate index sets per layout and intersect them. A nonempty intersection that
is a clean function = the universal transformation, extracted. An empty or
contradictory intersection = proof it is not a fixed file-readable function.

OUTPUT
Per layout: for bytes 0..31, the OS-selected table(s) and the inverted candidate
index set. Then the cross-layout intersection for each byte, and a verdict:
  - "UNIVERSAL TRANSFORM FOUND: byte B -> index I" if a consistent function
    emerges across all tested layouts, or
  - "NO CONSISTENT TRANSFORM" with the contradicting layouts shown.

Usage:
  python3 fuzz_extract_transform.py                  # default broad sample
  python3 fuzz_extract_transform.py us greek russian german polish arabic
  python3 fuzz_extract_transform.py --bytes 0,2,8,10 # only the four planes
"""

import sys
import struct
import ctypes

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uckeytranslate import _load_uckeytranslate, _translate
from keylayout_to_xkb.extract import uchr_parse as up
from keylayout_to_xkb.common.models import OutputKind


__version__ = '20260626'


# Probe keys: enough of the main block to identify a table unambiguously by its
# output vector. Letters + number row + common punctuation positions.
_PROBE_VKS = list(range(0, 51))


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


def _table_vector(p, table_index):
    """Full output vector for a char table (the string each probe key produces)."""

    if table_index >= p['ccount']:
        return None
    vec = []
    base = p['toffs'][table_index]
    for vk in _PROBE_VKS:
        if vk >= p['csize']:
            vec.append('\x00OOB'); continue
        entry = struct.unpack_from('<H', p['data'], base + 2 * vk)[0]
        ko = up._entry_to_key_output(
            entry, p['sr'], p['seqs'], p['state_active'], p['seq_active'], vk
        )
        if ko is None:
            vec.append('\x00NONE')
        elif ko.kind is OutputKind.DEAD:
            vec.append('\x00DEAD')
        else:
            vec.append(ko.output)
    return tuple(vec)


def _os_vector(handle, ptr, kbd_type, byte):
    """Full output vector from the REAL UCKeyTranslate at this modifier byte."""

    vec = []
    for vk in _PROBE_VKS:
        o = _translate(handle, ptr, kbd_type, vk, byte)
        if o is None:
            vec.append('\x00DEAD')
        else:
            vec.append(o)
    return tuple(vec)


def extract_for_layout(p, handle, ptr, kbd_type, bytes_to_test):
    """Return {byte: candidate_index_set} by inverting OS output through arr.

    Also returns the per-table vectors and the OS vectors, for diagnostics.
    """

    # Precompute every char-table's vector.
    table_vecs = {}
    for ti in range(p['ccount']):
        table_vecs[ti] = _table_vector(p, ti)

    # Build a reverse map: vector -> set of table indices holding it.
    vec_to_tables = {}
    for ti, vec in table_vecs.items():
        vec_to_tables.setdefault(vec, set()).add(ti)

    arr = p['arr']
    result = {}
    for byte in bytes_to_test:
        os_vec = _os_vector(handle, ptr, kbd_type, byte)
        # Which tables match the OS output exactly?
        os_tables = vec_to_tables.get(os_vec, set())
        # Inversion: which array indices select one of those tables?
        if os_tables:
            cand_idx = {N for N in range(len(arr)) if arr[N] in os_tables}
        else:
            cand_idx = set()  # OS produced something no single table holds
        result[byte] = {
            'os_tables': os_tables,
            'cand_idx': cand_idx,
            'os_vec_sample': os_vec[:6],
        }
    return result


def main(argv):
    bytes_to_test = list(range(32))   # default: the 32 low modifier combos
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == '--bytes':
            bytes_to_test = [int(x, 0) for x in argv[i + 1].split(',')]; i += 2
        else:
            args.append(argv[i].lower()); i += 1
    wants = args or ['u.s.', 'german', 'greek', 'russian', 'arabic',
                     'polish', 'turkish', 'hindi', 'thai', 'hebrew']

    try:
        payloads = extract_all_layouts()
    except Exception as error:
        print(f'extraction failed (need macOS): {error}')
        return 2
    handle, kbd_type = _load_uckeytranslate()
    print(f'kbd_type={kbd_type}  testing bytes={[hex(b) for b in bytes_to_test]}\n')

    # Per-layout extraction, and accumulate cross-layout candidate intersections.
    cross = {b: None for b in bytes_to_test}   # byte -> running intersection
    per_layout = []

    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data:
            continue
        if not any(tok in name.lower() for tok in wants):
            continue
        p = _prep(data)
        if p is None:
            continue
        buf = ctypes.create_string_buffer(data, len(data))
        ptr = ctypes.cast(buf, ctypes.c_void_p)
        res = extract_for_layout(p, handle, ptr, kbd_type, bytes_to_test)
        per_layout.append((name, res))

        print(f'=== {name} ===')
        for byte in bytes_to_test:
            r = res[byte]
            ci = sorted(r['cand_idx'])
            ot = sorted(r['os_tables'])
            print(f'  byte 0x{byte:02x}: OS table(s)={ot}  candidate index(es)={ci}'
                  f'  {r["os_vec_sample"]}')
            # update cross-layout intersection
            if cross[byte] is None:
                cross[byte] = set(r['cand_idx'])
            else:
                cross[byte] &= r['cand_idx']
        print()

    # Verdict: is there a clean universal function byte -> index?
    print('=' * 64)
    print('CROSS-LAYOUT INTERSECTION (indices that work for ALL tested layouts):')
    universal = {}
    contradictions = []
    for byte in bytes_to_test:
        inter = cross[byte] or set()
        print(f'  byte 0x{byte:02x}: {sorted(inter)}')
        if len(inter) == 0:
            contradictions.append(byte)
        else:
            universal[byte] = sorted(inter)

    print('\n' + '=' * 64)
    if not contradictions:
        print('CANDIDATE UNIVERSAL TRANSFORM (one consistent index per byte exists):')
        # Try to read off a simple closed form for the four planes.
        for byte in bytes_to_test:
            print(f'  f(0x{byte:02x}) in {universal[byte]}')
        # Check the four planes specifically for a clean single-valued function.
        planes = [0x00, 0x02, 0x08, 0x0A]
        if all(b in universal for b in planes):
            print('\n  Four-plane mapping (intersection):')
            for b in planes:
                print(f'    plane byte 0x{b:02x} -> index {universal[b]}')
        print('\n  -> A consistent file-readable transform MAY exist. Inspect the')
        print('     intersections above for a single-valued closed form.')
    else:
        print('NO CONSISTENT TRANSFORM across layouts.')
        print(f'  Bytes with EMPTY cross-layout intersection (contradictory): '
              f'{[hex(b) for b in contradictions]}')
        print('  For these bytes, different layouts require DIFFERENT array')
        print('  indices to reproduce the OS output -- so no fixed byte->index')
        print('  function read from the file can be correct for all layouts.')
        print('  This is proof the transform is not file-derivable; the OS path')
        print('  (UCKeyTranslate) is required. Per-layout detail above shows')
        print('  exactly which layouts contradict on which bytes.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
