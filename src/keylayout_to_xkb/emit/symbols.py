"""
keylayout_to_xkb/emit/symbols.py

Emit an XKB symbols block (plus the xkb_types it needs) from a parsed Layout,
faithfully reproducing each key's intrinsic modifier behavior.

The core principle: in XKB a key's TYPE is a per-key fact -- it declares how many
levels the key has and which modifier combination selects each one. We therefore
"play the record" for every key: read which planes it actually produces output
on, route each plane to its XKB modifier combination, and let that key advertise
exactly those levels. Keys that share an identical modifier signature are grouped
under one generated type purely so the file is not a thousand one-off types; the
grouping is a convenience, not a semantic merge. A uniform Latin layout collapses
to a single large type; a layout like Tibetan (whole Latin alphabet behind Caps,
varying per key) naturally yields several types of different widths. There is no
fixed level count and no NoSymbol padding to a layout-wide width -- the level
count falls out of the data per key-group.

Plane -> XKB modifier combination -> level:
    PLAIN              none                       level 1
    SHIFT              Shift                      level 2
    OPTION             LevelThree                 level 3
    SHIFT_OPTION       Shift + LevelThree         level 4
    CAPS               MacCaps                    level 5
    CAPS_SHIFT         MacCaps + Shift            level 6
    CAPS_OPTION        MacCaps + LevelThree       level 7
    CAPS_SHIFT_OPTION  MacCaps + Shift+LevelThree level 8

MacCaps is a custom modifier on Mod3 (the conventional free bit: Mod1=Alt,
Mod2=NumLock, Mod4=Super, Mod5=LevelThree are taken). The physical Caps Lock key
is bound to Mod3 via LockMods, so Caps toggles the caps layer on/off exactly like
macOS (verified on hardware: macOS Caps is a pure toggle/latch, not a hold), and
Shift/Option then select within it. This is entirely self-contained in the
emitted layout -- no external keymapper is required.

When a key's signature omits a plane (e.g. a Tibetan key with no Option output),
that key's type simply has no level for that modifier combination: pressing it
produces nothing, which is faithful. When two planes resolve to the SAME char
table (Variant.plane_tables), both modifier combinations map to the same level
index in the type, so e.g. a US key's Caps+Shift reaches the same level as Shift
without a duplicate level -- the record is played, duplicates are not invented.

Each cell becomes an XKB keysym token via emit/classify.py:
  * CHARS single codepoint -> named keysym or UXXXX
  * DEAD                    -> dead_* keysym
  * CHARS multi-codepoint   -> NoSymbol here, routed to XCompose by compose.py

The virtual-key -> XKB keycode map is by PHYSICAL POSITION (Mac virtual keycodes
are physical-position codes), correct regardless of which character a layout
assigns. ISO/ANSI keyboard variants differ only by the <TLDE>/<LSGT> arrangement.
"""

from keylayout_to_xkb.common.models import Layout, OutputKind, ModifierState
from keylayout_to_xkb.emit.classify import (
    char_to_keysym,
    dead_state_keysym,
    reserve_placeholders,
)


__version__ = '20260629'


# Planes in canonical level order, each with the XKB modifier-combination tokens
# that select it. MacCaps is the custom Mod3 modifier; LevelThree is the standard
# level-3 selector (RightAlt). An empty tuple is the no-modifier base level.
_PLANE_LEVEL_ORDER = (
    (ModifierState.PLAIN,             ()),
    (ModifierState.SHIFT,             ('Shift',)),
    (ModifierState.OPTION,            ('LevelThree',)),
    (ModifierState.SHIFT_OPTION,      ('Shift', 'LevelThree')),
    (ModifierState.CAPS,              ('MacCaps',)),
    (ModifierState.CAPS_SHIFT,        ('MacCaps', 'Shift')),
    (ModifierState.CAPS_OPTION,       ('MacCaps', 'LevelThree')),
    (ModifierState.CAPS_SHIFT_OPTION, ('MacCaps', 'Shift', 'LevelThree')),
)

