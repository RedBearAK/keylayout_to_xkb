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


# Private-Use Area base for placeholder keysyms standing in for multi-character
# direct key outputs (a single keypress that emits a multi-codepoint string with
# no precomposed form, e.g. a Tibetan stacked vowel or a Manipuri conjunct). XKB
# levels hold a single keysym, so the level carries an invisible PUA placeholder
# and an XCompose one-token rule (<UEnnn> : "string") expands it. This is the
# exact mechanism the X.Org locale Compose uses for Khmer (<U17ff> : "string").
# U+E000..U+F8FE is the BMP PUA; U+F8FF is reserved (Apple logo) so it is skipped.
_PLACEHOLDER_BASE = 0xE000
_PLACEHOLDER_LIMIT = 0xF8FF      # exclusive upper bound (skip the Apple logo)


def allocate_multichar_placeholders(layout) -> 'dict[str, str]':
    """Back-compat shim: the multi-char half of reserve_placeholders().

    Prefer reserve_placeholders() directly when you also need the dead-key
    placeholders; this returns only the {string: keysym} multi-char map, with the
    SAME allocation reserve_placeholders() produces (dead-key placeholders are
    still reserved internally so the keysym numbers do not drift between callers).
    """

    return reserve_placeholders(layout)['multichar']


# Combining-mark codepoint -> dead_* keysym name. A dead key applies a combining
# diacritic; this maps that diacritic (by its Unicode combining codepoint) to the
# XKB dead_* keysym that produces it. Every name here is verified to resolve in
# the host xkbcommon at module load (see _verified_dead_keysyms); any that does
# NOT resolve on a given system is dropped, so we never emit a name that would
# silently become NoSymbol. The set XKB ships is large, so most Mac dead keys --
# including many exotic below-/above- diacritics -- get a real named keysym.
_COMBINING_TO_DEAD = {
    0x0300: 'dead_grave',
    0x0301: 'dead_acute',
    0x0302: 'dead_circumflex',
    0x0303: 'dead_tilde',
    0x0304: 'dead_macron',
    0x0306: 'dead_breve',
    0x0307: 'dead_abovedot',
    0x0308: 'dead_diaeresis',
    0x0309: 'dead_hook',
    0x030A: 'dead_abovering',
    0x030B: 'dead_doubleacute',
    0x030C: 'dead_caron',
    0x030D: 'dead_aboveverticalline',
    0x030F: 'dead_doublegrave',
    0x0311: 'dead_invertedbreve',
    0x0313: 'dead_abovecomma',
    0x0314: 'dead_abovereversedcomma',
    0x031B: 'dead_horn',
    0x0323: 'dead_belowdot',
    0x0324: 'dead_belowdiaeresis',
    0x0325: 'dead_belowring',
    0x0326: 'dead_belowcomma',
    0x0327: 'dead_cedilla',
    0x0328: 'dead_ogonek',
    0x0329: 'dead_belowverticalline',
    0x032D: 'dead_belowcircumflex',
    0x032E: 'dead_belowbreve',
    0x0330: 'dead_belowtilde',
    0x0331: 'dead_belowmacron',
    0x0332: 'dead_lowline',
    0x0338: 'dead_longsolidusoverlay',
}


# Spacing-accent (and modifier-letter) codepoint -> the combining-mark codepoint
# it represents. macOS dead-key terminators are usually the SPACING form of an
# accent (e.g. U+00B4 ACUTE ACCENT, U+02C6 MODIFIER LETTER CIRCUMFLEX), so we
# normalize them to the combining codepoint to find the dead key. Unicode's
# <compat> decomposition of a spacing accent yields its combining form for many
# of these; this explicit table covers the ones we rely on without depending on
# decomposition quirks.
_SPACING_TO_COMBINING = {
    0x0060: 0x0300,   # GRAVE ACCENT
    0x00B4: 0x0301,   # ACUTE ACCENT
    0x005E: 0x0302,   # CIRCUMFLEX ACCENT
    0x02C6: 0x0302,   # MODIFIER LETTER CIRCUMFLEX ACCENT
    0x007E: 0x0303,   # TILDE
    0x02DC: 0x0303,   # SMALL TILDE
    0x00AF: 0x0304,   # MACRON
    0x02C9: 0x0304,   # MODIFIER LETTER MACRON
    0x02D8: 0x0306,   # BREVE
    0x02D9: 0x0307,   # DOT ABOVE
    0x00A8: 0x0308,   # DIAERESIS
    0x02DA: 0x030A,   # RING ABOVE
    0x02DD: 0x030B,   # DOUBLE ACUTE ACCENT
    0x02C7: 0x030C,   # CARON
    0x00B8: 0x0327,   # CEDILLA
    0x02DB: 0x0328,   # OGONEK
    0x02D4: 0x032E,   # MODIFIER LETTER UP TACK (below breve-ish; rare)
    0x02CD: 0x0331,   # MODIFIER LETTER LOW MACRON
    0x02CE: 0x0316,   # MODIFIER LETTER LOW GRAVE ACCENT (no dead key; will miss)
    0x02F3: 0x0325,   # MODIFIER LETTER LOW RING
}


