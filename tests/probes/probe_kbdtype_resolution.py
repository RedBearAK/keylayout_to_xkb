#!/usr/bin/env python3
"""
tests/probes/probe_kbdtype_resolution.py  (run on the Mac)

Learn two facts the PC-family cells divergence hinges on: the REAL value
LMGetKbdType() returns on this machine, and the rule UCKeyTranslate uses to
resolve a keyboard type that no keyboard-type record's range covers.

Background: multi-record layouts (Russian -- PC carries 27 records forming two
table sets that differ at the geometry keys, e.g. the Yo key) give the OS
per-type tables. The audit now parses from the record covering the Mac's
reported type -- but the full-coverage run shows this Mac's type is covered by
NO record's range, while the OS still deterministically picks the second table
set. This probe sweeps candidate keyboard types over one discriminating cell
and prints which table set each type resolves to, so the fallback rule can be
read straight off the mapping and encoded into the parser.

Usage:
  python3 tests/probes/probe_kbdtype_resolution.py                (Russian -- PC)
  python3 tests/probes/probe_kbdtype_resolution.py Canadian       (name filter)
"""

import os
import sys
import ctypes
import struct


def _bootstrap_src_on_path():
    """Locate the package 'src' dir relative to this file and add it to sys.path.

    Lets the probe run from the repo root, from tests/probes/, or from any
    nested probe folder without the caller setting PYTHONPATH.
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

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uchr_parse import (
    _parse_char_table_index,
    _read_keyboard_type_records,
)
from keylayout_to_xkb.extract.uckeytranslate import (
    _translate_full,
    _load_uckeytranslate,
)


__version__ = '20260703'


def _table_set_signature(data, record):
    """A record's char-index offset: records sharing it share every table."""

    return record['char_index_offset']


def _find_discriminating_cell(data, records):
    """A (virtual_key, modifier_byte) whose raw entry differs between the
    first two distinct table sets, plus the per-set raw entries for labeling.

    Returns (vk, modifier_byte, {signature: raw_entry}) or None when the
    layout has only one distinct table set (nothing to discriminate).
    """

    signatures = []
    for record in records:
        signature = _table_set_signature(data, record)
        if signature not in signatures:
            signatures.append(signature)
    if len(signatures) < 2:
        return None

    # Reuse the parser's own decoder rather than re-deriving the header
    # (a hand-rolled guess here misread it on the first try).
    sets = {sig: _parse_char_table_index(data, sig) for sig in signatures[:2]}
    tables_a, tables_b = sets[signatures[0]], sets[signatures[1]]
    for table_index in range(min(len(tables_a), len(tables_b))):
        offset_a, size_a = tables_a[table_index]
        offset_b, size_b = tables_b[table_index]
        for vk in range(min(size_a, size_b)):
            entry_a = struct.unpack_from('<H', data, offset_a + 2 * vk)[0]
            entry_b = struct.unpack_from('<H', data, offset_b + 2 * vk)[0]
            if entry_a != entry_b:
                # Plane byte 0 (plain) suffices when table 0 differs; when a
                # later table holds the difference, plain still exposes it in
                # practice for the geometry keys. Keep it simple: plain.
                return vk, 0x00, {signatures[0]: entry_a,
                                  signatures[1]: entry_b}
    return None


def main(argv):
    print('keyboard-type resolution probe (%s)\n' % __version__)
    filters = [arg.lower() for arg in argv] or ['russian – pc', 'russianwin']
    try:
        payloads = extract_all_layouts()
    except Exception as error:
        print('extraction failed (this probe needs macOS): %s' % error)
        return 2
    handle, real_type = _load_uckeytranslate()
    print('LMGetKbdType() on this machine: %d (0x%x)\n' % (real_type, real_type))

    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data:
            continue
        if not any(f in name.lower() for f in filters):
            continue
        _hf, _dv, _fi, count = struct.unpack_from('<HHII', data, 0)
        records = _read_keyboard_type_records(data, count)
        found = _find_discriminating_cell(data, records)
        if found is None:
            print('%s: single table set; not usable for this probe' % name)
            continue
        vk, modifier_byte, raw_by_sig = found
        print('%s: %d records, discriminating cell vk 0x%02x plain' %
              (name, count, vk))
        for record in records:
            sig = _table_set_signature(data, record)
            print('   types %3d-%-3d -> table set %d' %
                  (record['first'], record['last'], sig))

        buffer = ctypes.create_string_buffer(data, len(data))
        layout_ptr = ctypes.cast(buffer, ctypes.c_void_p)

        candidates = sorted({record['first'] for record in records}
                            | {record['last'] for record in records}
                            | {real_type}
                            | {0, 1, 2, 30, 36, 40, 41, 50, 58, 60, 91, 120,
                               126, 206, 210, 255, 1000})
        by_answer = {}
        print('   type -> OS output at the discriminating cell:')
        for candidate in candidates:
            output, dead = _translate_full(
                handle, layout_ptr, candidate, vk, modifier_byte)
            label = repr(output) if not dead else 'DEAD'
            by_answer.setdefault(label, []).append(candidate)
            marker = '  <-- REAL TYPE' if candidate == real_type else ''
            covered = any(r['first'] <= candidate <= r['last']
                          for r in records)
            print('      %4d: %-8s %s%s'
                  % (candidate, label,
                     '' if covered else '(uncovered)', marker))
        print('   grouped: %s' % {k: v for k, v in sorted(by_answer.items())})
        print('')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
