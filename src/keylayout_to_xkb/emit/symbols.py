"""
keylayout_to_xkb/emit/symbols.py

Emit an XKB symbols block from a parsed Layout's four character planes, in the
self-contained Macintosh-variant format (the de(mac) variant is the reference).

Output shape, per key:

    key <AC01> {[ a, A, aring, Aring ]};

The four level entries are the four ModifierState planes IN ORDER:
    level 1 = PLAIN, level 2 = SHIFT, level 3 = OPTION, level 4 = SHIFT_OPTION.
Option is reached on Linux via level-3 shift (RightAlt), matching how the Mac
Option key behaves; the emitted block includes the standard level3 include so
the layout is usable on its own.

Each cell becomes an XKB keysym token via emit/classify.py:
  * CHARS, single codepoint -> named keysym or UXXXX (classify.char_to_keysym)
  * DEAD                     -> dead_* keysym (classify.dead_state_keysym)
  * CHARS, multi-codepoint   -> CANNOT be an XKB keysym; emitted as the dead-key
                                terminator's keysym if it is a combining result,
                                otherwise left as an empty level and routed to
                                XCompose by the caller. symbols.py marks these so
                                compose.py can pick them up; it never silently
                                drops them.

The virtual-key -> XKB keycode map is by PHYSICAL POSITION (Mac virtual keycodes
are physical-position codes), so it is correct regardless of which character a
layout assigns to a position. It is grounded against the keycodes/evdev names
and cross-checked with the de(mac) variant.
"""

from keylayout_to_xkb.common.models import Layout, OutputKind, ModifierState
from keylayout_to_xkb.emit.classify import char_to_keysym, dead_state_keysym


__version__ = '20260623'


# The four planes, in XKB level order (1..4).
_LEVEL_PLANES = (
    ModifierState.PLAIN,
    ModifierState.SHIFT,
    ModifierState.OPTION,
    ModifierState.SHIFT_OPTION,
)

# Mac virtual keycode -> XKB keycode name, by PHYSICAL POSITION. These are the
# standard Mac virtual keycodes (Apple's Events.h kVK_* constants) for the ANSI
# block, plus the ISO <LSGT> key (kVK_ISO_Section = 0x0A) and the JIS key
# (0x5E). Position-based, so correct regardless of which character a layout
# assigns. Cross-checked against de(mac): vk0->AC01, vk6->AB01, vk12->AD01,
# vk18->AE01, vk0x32(Grave)->TLDE, vk0x0A(ISO)->LSGT.
_VK_TO_XKB = {
    # number row  (0x32 = Grave = TLDE)
    0x32: 'TLDE',
    0x12: 'AE01', 0x13: 'AE02', 0x14: 'AE03', 0x15: 'AE04', 0x17: 'AE05',
    0x16: 'AE06', 0x1A: 'AE07', 0x1C: 'AE08', 0x19: 'AE09', 0x1D: 'AE10',
    0x1B: 'AE11', 0x18: 'AE12',
    # top alpha row
    0x0C: 'AD01', 0x0D: 'AD02', 0x0E: 'AD03', 0x0F: 'AD04', 0x11: 'AD05',
    0x10: 'AD06', 0x20: 'AD07', 0x22: 'AD08', 0x1F: 'AD09', 0x23: 'AD10',
    0x21: 'AD11', 0x1E: 'AD12',
    # home row
    0x00: 'AC01', 0x01: 'AC02', 0x02: 'AC03', 0x03: 'AC04', 0x05: 'AC05',
    0x04: 'AC06', 0x26: 'AC07', 0x28: 'AC08', 0x25: 'AC09', 0x29: 'AC10',
    0x27: 'AC11', 0x2A: 'BKSL',
    # bottom row  (0x0A = ISO <> key = kVK_ISO_Section = LSGT)
    0x0A: 'LSGT',
    0x06: 'AB01', 0x07: 'AB02', 0x08: 'AB03', 0x09: 'AB04', 0x0B: 'AB05',
    0x2D: 'AB06', 0x2E: 'AB07', 0x2B: 'AB08', 0x2F: 'AB09', 0x2C: 'AB10',
    # space
    0x31: 'SPCE',
}


def _cell_keysym(layout: Layout, virtual_key: int, plane: ModifierState):
    """Return (keysym_token, needs_compose) for one cell.

    keysym_token is the XKB token for the cell, or None if the cell is absent or
    must go to XCompose. needs_compose is True when the cell holds multi-
    codepoint output that XKB cannot express as a single keysym, so compose.py
    must emit it; in that case keysym_token is None and the level is left empty.
    """

    ko = layout.keys.get(virtual_key, {}).get(plane)
    if ko is None:
        return None, False

    if ko.kind is OutputKind.DEAD:
        state = layout.dead_states.get(ko.dead_state_name)
        if state is None:
            return None, False
        token = dead_state_keysym(state.terminator, state.compositions)
        return token, False                     # unknown dead -> None, not compose

    if ko.kind is OutputKind.CHARS:
        if len(ko.output) > 1:
            return None, True                   # multi-codepoint -> XCompose
        return char_to_keysym(ko.output), False

    return None, False