def _terminator_combining(terminator: str) -> 'int | None':
    """The combining-mark codepoint a dead-key terminator represents, or None.

    Handles three forms macOS uses: a spacing accent (U+00B4 -> U+0301), a bare
    combining mark already, or a base+combining sequence (e.g. NBSP + combining
    mark, where the combining mark is the diacritic). Returns the combining
    codepoint so the caller can look up the dead key.
    """

    if not terminator:
        return None
    # base + combining (e.g. NBSP U+00A0 then a combining mark): take the mark.
    if len(terminator) >= 2:
        for char in terminator:
            if 0x0300 <= ord(char) <= 0x036F:     # combining diacritical marks
                return ord(char)
        return None
    code = ord(terminator)
    if 0x0300 <= code <= 0x036F:
        return code
    return _SPACING_TO_COMBINING.get(code)


def _verified_dead_keysyms() -> 'dict':
    """Combining-codepoint -> dead_* name, filtered to names the host resolves.

    Verified once against the running xkbcommon via xkb_keysym_from_name, so a
    name that does not exist on this system is dropped rather than emitted as a
    silent NoSymbol. Cached on the function object.
    """

    cached = getattr(_verified_dead_keysyms, '_cache', None)
    if cached is not None:
        return cached
    verified = {}
    resolver = _keysym_name_resolver()
    for combining, name in _COMBINING_TO_DEAD.items():
        if resolver is None or resolver(name):
            verified[combining] = name
    _verified_dead_keysyms._cache = verified
    return verified


def _keysym_name_resolver():
    """Return a callable name -> bool (resolves in xkbcommon), or None.

    Uses libxkbcommon if present; if not, returns None and the caller trusts the
    table (all names in _COMBINING_TO_DEAD are standard X11 dead keys).
    """

    cached = getattr(_keysym_name_resolver, '_cache', 'unset')
    if cached != 'unset':
        return cached
    resolver = None
    try:
        import ctypes
        import ctypes.util
        lib = ctypes.CDLL(ctypes.util.find_library('xkbcommon'))
        lib.xkb_keysym_from_name.restype = ctypes.c_uint32
        lib.xkb_keysym_from_name.argtypes = [ctypes.c_char_p, ctypes.c_int]

        def resolves(name: str) -> bool:
            return lib.xkb_keysym_from_name(name.encode(), 0) != 0

        resolver = resolves
    except Exception:
        resolver = None
    _keysym_name_resolver._cache = resolver
    return resolver


def dead_state_keysym(terminator: str, compositions: 'dict | None' = None) -> 'str | None':
    """Map a dead-key state to a named dead_* keysym, or None if there is none.

    The terminator is the accent produced when the dead key is followed by a key
    with no composition (often space); that accent identifies the dead key. We
    normalize it to its combining-mark codepoint and look up the verified dead_*
    keysym. Returns None when the terminator is not a recognized diacritic (e.g.
    a numero sign or glottal-stop letter used as a dead key) -- the caller then
    falls back to a PUA placeholder dead key (see the symbols/compose emitters),
    so no dead key is ever silently dropped, but we never invent a dead_* name.
    """

    accent = terminator
    if not accent and compositions:
        accent = compositions.get(' ', '')

    combining = _terminator_combining(accent)
    if combining is None:
        return None
    return _verified_dead_keysyms().get(combining)


def reserve_placeholders(layout) -> 'dict':
    """Allocate PUA placeholders for BOTH multi-char outputs and unnamed dead keys.

    Returns {'multichar': {string: keysym}, 'deadkey': {state_name: keysym}} with
    every placeholder drawn from one shared PUA counter so the two never collide.
    This is the single allocator both the symbols and compose emitters call, so
    all three stay in lockstep. Deterministic ordering (multi-char strings sorted,
    then dead-state names sorted) keeps output reproducible.
    """

    from keylayout_to_xkb.common.models import OutputKind

    strings = set()
    variants = getattr(layout, 'variants', None) or []
    for keys in [layout.keys] + [v.keys for v in variants]:
        for modmap in keys.values():
            for key_output in modmap.values():
                if (key_output.kind is OutputKind.CHARS
                        and key_output.output
                        and len(key_output.output) > 1):
                    strings.add(key_output.output)

    unnamed_deadkeys = []
    for state_name, dead_state in layout.dead_states.items():
        if dead_state_keysym(dead_state.terminator, dead_state.compositions) is None:
            unnamed_deadkeys.append(state_name)

    multichar = {}
    deadkey = {}
    codepoint = _PLACEHOLDER_BASE

    def next_keysym():
        nonlocal codepoint
        if codepoint == 0xF8FF:
            codepoint += 1
        if codepoint >= _PLACEHOLDER_LIMIT:
            raise ValueError(
                'reserve_placeholders: exhausted the PUA placeholder block'
            )
        keysym = unicode_keysym(codepoint)
        codepoint += 1
        return keysym

    for text in sorted(strings):
        multichar[text] = next_keysym()
    for state_name in sorted(unnamed_deadkeys):
        deadkey[state_name] = next_keysym()

    return {'multichar': multichar, 'deadkey': deadkey}


# End of file #
