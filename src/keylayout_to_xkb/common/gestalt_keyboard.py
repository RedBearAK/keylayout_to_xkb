"""
keylayout_to_xkb/common/gestalt_keyboard.py

macOS gestalt keyboard-type numbers and their physical-layout kind
(ANSI / ISO / JIS), used to resolve which keyboard-type variant of a 'uchr'
layout to build.

WHY THIS EXISTS
A 'uchr' layout advertises one or more keyboard-type records, each covering a
range of gestalt type numbers and pointing at its own complete char tables. The
file itself does NOT say which range is ANSI vs ISO vs JIS -- it only lists the
type numbers it supports. The ANSI/ISO/JIS identity of each type number lives
externally, in Apple's CarbonCore Gestalt.h, encoded in the constant NAMES
(...ANSIKbd / ...ISOKbd / ...JISKbd). At runtime the OS reads the connected
keyboard's gestalt type and selects the matching record; off-Mac we instead
emit one self-contained layout per advertised kind.

PROVENANCE
The numeric type->kind facts below are derived from the keyboard-type constants
in Apple's CarbonCore Gestalt.h. Only the numbers and their kind (read off each
constant's name) are reproduced here -- not Apple's comment text. A public copy
of the header (an Australian mirror of the Carbon framework headers) is at:
  https://github.com/216k155/MacOSX-SDKs/blob/master/MacOSX10.11.sdk/System/
  Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/
  CarbonCore.framework/Versions/A/Headers/Gestalt.h

Useful facts distilled from that header (restated, not quoted):
  * The gestalt selector for keyboard type is the four-char code 'kbd '.
  * "Domestic" (Dom) keyboards are the US/ANSI physical layout, so the few
    ...DomKbd constants are classified ANSI here.
  * Early ADB/standard keyboards (types 1,4,5,6,10,12, the adjustable keypads,
    PS/2) predate the ANSI/ISO/JIS naming and carry no kind: they are the
    generic/default layout (treated as 'unlabeled').
  * Type 3 is obsolete (originally Mac Plus) and now means "unknown third-party
    keyboard"; it carries no kind.
  * The Gestalt API itself is deprecated (Apple now points to sysctl and
    CGEventSourceGetKeyboardType); the type NUMBERS remain the values reported,
    so this classification stays valid for reading existing 'uchr' files.

Each kind's REPRESENTATIVE list is ordered most-modern/most-common first; a
layout is asked "do you advertise any of these?" and the first covered one is
used to resolve that kind's table.
"""

__version__ = '20260626'


# Gestalt keyboard-type number -> physical kind. Read off the ...ANSIKbd /
# ...ISOKbd / ...JISKbd (and ...DomKbd -> ANSI) constant names in Gestalt.h.
# Numbers absent here are generic/kind-less (early ADB, PS/2, adjustable, the
# obsolete type 3): they form the 'unlabeled' default layout.
KIND_BY_TYPE = {
    # ISO
    7: 'ISO', 8: 'ISO', 9: 'ISO', 11: 'ISO', 13: 'ISO', 16: 'ISO', 20: 'ISO',
    29: 'ISO', 32: 'ISO', 35: 'ISO', 38: 'ISO', 41: 'ISO',
    196: 'ISO', 199: 'ISO', 203: 'ISO', 205: 'ISO',
    # JIS
    17: 'JIS', 21: 'JIS', 30: 'JIS', 33: 'JIS', 36: 'JIS', 39: 'JIS', 42: 'JIS',
    197: 'JIS', 200: 'JIS', 201: 'JIS', 206: 'JIS', 207: 'JIS',
    # ANSI (includes the "Domestic" keyboards)
    28: 'ANSI', 31: 'ANSI', 34: 'ANSI', 37: 'ANSI', 40: 'ANSI',
    195: 'ANSI', 198: 'ANSI', 202: 'ANSI', 204: 'ANSI',
}


# Per-kind representative type numbers, most-modern/common first. Used to ask a
# layout "which of these do you advertise?" and resolve that kind from the first
# match. The spread (USB, then older ADB/PowerBook) maximizes the chance of a
# hit across the wide variety of vintages 'uchr' files target.
REPRESENTATIVE_TYPES = {
    'ANSI': [40, 37, 34, 31, 198, 204, 202, 195, 28],
    'ISO':  [41, 38, 35, 32, 199, 205, 203, 196, 29, 20, 16, 13, 11, 9, 8, 7],
    'JIS':  [42, 39, 36, 33, 200, 206, 207, 201, 197, 30, 21, 17],
}


# The four labels a layout may be split into. 'unlabeled' is the generic/default
# table reached by kind-less types; the others are emitted only when advertised.
KIND_LABELS = ('unlabeled', 'ANSI', 'ISO', 'JIS')


def kind_of_type(type_number: int) -> 'str | None':
    """Return 'ANSI'/'ISO'/'JIS' for a gestalt type, or None if generic."""

    return KIND_BY_TYPE.get(type_number)


def representative_type_for_kind(kind: str, covered_fn) -> 'int | None':
    """First representative type of 'kind' for which covered_fn(type) is True.

    covered_fn answers "does this layout advertise this type number?" (i.e. some
    keyboard-type record's [first, last] range contains it). Returns the chosen
    type number, or None if the layout advertises no type of this kind.
    """

    if kind not in REPRESENTATIVE_TYPES:
        raise ValueError(f'representative_type_for_kind: unknown kind {kind!r}')
    for type_number in REPRESENTATIVE_TYPES[kind]:
        if covered_fn(type_number):
            return type_number
    return None


def lowest_generic_type(ranges: 'list[tuple[int, int]]') -> 'int | None':
    """Lowest advertised kind-less type, for the 'unlabeled' default layout.

    'ranges' is the list of (first, last) gestalt ranges the layout advertises.
    Returns the smallest type number in any range that has no ANSI/ISO/JIS kind,
    or None if every advertised type carries a kind.
    """

    best = None
    for first, last in ranges:
        for type_number in range(first, last + 1):
            if type_number not in KIND_BY_TYPE:
                if best is None or type_number < best:
                    best = type_number
                break
    return best


# End of file #
