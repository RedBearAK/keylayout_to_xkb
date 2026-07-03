#!/usr/bin/env python3
"""
tests/probes/probe_eight_level_discriminators.py

Scan Mac keyboard layouts for keys whose eight output levels are VISUALLY
distinguishable, to find practical confirmation keys for full 8-level access
in the emitted XKB layouts.

Background: the caps quartet of most Apple layouts largely encodes 'uppercase
the base layer', so many cells duplicate their base-plane counterparts (e.g.
PolishPro's r key has L8 == L4 == the pound sign). Level SELECTION is verified
by index in the xkb probes regardless, but a human at a keyboard confirming
'all eight layers reachable' needs keys whose glyphs actually differ. This
probe reports, per layout:

  * GOLD keys: all eight cells present, literal characters, pairwise distinct
    -- one key confirms the entire stack.
  * L8 discriminators: caps+shift+option differs from shift+option (the cell
    PolishPro never distinguishes).
  * L7 discriminators: caps+option differs from option.

Keys containing dead-key cells are excluded: a dead cell produces no immediate
glyph, so it cannot serve for visual confirmation typing.

Sources: on macOS, all installed layouts via TIS (positional args filter by
name, case-insensitive). Off-Mac, *.uchr files from /mnt/user-data/uploads
plus any file or directory paths given as args.

Usage (from repo root or anywhere):
  python3 tests/probes/probe_eight_level_discriminators.py
  python3 tests/probes/probe_eight_level_discriminators.py polish german
  python3 tests/probes/probe_eight_level_discriminators.py ./raw_dump
"""

import os
import sys
import glob


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

from keylayout_to_xkb.emit.symbols import _VK_TO_XKB
from keylayout_to_xkb.common.models import ModifierState, OutputKind
from keylayout_to_xkb.extract.uchr_parse import parse_uchr


__version__ = '20260702'


_UPLOADS = '/mnt/user-data/uploads'

# Emission order: indices 0..7 == XKB levels L1..L8.
_LEVEL_PLANES = (
    ModifierState.PLAIN,
    ModifierState.SHIFT,
    ModifierState.OPTION,
    ModifierState.SHIFT_OPTION,
    ModifierState.CAPS,
    ModifierState.CAPS_SHIFT,
    ModifierState.CAPS_OPTION,
    ModifierState.CAPS_SHIFT_OPTION,
)


def _collect_payloads(args):
    """Yield (name, raw_bytes) from TIS on macOS, else from .uchr files.

    Positional args act as case-insensitive name filters in TIS mode, and as
    extra file/directory sources in file mode.
    """

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
        name = os.path.basename(path).split('keylayout')[-1]
        name = name.strip('._-').replace('.uchr', '') or path
        with open(path, 'rb') as handle:
            yield name, handle.read()


def _level_cells(outputs):
    """Return the 8 cell strings for a key, or None where absent/non-CHARS."""

    cells = []
    for plane in _LEVEL_PLANES:
        key_output = outputs.get(plane)
        if key_output is None or key_output.kind is not OutputKind.CHARS:
            cells.append(None)
            continue
        cells.append(key_output.output)
    return cells


def _classify_key(cells):
    """Classify one key's cells: ('gold' | 'l8' | 'l7' | None)."""

    if any(cell is None for cell in cells):
        # A dead or absent cell cannot be visually confirmed by typing; only
        # fully populated CHARS keys qualify for any discriminator class.
        return None
    if len(set(cells)) == 8:
        return 'gold'
    if cells[7] != cells[3]:
        return 'l8'
    if cells[6] != cells[2]:
        return 'l7'
    return None


def _scan_layout(name, data):
    """Return per-class key lists for one layout, or None on parse failure."""

    try:
        layout = parse_uchr(data, layout_name=name)
    except Exception as error:
        print(f'  {name}: parse failed ({error})')
        return None
    found = {'gold': [], 'l8': [], 'l7': []}
    for virtual_key in sorted(layout.keys):
        cells = _level_cells(layout.keys[virtual_key])
        key_class = _classify_key(cells)
        if key_class is not None:
            found[key_class].append((virtual_key, cells))
    return found


def _describe_key(virtual_key, cells):
    xkb_code = _VK_TO_XKB.get(virtual_key, '?')
    stack = ' '.join('%2s' % cell for cell in cells)
    return "vk 0x%02x %-6s ('%s' key)  L1-L8: %s" % (
        virtual_key, xkb_code, cells[0], stack)


def main(argv):
    print('eight-level discriminator probe (%s)\n' % __version__)
    ranking = []
    scanned = 0
    for name, data in _collect_payloads(argv):
        found = _scan_layout(name, data)
        if found is None:
            continue
        scanned += 1
        gold, l8, l7 = found['gold'], found['l8'], found['l7']
        print('%-28s gold=%-3d L8-only=%-3d L7-only=%d'
              % (name, len(gold), len(l8), len(l7)))
        for virtual_key, cells in gold[:3]:
            print('    GOLD %s' % _describe_key(virtual_key, cells))
        if not gold:
            for virtual_key, cells in l8[:2]:
                print('    L8   %s' % _describe_key(virtual_key, cells))
        ranking.append((len(gold), len(l8), len(l7), name))

    if not scanned:
        print('no layouts found: run on macOS, or pass .uchr files/dirs, or')
        print('place *.uchr files in %s' % _UPLOADS)
        return 1

    ranking.sort(reverse=True)
    print('\nbest layouts for full 8-level confirmation:')
    for gold_count, l8_count, l7_count, name in ranking[:8]:
        verdict = ('one GOLD key confirms everything' if gold_count
                   else 'has L8 discriminators' if l8_count
                   else 'L7 only -- L8 not visually confirmable' if l7_count
                   else 'no visual discriminators')
        print('  %-28s gold=%-3d l8=%-3d l7=%-3d  %s'
              % (name, gold_count, l8_count, l7_count, verdict))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
