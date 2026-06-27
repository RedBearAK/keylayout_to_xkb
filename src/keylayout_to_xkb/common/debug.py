"""
keylayout_to_xkb/common/debug.py

Shared diagnostic helpers. The extraction stages are where our knowledge is
fuzziest (exact 'uchr' field offsets, the virtual-keycode table, the ctypes
marshalling on current macOS), so every risky step routes its instrumentation
through here. A single DEBUG flag, flipped by the --debug CLI option, turns
all of it on at once.

Nothing here is load-bearing for output correctness; it exists purely so a
failure at one stage produces a readable dump instead of a traceback that
hides the real cause three stages downstream.
"""

import sys


__version__ = '20260622'


DEBUG = False


def set_debug(enabled: bool) -> None:
    """Flip the module-level DEBUG flag from the CLI entry point."""

    global DEBUG
    DEBUG = bool(enabled)


def dbg(label: str, message: str = '') -> None:
    """Print a debug line to stderr when DEBUG is on.

    Kept to stderr so that any stdout (e.g. emitted layout text) stays clean
    and pipeable.
    """

    if not DEBUG:
        return

    if message:
        print(f'[dbg] {label}: {message}', file=sys.stderr)
        return

    print(f'[dbg] {label}', file=sys.stderr)


def hex_window(data: bytes, offset: int, count: int = 32) -> str:
    """Return a readable hex+offset window into a byte buffer.

    Used to eyeball 'uchr' table headers when an offset looks wrong. The
    double-subtraction / wrong-base class of bug is invisible at the value
    level but obvious here, because the bytes simply will not look like a
    plausible table header.
    """

    end = min(offset + count, len(data))
    chunk = data[offset:end]
    hex_part = ' '.join(f'{byte:02x}' for byte in chunk)
    return f'@{offset} (0x{offset:x}) [{len(chunk)} bytes]: {hex_part}'


def warn(label: str, message: str) -> None:
    """Loud, non-fatal warning to stderr.

    Used when the parser meets something it can represent but did not expect
    (an unknown modifier table, a state record variant we have not mapped).
    Per the loud-failure principle, these are never swallowed silently even
    when DEBUG is off.
    """

    print(f'[warn] {label}: {message}', file=sys.stderr)


# End of file #
