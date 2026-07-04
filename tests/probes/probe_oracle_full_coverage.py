#!/usr/bin/env python3
"""
tests/probes/probe_oracle_full_coverage.py  (run on the Mac)

The everything-everywhere oracle: verify the off-Mac extraction pathway --
every plane cell, every single-level composition, and every chain SEQUENCE the
compose emitter would write -- against the OS native tool, and report how
close the model really comes to matching it.

Design: the cell and composition sections are OS-first (reusing the standing
--verify machinery: enumerate what UCKeyTranslate produces, compare the
model). The sequence section is MODEL-first by necessity: enumerating all key
sequences OS-first is combinatorial (128 keys to stacking depth 4+), so
instead every claim the model's chain graph makes is executed against the
real tool, threading the dead-key state through successive UCKeyTranslate
calls exactly as typing would. Intermediate steps must stay silent and land
in the model's predicted state (OS deadKeyState == uchr state number, proven
on Latvian and Kildin Sami); the final step must produce the model's result.
The walk mirrors emit/compose._emit_state_lines, so what gets verified is
what gets emitted -- including the dead-key-triggered outputs the single-level
audit never sees (445 of them in Tibetan Wylie alone).

Usage:
  python3 tests/probes/probe_oracle_full_coverage.py                (all layouts)
  python3 tests/probes/probe_oracle_full_coverage.py Tibet Latvian  (filtered)
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

from keylayout_to_xkb.common.models import (
    OutputKind,
    PLANE_MODIFIER_BYTE,
)
from keylayout_to_xkb.verify.os_oracle import (
    build_os_reference,
    compare_reference,
)
from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.emit.compose import _CHAIN_BLOCKED_LAYOUTS
from keylayout_to_xkb.extract import uchr_parse
from keylayout_to_xkb.extract.uchr_parse import parse_uchr
from keylayout_to_xkb.extract.uckeytranslate import (
    _translate_step,
    _load_uckeytranslate,
)


__version__ = '20260703f'

# Mirrors emit/compose._MAX_CHAIN_DEPTH: the probe verifies what the emitter
# emits, so the two walks must share the same horizon.
_MAX_CHAIN_DEPTH = 6

_DIFF_SAMPLES_PER_LAYOUT = 6

# Accumulator-style state machines (Unicode Hex Input: 16 transitions per
# state, four deep = 65536 leaf claims) would otherwise dominate the catalog
# denominator and the runtime while verifying nothing new after the first few
# hundred paths.
_MAX_SEQUENCE_CLAIMS_PER_LAYOUT = 2000


def _key_lookup_maps(layout):
    """Reverse maps from the model's keys: how to PRESS a state or a char.

    Returns (dead_keys, char_keys):
      dead_keys: state_name -> (virtual_key, modifier_byte) entering that state
      char_keys: single char -> (virtual_key, modifier_byte) producing it
    Lower modifier bytes win ties (plain before shift before option...), so
    sequences use the simplest physical chord available.
    """

    dead_keys = {}
    char_keys = {}
    for virtual_key, planes in layout.keys.items():
        for plane, key_output in planes.items():
            modifier_byte = PLANE_MODIFIER_BYTE.get(plane)
            if modifier_byte is None:
                continue
            if key_output.kind is OutputKind.DEAD:
                name = key_output.dead_state_name
                if name is None:
                    continue
                known = dead_keys.get(name)
                if known is None or modifier_byte < known[1]:
                    dead_keys[name] = (virtual_key, modifier_byte)
            elif key_output.kind is OutputKind.CHARS:
                output = key_output.output
                if not output or len(output) != 1:
                    continue
                known = char_keys.get(output)
                if known is None or modifier_byte < known[1]:
                    char_keys[output] = (virtual_key, modifier_byte)
    return dead_keys, char_keys


def _composing_char_keys(data):
    """char -> (virtual_key, modifier_byte) for cells that actually COMPOSE.

    Composition reachability is per-CELL: only a cell whose raw entry is
    state-flagged (references a state record) composes; a literal cell
    producing the same character falls back to terminator + base. The parsed
    model cannot distinguish the two (both decode to CHARS), and preferring
    the lowest-modifier cell pressed literal cells on Azeri, Tongan, the
    Sami PC family and the whole Nordic no-sign group -- making the OS's
    correct fallback look like hundreds of model divergences. This map is
    built from the raw tables and OVERRIDES the model-derived lookup.
    """

    (_first, _last, mod_off, ci, sro, _term, seqo) = struct.unpack_from(
        '<IIIIIII', data, 12)
    char_tables = uchr_parse._parse_char_table_index(data, ci)
    state_records = uchr_parse._parse_state_records(data, sro)
    sequences = uchr_parse._parse_sequence_table(data, seqo)
    table_map, default_table = uchr_parse._parse_modifier_table_map(
        data, mod_off)

    composing = {}
    for plane, modifier_byte in sorted(
            PLANE_MODIFIER_BYTE.items(), key=lambda item: item[1]):
        if 0 <= modifier_byte < len(table_map):
            table_index = table_map[modifier_byte]
        else:
            table_index = default_table
        if not (0 <= table_index < len(char_tables)):
            continue
        offset, size = char_tables[table_index]
        for virtual_key in range(size):
            entry = struct.unpack_from(
                '<H', data, offset + 2 * virtual_key)[0]
            if entry in (0xFFFE, 0xFFFF):
                continue
            if (entry & 0xC000) != 0x4000:
                continue
            state_index = entry & 0x3FFF
            if state_index >= len(state_records):
                continue
            zero = state_records[state_index]['zero']
            if zero in (0xFFFE, 0xFFFF):
                continue
            resolved = uchr_parse._resolve_char_data(zero, sequences)
            if not resolved or len(resolved) != 1 or resolved in composing:
                continue
            composing[resolved] = (virtual_key, modifier_byte)
    return composing


def _sequence_claims(layout, dead_keys, char_keys):
    """Every sequence the compose walk would emit, as pressable key steps.

    Yields (steps, state_names, expected_result, description, final_base):
      steps: list of (virtual_key, modifier_byte), one per keypress
      state_names: the model's predicted state after each NON-final press
      expected_result: the final output string the model claims
      description: human-readable sequence for diff reporting
      final_base: the base CHAR of a char-keyed final step (None for a
        dead-keyed final), carried explicitly so the X-convention check can
        use it without parsing the description (repr(' ') contains a space)
    Claims whose trigger or base has no pressable key are counted by the
    caller via the None sentinel yielded as (None, None, None, reason, None).

    Unaddressable claims are EXPECTED on some layouts: Tibetan Wylie carries
    46 of them, all keyed by Latin vowels whose records are ORPHANS -- zero
    a Latin letter, next-state 0, referenced by no cell in any char table.
    Apple ships that composition data, but no key on the keyboard type can
    trigger it, so the OS cannot reach it either (the single-level OS-first
    audit never finds those results). They are honestly untestable, not a
    coverage gap.
    """

    def _walk(dead_state, steps, state_names, labels, visited):
        for base_char, result in sorted(dead_state.compositions.items()):
            press = char_keys.get(base_char)
            if press is None:
                yield (None, None, None,
                       'no key produces base %r' % base_char, None)
                continue
            yield (steps + [press], state_names, result,
                   ' '.join(labels + [repr(base_char)]), base_char)

        for trigger_state, result in sorted(
                dead_state.dead_compositions.items()):
            press = dead_keys.get(trigger_state)
            if press is None:
                yield (None, None, None,
                       'no key enters trigger state %s' % trigger_state, None)
                continue
            yield (steps + [press], state_names, result,
                   ' '.join(labels + ['dead:%s' % trigger_state]), None)

        if len(steps) >= _MAX_CHAIN_DEPTH - 1:
            return

        for base_char, next_name in sorted(dead_state.char_transitions.items()):
            next_state = layout.dead_states.get(next_name)
            press = char_keys.get(base_char)
            if next_state is None or next_name in visited or press is None:
                continue
            yield from _walk(
                next_state, steps + [press], state_names + [next_name],
                labels + [repr(base_char)], visited | {next_name})

        for trigger_state, next_name in sorted(
                dead_state.dead_transitions.items()):
            next_state = layout.dead_states.get(next_name)
            press = dead_keys.get(trigger_state)
            if next_state is None or next_name in visited or press is None:
                continue
            yield from _walk(
                next_state, steps + [press], state_names + [next_name],
                labels + ['dead:%s' % trigger_state], visited | {next_name})

    for state_name in sorted(layout.dead_states):
        dead_state = layout.dead_states[state_name]
        if not dead_state.ground:
            continue
        entry = dead_keys.get(state_name)
        if entry is None:
            yield (None, None, None,
                   'ground state %s has no entering key' % state_name, None)
            continue
        yield from _walk(dead_state, [entry], [state_name],
                         ['dead:%s' % state_name], {state_name})


def _run_sequence(handle, layout_ptr, kbd_type, steps, state_names):
    """Execute one sequence against the OS; return (final_output, problem).

    problem is None on a clean run, else a string naming the first divergent
    step: unexpected intermediate output, or a dead state whose CURRENT
    component does not match the model's prediction.

    STATE ENCODING: for chained (level 2+) states, UCKeyTranslate's
    deadKeyState is a stack encoding, (previous_state << 16) | current_state
    -- decoded from the first full-catalog run, where Polytonic reported
    0x0001_000A (ground 1, current 10), Hindi 0x0002_0001, and Wylie
    0x0013_0032, each with the LOW half exactly matching the model's
    prediction. Single-level states report the bare number (Latvian, Kildin).
    Only the low half is compared; a plain-number match is accepted too.
    """

    dead_state = 0
    for index, (virtual_key, modifier_byte) in enumerate(steps[:-1]):
        output, dead_state = _translate_step(
            handle, layout_ptr, kbd_type, virtual_key, modifier_byte,
            dead_state)
        if output:
            return output, ('step %d emitted %r instead of staying dead'
                            % (index + 1, output))
        if dead_state == 0:
            return '', ('step %d fell back to ground; the OS does not chain '
                        'here' % (index + 1))
        predicted = state_names[index]
        if not predicted.isdigit():
            continue
        current = dead_state & 0xFFFF
        if dead_state != int(predicted) and current != int(predicted):
            return '', ('step %d landed in OS state 0x%x (current %d, '
                        'stack %d), model predicts %s'
                        % (index + 1, dead_state, current,
                           dead_state >> 16, predicted))
    virtual_key, modifier_byte = steps[-1]
    output, _final_state = _translate_step(
        handle, layout_ptr, kbd_type, virtual_key, modifier_byte, dead_state)
    return output, None


def probe_layout(name, data, handle, kbd_type):
    """Verify one layout completely; returns a per-section counts dict."""

    counts = {'cells_agree': 0, 'cells_total': 0,
              'comps_agree': 0, 'comps_total': 0,
              'seq_agree': 0, 'seq_total': 0, 'seq_unaddressable': 0,
              'seq_convention': 0}

    # Parse from the record covering the Mac's REAL keyboard type -- the same
    # tables the oracle answers with (multi-record PC-family layouts differ
    # per type at the geometry keys).
    layout = parse_uchr(data, layout_name=name, kbd_type=kbd_type)

    reference = build_os_reference(data)
    result = compare_reference(layout, reference, name)
    counts['cells_agree'] = result.cells_agree
    counts['cells_total'] = result.cells_checked
    counts['comps_agree'] = result.comps_agree
    counts['comps_total'] = result.comps_checked

    buffer = ctypes.create_string_buffer(data, len(data))
    layout_ptr = ctypes.cast(buffer, ctypes.c_void_p)
    dead_keys, char_keys = _key_lookup_maps(layout)
    char_keys.update(_composing_char_keys(data))

    diffs = []
    capped = False
    chain_blocked = (name or '').strip().lower() in _CHAIN_BLOCKED_LAYOUTS
    for steps, state_names, expected, description, final_base in \
            () if chain_blocked else \
            _sequence_claims(layout, dead_keys, char_keys):
        if counts['seq_total'] >= _MAX_SEQUENCE_CLAIMS_PER_LAYOUT:
            capped = True
            break
        if steps is None:
            counts['seq_unaddressable'] += 1
            continue
        counts['seq_total'] += 1
        output, problem = _run_sequence(
            handle, layout_ptr, kbd_type, steps, state_names)
        if problem is None and output == expected:
            counts['seq_agree'] += 1
            continue
        # X-convention rows, same allowance the --verify audit applies: the
        # model deliberately stores terminator-only where the OS emits
        # terminator + base (canonically dead + space -> bare accent vs
        # accent-then-space).
        if (problem is None and final_base is not None
                and output == expected + final_base):
            counts['seq_agree'] += 1
            counts['seq_convention'] += 1
            continue
        if len(diffs) < _DIFF_SAMPLES_PER_LAYOUT:
            detail = problem or ('OS produced %r, model claims %r'
                                 % (output, expected))
            diffs.append('    [%s] -> %s' % (description, detail))

    if (counts['cells_agree'] != counts['cells_total']
            or counts['comps_agree'] != counts['comps_total']
            or counts['seq_agree'] != counts['seq_total']):
        notes = ''
        if chain_blocked:
            notes += '  (sequences skipped: chain-blocked layout)'
        if counts['seq_unaddressable']:
            notes += '  (%d unaddressable)' % counts['seq_unaddressable']
        if capped:
            notes += '  (claims capped at %d)' % _MAX_SEQUENCE_CLAIMS_PER_LAYOUT
        print('  %s: cells %d/%d  comps %d/%d  sequences %d/%d%s'
              % (name, counts['cells_agree'], counts['cells_total'],
                 counts['comps_agree'], counts['comps_total'],
                 counts['seq_agree'], counts['seq_total'], notes))
        for diff in result.cell_diffs[:4]:
            print('    cell vk 0x%02x %s: parser %r, OS %r [%s]'
                  % (diff.virtual_key, diff.plane, diff.parser_says,
                     diff.os_says, diff.kind))
        for diff in result.comp_diffs[:4]:
            print('    comp vk 0x%02x %s base %r: parser %r, OS %r'
                  % (diff.dead_key, diff.plane, diff.base_char,
                     diff.parser_says, diff.os_says))
        for line in diffs:
            print(line)
    return counts


def main(argv):
    print('oracle full-coverage probe (%s)\n' % __version__)
    filters = [arg.lower() for arg in argv]
    try:
        payloads = extract_all_layouts()
    except Exception as error:
        print('extraction failed (this probe needs macOS): %s' % error)
        return 2
    handle, kbd_type = _load_uckeytranslate()

    totals = {}
    layouts = 0
    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data:
            continue
        if filters and not any(f in name.lower() for f in filters):
            continue
        layouts += 1
        counts = probe_layout(name, data, handle, kbd_type)
        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + value

    if not layouts:
        print('no layouts matched the given filters.')
        return 1

    def _pct(agree, total):
        return 100.0 * agree / total if total else 100.0

    print('')
    print('layouts probed: %d' % layouts)
    print('cells:        %6d/%6d  (%.2f%%)'
          % (totals.get('cells_agree', 0), totals.get('cells_total', 0),
             _pct(totals.get('cells_agree', 0), totals.get('cells_total', 0))))
    print('compositions: %6d/%6d  (%.2f%%)'
          % (totals.get('comps_agree', 0), totals.get('comps_total', 0),
             _pct(totals.get('comps_agree', 0), totals.get('comps_total', 0))))
    print('sequences:    %6d/%6d  (%.2f%%)  [%d unaddressable, '
          '%d via X-convention]'
          % (totals.get('seq_agree', 0), totals.get('seq_total', 0),
             _pct(totals.get('seq_agree', 0), totals.get('seq_total', 0)),
             totals.get('seq_unaddressable', 0),
             totals.get('seq_convention', 0)))
    perfect = all(totals.get(a, 0) == totals.get(t, 0) for a, t in (
        ('cells_agree', 'cells_total'),
        ('comps_agree', 'comps_total'),
        ('seq_agree', 'seq_total'),
    ))
    if perfect:
        print('VERDICT: the extraction pathway matches the OS oracle '
              'completely.')
    else:
        print('VERDICT: divergences above; send this output for analysis.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
