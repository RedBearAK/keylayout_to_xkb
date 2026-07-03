#!/usr/bin/env python3
"""
src/keylayout_to_xkb/extract/test_uckt_matcher.py

Off-Mac tests for the UCKeyTranslate plane matcher.

resolve_plane_tables_via_os() only ever runs on macOS, which means the test
container structurally cannot exercise it -- exactly how its plane list stayed
at four while the content resolver grew to eight, dropping the caps layers
from every on-Mac generation while every off-Mac test stayed green. These
tests close that gap: they monkeypatch the module's OS entry points with a
FAKE translate that answers from the layout's own char tables per the on-disk
byte+2 semantics (the behavior the caps-layer probe validated against the real
UCKeyTranslate at ~99.96%), then run the REAL matcher against it.

Covered: all eight planes resolve; the resolution is output-equivalent to the
content resolver (index-equality is deliberately NOT required, because with
duplicate char tables the matcher's best-match may legitimately pick a
different index carrying identical content); and the caps acid cell -- Sacute
on the r key's caps+option plane -- survives the round trip.

Project test style: each test prints, returns True/False, main() scores. Runs
standalone or under pytest. Self-skips (as a pass) when the PolishPro fixture
is not present in the uploads directory.
"""

import os
import sys
import struct

from keylayout_to_xkb.common.models import ModifierState, PLANE_MODIFIER_BYTE
from keylayout_to_xkb.extract import uchr_parse
from keylayout_to_xkb.extract import uckeytranslate


__version__ = '20260703'


_FIXTURE = '/mnt/user-data/uploads/com_apple_keylayout_PolishPro.uchr'
_LATVIAN_FIXTURE = '/mnt/user-data/uploads/com_apple_keylayout_Latvian.uchr'

_ACID_VK = 0x0F         # the r key
_ACID_CHAR = 'Ś'        # PolishPro caps+option on r


def _load_fixture_pieces(fixture_path=_FIXTURE):
    """Parse the fixture's raw pieces the matcher needs, via uchr_parse.

    Returns (data, char_tables, table_map, default_table, table_outputs_fn) or
    None when the fixture is absent. Mirrors the header walk the caps-layer
    probe uses: keyboard-type header[0] carries the char-index, state-record,
    and sequence offsets, plus the modifier-map offset.
    """

    if not os.path.isfile(fixture_path):
        return None
    with open(fixture_path, 'rb') as handle:
        data = handle.read()

    (_first, _last, mod_offset, char_index_offset,
     state_records_offset, _terminators_offset,
     sequences_offset) = struct.unpack_from('<IIIIIII', data, 12)

    char_tables = uchr_parse._parse_char_table_index(data, char_index_offset)
    state_records = uchr_parse._parse_state_records(data, state_records_offset)
    sequences = uchr_parse._parse_sequence_table(data, sequences_offset)
    table_map, default_table = uchr_parse._parse_modifier_table_map(
        data, mod_offset)

    def table_outputs_fn(table_index):
        return uchr_parse._table_outputs(
            data, char_tables[table_index], state_records, sequences,
        )

    return (data, char_tables, table_map, default_table,
            state_records, sequences, table_outputs_fn)


def _run_matcher_with_fake_os(pieces):
    """Run the real matcher against a fake OS honoring byte+2 semantics.

    Returns (resolved, content) plane->table dicts. The fake _translate
    answers each (virtual_key, modifier_byte) query straight from the char
    table the on-disk modifier map selects for that byte -- the behavior the
    real UCKeyTranslate was measured to match at ~99.96% on the caps quartet.
    """

    (data, char_tables, table_map, default_table,
     state_records, sequences, table_outputs_fn) = pieces

    outputs_cache = {}

    def _outputs_for(table_index):
        if table_index not in outputs_cache:
            outputs_cache[table_index] = table_outputs_fn(table_index)
        return outputs_cache[table_index]

    def fake_load():
        return object(), 40

    def fake_translate(_handle, _layout_ptr, _kbd_type, virtual_key,
                       modifier_byte):
        index = modifier_byte
        if 0 <= index < len(table_map):
            table_index = table_map[index]
        else:
            table_index = default_table
        if not (0 <= table_index < len(char_tables)):
            return None
        return _outputs_for(table_index).get(virtual_key)

    cells_cache = {}

    def _cells_for(table_index):
        if table_index not in cells_cache:
            cells_cache[table_index] = uchr_parse._table_cells(
                data, char_tables[table_index], state_records, sequences,
            )
        return cells_cache[table_index]

    def fake_translate_full(_handle, _layout_ptr, _kbd_type, virtual_key,
                            modifier_byte):
        index = modifier_byte
        if 0 <= index < len(table_map):
            table_index = table_map[index]
        else:
            table_index = default_table
        if not (0 <= table_index < len(char_tables)):
            return '', 0
        cell = _cells_for(table_index).get(virtual_key)
        if cell is None:
            return '', 0
        kind, output = cell
        if kind == 'dead':
            return '', 1
        return output, 0

    orig_load = uckeytranslate._load_uckeytranslate
    orig_translate = uckeytranslate._translate
    orig_translate_full = uckeytranslate._translate_full
    uckeytranslate._load_uckeytranslate = fake_load
    uckeytranslate._translate = fake_translate
    uckeytranslate._translate_full = fake_translate_full
    try:
        resolved = uckeytranslate.resolve_plane_tables_via_os(
            data, char_tables, table_outputs_fn,
            table_cells_fn=_cells_for,
            ondisk_tables=None,
        )
    finally:
        uckeytranslate._load_uckeytranslate = orig_load
        uckeytranslate._translate = orig_translate
        uckeytranslate._translate_full = orig_translate_full

    content = uchr_parse._resolve_plane_tables(
        data, char_tables, table_map, default_table,
        state_records, sequences,
    )
    return resolved, content


