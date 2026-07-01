#!/usr/bin/env python3
"""
probe_parser_vs_oracle.py  (repo root, run on macOS)

End-to-end validation of the PROJECT PARSER against the native OS oracle, after
adopting the byte+2 plane-resolution transform. For every installed layout, it
parses the layout with the project's own parse_uchr (off-Mac code path: it does
NOT call resolve_plane_tables_via_os -- see note below), then compares every
plane cell against build_os_reference (the real UCKeyTranslate).

This measures how well the project reproduces the native tool using only the
file-derived decode -- the whole point of the off-Mac path.

IMPORTANT: parse_uchr prefers the OS resolver on macOS. To test the FILE-ONLY
path here, we monkeypatch resolve_plane_tables_via_os to return None for the
duration, forcing the content/byte+2 resolver. The oracle reference still uses
the real OS, so this is a true file-only-vs-OS comparison even while on a Mac.

Reports per-layout cell match rate and an overall score, plus a breakdown of
the first few mismatches per layout for diagnosis. Dead-key cells are compared
as dead-vs-dead; character cells by exact string.

Usage:
  python3 probe_parser_vs_oracle.py
  python3 probe_parser_vs_oracle.py greek turkish russian
  python3 probe_parser_vs_oracle.py --max-miss 6
"""

import sys

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract import uchr_parse as up
from keylayout_to_xkb.extract import uckeytranslate as uckt
from keylayout_to_xkb.extract.uckeytranslate import build_os_reference, OSOracleUnavailable
from keylayout_to_xkb.common.models import ModifierState


__version__ = '20260626'


_PLANE_NAME = {
    ModifierState.PLAIN: 'plain',
    ModifierState.SHIFT: 'shift',
    ModifierState.OPTION: 'option',
    ModifierState.SHIFT_OPTION: 'shift_option',
}


def _force_file_only():
    """Make parse_uchr use the file-only resolver even on macOS.

    parse_uchr calls resolve_plane_tables_via_os first; we stub it to None so
    the byte+2 content resolver runs. Returns the original for restoration.
    """

    original = up.resolve_plane_tables_via_os
    up.resolve_plane_tables_via_os = lambda *a, **k: None
    return original


def main(argv):
    max_miss = 4
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == '--max-miss':
            max_miss = int(argv[i + 1]); i += 2
        else:
            args.append(argv[i].lower()); i += 1

    try:
        payloads = extract_all_layouts()
    except Exception as error:
        print(f'extraction failed (need macOS): {error}')
        return 2

    original_resolver = _force_file_only()
    try:
        grand_match = 0
        grand_total = 0
        layouts_clean = 0
        layouts_tested = 0
        worst = []

        for payload in payloads:
            data = payload.get('data')
            name = payload.get('name') or '?'
            if not data:
                continue
            if args and not any(tok in name.lower() for tok in args):
                continue

            # Parse with the project (file-only path forced).
            try:
                layout = up.parse_uchr(data, layout_name=name)
            except Exception as error:
                print(f'{name[:36]:36}  PARSE FAILED: {error}')
                continue

            # Oracle reference (real OS).
            try:
                ref = build_os_reference(data)
            except OSOracleUnavailable as reason:
                print(f'oracle unavailable (need macOS): {reason}')
                return 2

            cells = ref['cells']
            match = 0
            total = 0
            misses = []
            for (vk, plane_name), info in cells.items():
                os_out = info['output']
                os_dead = info['dead']
                # project's value for this vk/plane
                plane = next(p for p, n in _PLANE_NAME.items() if n == plane_name)
                ko = layout.keys.get(vk, {}).get(plane)
                if ko is None:
                    proj_out, proj_dead = '', False
                elif ko.kind.name == 'DEAD':
                    proj_out, proj_dead = '', True
                else:
                    proj_out, proj_dead = ko.output, False

                total += 1
                if os_dead:
                    ok = proj_dead
                else:
                    ok = (proj_out == os_out) or (proj_out == '' and os_out == '')
                if ok:
                    match += 1
                elif len(misses) < max_miss:
                    misses.append((vk, plane_name, proj_out, proj_dead, os_out, os_dead))

            grand_match += match
            grand_total += total
            layouts_tested += 1
            rate = 100.0 * match / total if total else 100.0
            if match == total:
                layouts_clean += 1
            else:
                worst.append((rate, name, match, total))
                print(f'{name[:36]:36} {match}/{total} ({rate:.1f}%)')
                for (vk, pn, po, pd, oo, od) in misses:
                    ps = 'DEAD' if pd else repr(po)
                    os_ = 'DEAD' if od else repr(oo)
                    print(f'     vk{vk:<3} {pn:12} project={ps} oracle={os_}')

        print('\n' + '=' * 60)
        print(f'layouts tested:      {layouts_tested}')
        print(f'layouts 100% clean:  {layouts_clean}')
        if grand_total:
            print(f'overall cell match:  {grand_match}/{grand_total} '
                  f'({100.0 * grand_match / grand_total:.2f}%)')
        if worst:
            worst.sort()
            print('\nlowest-scoring layouts:')
            for rate, name, m, t in worst[:15]:
                print(f'  {name}: {m}/{t} ({rate:.1f}%)')
    finally:
        up.resolve_plane_tables_via_os = original_resolver

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
