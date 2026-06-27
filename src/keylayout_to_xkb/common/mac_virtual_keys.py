"""
keylayout_to_xkb/common/mac_virtual_keys.py

macOS virtual keycodes (the kVK_* constants from Carbon's Events.h).

These are NOT evdev or XKB keycodes. They are the indices macOS 'uchr' tables
are addressed by. The emit stage will translate these to XKB keycodes via a
separate map; this module's only job is to give each macOS virtual keycode a
stable name so debug dumps and the optspecialchars cross-check are readable.

FUZZY-DATA WARNING: the ANSI block (letters, digits, common punctuation) is
well known and high-confidence. The function-key / keypad / ISO-extra block
is reconstructed and lower-confidence. Since the Option-layer port only cares
about the character-producing keys, any gap in the high-numbered block is
harmless, but the dead-key trigger keys (E, U, I, etc.) MUST be correct here,
so those are the ones to verify first against the optspecialchars triggers.

Reference for verification, not copied from: Carbon/HIToolbox Events.h.
"""


__version__ = '20260622'


# High-confidence ANSI block. macOS virtual keycode -> human-readable name.
# The name is the physical key (US-QWERTY legend), not the character produced.
VK_NAMES = {
    0x00: 'A',
    0x01: 'S',
    0x02: 'D',
    0x03: 'F',
    0x04: 'H',
    0x05: 'G',
    0x06: 'Z',
    0x07: 'X',
    0x08: 'C',
    0x09: 'V',
    0x0B: 'B',
    0x0C: 'Q',
    0x0D: 'W',
    0x0E: 'E',
    0x0F: 'R',
    0x10: 'Y',
    0x11: 'T',
    0x12: '1',
    0x13: '2',
    0x14: '3',
    0x15: '4',
    0x16: '6',
    0x17: '5',
    0x18: 'Equal',
    0x19: '9',
    0x1A: '7',
    0x1B: 'Minus',
    0x1C: '8',
    0x1D: '0',
    0x1E: 'RightBracket',
    0x1F: 'O',
    0x20: 'U',
    0x21: 'LeftBracket',
    0x22: 'I',
    0x23: 'P',
    0x25: 'L',
    0x26: 'J',
    0x27: 'Quote',
    0x28: 'K',
    0x29: 'Semicolon',
    0x2A: 'Backslash',
    0x2B: 'Comma',
    0x2C: 'Slash',
    0x2D: 'N',
    0x2E: 'M',
    0x2F: 'Period',
    0x32: 'Grave',
    0x31: 'Space',
}


# The dead-key trigger keys we expect on ABC Extended, by name. The validation
# oracle uses this to confirm the keycode table put the right physical key at
# the right virtual code: if Option+<this key> does not enter a dead state in
# the parsed model, either the keycode map or the parser is wrong.
EXPECTED_ABC_EXTENDED_DEAD_TRIGGERS = {
    'Grave',            # grave accent
    '6',                # circumflex
    'W',                # dot above
    'E',                # acute
    'U',                # umlaut / diaeresis
    'I',                # apostrophe / horn
    'P',                # comma below
    'A',                # macron
    'H',                # low macron / line below
    # Not exhaustive; this is the high-confidence subset for the first check.
    # The full set of 25 is verified against the optspecialchars appendix,
    # not hardcoded here, since exact triggers can shift between macOS versions.
}


def vk_name(virtual_keycode: int) -> str:
    """Return a readable name for a macOS virtual keycode, or a hex fallback."""

    name = VK_NAMES.get(virtual_keycode)
    if name is not None:
        return name

    return f'VK_0x{virtual_keycode:02x}'


# End of file #
