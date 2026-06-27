"""
keylayout_to_xkb/emit/classify.py

Classification stage of the emitter: turn parsed outputs into the XKB tokens the
symbols and Compose emitters need. This is the foundation both emit/symbols.py
and emit/compose.py build on, so it owns every "what is the XKB name for this
character / this dead key" decision in one place.

Two jobs:

  1. char_to_keysym(s): map a single-character output to an XKB keysym token --
     a NAMED keysym ('aogonek', 'udiaeresis') where one exists, else the Unicode
     keysym form ('U0105'). Multi-character strings have NO single keysym and
     return None (the caller routes them to XCompose).

  2. dead_state_keysym(dead_state): map a dead-key state to its dead_* keysym
     ('dead_diaeresis', 'dead_circumflex', ...) by looking at the bare accent the
     state produces (its terminator, or a space-composition fallback).

KEYSYM NAMES ARE NOT INVENTED. The codepoint -> name map is parsed from the
installed X11 keysymdef.h, so names match the running system's keysym database
rather than a hand-maintained guess. A critical detail learned from the file:
the keysym's hex VALUE is not the codepoint (XK_aogonek is 0x01b1 but U+0105);
the real codepoint is in the '/* U+XXXX ... */' comment. We parse the comment.
For the low range without a comment, the Latin-1 rule (keysym value == codepoint
for 0x20..0xff) supplies the mapping. XKB cannot define new keysyms, so anything
without a named keysym MUST use the UXXXX form, never an invented name.

The dead-accent -> dead_* mapping is a small curated table: the relationship
between a bare accent character and its dead-key keysym is not encoded in
keysymdef.h, so it is stated explicitly here and kept minimal.
"""

import os
import re


__version__ = '20260623'


# Standard install path for the X11 keysym database. Overridable via env for
# odd layouts / testing. If absent, only the Latin-1 direct rule and the UXXXX
# fallback are available, which still produces valid (if less named) output.
_KEYSYMDEF_PATHS = (
    os.environ.get('KEYLAYOUT_KEYSYMDEF', ''),
    '/usr/include/X11/keysymdef.h',
    '/usr/X11/include/X11/keysymdef.h',
)

# Parsed lazily and cached: codepoint (int) -> preferred keysym name (str).
_codepoint_to_name = None

# Lines look like:  #define XK_aogonek  0x01b1  /* U+0105 LATIN SMALL ... */
# Capture the keysym name, its hex value, and the optional U+XXXX codepoint.
_define_rgx = re.compile(
    r'^#define\s+XK_(\w+)\s+0x([0-9A-Fa-f]+)\s*(?:/\*\s*U\+([0-9A-Fa-f]+))?'
)

# Names we never want to PREFER even if they map to a codepoint: deprecated
# spellings and ambiguous aliases. Parsing keeps the first good name per
# codepoint and skips these so e.g. a deprecated misspelling cannot win.
_DEPRECATED_NAME_MARKERS = ('deprecated',)


def _load_keysymdef() -> dict:
    """Parse keysymdef.h into {codepoint: keysym_name}, cached after first call.

    Prefers the codepoint from the '/* U+XXXX */' comment. For commentless
    definitions in the Latin-1 range (0x20..0xff) the keysym value equals the
    codepoint, so that direct mapping is used. The first non-deprecated name
    seen for a codepoint wins; later aliases do not overwrite it.
    """

    global _codepoint_to_name
    if _codepoint_to_name is not None:
        return _codepoint_to_name

    mapping = {}
    path = next((p for p in _KEYSYMDEF_PATHS if p and os.path.isfile(p)), None)
    if path is None:
        _codepoint_to_name = mapping
        return mapping

    with open(path, 'r', encoding='utf-8', errors='replace') as handle:
        for line in handle:
            match = _define_rgx.match(line)
            if not match:
                continue
            name, hexval, hexcp = match.group(1), match.group(2), match.group(3)
            if any(marker in line.lower() for marker in _DEPRECATED_NAME_MARKERS):
                continue

            if hexcp is not None:
                codepoint = int(hexcp, 16)
            else:
                value = int(hexval, 16)
                # Latin-1 direct rule: only for the printable Latin-1 range,
                # where the keysym value is defined to equal the codepoint.
                if 0x20 <= value <= 0xff:
                    codepoint = value
                else:
                    continue

            mapping.setdefault(codepoint, name)

    _codepoint_to_name = mapping
    return mapping


def unicode_keysym(codepoint: int) -> str:
    """Return the XKB Unicode keysym token for a codepoint, e.g. 0x0105 -> 'U0105'.

    XKB accepts 'UXXXX' (4+ hex digits, uppercase) for any codepoint that lacks
    a named keysym. This is the universal fallback; it always works.
    """

    return 'U{:04X}'.format(codepoint)


def char_to_keysym(text: str) -> 'str | None':
    """Map a single-character output to an XKB keysym token.

    Returns a named keysym ('aogonek') if keysymdef.h has one for the
    character's codepoint, otherwise the Unicode form ('U0105'). Returns None
    for empty or multi-character input: those have no single keysym and must be
    emitted via XCompose by the caller. Be explicit here so callers never
    silently treat a multi-char string as a keysym.
    """

    if not text or len(text) > 1:
        return None

    codepoint = ord(text)
    name = _load_keysymdef().get(codepoint)
    if name is not None:
        return name
    return unicode_keysym(codepoint)


# Bare-accent character -> dead_* keysym. The terminator of a dead-key state is
# the bare accent it produces (acute then space -> the acute accent), which
# identifies the dead key. keysymdef.h does not encode this relationship, so it
# is stated explicitly. Keep minimal and add entries only as real layouts need
# them; an unmapped accent falls through to a codepoint-based guess below.
_ACCENT_CHAR_TO_DEAD = {
    '\u0060': 'dead_grave',         # ` GRAVE ACCENT
    '\u00b4': 'dead_acute',         # acute accent
    '\u005e': 'dead_circumflex',    # ^ CIRCUMFLEX ACCENT
    '\u02c6': 'dead_circumflex',    # MODIFIER LETTER CIRCUMFLEX
    '\u007e': 'dead_tilde',         # ~ TILDE
    '\u02dc': 'dead_tilde',         # SMALL TILDE
    '\u00a8': 'dead_diaeresis',     # diaeresis
    '\u00af': 'dead_macron',        # macron
    '\u02d8': 'dead_breve',         # breve
    '\u02d9': 'dead_abovedot',      # dot above
    '\u02da': 'dead_abovering',     # ring above
    '\u02dd': 'dead_doubleacute',   # double acute
    '\u02c7': 'dead_caron',         # caron
    '\u00b8': 'dead_cedilla',       # cedilla
    '\u02db': 'dead_ogonek',        # ogonek
}


def dead_state_keysym(terminator: str, compositions: 'dict | None' = None) -> 'str | None':
    """Map a dead-key state to its dead_* keysym from the bare accent it makes.

    The terminator is the accent produced when the dead key is followed by a key
    with no composition (often space). That bare accent identifies the dead key.
    Falls back to the space-composition if the terminator is empty, then returns
    None if the accent is unrecognised (the caller may then emit the dead key as
    a literal-producing key or skip it, but must not invent a dead_* name).
    """

    accent = terminator
    if not accent and compositions:
        accent = compositions.get(' ', '')

    if not accent or len(accent) != 1:
        return None

    return _ACCENT_CHAR_TO_DEAD.get(accent)


# End of file #
