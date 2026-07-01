#!/usr/bin/env python3
"""
tests/probes/probe_deadkey_oracle.py  (run on macOS)

Verify dead-key COMPOSITIONS against the real UCKeyTranslate.

The question this settles: a layout's dead-key compositions can look incoherent
when dumped (e.g. one dead key appearing to yield mixed, unrelated accents). This
probe asks the OS directly -- it drives the real UCKeyTranslate through each
dead-key SEQUENCE (press the dead key, then press a base key while carrying the
dead-key state UCKeyTranslate hands back) and prints what macOS actually emits.
It then compares that to what the binary parser extracted for the same sequence.

If the OS output MATCHES the parser, the extraction is faithful and any apparent
"scramble" is just the layout's real design (a grab-bag of international
characters that does not read as a clean accent progression). If the OS output
DIFFERS, the parser's composition assembly has a genuine bug.

The OS path needs a real Mac (UCKeyTranslate from HIToolbox). Run:

    python3 tests/probes/probe_deadkey_oracle.py
    python3 tests/probes/probe_deadkey_oracle.py PolishPro German

Default target is PolishPro (the layout whose dead-key dump looked incoherent).
"""

import os
import sys
import ctypes
import struct


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
from keylayout_to_xkb.extract.uckeytranslate import (
    _load_uckeytranslate,
    _utf16_units_to_str,
)
from keylayout_to_xkb.extract.uchr_parse import parse_uchr
from keylayout_to_xkb.common.models import ModifierState, OutputKind
from keylayout_to_xkb.common.mac_virtual_keys import VK_NAMES, vk_name


__version__ = '20260629'


_KEY_ACTION_DOWN = 0

# Carbon modifier bytes for the planes a dead-key trigger or base key uses.
# Triggers on most layouts are Option (0x08); base keys are plain (0x00) or
# Shift (0x02). We probe base keys at plain and shift so capitals compose too.
_OPTION_BYTE = 0x08
_PLAIN_BYTE = 0x00
_SHIFT_BYTE = 0x02


def _press(handle, ptr, kbd_type, virtual_key, modifier_byte, dead_state):
    """One UCKeyTranslate press carrying an in/out dead-key state.

    Returns (output_str_or_None, new_dead_state). output is None when the press
    only entered/advanced a dead-key state with no emitted characters. The
    dead_state is threaded so a dead key followed by a base key composes exactly
    as the OS does it live.
    """

    state = ctypes.c_uint32(dead_state)
    buffer_len = 8
    actual_len = ctypes.c_ulong(0)
    out_buffer = (ctypes.c_uint16 * buffer_len)()
    status = handle.UCKeyTranslate(
        ptr,
        virtual_key,
        _KEY_ACTION_DOWN,
        modifier_byte,
        kbd_type,
        0,                                  # options 0: dead keys visible
        ctypes.byref(state),
        buffer_len,
        ctypes.byref(actual_len),
        out_buffer,
    )
    if status != 0:
        return ('', state.value)
    if actual_len.value == 0 and state.value != 0:
        return (None, state.value)
    return (_utf16_units_to_str(out_buffer, actual_len.value), state.value)


def _os_compose(handle, ptr, kbd_type, trigger_vk, trigger_byte, base_vk, base_byte):
    """Drive the OS through (dead key) then (base key); return the composed str.

    Mirrors a live keypress sequence: press the trigger (enters a dead state),
    then press the base key carrying that state. Returns the OS's emitted string
    (often the composed character), or '' if nothing was produced.
    """

    _out, dead_state = _press(handle, ptr, kbd_type, trigger_vk, trigger_byte, 0)
    if dead_state == 0:
        # Trigger did not actually enter a dead state at this plane.
        return None
    composed, _final = _press(handle, ptr, kbd_type, base_vk, base_byte, dead_state)
    return composed if composed is not None else ''


def _trigger_cells(layout):
    """Map dead-state name -> (trigger_vk, trigger_modifier_byte) from the parse.

    Finds each DEAD cell and records the key+plane that enters that state, so the
    probe can reproduce the same trigger the parser saw.
    """

    plane_byte = {
        ModifierState.PLAIN: _PLAIN_BYTE,
        ModifierState.SHIFT: _SHIFT_BYTE,
        ModifierState.OPTION: _OPTION_BYTE,
    }
    triggers = {}
    for vk, modmap in layout.keys.items():
        for plane, key_output in modmap.items():
            if key_output.kind is OutputKind.DEAD:
                byte = plane_byte.get(plane)
                if byte is not None and key_output.dead_state_name not in triggers:
                    triggers[key_output.dead_state_name] = (vk, byte, plane)
    return triggers


# Base keys to probe: the main letter block, both plain and shift, so we cover
# the same base characters the parser's compositions are keyed by.
def _base_probe_keys():
    """Yield (base_vk, base_byte, label) for letters at plain and shift."""

    letters = [vk for vk, name in VK_NAMES.items() if len(name) == 1 and name.isalpha()]
    for vk in sorted(letters):
        yield (vk, _PLAIN_BYTE, VK_NAMES[vk].lower())
    for vk in sorted(letters):
        yield (vk, _SHIFT_BYTE, VK_NAMES[vk].upper())