_PLANE_INDEX = {plane: i for i, (plane, _mods) in enumerate(_PLANE_LEVEL_ORDER)}


# Mac virtual keycode -> XKB keycode name, by PHYSICAL POSITION. Standard Mac
# kVK_* constants for the ANSI block, plus ISO <LSGT> (0x0A). Position-based, so
# correct regardless of which character a layout assigns.
_VK_TO_XKB = {
    0x32: 'TLDE',
    0x12: 'AE01', 0x13: 'AE02', 0x14: 'AE03', 0x15: 'AE04', 0x17: 'AE05',
    0x16: 'AE06', 0x1A: 'AE07', 0x1C: 'AE08', 0x19: 'AE09', 0x1D: 'AE10',
    0x1B: 'AE11', 0x18: 'AE12',
    0x0C: 'AD01', 0x0D: 'AD02', 0x0E: 'AD03', 0x0F: 'AD04', 0x11: 'AD05',
    0x10: 'AD06', 0x20: 'AD07', 0x22: 'AD08', 0x1F: 'AD09', 0x23: 'AD10',
    0x21: 'AD11', 0x1E: 'AD12',
    0x00: 'AC01', 0x01: 'AC02', 0x02: 'AC03', 0x03: 'AC04', 0x05: 'AC05',
    0x04: 'AC06', 0x26: 'AC07', 0x28: 'AC08', 0x25: 'AC09', 0x29: 'AC10',
    0x27: 'AC11', 0x2A: 'BKSL',
    0x0A: 'LSGT',
    0x06: 'AB01', 0x07: 'AB02', 0x08: 'AB03', 0x09: 'AB04', 0x0B: 'AB05',
    0x2D: 'AB06', 0x2E: 'AB07', 0x2B: 'AB08', 0x2F: 'AB09', 0x2C: 'AB10',
    0x31: 'SPCE',
}


def _cell_token(layout: Layout, virtual_key: int, plane: ModifierState,
                placeholders: 'dict') -> 'str | None':
    """XKB keysym token for one cell, or None if the cell is absent.

    DEAD -> its named dead_* keysym if the diacritic has one, else the dead-state
    PUA placeholder (for non-standard dead keys like a numero sign or a Vietnamese
    base-vowel tone key); single-char CHARS -> named keysym or UXXXX; multi-char
    CHARS -> the cell's multi-char placeholder keysym. `placeholders` is the
    reserve_placeholders() result ({'multichar':..., 'deadkey':...}). Returns None
    only when there is genuinely no output for the cell.
    """

    key_output = layout.keys.get(virtual_key, {}).get(plane)
    if key_output is None:
        return None
    if key_output.kind is OutputKind.DEAD:
        state = layout.dead_states.get(key_output.dead_state_name)
        if state is None:
            return None
        named = dead_state_keysym(state.terminator, state.compositions)
        if named is not None:
            return named
        return placeholders['deadkey'].get(key_output.dead_state_name)
    if key_output.kind is OutputKind.CHARS:
        if len(key_output.output) > 1:
            return placeholders['multichar'].get(key_output.output)
        return char_to_keysym(key_output.output)
    return None


def _key_present_planes(layout: Layout, virtual_key: int) -> 'list[ModifierState]':
    """The planes this key actually produces output on, in canonical order.

    A plane counts as present when the key has a DEAD cell or a non-empty CHARS
    cell there. Planes the key lacks are simply absent (the key has no level for
    that modifier combination), which is faithful to the source.
    """

    modmap = layout.keys.get(virtual_key, {})
    present = []
    for plane, _mods in _PLANE_LEVEL_ORDER:
        key_output = modmap.get(plane)
        if key_output is None:
            continue
        if key_output.kind is OutputKind.DEAD:
            present.append(plane)
        elif key_output.kind is OutputKind.CHARS and key_output.output:
            present.append(plane)
    return present


