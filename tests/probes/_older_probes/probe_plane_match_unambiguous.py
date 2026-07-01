#!/usr/bin/env python3
"""
probe_plane_match_unambiguous.py  (repo root, run on macOS)

Before generalizing the OS plane resolver to take a kbd_type, verify the
assumption it relies on: when UCKeyTranslate is driven at a given KIND's
representative type (e.g. ISO=199), does its output uniquely identify that
kind's OWN char table, or can it tie with / better-match a different table?

If the kind's own table always wins uniquely, the per-variant OS resolution is
safe: drive at the kind's type, match against the tables, get the right one.
A tie or wrong winner would mean the match must be CONSTRAINED to the variant's
own table rather than chosen across all tables.

Method: for each multi-record layout and each advertised kind, drive
UCKeyTranslate at that kind's representative type (plain plane), then score the
OS output against every distinct table's decoded outputs. Report whether the
kind's own table is the unique top scorer.

Usage:
  python3 probe_plane_match_unambiguous.py
  python3 probe_plane_match_unambiguous.py turkish us
"""

import sys
import struct
import ctypes

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uckeytranslate import _load_uckeytranslate, _translate
from keylayout_to_xkb.extract import uchr_parse as up
from keylayout_to_xkb.common.gestalt_keyboard import REPRESENTATIVE_TYPES


__version__ = '20260626'

_PROBE_VKS = list(range(0, 51))


def _records(data):
    _hf, _dv, _fi, ktc = struct.unpack_from('<HHII', data, 0)
    out = []
    for i in range(ktc):
        f, l, mod, ci, sr, st, seq = struct.unpack_from('<IIIIIII', data, 12 + i * 28)
        out.append((i, f, l, mod, ci, sr, st, seq))
    return out


def _advertised(records, t):
    return any(f <= t <= l for (_i, f, l, *_o) in records)


def _rep_for(records, kind):
    for t in REPRESENTATIVE_TYPES[kind]:
        if _advertised(records, t):
            return t
    return None


def _table_probe(data, ci, mod, sr_off, seq_off, maxout):
    cmarker, csize = struct.unpack_from('<HH', data, ci)
    ccount = struct.unpack_from('<I', data, ci + 4)[0]
    toffs = [struct.unpack_from('<I', data, ci + 8 + 4 * j)[0] for j in range(ccount)]
    m, default, mc = struct.unpack_from('<HHH', data, mod)
    arr = [data[mod + 6 + k] for k in range(mc)]
    seqs = up._parse_sequence_table(data, seq_off)
    sr = up._parse_state_records(data, sr_off)
    out = {}
    ti = arr[2] if 2 < len(arr) else default   # plain plane = index 2 (byte+2)
    for vk in _PROBE_VKS:
        if ti < len(toffs) and vk < csize:
            e = struct.unpack_from('<H', data, toffs[ti] + 2 * vk)[0]
            ko = up._entry_to_key_output(e, sr, seqs, len(sr) > 0, maxout >= 2, vk)
            if ko and ko.kind.name == 'CHARS' and len(ko.output) == 1:
                out[vk] = ko.output
    return out


def main(argv):
    wants = [a.lower() for a in argv]
    payloads = extract_all_layouts()
    handle, build_type = _load_uckeytranslate()
    print(f'build machine kbd_type={build_type}\n')

    any_problem = False
    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data:
            continue
        if wants and not any(tok in name.lower() for tok in wants):
            continue
        records = _records(data)
        if len(records) <= 1:
            continue
        _hf, _dv, fi, _k = struct.unpack_from('<HHII', data, 0)
        maxout = up._parse_max_output_char_length(data, fi)
        buf = ctypes.create_string_buffer(data, len(data))
        ptr = ctypes.cast(buf, ctypes.c_void_p)

        printed_header = False
        for kind in ('ANSI', 'ISO', 'JIS'):
            t = _rep_for(records, kind)
            if t is None:
                continue
            rec = next(r for r in records if r[1] <= t <= r[2])
            _i, _f, _l, mod, ci, sr, st, seq = rec
            my_ci = ci
            # OS output at this type, plain plane
            os_out = {}
            for vk in _PROBE_VKS:
                p = _translate(handle, ptr, t, vk, 0x00)
                if p and len(p) == 1:
                    os_out[vk] = p
            # score against every distinct table
            scores = []
            seen = set()
            for r in records:
                _i2, _f2, _l2, mod2, ci2, sr2, st2, seq2 = r
                if ci2 in seen:
                    continue
                seen.add(ci2)
                probe = _table_probe(data, ci2, mod2, sr2, seq2, maxout)
                match = sum(1 for vk in os_out if probe.get(vk) == os_out[vk])
                scores.append((match, ci2))
            scores.sort(reverse=True)
            top_score, top_ci = scores[0]
            ties = [s for s in scores if s[0] == top_score]
            ok = (top_ci == my_ci) and len(ties) == 1
            if not ok:
                any_problem = True
            if not printed_header:
                print(f'=== {name} ({len(records)} records) ===')
                printed_header = True
            flag = 'OK' if ok else ('WRONG-TABLE' if top_ci != my_ci else 'TIE')
            print(f'  {kind:4} type {t:3} own-table@{my_ci:5}: '
                  f'OS best@{top_ci:5} {top_score}/{len(os_out)} '
                  f'{flag}{"  <--" if not ok else ""}')

    print('\n' + '=' * 56)
    if any_problem:
        print('AT LEAST ONE kind did NOT uniquely match its own table.')
        print('  -> per-variant OS resolution must CONSTRAIN matching to the')
        print('     variant\'s own table, not choose across all tables.')
    else:
        print('Every advertised kind uniquely matched its own table.')
        print('  -> driving UCKeyTranslate at the kind\'s type and matching')
        print('     across tables is SAFE; the right table always wins.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