def emit_symbols(layout: Layout, variant_name: str, display_name: str) -> str:
    """Emit a complete xkb_symbols block for one layout variant.

    variant_name is the bare identifier used in the file (e.g. 'pl_mac');
    display_name is the human label shown in pickers ('Polish (Macintosh)').
    Returns the block text. Cells needing XCompose are left as empty levels
    here and handled by compose.py against the same Layout.
    """

    rows = _build_key_rows(layout)
    return _render_variant(rows, variant_name, display_name)


def _build_key_rows(layout: Layout) -> 'dict':
    """Build {xkb_code: levels_body} for every populated key.

    levels_body is the 'a, A, aogonek, Aogonek' text between the brackets, with
    trailing NoSymbol levels trimmed. Keys that map nothing are omitted. Built
    once so both the ANSI and ISO variants render from the same data; the ISO
    variant only differs by swapping the <TLDE> and <LSGT> rows.
    """

    rows = {}
    for virtual_key, xkb_code in _VK_TO_XKB.items():
        tokens = []
        any_present = False
        for plane in _LEVEL_PLANES:
            token, _needs_compose = _cell_keysym(layout, virtual_key, plane)
            if token is None:
                tokens.append('NoSymbol')
            else:
                tokens.append(token)
                any_present = True

        if not any_present:
            continue

        while len(tokens) > 1 and tokens[-1] == 'NoSymbol':
            tokens.pop()

        rows[xkb_code] = ', '.join(tokens)

    return rows


def _render_variant(rows: 'dict', variant_name: str, display_name: str) -> str:
    """Render one xkb_symbols block from prebuilt key rows."""

    lines = []
    lines.append('xkb_symbols "%s" {' % variant_name)
    lines.append('')
    lines.append('    name[Group1]= "%s";' % display_name)
    lines.append('')

    for xkb_code in sorted(rows):
        lines.append('    key <%s>\t{[ %s ]};' % (xkb_code, rows[xkb_code]))

    lines.append('')
    # Option = level 3 via RightAlt, matching Mac Option behaviour. Self-
    # contained so the variant works without external layout dependencies.
    lines.append('    include "level3(ralt_switch)"')
    lines.append('};')

    return '\n'.join(lines)


def _swap_iso_keys(rows: 'dict') -> 'dict':
    """Return a copy of rows with the <TLDE> and <LSGT> definitions swapped.

    Apple ISO keyboards report the grave/tilde key (<TLDE>) and the section/ISO
    key (<LSGT>) with PERMUTED scancodes relative to PC-ISO hardware, so the ISO
    variant binds each definition to the other physical key to compensate. This
    is the same fix upstream xkeyboard-config applies for its mac-iso variants
    (the <TLDE>/<LSGT> permutation noted in the 2.45/2.46 release notes).

    Only applies when BOTH keys are present in this layout. If either is absent
    there is nothing to permute (an ANSI keyboard simply lacks <LSGT>), so the
    rows are returned unchanged -- the swap and the missing-key case never
    coexist on the same hardware.

    NOTE: implemented as a clean row swap from our corrected understanding of
    the Apple ISO permutation; if the authoritative current us(mac-iso) does
    something subtler, this single function is the place to adjust.
    """

    if 'TLDE' not in rows or 'LSGT' not in rows:
        return dict(rows)

    swapped = dict(rows)
    swapped['TLDE'], swapped['LSGT'] = rows['LSGT'], rows['TLDE']
    return swapped


def emit_symbols_variants(
    layout: Layout,
    base_identifier: str,
    base_display: str,
) -> 'list[tuple[str, str]]':
    """Emit both the ANSI and ISO Macintosh variants for a layout.

    Returns a list of (variant_name, block_text). base_identifier is the variant
    stem (e.g. 'mac'); the emitted variants are '<stem>-ansi' and '<stem>-iso'.
    base_display is the human label stem (e.g. 'Polish Pro (Macintosh'); the
    suffix ', ANSI)' / ', ISO)' is appended. Both are emitted because the two
    differ only in the <TLDE>/<LSGT> arrangement (see _swap_iso_keys), and a user
    picks the one matching their Apple keyboard's physical type.
    """

    rows = _build_key_rows(layout)
    iso_rows = _swap_iso_keys(rows)

    ansi_name = '%s-ansi' % base_identifier
    iso_name = '%s-iso' % base_identifier
    ansi_display = '%s, ANSI)' % base_display
    iso_display = '%s, ISO)' % base_display

    return [
        (ansi_name, _render_variant(rows, ansi_name, ansi_display)),
        (iso_name, _render_variant(iso_rows, iso_name, iso_display)),
    ]


# End of file #