def _key_signature(layout: Layout, plane_tables: 'dict', virtual_key: int) -> 'tuple':
    """A key's intrinsic modifier behavior, as a hashable signature.

    Two keys share a signature (and therefore a type) when they have output on
    the same planes AND those planes route to the same relative table structure.
    The signature is the tuple of (plane_index, table_index) for present planes;
    table_index comes from plane_tables so planes that collapse to a shared table
    (e.g. Caps+Shift == Shift on a Latin layout) group consistently. A None
    table_index is kept as-is, so such keys only group with others also lacking
    it on that plane.
    """

    signature = []
    for plane in _key_present_planes(layout, virtual_key):
        signature.append((_PLANE_INDEX[plane], plane_tables.get(plane)))
    return tuple(signature)


def _type_name(stem: str, ordinal: int) -> str:
    """Generated type name, unique within the emitted file."""

    return 'MAC_%s_%d' % (stem.upper(), ordinal)


def _build_type_levels(present_planes: 'list[ModifierState]') -> 'list[tuple]':
    """Assign present planes to consecutive level indices (1-based).

    Returns a list of (level_index, plane, modifier_tokens). Planes keep their
    canonical order; level indices are consecutive from 1 with no gaps (XKB
    levels must be contiguous). The modifier tokens are what the type's map[]
    line lists to select that level.
    """

    levels = []
    modifier_lookup = dict(_PLANE_LEVEL_ORDER)
    for index, plane in enumerate(present_planes, start=1):
        levels.append((index, plane, modifier_lookup[plane]))
    return levels


def _render_type(type_name: str, levels: 'list[tuple]') -> str:
    """Render one xkb_types entry for a key-group's level structure."""

    used_modifiers = []
    for _index, _plane, mod_tokens in levels:
        for token in mod_tokens:
            if token not in used_modifiers:
                used_modifiers.append(token)

    lines = []
    lines.append('    type "%s" {' % type_name)
    if used_modifiers:
        lines.append('        modifiers = %s;' % '+'.join(used_modifiers))
    else:
        lines.append('        modifiers = None;')

    for index, _plane, mod_tokens in levels:
        combo = '+'.join(mod_tokens) if mod_tokens else 'None'
        lines.append('        map[%s] = Level%d;' % (combo, index))

    for index, plane, _mod_tokens in levels:
        lines.append('        level_name[Level%d] = "%s";' % (index, plane.value))

    lines.append('    };')
    return '\n'.join(lines)


def _level_tokens(layout: Layout, virtual_key: int,
                  present_planes: 'list[ModifierState]',
                  placeholders: 'dict') -> 'list[str]':
    """The keysym tokens for a key, one per present plane (in level order).

    Multi-char cells resolve to their PUA placeholder keysym (expanded by an
    XCompose rule). A cell that genuinely yields no token (e.g. a multi-char
    string with no allocated placeholder, which should not happen) becomes
    NoSymbol so the level position is preserved.
    """

    tokens = []
    for plane in present_planes:
        token = _cell_token(layout, virtual_key, plane, placeholders)
        tokens.append(token if token is not None else 'NoSymbol')
    return tokens


def _build_key_groups(layout: Layout, plane_tables: 'dict') -> 'tuple':
    """Group keys by signature and build the per-group types and key rows.

    Returns (types, key_rows):
      types    -- list of (type_name, type_text) in stable order
      key_rows -- dict xkb_code -> (type_name, tokens_csv)
    Keys with no present planes (nothing to type) are omitted entirely.
    """

    placeholders = reserve_placeholders(layout)

    signature_keys = {}
    signature_planes = {}
    for virtual_key, xkb_code in _VK_TO_XKB.items():
        if virtual_key not in layout.keys:
            continue
        present = _key_present_planes(layout, virtual_key)
        if not present:
            continue
        signature = _key_signature(layout, plane_tables, virtual_key)
        signature_keys.setdefault(signature, []).append((virtual_key, xkb_code))
        signature_planes[signature] = present

    ordered_signatures = sorted(
        signature_keys,
        key=lambda sig: (-len(signature_keys[sig]), sig),
    )

    types = []
    key_rows = {}
    for ordinal, signature in enumerate(ordered_signatures, start=1):
        present = signature_planes[signature]
        levels = _build_type_levels(present)
        type_name = _type_name('KEY', ordinal)
        types.append((type_name, _render_type(type_name, levels)))
        for virtual_key, xkb_code in signature_keys[signature]:
            tokens = _level_tokens(layout, virtual_key, present, placeholders)
            key_rows[xkb_code] = (type_name, ', '.join(tokens))

    return types, key_rows


