#!/usr/bin/env python3
"""
tests/probes/probe_kbgetlayouttype_dump.py  (run on the Mac)

Dump Apple's OWN keyboard-type classification table via KBGetLayoutType(),
the HIToolbox call that maps a physical keyboard type number to
kKeyboardJIS / kKeyboardANSI / kKeyboardISO.

Purpose: the one unresolved corner of keyboard-type resolution is the
192/193 containment inversion on Russian -- PC, and the MODERN_TRANSLATED
set {58, 91} is empirically derived from sweeps rather than from Apple's
table. If HIToolbox still exports KBGetLayoutType, this probe reads the
authoritative kind for EVERY type 0..255 (plus a few larger sweep values),
which either:

  * closes 192/193 with Apple's own classification (and shows whether the
    pre-containment translation theory matches their table), and reveals
    the modern ISO/JIS values (92/93?) without needing ISO/JIS hardware; or
  * proves the symbol is gone from modern HIToolbox, in which case the
    corner stays closed-pending-hardware and this probe documents that.

The dump is compared against common/gestalt_keyboard.KIND_BY_TYPE and every
difference is printed -- differences are FINDINGS, not errors: our table was
read off classic Gestalt.h constant names and Apple's runtime table is the
truth.

Usage:
  python3 tests/probes/probe_kbgetlayouttype_dump.py
"""

import os
import sys
import ctypes


def _bootstrap_src_on_path():
    """Locate the package 'src' dir relative to this file and add it to sys.path.

    Lets the probe run from the repo root, from tests/probes/, or from any
    nested probe folder without the caller setting PYTHONPATH.
    """

    here = os.path.dirname(os.path.abspath(__file__))
    cursor = here
    for _ in range(8):
        candidate = os.path.join(cursor, 'src')
        if os.path.isdir(os.path.join(candidate, 'keylayout_to_xkb')):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return candidate
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    raise RuntimeError('could not locate src/keylayout_to_xkb above this file')


_bootstrap_src_on_path()

from keylayout_to_xkb.common.gestalt_keyboard import (
    KIND_BY_TYPE,
    MODERN_TRANSLATED_TYPES,
)


__version__ = '20260704b'

_HITOOLBOX_PATH = ('/System/Library/Frameworks/Carbon.framework/Frameworks/'
                   'HIToolbox.framework/HIToolbox')

def _decode_kind(raw):
    """Decode a KBGetLayoutType return value.

    Modern HIToolbox returns FOURCC OSType codes, not the classic 0/1/2
    enum: 0x414E5349 'ANSI', 0x49534F20 'ISO ', 0x4A495320 'JIS ',
    0x3F3F3F3F '????' (unknown to Apple). The first dump run printed raw
    integers because this decoder assumed the classic enum -- the numbers
    were perfectly meaningful, just wearing FourCC.
    """

    chars = ''.join(chr((raw >> shift) & 0xFF) for shift in (24, 16, 8, 0))
    if all(' ' <= ch <= '~' for ch in chars):
        label = chars.strip()
        return label if label != '????' else 'UNKNOWN-TO-APPLE'
    if raw in (0, 1, 2):
        return {0: 'JIS', 1: 'ANSI', 2: 'ISO'}[raw]
    return 'UNDECODED(%d)' % raw

_SWEEP_EXTRAS = (256, 300, 500, 1000, 1201, 1202, 1300)

_FOCUS_TYPES = (58, 91, 92, 93, 192, 193, 194)


def main(argv):
    print('KBGetLayoutType dump probe (%s)\n' % __version__)
    try:
        handle = ctypes.CDLL(_HITOOLBOX_PATH)
    except OSError as error:
        print('could not load HIToolbox (this probe needs macOS): %s' % error)
        return 2
    if not hasattr(handle, 'KBGetLayoutType'):
        print('HIToolbox no longer exports KBGetLayoutType.')
        print('VERDICT: the 192/193 corner stays closed pending real')
        print('hardware that reports those types; MODERN_TRANSLATED_TYPES')
        print('remains sweep-derived.')
        return 1

    handle.KBGetLayoutType.restype = ctypes.c_int32
    handle.KBGetLayoutType.argtypes = [ctypes.c_int16]

    apple = {}
    for type_number in list(range(0, 256)) + list(_SWEEP_EXTRAS):
        raw = handle.KBGetLayoutType(type_number)
        apple[type_number] = _decode_kind(raw)

    print('focus types:')
    for type_number in _FOCUS_TYPES:
        ours = KIND_BY_TYPE.get(type_number)
        modern = ' [MODERN_TRANSLATED]' if type_number in \
            MODERN_TRANSLATED_TYPES else ''
        print('   type %3d: Apple says %-12s ours says %s%s'
              % (type_number, apple[type_number], ours, modern))

    print('\ndifferences vs our KIND_BY_TYPE (findings, not errors):')
    differences = 0
    for type_number in sorted(apple):
        ours = KIND_BY_TYPE.get(type_number)
        theirs = apple[type_number]
        # Our table records only classified types; Apple returns a kind for
        # everything. Report only rows where BOTH have an opinion and they
        # differ, or where ours has one Apple does not confirm.
        if ours is not None and ours != theirs:
            differences += 1
            print('   type %3d: Apple %-12s ours %s'
                  % (type_number, theirs, ours))
    if not differences:
        print('   none -- every classified type in our table matches Apple.')

    print('\nfull Apple kind runs (compressed):')
    run_start = 0
    run_kind = apple[0]
    for type_number in range(1, 256):
        if apple[type_number] != run_kind:
            print('   %3d-%3d: %s' % (run_start, type_number - 1, run_kind))
            run_start = type_number
            run_kind = apple[type_number]
    print('   %3d-255: %s' % (run_start, run_kind))
    for type_number in _SWEEP_EXTRAS:
        print('   %4d: %s' % (type_number, apple[type_number]))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
