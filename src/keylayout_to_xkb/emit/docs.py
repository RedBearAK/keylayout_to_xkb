"""
keylayout_to_xkb/emit/docs.py

Generate a human-readable Markdown reference for a parsed Layout, in the spirit
of the hand-made optspecialchars catalogues (github.com/RedBearAK/optspecialchars).

The document is a FAITHFUL dump of what the keyboard produces: for every physical
key, what each modifier plane emits, plus the dead keys and their compositions
and the keyboard-type variants. It is organized by keyboard row (number row, top
letter row, home row, bottom row) so each table is small enough to print without
breaking mid-table, with HTML page breaks between sections for clean PDF export.

Design decisions (settled with the maintainer):
  * Physical key labels use the US-QWERTY position. That only reads correctly for
    layouts that ARE physically US-QWERTY; the header says so, and the per-key
    Base output is the true keycap legend for any layout.
  * Every plane that resolves to its OWN char table gets full row tables, even if
    some of its characters coincide with another plane -- this is documentation
    of what the keys produce, so coincidental duplicates are fine. A plane is
    only collapsed (folded into another plane's header) when it resolves to the
    EXACT SAME table as another plane, i.e. there is literally nothing new to
    show. This uses Variant.plane_tables (the layout's own modifier-map routing),
    not a cell-by-cell comparison.
  * Dead keys are named by their accent (from the terminator), with the trigger
    key and the standalone accent shown. Non-composing follows fall back to
    'accent + base' on the OS and are intentionally NOT enumerated.
  * Character descriptions come from unicodedata.name(), with a small fallback
    table for the Private-Use Apple logo and the C0/DEL control codes.

This module reads ONLY the parsed model; it performs no extraction and makes no
XKB decisions, so it can validate the extraction layer independently of the
symbols/compose emitters.
"""

import unicodedata

from keylayout_to_xkb.common.models import ModifierState, OutputKind
from keylayout_to_xkb.common.policy import layout_is_unicode_accumulator
from keylayout_to_xkb.common.mac_virtual_keys import VK_NAMES


__version__ = '20260629'


