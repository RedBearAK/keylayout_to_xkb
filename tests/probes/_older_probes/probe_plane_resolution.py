#!/usr/bin/env python3
"""
probe_plane_resolution.py  (repo root, run on macOS)

Three-way comparison of plane->table resolution for EVERY installed macOS
layout, to decide whether the _MIN_SCRIPT_LETTERS content heuristic can be
replaced by a deterministic modifier-structure rule.

For each layout it resolves the plain/shift/option/shift_option planes by:

  1. ORACLE   - resolve_plane_tables_via_os (UCKeyTranslate). Ground truth.
  2. CONTENT  - _resolve_plane_tables (the current off-Mac heuristic, with
                _MIN_SCRIPT_LETTERS). What runs on Linux today.
  3. BAREST   - a first-cut deterministic rule (HYPOTHESIS UNDER TEST): the
                primary plane is the char table reached by the lowest modifier
                index that has neither cmd (0x01) nor control (0x10) set, and
                the four planes are the char-tables ordered by their barest
                reaching modifier index. This is the same "order by minimal
                modifier" idea already used on the uncased path, applied
                uniformly. It may be WRONG; the point is to see where.

It prints, per layout, the three resolutions and (on disagreement) the raw
modifier-reach map so the pattern is inspectable. At the end it tallies how
often CONTENT and BAREST each agree with ORACLE.

Run:
    python3 probe_plane_resolution.py            # all layouts
    python3 probe_plane_resolution.py --only greek,ukrainian,thai,tibetan

This script imports parser internals read-only and does NOT modify any module.
It is a diagnostic, not part of the package.
"""

import sys
import struct

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uckeytranslate import resolve_plane_tables_via_os
from keylayout_to_xkb.common.models import ModifierState
from keylayout_to_xkb.extract import uchr_parse as up


__version__ = '20260624'


_PLANES = [
    ModifierState.PLAIN,
    ModifierState.SHIFT,
    ModifierState.OPTION,
    ModifierState.SHIFT_OPTION,
]

_MOD_CMD_OR_CONTROL = 0x11


def _prep(data):
    """Run the same setup parse_uchr does, up to the inputs the resolvers need.

    Returns a dict of the pieces, or None if the layout is unparseable here.
    """

    if len(data) < 40:
        return None
    try:
        header_format, data_version, feature_info_offset, kbd_type_count = \
            struct.unpack_from('<HHII', data, 0)
        (
            _first, _last, mod_to_table_offset, char_index_offset,
            state_records_offset, _terminators_offset, sequence_data_offset,
        ) = struct.unpack_from('<IIIIIII', data, 12)

        max_output = up._parse_max_output_char_length(data, feature_info_offset)
        sequences = up._parse_sequence_table(data, sequence_data_offset)
        state_records = up._parse_state_records(data, state_records_offset)
        state_active = len(state_records) > 0
        seq_active = max_output >= 2
        char_tables = up._parse_char_table_index(data, char_index_offset)
        table_map, default_table = up._parse_modifier_table_map(data, mod_to_table_offset)
    except Exception as error:
        print(f'    (prep failed: {error})')
        return None

    return {
        'data': data,
        'char_tables': char_tables,
        'table_map': table_map,
        'default_table': default_table,
        'state_records': state_records,
        'sequences': sequences,
        'state_active': state_active,
        'seq_active': seq_active,
    }


def _resolve_oracle(p):
    def table_outputs_for(ti):
        return up._table_outputs(
            p['data'], p['char_tables'][ti], p['state_records'], p['sequences'],
            p['state_active'], p['seq_active'],
        )
    try:
        return resolve_plane_tables_via_os(p['data'], p['char_tables'], table_outputs_for)
    except Exception as error:
        print(f'    (oracle failed: {error})')
        return None


def _resolve_content(p):
    try:
        return up._resolve_plane_tables(
            p['data'], p['char_tables'], p['table_map'], p['default_table'],
            p['state_records'], p['sequences'], p['state_active'], p['seq_active'],
        )
    except Exception as error:
        print(f'    (content failed: {error})')
        return None


