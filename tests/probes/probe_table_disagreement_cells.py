#!/usr/bin/env python3
"""
tests/probes/probe_table_disagreement_cells.py  (run on the Mac)

Settle the near-twin table disagreements with the native tool.

Background: for some layouts the OS plane matcher and the on-disk
keyModifiersToTableNum decode pick DIFFERENT char tables for a plane. The
disagreeing pairs turn out to be near-twins (2-6 differing cells out of ~107),
and the differing cells are dead keys or characters outside the matcher's
fixed probe set -- so the matcher cannot tell the twins apart and takes the
lowest index. This probe asks real UCKeyTranslate about EXACTLY the cells
where the two candidate tables differ, dead-state included, and reports which
table the OS actually behaves like, per cell.

Expected outcome (per the container analysis): the OS agrees with the ON-DISK
table, meaning the byte-indexed map decode is authoritative and the matcher is
the component to fix.

Usage:
  python3 tests/probes/probe_table_disagreement_cells.py Latvian Kana Zhuyin
  python3 tests/probes/probe_table_disagreement_cells.py    (all layouts)
"""

import os
import sys
import ctypes
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

from keylayout_to_xkb.extract import uchr_parse
from keylayout_to_xkb.common.models import PLANE_MODIFIER_BYTE
from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uckeytranslate import (
    _translate_full,
    _load_uckeytranslate,
    resolve_plane_tables_via_os,
)


__version__ = '20260703b'


def _pieces(data):
    """Return (char_tables, table_map, default_table, table_outputs_fn)."""

    (_first, _last, mod_off, ci, sro, _t, seqo) = struct.unpack_from(
        '<IIIIIII', data, 12)
    char_tables = uchr_parse._parse_char_table_index(data, ci)
    state_records = uchr_parse._parse_state_records(data, sro)
    sequences = uchr_parse._parse_sequence_table(data, seqo)
    table_map, default_table = uchr_parse._parse_modifier_table_map(
        data, mod_off)

    def table_outputs_fn(table_index):
        return uchr_parse._table_outputs(
            data, char_tables[table_index], state_records, sequences)

    return char_tables, table_map, default_table, table_outputs_fn


def _ondisk_table(table_map, default_table, table_count, modifier_byte):
    """The byte-indexed on-disk pick, mirroring the content resolver."""

    index = modifier_byte           # direct: the map decode is struct-aligned
    if 0 <= index < len(table_map):
        table_index = table_map[index]
    else:
        table_index = default_table
    if 0 <= table_index < table_count:
        return table_index
    return None


def probe_layout(name, data, handle, kbd_type):
    """Report OS behavior at every cell where matcher and on-disk disagree.

    Returns (matcher_win_count, ondisk_win_count) across probed cells.
    """

    char_tables, table_map, default_table, outputs_fn = _pieces(data)
    matched = resolve_plane_tables_via_os(data, char_tables, outputs_fn)
    if not matched:
        return 0, 0
    buffer = ctypes.create_string_buffer(data, len(data))
    layout_ptr = ctypes.cast(buffer, ctypes.c_void_p)

    matcher_wins = ondisk_wins = 0
    for plane, modifier_byte in PLANE_MODIFIER_BYTE.items():
        picked = matched.get(plane)
        ondisk = _ondisk_table(table_map, default_table, len(char_tables),
                               modifier_byte)
        if picked is None or ondisk is None or picked == ondisk:
            continue
        outs_picked = outputs_fn(picked)
        outs_ondisk = outputs_fn(ondisk)
        differing = sorted(vk for vk in set(outs_picked) | set(outs_ondisk)
                           if outs_picked.get(vk) != outs_ondisk.get(vk))
        if not differing:
            continue
        print('  %s plane %s: matcher=%d ondisk=%d, %d differing cell(s):'
              % (name, plane.value, picked, ondisk, len(differing)))
        for vk in differing[:6]:
            os_out, dead_state = _translate_full(
                handle, layout_ptr, kbd_type, vk, modifier_byte)
            cell_picked = outs_picked.get(vk)
            cell_ondisk = outs_ondisk.get(vk)
            verdict = ('DEAD(state=%d)' % dead_state) if dead_state \
                else repr(os_out)
            if os_out == cell_ondisk:
                agrees = 'ondisk'
                ondisk_wins += 1
            elif os_out == cell_picked:
                agrees = 'matcher'
                matcher_wins += 1
            else:
                agrees = 'NEITHER'
            print('    vk 0x%02x: OS=%s  matcher_cell=%r  ondisk_cell=%r'
                  '  -> agrees with %s'
                  % (vk, verdict, cell_picked, cell_ondisk, agrees))
    return matcher_wins, ondisk_wins


def main(argv):
    print('table disagreement cells probe (%s)\n' % __version__)
    filters = [arg.lower() for arg in argv]
    try:
        payloads = extract_all_layouts()
    except Exception as error:
        print('extraction failed (this probe needs macOS): %s' % error)
        return 2
    handle, kbd_type = _load_uckeytranslate()

    total_matcher = total_ondisk = 0
    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data:
            continue
        if filters and not any(f in name.lower() for f in filters):
            continue
        matcher_wins, ondisk_wins = probe_layout(name, data, handle, kbd_type)
        total_matcher += matcher_wins
        total_ondisk += ondisk_wins

    print('\ncells where the OS agreed with: matcher=%d  ondisk=%d'
          % (total_matcher, total_ondisk))
    if total_ondisk and not total_matcher:
        print('VERDICT: the on-disk map is authoritative; fix the matcher.')
    elif total_matcher and not total_ondisk:
        print('VERDICT: the matcher is authoritative; the map decode is off.')
    elif total_matcher or total_ondisk:
        print('VERDICT: mixed -- both mechanisms in play; send this output.')
    else:
        print('no disagreement cells found for the given filters.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