def _render_layout(types: 'list', key_rows: 'dict',
                   variant_name: str, display_name: str) -> str:
    """Render a full xkb_symbols block with its accompanying xkb_types."""

    lines = []

    lines.append('xkb_types "%s" {' % variant_name)
    lines.append('')
    # LevelThree is the standard virtual modifier for the Option/AltGr layer
    # (mapped to RightAlt by the level3 include); MacCaps is our custom caps-layer
    # modifier on Mod3. Both must be declared before the types reference them.
    lines.append('    virtual_modifiers LevelThree, MacCaps;')
    lines.append('')
    for _name, text in types:
        lines.append(text)
        lines.append('')
    lines.append('};')
    lines.append('')

    lines.append('xkb_symbols "%s" {' % variant_name)
    lines.append('')
    lines.append('    name[Group1] = "%s";' % display_name)
    lines.append('')

    for xkb_code in sorted(key_rows):
        type_name, tokens_csv = key_rows[xkb_code]
        lines.append(
            '    key <%s> {\n'
            '        type[Group1] = "%s",\n'
            '        symbols[Group1] = [ %s ]\n'
            '    };' % (xkb_code, type_name, tokens_csv)
        )

    lines.append('')
    lines.append('    key <CAPS> {')
    lines.append('        type[Group1] = "ONE_LEVEL",')
    lines.append('        symbols[Group1] = [ Caps_Lock ],')
    lines.append('        actions[Group1] = [ LockMods(modifiers = MacCaps) ]')
    lines.append('    };')
    lines.append('    modifier_map Mod3 { <CAPS> };')
    lines.append('')
    lines.append('    include "level3(ralt_switch)"')
    lines.append('};')

    return '\n'.join(lines)


def _swap_iso_keys(key_rows: 'dict') -> 'dict':
    """Return a copy of key_rows with <TLDE> and <LSGT> swapped, for the ISO
    variant. Apple ISO keyboards permute these two scancodes relative to PC-ISO
    hardware. Only applies when both are present.
    """

    if 'TLDE' not in key_rows or 'LSGT' not in key_rows:
        return dict(key_rows)
    swapped = dict(key_rows)
    swapped['TLDE'], swapped['LSGT'] = key_rows['LSGT'], key_rows['TLDE']
    return swapped


def _variant_plane_tables(layout: Layout) -> 'dict':
    """The primary variant's plane_tables, or empty if unavailable."""

    if layout.variants and layout.variants[0].plane_tables:
        return layout.variants[0].plane_tables
    return {}


def emit_symbols(layout: Layout, variant_name: str, display_name: str) -> str:
    """Emit a complete xkb_types + xkb_symbols block for one layout."""

    plane_tables = _variant_plane_tables(layout)
    types, key_rows = _build_key_groups(layout, plane_tables)
    return _render_layout(types, key_rows, variant_name, display_name)


def emit_symbols_variants(layout: Layout, base_identifier: str,
                          base_display: str) -> 'list[tuple[str, str]]':
    """Emit both the ANSI and ISO Macintosh variants for a layout."""

    plane_tables = _variant_plane_tables(layout)
    types, key_rows = _build_key_groups(layout, plane_tables)
    iso_rows = _swap_iso_keys(key_rows)

    ansi_name = '%s-ansi' % base_identifier
    iso_name = '%s-iso' % base_identifier
    ansi_display = '%s, ANSI)' % base_display
    iso_display = '%s, ISO)' % base_display

    return [
        (ansi_name, _render_layout(types, key_rows, ansi_name, ansi_display)),
        (iso_name, _render_layout(types, iso_rows, iso_name, iso_display)),
    ]


# End of file #
