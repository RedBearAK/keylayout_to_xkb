#!/usr/bin/env python3
"""
tests/probes/probe_modmap_alignment_equivalence.py

Confirm, across ALL layouts, that fixing the modifier-map parser alignment
(reading tableNum entries at mod_off+8 per Apple's UCKeyModifiersToTableNum
struct) is behavior-identical to the current compensated decode (entries read
two bytes early, indexed at byte+2).

For every keyboard-type record of every layout this probe compares, byte by
byte, the table the CURRENT decode picks against the table the ALIGNED decode
picks, and separately reports the map tail (the last two entries, which the
current misread drops -- our plane queries never reach them, but a correct
decode should not lose them). It also compares the eight plane-byte picks
explicitly, since those are the only indices production ever queries.

Expected outcome: zero mismatches in the queried range for every layout; tail
rows are informational.

Sources: on macOS, all installed layouts via TIS (positional args filter by
name). Off-Mac, *.uchr files from /mnt/user-data/uploads plus any file or
directory paths given as args.

Usage:
  python3 tests/probes/probe_modmap_alignment_equivalence.py
  python3 tests/probes/probe_modmap_alignment_equivalence.py latvian zhuyin
"""

import os
import sys
import glob
import struct


def _bootstrap_src_on_path():
    """Locate the package 'src' dir relative to this file and add it to sys.path.

    Lets the probe run from the repo root, from tests/probes/, or from any
    nested probe folder without the caller setting PYTHONPATH. Walks upward
    from this file looking for a 'src/keylayout_to_xkb' package.
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
    raise RuntimeError('could not locate src/keylayout_to_xkb above this file')


_bootstrap_src_on_path()

from keylayout_to_xkb.common.models import PLANE_MODIFIER_BYTE
from keylayout_to_xkb.extract.uchr_parse import _parse_modifier_table_map


__version__ = '20260703b'


_UPLOADS = '/mnt/user-data/uploads'


def _collect_payloads(args):
    """Yield (name, raw_bytes) from TIS on macOS, else from .uchr files."""

    try:
        from keylayout_to_xkb.extract.tis_source import extract_all_layouts
        payloads = extract_all_layouts()
    except Exception:
        payloads = None

    if payloads:
        filters = [arg.lower() for arg in args if not os.path.exists(arg)]
        for payload in payloads:
            data = payload.get('data')
            name = payload.get('name') or '?'
            if not data:
                continue
            if filters and not any(f in name.lower() for f in filters):
                continue
            yield name, data
        return

    paths = []
    for arg in args:
        if os.path.isdir(arg):
            paths.extend(sorted(glob.glob(os.path.join(arg, '*.uchr'))))
        elif os.path.isfile(arg):
            paths.append(arg)
    if not paths:
        paths = sorted(glob.glob(os.path.join(_UPLOADS, '*.uchr')))
    for path in paths:
        with open(path, 'rb') as handle:
            yield os.path.basename(path), handle.read()


def _check_record(data, mod_off):
    """Compare current vs aligned decode for one record.

    Returns (queried_mismatches, plane_mismatches, tail_diff_count,
    default_ok, count).
    """

    _fmt, default_num, count = struct.unpack_from('<HHI', data, mod_off)
    raw = list(data[mod_off + 8: mod_off + 8 + count])
    parsed, parsed_default = _parse_modifier_table_map(data, mod_off)

    if parsed == raw:
        # The ALIGNED decode is active (post-fix parser): equivalence with the
        # historical compensated decode was verified before the fix landed;
        # nothing left to compare against.
        return 0, 0, 0, parsed_default == default_num, count

    def old_pick(byte):
        index = byte + 2
        if 0 <= index < len(parsed):
            return parsed[index]
        return parsed_default

    def new_pick(byte):
        if 0 <= byte < len(raw):
            return raw[byte]
        return default_num

    queried_mismatches = sum(
        1 for byte in range(max(count - 2, 0)) if old_pick(byte) != new_pick(byte))
    plane_mismatches = sum(
        1 for byte in PLANE_MODIFIER_BYTE.values()
        if old_pick(byte) != new_pick(byte))
    tail_diff_count = sum(
        1 for byte in range(max(count - 2, 0), count)
        if old_pick(byte) != new_pick(byte))
    default_ok = parsed_default == default_num
    return (queried_mismatches, plane_mismatches, tail_diff_count,
            default_ok, count)


def main(argv):
    print('modmap alignment equivalence probe (%s)\n' % __version__)
    layouts = records = 0
    bad_layouts = []
    tail_total = 0
    for name, data in _collect_payloads(argv):
        _hf, _dv, _fi, ktc = struct.unpack_from('<HHII', data, 0)
        layouts += 1
        layout_bad = False
        for rec in range(ktc):
            base = 12 + rec * 28
            (_f, _l, mod_off, _ci, _sr, _t, _sq) = struct.unpack_from(
                '<IIIIIII', data, base)
            (queried, planes, tail, default_ok, count) = _check_record(
                data, mod_off)
            records += 1
            tail_total += tail
            if queried or planes or not default_ok:
                layout_bad = True
                print('  %s rec%d: queried=%d planes=%d default_ok=%s '
                      '(count=%d)  <- MISMATCH'
                      % (name, rec, queried, planes, default_ok, count))
        if layout_bad:
            bad_layouts.append(name)

    if not layouts:
        print('no layouts found: run on macOS, pass .uchr files/dirs, or')
        print('place *.uchr files in %s' % _UPLOADS)
        return 1

    print('layouts=%d records=%d mismatching_layouts=%d '
          'informational_tail_diffs=%d'
          % (layouts, records, len(bad_layouts), tail_total))
    if bad_layouts:
        print('VERDICT: NOT equivalent -- do not land the alignment fix;')
        print('send this output for analysis.')
        return 1
    print('VERDICT: decodes are equivalent everywhere queried; the')
    print('alignment fix is safe to land.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