# Physical keyboard rows, by US-QWERTY legend name, left-to-right within each
# row. The space bar lives on the bottom row (it is just another key on that
# row). Names here must match VK_NAMES values.
KEYBOARD_ROWS = (
    ('Number row',
     ('Grave', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', 'Minus', 'Equal')),
    ('Top letter row (QWERTY)',
     ('Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P',
      'LeftBracket', 'RightBracket', 'Backslash')),
    ('Home row (ASDF)',
     ('A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', 'Semicolon', 'Quote')),
    ('Bottom row (ZXCV)',
     ('Z', 'X', 'C', 'V', 'B', 'N', 'M', 'Comma', 'Period', 'Slash', 'Space')),
)


# Planes in document order, with their display labels. Base is annotated so the
# unmodified layer is unambiguous.
_PLANE_ORDER = (
    (ModifierState.PLAIN,             'Base (No Modifiers)'),
    (ModifierState.SHIFT,             'Shift'),
    (ModifierState.OPTION,            'Option'),
    (ModifierState.SHIFT_OPTION,      'Shift + Option'),
    (ModifierState.CAPS,              'Caps'),
    (ModifierState.CAPS_SHIFT,        'Caps + Shift'),
    (ModifierState.CAPS_OPTION,       'Caps + Option'),
    (ModifierState.CAPS_SHIFT_OPTION, 'Caps + Shift + Option'),
)


# Friendly names for the bare accent a dead key produces (its terminator). The
# relationship between an accent character and its conventional name is not in
# the Unicode database in a usable form for spacing/combining accents, so this
# small table states it; describe() supplies a fallback for anything missing.
_ACCENT_NAMES = {
    '\u00a8': 'Diaeresis / Umlaut',
    '^':      'Circumflex',
    '`':      'Grave',
    '\u00b4': 'Acute',
    '~':      'Tilde',
    '\u00af': 'Macron',
    '\u02d8': 'Breve',
    '\u02d9': 'Dot above',
    '\u02da': 'Ring above',
    '\u02dd': 'Double acute',
    '\u02db': 'Ogonek',
    '\u02c7': 'Caron',
    '\u00b8': 'Cedilla',
    ',':      'Comma below',
    '\u00b0': 'Ring',
}


# Descriptions for codepoints unicodedata.name() cannot name: the Private-Use
# Apple logo and the C0/DEL control codes a faithful layout carries on its
# non-character keys.
_DESC_FALLBACK = {
    0xF8FF: 'Apple logo (private use)',
    0x00: 'Null', 0x03: 'Enter', 0x08: 'Backspace', 0x09: 'Tab',
    0x0A: 'Line feed', 0x0D: 'Return', 0x1B: 'Escape', 0x10: 'Function key',
    0x1C: 'Cursor left', 0x1D: 'Cursor right', 0x1E: 'Cursor up',
    0x1F: 'Cursor down', 0x7F: 'Delete', 0xA0: 'No-break space',
}


_NAME_TO_VK = {name: vk for vk, name in VK_NAMES.items()}


def _char_named(char: str) -> bool:
    """True if unicodedata can name this single character."""

    try:
        unicodedata.name(char)
        return True
    except ValueError:
        return False


def describe(output: str) -> str:
    """Human-readable description of an output string.

    Single characters use unicodedata.name() with the fallback table; multi-
    character strings describe each component joined by ' + '.
    """

    if not output:
        return ''
    if len(output) == 1:
        try:
            return unicodedata.name(output)
        except ValueError:
            return _DESC_FALLBACK.get(ord(output), f'U+{ord(output):04X} (unnamed)')
    parts = []
    for char in output:
        if _char_named(char):
            parts.append(unicodedata.name(char))
        else:
            parts.append(_DESC_FALLBACK.get(ord(char), f'U+{ord(char):04X}'))
    return ' + '.join(parts)


def _accent_name(terminator: str) -> str:
    """Friendly name for a dead key's bare accent (its terminator)."""

    if not terminator:
        return 'Dead key'
    if terminator in _ACCENT_NAMES:
        return _ACCENT_NAMES[terminator]
    return describe(terminator).title()


def _codepoints(output: str) -> str:
    """Space-separated U+XXXX list for an output string."""

    return ' '.join(f'U+{ord(char):04X}' for char in output) if output else ''


def _md_glyph(output: str) -> str:
    """Render an output string as a Markdown inline-code glyph, safely.

    Spaces are shown as visible tokens; a literal backtick is wrapped in double
    backticks with padding (the GFM-correct way to show a backtick in a code
    span); pipes are escaped so they do not break the table cell.
    """

    if output == ' ':
        return '(space)'
    if output == '\u00a0':
        return '(nbsp)'
    escaped = output.replace('|', '\\|')
    if '`' in output:
        return '`` ' + escaped + ' ``'
    return '`' + escaped + '`'


def _cell(key_output) -> 'tuple | None':
    """Reduce a KeyOutput to ('dead', state) / ('chars', text) / None."""

    if key_output is None:
        return None
    if key_output.kind is OutputKind.DEAD:
        return ('dead', key_output.dead_state_name)
    if key_output.kind is OutputKind.CHARS and key_output.output:
        return ('chars', key_output.output)
    return None


def _trigger_label(keys: dict, state_name: str) -> str:
    """Readable 'Option+U'-style label for the key that enters a dead state."""

    plane_label = {
        ModifierState.PLAIN: '',
        ModifierState.SHIFT: 'Shift',
        ModifierState.OPTION: 'Option',
        ModifierState.SHIFT_OPTION: 'Shift+Option',
    }
    for virtual_key, modmap in keys.items():
        for plane, key_output in modmap.items():
            if (key_output.kind is OutputKind.DEAD
                    and key_output.dead_state_name == state_name):
                prefix = plane_label.get(plane, plane.value)
                key_name = VK_NAMES.get(virtual_key, str(virtual_key))
                return f'{prefix}+{key_name}' if prefix else key_name
    return '?'


def _plane_groups(variant) -> 'list[tuple[list, list]]':
    """Group planes that resolve to the same char table.

    Returns a list of (planes, labels) groups, in document order. Planes whose
    plane_tables index is equal share a group (documentation-identical, so one
    table set documents them all under a combined header). Planes with no
    plane_tables info, or a unique index, stand alone. Only planes that are
    actually present in the variant's keys are considered.
    """

    plane_tables = getattr(variant, 'plane_tables', {}) or {}
    present = set()
    for modmap in variant.keys.values():
        present.update(modmap.keys())

    groups = []
    index_to_group = {}
    for plane, label in _PLANE_ORDER:
        if plane not in present:
            continue
        table_index = plane_tables.get(plane)
        # Only collapse when we have a real index to compare; otherwise the plane
        # always stands on its own (faithful: never hide a plane on a guess).
        if table_index is not None and table_index in index_to_group:
            planes, labels = index_to_group[table_index]
            planes.append(plane)
            labels.append(label)
        else:
            group = ([plane], [label])
            groups.append(group)
            if table_index is not None:
                index_to_group[table_index] = group
    return groups


def _row_tables_for_planes(variant, planes: list, lines: list) -> None:
    """Emit per-keyboard-row tables for a group of planes (the group's content
    is identical across the planes, so the FIRST plane drives the output)."""

    plane = planes[0]
    keys = variant.keys
    for row_label, key_names in KEYBOARD_ROWS:
        rows = []
        for key_name in key_names:
            virtual_key = _NAME_TO_VK.get(key_name)
            if virtual_key is None or virtual_key not in keys:
                continue
            cell = _cell(keys[virtual_key].get(plane))
            if cell is None:
                continue
            rows.append((key_name, cell))
        if not rows:
            continue
        lines.append(f'\n**{row_label}**\n')
        lines.append('\n| Key | Output | Code point | Description |')
        lines.append('|-----|--------|-----------|-------------|')
        for key_name, cell in rows:
            if cell[0] == 'dead':
                accent = _dead_accent_labels.get(cell[1], cell[1])
                lines.append(
                    f'| {key_name} | \u27e8dead key\u27e9 | | Dead key: {accent} |'
                )
            else:
                lines.append(
                    f'| {key_name} | {_md_glyph(cell[1])} | '
                    f'{_codepoints(cell[1])} | {describe(cell[1])} |'
                )


# Populated per-call by generate_layout_doc so _row_tables_for_planes can render
# a dead cell with its accent name. Kept module-level to avoid threading it
# through every helper; generate_layout_doc sets it before emitting any rows.
_dead_accent_labels = {}


def _summary_section(variant, lines: list) -> None:
    """Emit the by-keyboard-row by-key summary (four base planes per key)."""

    keys = variant.keys

    def glyph(virtual_key, plane):
        cell = _cell(keys.get(virtual_key, {}).get(plane))
        if cell is None:
            return ''
        if cell[0] == 'dead':
            return '\u27e8dead\u27e9'
        return _md_glyph(cell[1])

    first_row = True
    for row_label, key_names in KEYBOARD_ROWS:
        present_names = [
            name for name in key_names
            if _NAME_TO_VK.get(name) in keys
            and _cell(keys.get(_NAME_TO_VK[name], {}).get(ModifierState.PLAIN)) is not None
        ]
        if not present_names:
            continue
        if not first_row:
            lines.append('\n<div style="page-break-before: always"></div>\n')
        first_row = False
        lines.append(f'\n**{row_label}**\n')
        lines.append('\n| Key | Base | Shift | Option | Shift+Option |')
        lines.append('|-----|------|-------|--------|--------------|')
        for name in present_names:
            virtual_key = _NAME_TO_VK[name]
            lines.append(
                f'| {name} | {glyph(virtual_key, ModifierState.PLAIN)} | '
                f'{glyph(virtual_key, ModifierState.SHIFT)} | '
                f'{glyph(virtual_key, ModifierState.OPTION)} | '
                f'{glyph(virtual_key, ModifierState.SHIFT_OPTION)} |'
            )


def _dead_key_section(layout, variant, lines: list) -> None:
    """Emit the dead-key composition tables."""

    if not layout.dead_states:
        return
    lines.append('\n<div style="page-break-before: always"></div>\n')
    lines.append('\n## Dead keys\n')
    if layout_is_unicode_accumulator(layout):
        lines.append(
            '\nThis layout uses its dead keys as a Unicode hex-digit '
            'accumulator: holding Option and typing a four-digit hex code '
            'enters the corresponding codepoint (65,536 leaf sequences in '
            'total). Enumerating the intermediate states adds nothing a reader '
            'can use, so this section is collapsed -- %d dead-key states '
            'omitted.\n' % len(layout.dead_states)
        )
        lines.append(
            '\nOn Linux, the direct equivalent is Ctrl+Shift+U followed by the '
            'hex codepoint, committed with Space or Enter. This is the GTK '
            'Unicode input method: it works natively in GTK apps (including '
            'GTK4 under Wayland) and elsewhere through the IBus or fcitx5 '
            'input frameworks, and -- unlike the macOS Unicode Hex Input -- it '
            'can enter codepoints beyond U+FFFF, such as emoji.\n'
        )
        lines.append(
            '\nFor accents and symbols without memorizing codepoints, the '
            'Compose (Multi_key) key offers thousands of mnemonic sequences '
            '(for example, Compose followed by a quote and then a vowel yields '
            'an accented letter such as \u00e9), configurable per desktop and '
            'extensible through a personal ~/.XCompose file.\n'
        )
        return
    lines.append(
        '\nEach dead key composes with the next key pressed. A base key with no '
        'listed composition falls back to the dead key\'s accent character '
        'followed by the base character (for example, umlaut then `s` gives '
        '`\u00a8s`).\n'
    )
    for state_name, dead_state in layout.dead_states.items():
        terminator = getattr(dead_state, 'terminator', '') or ''
        trigger = _trigger_label(variant.keys, state_name)
        lines.append(f'\n### {_accent_name(terminator)} (reached by {trigger})\n')
        lines.append(
            f'\nStandalone accent: {_md_glyph(terminator)} '
            f'({describe(terminator)}). {len(dead_state.compositions)} '
            f'compositions.\n'
        )
        lines.append('\n| Base | Result | Code point | Description |')
        lines.append('|------|--------|-----------|-------------|')
        for base, result in dead_state.compositions.items():
            lines.append(
                f'| {_md_glyph(base)} | {_md_glyph(result)} | '
                f'{_codepoints(result)} | {describe(result)} |'
            )


def generate_layout_doc(layout, variant=None) -> str:
    """Build the Markdown reference for a layout (its primary variant by default).

    Pass a specific Variant to document a non-primary keyboard-type variant.
    Returns the document as a single string; the caller writes it to a file.
    """

    if variant is None:
        variant = layout.variants[0] if layout.variants else None
    if variant is None:
        raise ValueError('generate_layout_doc: layout has no variant to document')

    global _dead_accent_labels
    _dead_accent_labels = {
        name: _accent_name(getattr(dead_state, 'terminator', '') or '')
        for name, dead_state in layout.dead_states.items()
    }

    lines = []
    lines.append(f'# {layout.name} \u2014 keyboard layout reference\n')
    lines.append(
        'Generated by keylayout_to_xkb. Physical key labels use the US-QWERTY '
        'position; for a layout that is physically US-QWERTY the labels match '
        'the keycaps, otherwise read each key\'s Base output as the true legend.\n'
    )

    tags = [v.tag or 'primary' for v in layout.variants]
    if len(tags) > 1:
        lines.append(f'\n**Keyboard-type variants:** {", ".join(tags)}\n')

    # By-key summary.
    lines.append('\n## By-key summary\n')
    lines.append(
        '\nQuick reference: the four base planes for every character key, by '
        'keyboard row. Per-layer sections below add the caps planes, code '
        'points, and full descriptions.\n'
    )
    _summary_section(variant, lines)

    # Per-layer detail, collapsing planes that resolve to the same table.
    for planes, labels in _plane_groups(variant):
        # A group with content on at least one row earns a section.
        has_content = False
        for _row_label, key_names in KEYBOARD_ROWS:
            for key_name in key_names:
                virtual_key = _NAME_TO_VK.get(key_name)
                if virtual_key in variant.keys and _cell(
                    variant.keys[virtual_key].get(planes[0])
                ) is not None:
                    has_content = True
                    break
            if has_content:
                break
        if not has_content:
            continue
        lines.append('\n<div style="page-break-before: always"></div>\n')
        lines.append(f'\n## Layer: {" / ".join(labels)}\n')
        if len(labels) > 1:
            lines.append(
                f'\n(These modifier combinations produce identical output; '
                f'they share one key table.)\n'
            )
        _row_tables_for_planes(variant, planes, lines)

    # Dead keys.
    _dead_key_section(layout, variant, lines)

    return '\n'.join(lines) + '\n'


def _main(argv) -> int:
    """Generate a doc from a .uchr or .keylayout file path, for manual use.

    Usage: python3 -m keylayout_to_xkb.emit.docs <file> [output.md]
    Prints to stdout if no output path is given.
    """

    if not argv:
        print('usage: docs.py <file.uchr|file.keylayout> [output.md]')
        return 2
    path = argv[0]
    if path.endswith('.keylayout'):
        from keylayout_to_xkb.extract.keylayout_xml import parse_keylayout_xml
        layout = parse_keylayout_xml(path)
    else:
        from keylayout_to_xkb.extract.uchr_parse import parse_uchr
        with open(path, 'rb') as handle:
            data = handle.read()
        name = path.split('/')[-1].split('.')[0]
        layout = parse_uchr(data, layout_name=name)

    doc = generate_layout_doc(layout)
    if len(argv) > 1:
        with open(argv[1], 'w') as handle:
            handle.write(doc)
        print(f'wrote {len(doc)} chars to {argv[1]}')
    else:
        print(doc)
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(_main(sys.argv[1:]))


# End of file #
