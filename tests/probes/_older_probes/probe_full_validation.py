#!/usr/bin/env python3
"""
probe_full_validation.py  (repo root, run on macOS)

Comprehensive parser-vs-oracle validator. For every installed layout it parses
with the project's own parse_uchr (file-only path forced) and checks EVERY
variant the parser produced -- the primary plus each ANSI/ISO/JIS variant --
against the native UCKeyTranslate, with each variant driven at its OWN keyboard
type. This is the single check that verifies the whole multi-variant pipeline:
plane resolution, entry decode, dead keys, sequences, SMP, AND keyboard-type
variant resolution.

For each variant the comparison uses the variant's representative keyboard type
(from its keyboard_type_range) so the OS produces that physical layout's output.
Cells are compared exactly (chars and dead-key state); the few known per-layout
quirk cells will show as small residual mismatches.

Reports, per layout: each variant's match rate. Aggregates: clean-variant count,
overall cell match, and the lowest-scoring variants for follow-up.

Usage:
  python3 probe_full_validation.py
  python3 probe_full_validation.py turkish wancho
  python3 probe_full_validation.py --max-miss 6
"""

import sys
import struct
import ctypes

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract import uchr_parse as up
from keylayout_to_xkb.extract.uckeytranslate import _load_uckeytranslate, _translate
from keylayout_to_xkb.common.models import ModifierState


__version__ = '20260626'


_PLANE_BYTES = {
    ModifierState.PLAIN: 0x00,
    ModifierState.SHIFT: 0x02,
    ModifierState.OPTION: 0x08,
    ModifierState.SHIFT_OPTION: 0x0A,
}


def _force_file_only():
    original = up.resolve_plane_tables_via_os
    up.resolve_plane_tables_via_os = lambda *a, **k: None
    return original


def _variant_type(variant):
    """A representative keyboard type to drive the OS at for this variant.

    Uses the variant's keyboard_type_range (the record's first gestalt type).
    Falls back to 0 if unknown.
    """

    if variant.keyboard_type_range:
        return variant.keyboard_type_range[0]
    return 0


def _os_cell(handle, ptr, kbd_type, byte, vk):
    produced = _translate(handle, ptr, kbd_type, vk, byte)
    if produced is None:
        return ('DEAD', None)
    return ('CHARS', produced)


def _parser_cell(variant, vk, plane):
    ko = variant.keys.get(vk, {}).get(plane)
    if ko is None:
        return ('NONE', None)
    if ko.kind.name == 'DEAD':
        return ('DEAD', None)
    return ('CHARS', ko.output)


def main(argv):
    max_miss = 4
    wants = []
    i = 0
    while i < len(argv):
        if argv[i] == '--max-miss':
            max_miss = int(argv[i + 1]); i += 2
        else:
            wants.append(argv[i].lower()); i += 1

    try:
        payloads = extract_all_layouts()
    except Exception as error:
        print(f'extraction failed (need macOS): {error}')
        return 2
    handle, _build_type = _load_uckeytranslate()
    original = _force_file_only()
    try:
        grand_match = grand_total = 0
        variants_total = variants_clean = 0
        worst = []

        for payload in payloads:
            data = payload.get('data')
            name = payload.get('name') or '?'
            if not data:
                continue
            if wants and not any(tok in name.lower() for tok in wants):
                continue
            try:
                layout = up.parse_uchr(data, layout_name=name)
            except Exception as error:
                print(f'{name[:34]:34} PARSE FAILED: {error}')
                continue
            buf = ctypes.create_string_buffer(data, len(data))
            ptr = ctypes.cast(buf, ctypes.c_void_p)

            printed = False
            for variant in layout.variants:
                kbd_type = _variant_type(variant)
                # determine key count from the variant's own keys
                vks = sorted(variant.keys.keys())
                if not vks:
                    continue
                match = total = 0
                misses = []
                for vk in range(max(vks) + 1):
                    for plane, byte in _PLANE_BYTES.items():
                        ours = _parser_cell(variant, vk, plane)
                        os_ = _os_cell(handle, ptr, kbd_type, byte, vk)
                        # "parser has nothing" and "OS emits empty" both mean no
                        # output -- skip those as non-cells, not mismatches.
                        if ours == ('NONE', None) and os_ == ('CHARS', ''):
                            continue
                        total += 1
                        if ours == os_:
                            match += 1
                        elif len(misses) < max_miss:
                            misses.append((vk, plane, ours, os_))
                grand_match += match
                grand_total += total
                variants_total += 1
                rate = 100.0 * match / total if total else 100.0
                tag = variant.tag or 'primary'
                if match == total:
                    variants_clean += 1
                else:
                    worst.append((rate, name, tag, match, total))
                    if not printed:
                        print(f'=== {name} ===')
                        printed = True
                    print(f'  variant {tag:9} (type {kbd_type:3}) '
                          f'{match}/{total} ({rate:.1f}%)')
                    for vk, plane, ours, os_ in misses:
                        print(f'      vk{vk:<3} {plane.value:12} '
                              f'parser={ours} os={os_}')

        print('\n' + '=' * 60)
        print(f'variants checked:   {variants_total}')
        print(f'variants 100% clean:{variants_clean}')
        if grand_total:
            print(f'overall cell match: {grand_match}/{grand_total} '
                  f'({100.0 * grand_match / grand_total:.2f}%)')
        if worst:
            worst.sort()
            print('\nlowest-scoring variants:')
            for rate, name, tag, m, t in worst[:20]:
                print(f'  {name} [{tag}]: {m}/{t} ({rate:.1f}%)')
    finally:
        up.resolve_plane_tables_via_os = original
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