def _check_layout(name, data, handle, kbd_type):
    """Compare OS dead-key compositions to the parser's for one layout.

    Returns (match, fallback, mismatch, mismatches):
      match    -- real compositions the parser stored that equal the OS output
      fallback -- non-composing follows where the OS emits terminator+base and
                  the parser correctly stored no composition (faithful, not a bug)
      mismatch -- genuine disagreements worth investigating
      mismatches -- list of (state_name, base_label, os_output, parser_output)
    """

    layout = parse_uchr(data, layout_name=name)
    if not layout.dead_states:
        print(f'{name}: no dead states; nothing to check')
        return (0, 0, 0, [])

    buf = ctypes.create_string_buffer(data, len(data))
    ptr = ctypes.cast(buf, ctypes.c_void_p)
    triggers = _trigger_cells(layout)

    match = 0
    fallback = 0
    mismatch = 0
    mismatches = []

    for state_name, dead_state in layout.dead_states.items():
        trig = triggers.get(state_name)
        if trig is None:
            print(f'  {name} state {state_name}: no trigger cell found, skipping')
            continue
        trigger_vk, trigger_byte, trigger_plane = trig
        print(f'\n  {name} dead state {state_name} '
              f'(trigger {trigger_plane.value}+{vk_name(trigger_vk)}, '
              f'{len(dead_state.compositions)} parser compositions):')
        print(f'    {"base":6} {"OS":>10}   {"parser":>8}   verdict')
        for base_vk, base_byte, label in _base_probe_keys():
            os_out = _os_compose(
                handle, ptr, kbd_type,
                trigger_vk, trigger_byte, base_vk, base_byte,
            )
            if os_out is None:
                continue
            parser_out = dead_state.compositions.get(label)
            # Only report where at least one side has something to say.
            if not os_out and parser_out is None:
                continue

            if parser_out is not None:
                # The parser claims a real composition: it must match the OS.
                if os_out == parser_out:
                    match += 1
                    mark = 'ok'
                else:
                    mismatch += 1
                    mismatches.append((state_name, label, os_out, parser_out))
                    mark = 'DIFF'
            else:
                # No parser composition. The CORRECT OS behavior for a
                # non-composing follow is the dead state's terminator (its
                # standalone accent) followed by the unchanged base character.
                # Recognize that as a faithful fallback, not a mismatch -- the
                # parser intentionally stores no composition for these, and the
                # terminator (captured on the dead state) drives the fallback at
                # emit time.
                expected_fallback = (dead_state.terminator or '') + label
                if os_out == expected_fallback:
                    fallback += 1
                    mark = 'fallback'
                else:
                    # Parser has nothing AND the OS output is not the plain
                    # terminator+base fallback -- a genuine gap worth seeing.
                    mismatch += 1
                    mismatches.append((state_name, label, os_out, parser_out))
                    mark = 'DIFF'
            print(f'    {label:6} {os_out!r:>10}   {parser_out!r:>8}   {mark}')
    return (match, fallback, mismatch, mismatches)


def main(argv):
    wants = [a for a in argv] or ['PolishPro']
    try:
        payloads = extract_all_layouts()
    except Exception as error:
        print(f'extraction failed (need macOS): {error}')
        return 2
    handle, kbd_type = _load_uckeytranslate()
    print(f'kbd_type={kbd_type}\n')

    by_name = {}
    for payload in payloads:
        data = payload.get('data')
        if not data:
            continue
        # source_id is the stable dotted id, e.g. 'com.apple.keylayout.PolishPro'.
        # Key by the part after the last dot (the layout's canonical short name),
        # which is what the .uchr filenames and our arguments use. The 'name'
        # field is a localized display name and is unreliable for matching.
        source_id = payload.get('source_id') or ''
        short = source_id.rsplit('.', 1)[-1] if source_id else (payload.get('name') or '')
        if short:
            by_name[short.lower()] = data

    if not by_name:
        print('no keyboard layouts with uchr data were found on this system')
        return 2

    total_match = 0
    total_fallback = 0
    total_mismatch = 0
    for want in wants:
        data = by_name.get(want.lower())
        if data is None:
            # Try a contains-match for convenience.
            hits = [k for k in by_name if want.lower() in k]
            if len(hits) == 1:
                data = by_name[hits[0]]
            else:
                print(f'{want}: not found.')
                if hits:
                    print(f'  did you mean: {sorted(hits)[:8]}')
                else:
                    print(f'  available layouts ({len(by_name)}): '
                          f'{sorted(by_name)[:20]}')
                continue
        m, fb, mm, _ = _check_layout(want, data, handle, kbd_type)
        total_match += m
        total_fallback += fb
        total_mismatch += mm

    total = total_match + total_fallback + total_mismatch
    print('\n' + '=' * 60)
    if total == 0:
        print('no dead-key compositions compared')
        return 0
    faithful = total_match + total_fallback
    pct = 100.0 * faithful / total
    print(f'real compositions matching OS:   {total_match}')
    print(f'terminator-fallback (correct):   {total_fallback}')
    print(f'genuine mismatches:              {total_mismatch}')
    print(f'faithful total: {faithful}/{total} ({pct:.1f}%)')
    if total_mismatch == 0:
        print('VERDICT: extraction is faithful. Every real composition matches')
        print('the OS, and every non-composing follow is the OS terminator+base')
        print('fallback the parser correctly models via the dead-state terminator.')
        print('Eccentric-looking compositions (e.g. Polish Pro) are the real')
        print('layout, not a bug.')
    else:
        print(f'VERDICT: {total_mismatch} genuine mismatch(es) -- neither a real')
        print('composition match nor the terminator+base fallback. Investigate')
        print('these specific rows (shown as DIFF above).')
    return 0 if total_mismatch == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