def _resolve_barest(p):
    """HYPOTHESIS: planes are the char tables ordered by barest reaching modifier.

    For each char table, find the minimum modifier index (no cmd/control bit)
    that reaches it. Tables reachable only via cmd/control are excluded (shortcut
    layers). Order the remaining tables by that minimal index; the first four map
    to plain/shift/option/shift_option.

    This deliberately uses ONLY the modifier structure -- no letter counting, no
    script detection, no threshold. If it matches the oracle as well as or better
    than the content heuristic, _MIN_SCRIPT_LETTERS is replaceable.
    """

    table_map = p['table_map']
    if not table_map:
        return None
    reach = up._modifier_reach_by_table(table_map)

    barest = {}
    for ti in range(len(p['char_tables'])):
        char_indices = [i for i in reach.get(ti, []) if not (i & _MOD_CMD_OR_CONTROL)]
        if char_indices:
            barest[ti] = min(char_indices)

    ordered = sorted(barest, key=lambda ti: barest[ti])
    resolved = {}
    for plane, ti in zip(_PLANES, ordered):
        resolved[plane] = ti
    return resolved


def _fmt(resolution):
    if not resolution:
        return '(none)'
    return ' '.join(
        f'{plane.value[:5]}={resolution.get(plane, "-")}' for plane in _PLANES
    )


def _same(a, b):
    """Do two resolutions agree on all four planes (where both define them)?"""
    if not a or not b:
        return False
    for plane in _PLANES:
        if a.get(plane) != b.get(plane):
            return False
    return True


def _reach_summary(p):
    """Compact 'modifier index -> table' for the no-cmd/control indices."""
    table_map = p['table_map']
    if not table_map:
        return '(no modifier map)'
    rows = []
    for i, ti in enumerate(table_map):
        if i & _MOD_CMD_OR_CONTROL:
            continue
        rows.append(f'{i:#04x}->t{ti}')
    # de-dup consecutive identical for brevity
    return ' '.join(rows[:16]) + (' ...' if len(rows) > 16 else '')


def main(argv):
    only = None
    if '--only' in argv:
        idx = argv.index('--only')
        only = [s.strip().lower() for s in argv[idx + 1].split(',')]

    try:
        payloads = extract_all_layouts()
    except Exception as error:
        print(f'extraction failed (need macOS): {error}')
        return 2

    content_agree = 0
    barest_agree = 0
    both_defined = 0
    content_vs_barest_differ = 0
    total = 0
    disagreements = []

    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        source_id = payload.get('source_id') or ''
        if not data:
            continue
        if only and not any(tok in name.lower() or tok in source_id.lower() for tok in only):
            continue

        p = _prep(data)
        if p is None:
            continue

        oracle = _resolve_oracle(p)
        content = _resolve_content(p)
        barest = _resolve_barest(p)
        total += 1

        if oracle:
            both_defined += 1
            c_ok = _same(content, oracle)
            b_ok = _same(barest, oracle)
            content_agree += 1 if c_ok else 0
            barest_agree += 1 if b_ok else 0
            if not _same(content, barest):
                content_vs_barest_differ += 1

            flag = ''
            if not c_ok or not b_ok:
                flag = '   <-- DISAGREE'
                disagreements.append((name, source_id))
            print(f'{name[:34]:34} {flag}')
            print(f'    oracle : {_fmt(oracle)}')
            print(f'    content: {_fmt(content)}   {"ok" if c_ok else "DIFF"}')
            print(f'    barest : {_fmt(barest)}   {"ok" if b_ok else "DIFF"}')
            if not c_ok or not b_ok:
                print(f'    reach  : {_reach_summary(p)}')
            print()
        else:
            # No oracle (shouldn't happen on Mac) -- just show the two off-Mac ones
            print(f'{name[:34]:34}   (no oracle)')
            print(f'    content: {_fmt(content)}')
            print(f'    barest : {_fmt(barest)}')
            print()

    print('=' * 60)
    print(f'layouts compared (with oracle): {both_defined}')
    print(f'  content heuristic agrees with oracle: {content_agree}/{both_defined}')
    print(f'  barest-modifier  agrees with oracle: {barest_agree}/{both_defined}')
    print(f'  content vs barest differ:            {content_vs_barest_differ}')
    if disagreements:
        print('\nlayouts where at least one resolver disagreed with oracle:')
        for name, sid in disagreements:
            print(f'  {name}  ({sid})')
    print('\nInterpretation:')
    print('  - If barest >= content and barest == both_defined, the threshold is')
    print('    replaceable by the deterministic barest-modifier rule.')
    print('  - Where barest disagrees with oracle, inspect the reach line: the')
    print('    binary modifier index may not cleanly identify the bare plane,')
    print('    which is the reason the content heuristic exists.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
