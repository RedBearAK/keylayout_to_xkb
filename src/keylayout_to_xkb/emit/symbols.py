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
    CAPS               LevelFive                  level 5
    CAPS_SHIFT         LevelFive + Shift          level 6
    CAPS_OPTION        LevelFive + LevelThree     level 7
    CAPS_SHIFT_OPTION  LevelFive+Shift+LevelThree level 8

LevelFive is the standard virtual modifier the EIGHT_LEVEL system type already
maps to levels 5-8. The emitted <CAPS> key backs it with the REAL Lock bit and
LOCKS it via an explicit LockMods action, so Caps toggles the caps layer on/off
exactly like macOS (verified on hardware: macOS Caps is a pure toggle/latch,
not a hold), Shift/Option select within it, the Caps Lock LED tracks the layer
(LED-driven notifications work unchanged), and caps state carries across layout
switches in both directions like the single global caps on macOS. This is
entirely self-contained in the emitted layout -- no external keymapper is
required. See _render_layout for why each piece of the <CAPS> wiring is
load-bearing, including the one known multi-layout corner on modern
libxkbcommon.

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


__version__ = '20260704'


# Planes in canonical level order, each with the XKB modifier-combination tokens
# that select it. These map EXACTLY onto the standard EIGHT_LEVEL key type:
# LevelThree (RightAlt) selects the Option layer, LevelFive (bound to CapsLock as
# a lock) selects the caps layer. Using the STANDARD selectors -- not a custom
# MacCaps modifier -- means keys reference the standard EIGHT_LEVEL/FOUR_LEVEL/etc
# types that ship in every system's 'complete' types component. That matters
# because desktop environments (KDE) load the standard types automatically but do
# NOT reliably load a layout's own custom types file, which left every key stuck
# on level 1 (Shift/AltGr/CapsLock dead). An empty tuple is the base level.
_PLANE_LEVEL_ORDER = (
    (ModifierState.PLAIN,             ()),
    (ModifierState.SHIFT,             ('Shift',)),
    (ModifierState.OPTION,            ('LevelThree',)),
    (ModifierState.SHIFT_OPTION,      ('Shift', 'LevelThree')),
    (ModifierState.CAPS,              ('LevelFive',)),
    (ModifierState.CAPS_SHIFT,        ('Shift', 'LevelFive')),
    (ModifierState.CAPS_OPTION,       ('LevelThree', 'LevelFive')),
    (ModifierState.CAPS_SHIFT_OPTION, ('Shift', 'LevelThree', 'LevelFive')),
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


def _cell_token(layout: Layout, keys: 'dict', virtual_key: int,
                plane: ModifierState, placeholders: 'dict') -> 'str | None':
    """XKB keysym token for one cell, or None if the cell is absent.

    DEAD -> its named dead_* keysym if the diacritic has one, else the dead-state
    PUA placeholder (for non-standard dead keys like a numero sign or a Vietnamese
    base-vowel tone key); single-char CHARS -> named keysym or UXXXX; multi-char
    CHARS -> the cell's multi-char placeholder keysym. `placeholders` is the
    reserve_placeholders() result ({'multichar':..., 'deadkey':...}). Returns None
    only when there is genuinely no output for the cell.
    """

    key_output = keys.get(virtual_key, {}).get(plane)
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


# Standard XKB key types by max level. These ship in every system's 'complete'
# types component, so KDE loads them automatically -- unlike a layout's own custom
# types, which KDE does not reliably load. The plane->level order above matches
# these types exactly: Shift=L2, LevelThree=L3/L4, LevelFive=L5..L8.
#
# We use the PLAIN types, NOT the _ALPHABETIC variants. CapsLock locks the Lock
# bit (which backs LevelFive), and the alphabetic types give Lock its classic
# capitalize/shift-reverses semantics -- which would be WRONG here:
# EIGHT_LEVEL_ALPHABETIC maps Shift+Lock+LevelThree back down to Level3 (Shift
# "reverses" caps), scrambling the Mac caps+Option layers (L7/L8). Plain types
# keep Lock out of level arithmetic except through LevelFive itself, so
# EIGHT_LEVEL selects L5..L8 cleanly, giving correct caps, caps+opt and
# caps+shift+opt output for every key, letters and punctuation alike.
_STANDARD_TYPE = {
    1: 'ONE_LEVEL',
    2: 'TWO_LEVEL',
    4: 'FOUR_LEVEL',
    8: 'EIGHT_LEVEL',
}


def _standard_type_for(max_level: int) -> str:
    """Pick the smallest standard type whose level count covers max_level.

    Standard types come in 1/2/4/8 levels; a key that reaches level 5-8 needs the
    8-level type (with NoSymbol padding for any absent middle levels), a key
    reaching level 3-4 needs the 4-level type, and so on.
    """

    if max_level <= 1:
        return _STANDARD_TYPE[1]
    if max_level <= 2:
        return _STANDARD_TYPE[2]
    if max_level <= 4:
        return _STANDARD_TYPE[4]
    return _STANDARD_TYPE[8]


def _padded_tokens(layout: Layout, keys: 'dict', virtual_key: int,
                   placeholders: 'dict') -> 'tuple':
    """Build a key's level tokens padded to a contiguous 1..max_level list.

    The plane->level order is fixed (plain=1, shift=2, option=3, shift+option=4,
    caps=5..caps+shift+option=8). A key may lack some planes; standard XKB types
    require contiguous levels, so absent levels below the highest present one are
    filled with NoSymbol. Returns (tokens_list, max_level).
    """

    # Map each present plane to its fixed level index (1-based) and token.
    level_token = {}
    for plane, _mods in _PLANE_LEVEL_ORDER:
        token = _cell_token(layout, keys, virtual_key, plane, placeholders)
        if token is not None:
            level_token[_PLANE_INDEX[plane] + 1] = token

    if not level_token:
        return [], 0
    max_level = max(level_token)
    tokens = [level_token.get(lvl, 'NoSymbol') for lvl in range(1, max_level + 1)]
    return tokens, max_level


def _build_key_groups(layout: Layout, keys: 'dict',
                      plane_tables: 'dict') -> 'tuple':
    """Build the per-key standard-type assignment and padded symbol rows.

    Returns (types, key_rows):
      types    -- always empty now (we reference standard system types, so the
                  emitter defines none of its own); kept for signature stability.
      key_rows -- dict xkb_code -> (standard_type_name, tokens_csv)
    Keys with no output on any plane are omitted entirely.
    """

    placeholders = reserve_placeholders(layout)

    key_rows = {}
    for virtual_key, xkb_code in _VK_TO_XKB.items():
        if virtual_key not in keys:
            continue
        tokens, max_level = _padded_tokens(layout, keys, virtual_key,
                                           placeholders)
        if not tokens:
            continue
        type_name = _standard_type_for(max_level)
        key_rows[xkb_code] = (type_name, ', '.join(tokens))

    return [], key_rows


def _render_layout(types: 'list', key_rows: 'dict',
                   variant_name: str, display_name: str) -> str:
    """Render the xkb_symbols block.

    No custom xkb_types are emitted: every key references a STANDARD system type
    (ONE/TWO/FOUR/EIGHT_LEVEL), which the 'complete' types component always
    provides. This is what makes the layout work in desktop environments like KDE,
    which load the standard types automatically but do not reliably load a layout's
    own custom types file.

    Two standard includes wire up the modifier layers:
      * level3(ralt_switch): RightAlt -> LevelThree (the Option/AltGr layer, L3/L4)
      * <CAPS> -> Lock-backed LockMods: CapsLock LOCKS LevelFive (backed by
        the real Lock bit, so the Caps LED tracks the caps layer), selecting
        the Mac caps layer (L5..L8) with a clean
        toggle. Combined with plain (non-alphabetic) types
        this reproduces the full Mac model: caps uppercases letters (L5), and
        caps+Option / caps+Shift+Option reach the distinct L7/L8 glyphs (e.g.
        Ś, £ on the Polish 'r' key) instead of collapsing back down as the
        _ALPHABETIC types do.
    """

    lines = []
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
    # CapsLock LOCKS LevelFive, selecting the Mac caps layer (L5..L8) -- and
    # LevelFive is BACKED BY THE REAL Lock BIT, so the caps layer, the Caps
    # Lock LED, and any LED-driven notification move together, and caps
    # engaged here carries into sibling layouts as ordinary uppercase (and
    # vice versa), like the single global caps on macOS.
    #
    #   * vmods = LevelFive on <CAPS> plus modifier_map Lock { <CAPS> } binds
    #     LevelFive to Lock. This binding is contamination-proof BY CHOICE OF
    #     TARGET: in a multi-layout keymap the sibling groups put Caps_Lock on
    #     this key, and symbols/pc's keysym-based 'modifier_map Lock' adds
    #     Lock to the key's modmap -- the contaminant IS the target. (The
    #     earlier Mod3-backed design was correct but LED-blind: nothing ever
    #     lit the Caps LED, so LED-based notifications read a constant.)
    #   * The explicit LockMods action lives in the SYMBOLS block, so no
    #     compat interpret is required: compat 'complete' does NOT include
    #     level5(level5_lock), and per-layout compat loading is unverified.
    #     Symbols demonstrably load (Shift/AltGr work).
    #   * The <LVL5> override neutralizes symbols/pc's stock
    #     'key <LVL5> {[ISO_Level5_Shift]}; modifier_map Mod3 {<LVL5>};'
    #     which otherwise binds Mod3 into LevelFive via the compat interpret.
    #     Explicit actions suppress interprets, so single-layout keymaps and
    #     older libxkbcommon get LevelFive = Lock exactly. On modern
    #     libxkbcommon (1.8+) in MULTI-layout keymaps the neutralizer cannot
    #     reach pc's Group1 (our section is group-remapped), so LevelFive
    #     compiles as Lock+Mod3 there. Benign in normal use -- only this key
    #     sets both bits -- with one known corner: engage caps here, DISENGAGE
    #     it from a sibling layout, then tap caps here again, and the two bits
    #     flip-flop (uppercase-without-caps-layer alternating with inert-off)
    #     until one caps tap in a sibling layout re-syncs. The LED reports
    #     every state truthfully. Upstream deleted the analogous <HYPR> line
    #     from pc in 2.47; if <LVL5> follows, the corner disappears.
    #   * Level 2 of <CAPS> (Shift held) is the CANCEL: LockMods with
    #     affect=unlock unconditionally clears every bit LevelFive resolves
    #     to -- the guaranteed escape from the corner above, verified from
    #     the fully-locked, residue, and flip-flop states alike. It cannot
    #     interfere with caps-layer TYPING (L6/L8 go through the letter keys,
    #     never this key), and sibling layouts' caps is group-local and
    #     untouched. The one deviation from macOS: Shift+Caps unlocks instead
    #     of toggling -- accepted as the reset gesture.
    #
    # Verified: libxkbcommon 1.6.0/1.8.1/1.9.2/1.10.0/1.11.0/1.12.2/master,
    # xkeyboard-config 2.41/2.47, single- and three-layout merged keymaps,
    # LED state asserted via xkb_state_led_name_is_active, plus xkbcomp for
    # the X11 path. Plain (non-alphabetic) EIGHT_LEVEL types map LevelFive
    # cleanly to L5..L8, so caps+Option reaches L7 and caps+Shift+Option
    # reaches L8 (no shift-reverses-caps collapse).
    lines.append('    key <LVL5> {')
    lines.append('        type[Group1] = "ONE_LEVEL",')
    lines.append('        symbols[Group1] = [ NoSymbol ],')
    lines.append('        actions[Group1] = [ NoAction() ]')
    lines.append('    };')
    lines.append('    key <CAPS> {')
    lines.append('        type[Group1] = "TWO_LEVEL",')
    lines.append('        symbols[Group1] = [ ISO_Level5_Lock, ISO_Level5_Lock ],')
    lines.append('        vmods = LevelFive,')
    lines.append('        actions[Group1] = [')
    lines.append('            LockMods(modifiers=LevelFive),')
    lines.append('            LockMods(modifiers=LevelFive, affect=unlock)')
    lines.append('        ]')
    lines.append('    };')
    lines.append('    modifier_map Lock { <CAPS> };')
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
    types, key_rows = _build_key_groups(layout, layout.keys, plane_tables)
    return _render_layout(types, key_rows, variant_name, display_name)


def _kind_keys(layout: Layout, tag: str) -> 'dict':
    """The key tables macOS uses on hardware of the given kind.

    Layouts that carry keyboard-type variant tables (the PC family) expose
    them as Variant entries tagged 'ansi'/'iso'/'jis'; absence of a tag
    means the OS resolves that hardware kind to the primary tables, so
    falling back to layout.keys is correct BY CONSTRUCTION, not merely
    convenient. Every layout without kind tables therefore emits exactly
    what it always did.
    """

    for variant in layout.variants or []:
        if variant.tag == tag and variant.keys:
            return variant.keys
    return layout.keys


def emit_symbols_variants(layout: Layout, base_identifier: str,
                          base_display: str) -> 'list[tuple[str, str]]':
    """Emit both the ANSI and ISO Macintosh variants for a layout.

    Each variant carries the key tables macOS uses on that hardware kind
    (see _kind_keys) -- the '-ansi' variant of Russian - PC really types
    the Cyrillic io on the backquote key, as a Mac with an ANSI keyboard
    does. The ISO variant additionally swaps <TLDE>/<LSGT>, because Apple
    ISO keyboards permute those two scancodes. JIS kind tables are not
    emitted: Linux JIS setups use the jp layout family.
    """

    plane_tables = _variant_plane_tables(layout)
    ansi_keys = _kind_keys(layout, 'ansi')
    iso_keys = _kind_keys(layout, 'iso')
    types, key_rows = _build_key_groups(layout, ansi_keys, plane_tables)
    if iso_keys is ansi_keys:
        iso_base = key_rows
    else:
        _types, iso_base = _build_key_groups(layout, iso_keys, plane_tables)
    iso_rows = _swap_iso_keys(iso_base)

    ansi_name = '%s-ansi' % base_identifier
    iso_name = '%s-iso' % base_identifier
    ansi_display = '%s, ANSI)' % base_display
    iso_display = '%s, ISO)' % base_display

    return [
        (ansi_name, _render_layout(types, key_rows, ansi_name, ansi_display)),
        (iso_name, _render_layout(types, iso_rows, iso_name, iso_display)),
    ]


# End of file #