def test_all_eight_planes_resolve():
    """The matcher, fed all shared plane bytes, resolves the full plane set."""

    pieces = _load_fixture_pieces()
    if pieces is None:
        print('  skipped (fixture missing)')
        return True
    resolved, _content = _run_matcher_with_fake_os(pieces)
    if resolved is None:
        print('  matcher returned None')
        return False
    expected = set(PLANE_MODIFIER_BYTE)
    missing = sorted(plane.value for plane in expected - set(resolved))
    if missing:
        print('  unresolved plane(s): %s' % ', '.join(missing))
        return False
    print('  resolved %d/%d planes' % (len(resolved), len(expected)))
    return True


def test_output_equivalent_to_content_resolver():
    """Per plane, the matcher's table carries the same outputs as content's.

    Index equality is not required: duplicate tables make the lowest
    best-scoring index a legitimate pick. Content equality is what matters.
    """

    pieces = _load_fixture_pieces()
    if pieces is None:
        print('  skipped (fixture missing)')
        return True
    resolved, content = _run_matcher_with_fake_os(pieces)
    if not resolved:
        print('  matcher returned nothing')
        return False
    table_outputs_fn = pieces[6]
    mismatched = []
    for plane in sorted(content, key=lambda p: p.value):
        if plane not in resolved:
            continue
        if table_outputs_fn(resolved[plane]) != table_outputs_fn(content[plane]):
            mismatched.append('%s (os=%d content=%d)'
                              % (plane.value, resolved[plane], content[plane]))
    if mismatched:
        print('  content mismatch: %s' % '; '.join(mismatched))
        return False
    print('  all resolved planes output-equivalent to content resolution')
    return True


def test_caps_acid_cell():
    """The caps+option plane the matcher picks carries Sacute on the r key."""

    pieces = _load_fixture_pieces()
    if pieces is None:
        print('  skipped (fixture missing)')
        return True
    resolved, _content = _run_matcher_with_fake_os(pieces)
    if not resolved or ModifierState.CAPS_OPTION not in resolved:
        print('  caps+option plane not resolved')
        return False
    table_outputs_fn = pieces[6]
    produced = table_outputs_fn(resolved[ModifierState.CAPS_OPTION]).get(_ACID_VK)
    if produced != _ACID_CHAR:
        print('  acid cell produced %r, want %r' % (produced, _ACID_CHAR))
        return False
    print('  caps+option r key -> %s' % produced)
    return True


def test_near_twin_discrimination():
    """The Latvian near-twin case: fake-OS resolution must pick table 8.

    Latvian's plain plane is the historical worst case: tables 0 and 8 agree
    on 106 of 109 cells and every discriminating cell (dead accents) lies
    outside the fixed probe set, so the blind matcher tied and took table 0
    while the on-disk map (verified against the native tool at every
    disagreement cell) says table 8. The sighted matcher must settle the tie
    at the differing cells and land on 8.
    """

    pieces = _load_fixture_pieces(_LATVIAN_FIXTURE)
    if pieces is None:
        print('  skipped (Latvian fixture missing)')
        return True
    resolved, _content = _run_matcher_with_fake_os(pieces)
    if not resolved or ModifierState.PLAIN not in resolved:
        print('  plain plane not resolved')
        return False
    picked = resolved[ModifierState.PLAIN]
    if picked != 8:
        print('  plain resolved to table %d, want 8 (the on-disk twin)'
              % picked)
        return False
    print('  near-twin tie settled at differing cells: plain -> table 8')
    return True


def main():
    print('uckt matcher tests:\n')
    tests = (
        ('all eight planes resolve', test_all_eight_planes_resolve),
        ('output-equivalent to content resolver',
         test_output_equivalent_to_content_resolver),
        ('caps acid cell (Sacute)', test_caps_acid_cell),
        ('near-twin discrimination (Latvian)', test_near_twin_discrimination),
    )
    score = 0
    for label, test_fn in tests:
        passed = bool(test_fn())
        print('  -> %s: %s\n' % (label, 'PASS' if passed else 'FAIL'))
        score += 1 if passed else 0
    print('score: %d/%d' % (score, len(tests)))
    return 0 if score == len(tests) else 1


if __name__ == '__main__':
    sys.exit(main())


# End of file #
