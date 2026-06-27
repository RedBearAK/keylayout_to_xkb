"""
keylayout_to_xkb/extract/tis_source.py

Pulls raw 'uchr' keyboard-layout bytes from macOS via the Text Input Source
(TIS) APIs in the Carbon/HIToolbox framework, using ctypes.

This is the thinnest possible bridge: enumerate input sources, and for each
one that exposes Unicode key-layout data, hand back the raw CFData bytes plus
whatever name/id we can read. It does NOT parse the bytes; parsing is
uchr_parse.py's job. Keeping extraction and parsing separate means a failure
here ("got 0 sources" / "got 0 bytes") is clearly distinguishable from a
parser bug.

FUZZY AREA: Carbon is deprecated on current macOS (Sonoma included). The
framework still loads and these symbols still resolve, but the exact ctypes
restype/argtypes and CoreFoundation marshalling is the most likely thing to
need adjustment on real hardware. Every step logs, and raw bytes can be
dumped to disk before any parsing is attempted, so extraction can be proven
in isolation.

This module only runs on macOS. On any other platform the import of the
frameworks will fail loudly; that is intentional.
"""

import ctypes

from ctypes import util as ctypes_util

from keylayout_to_xkb.common.debug import dbg, warn, hex_window


__version__ = '20260622'


# CoreFoundation type-id constants we rely on are resolved at runtime rather
# than hardcoded. The framework paths are the standard absolute locations.
_CF_PATH = '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation'
_HITOOLBOX_PATH = (
    '/System/Library/Frameworks/Carbon.framework/Versions/A/'
    'Frameworks/HIToolbox.framework/Versions/A/HIToolbox'
)


class TISExtractionError(RuntimeError):
    """Raised when the TIS bridge cannot be set up at all."""


def _load_frameworks():
    """Load CoreFoundation and HIToolbox via ctypes.

    Returns the two CDLL handles. Tries the absolute framework paths first,
    then falls back to ctypes.util.find_library, which on macOS knows how to
    resolve framework names.
    """

    core_foundation = None
    hitoolbox = None

    try:
        core_foundation = ctypes.CDLL(_CF_PATH)
    except OSError:
        found = ctypes_util.find_library('CoreFoundation')
        if found:
            core_foundation = ctypes.CDLL(found)

    try:
        hitoolbox = ctypes.CDLL(_HITOOLBOX_PATH)
    except OSError:
        found = ctypes_util.find_library('Carbon')
        if found:
            hitoolbox = ctypes.CDLL(found)

    if core_foundation is None or hitoolbox is None:
        raise TISExtractionError(
            'could not load CoreFoundation and/or HIToolbox; '
            'this module only runs on macOS'
        )

    dbg('tis', 'frameworks loaded')
    return core_foundation, hitoolbox


def _configure_signatures(core_foundation, hitoolbox) -> None:
    """Set restype/argtypes on the framework functions we call.

    Getting these wrong is a top-3 cause of segfaults in ctypes-against-Carbon
    code, so they are all set explicitly rather than relying on the int
    default. If something segfaults on your machine, this function is the
    first suspect and the --debug dumps around each call narrow it down.
    """

    void_p = ctypes.c_void_p

    # CoreFoundation
    core_foundation.CFArrayGetCount.restype = ctypes.c_long
    core_foundation.CFArrayGetCount.argtypes = [void_p]

    core_foundation.CFArrayGetValueAtIndex.restype = void_p
    core_foundation.CFArrayGetValueAtIndex.argtypes = [void_p, ctypes.c_long]

    core_foundation.CFDataGetLength.restype = ctypes.c_long
    core_foundation.CFDataGetLength.argtypes = [void_p]

    core_foundation.CFDataGetBytePtr.restype = ctypes.POINTER(ctypes.c_ubyte)
    core_foundation.CFDataGetBytePtr.argtypes = [void_p]

    core_foundation.CFStringGetCStringPtr.restype = ctypes.c_char_p
    core_foundation.CFStringGetCStringPtr.argtypes = [void_p, ctypes.c_uint32]

    core_foundation.CFStringGetCString.restype = ctypes.c_bool
    core_foundation.CFStringGetCString.argtypes = [
        void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32,
    ]

    # HIToolbox / TIS
    hitoolbox.TISCreateInputSourceList.restype = void_p
    hitoolbox.TISCreateInputSourceList.argtypes = [void_p, ctypes.c_bool]

    hitoolbox.TISGetInputSourceProperty.restype = void_p
    hitoolbox.TISGetInputSourceProperty.argtypes = [void_p, void_p]

    dbg('tis', 'function signatures configured')


def _cfstring_to_str(core_foundation, cfstring_ptr) -> str:
    """Best-effort conversion of a CFStringRef to a Python str.

    Tries the fast CFStringGetCStringPtr path, falls back to the buffer copy.
    Returns an empty string rather than raising, since a missing name should
    not abort extraction of an otherwise-good layout.
    """

    if not cfstring_ptr:
        return ''

    # kCFStringEncodingUTF8 == 0x08000100
    utf8 = 0x08000100

    fast = core_foundation.CFStringGetCStringPtr(cfstring_ptr, utf8)
    if fast:
        try:
            return fast.decode('utf-8', 'replace')
        except (AttributeError, UnicodeDecodeError):
            pass

    buffer_len = 512
    buffer = ctypes.create_string_buffer(buffer_len)
    ok = core_foundation.CFStringGetCString(cfstring_ptr, buffer, buffer_len, utf8)
    if ok:
        return buffer.value.decode('utf-8', 'replace')

    return ''


def _get_property_constant(hitoolbox, symbol_name: str):
    """Read an exported CFStringRef constant (a TIS property key) by symbol.

    The TIS property keys (kTISPropertyUnicodeKeyLayoutData, etc.) are exported
    as global CFStringRef symbols. ctypes reads them as the pointer value at
    the symbol's address.
    """

    try:
        return ctypes.c_void_p.in_dll(hitoolbox, symbol_name)
    except ValueError:
        warn('tis', f'TIS property symbol not found: {symbol_name}')
        return None


def extract_all_layouts(dump_dir: 'str | None' = None) -> 'list[dict]':
    """Enumerate input sources and return raw 'uchr' payloads.

    Each returned dict has keys:
        'name'      -> localized layout name (may be empty)
        'source_id' -> TIS input source id (may be empty)
        'data'      -> raw 'uchr' bytes (bytes object), or None if absent

    If 'dump_dir' is given, each layout's raw bytes are also written there as
    <source_id_or_index>.uchr BEFORE any parsing, so extraction can be proven
    independently. This is the first checkpoint: if these files exist and have
    plausible sizes, the ctypes bridge works and any later failure is a parser
    problem, not an extraction problem.
    """

    core_foundation, hitoolbox = _load_frameworks()
    _configure_signatures(core_foundation, hitoolbox)

    prop_uchr = _get_property_constant(hitoolbox, 'kTISPropertyUnicodeKeyLayoutData')
    prop_name = _get_property_constant(hitoolbox, 'kTISPropertyLocalizedName')
    prop_id = _get_property_constant(hitoolbox, 'kTISPropertyInputSourceID')

    if prop_uchr is None:
        raise TISExtractionError(
            'kTISPropertyUnicodeKeyLayoutData symbol unavailable; '
            'cannot locate layout data'
        )

    source_list = hitoolbox.TISCreateInputSourceList(None, True)
    if not source_list:
        raise TISExtractionError('TISCreateInputSourceList returned null')

    count = core_foundation.CFArrayGetCount(source_list)
    dbg('tis', f'input source count: {count}')

    results = []

    for index in range(count):
        source = core_foundation.CFArrayGetValueAtIndex(source_list, index)
        if not source:
            warn('tis', f'null input source at index {index}')
            continue

        name = ''
        source_id = ''

        if prop_name is not None:
            name_ref = hitoolbox.TISGetInputSourceProperty(source, prop_name)
            name = _cfstring_to_str(core_foundation, name_ref)

        if prop_id is not None:
            id_ref = hitoolbox.TISGetInputSourceProperty(source, prop_id)
            source_id = _cfstring_to_str(core_foundation, id_ref)

        data_ref = hitoolbox.TISGetInputSourceProperty(source, prop_uchr)
        if not data_ref:
            # Many input sources (input methods, non-keyboard sources) have no
            # 'uchr' data. That is expected, not an error; skip quietly except
            # under debug.
            dbg('tis', f'no uchr data: index={index} name={name!r} id={source_id!r}')
            results.append({'name': name, 'source_id': source_id, 'data': None})
            continue

        data_len = core_foundation.CFDataGetLength(data_ref)
        byte_ptr = core_foundation.CFDataGetBytePtr(data_ref)
        if not byte_ptr or data_len <= 0:
            warn('tis', f'uchr data present but unreadable: name={name!r}')
            results.append({'name': name, 'source_id': source_id, 'data': None})
            continue

        raw = ctypes.string_at(byte_ptr, data_len)
        dbg('tis', f'layout name={name!r} id={source_id!r} bytes={data_len}')
        dbg('tis', hex_window(raw, 0, 32))

        if dump_dir is not None:
            _dump_raw(dump_dir, source_id or f'index_{index}', raw)

        results.append({'name': name, 'source_id': source_id, 'data': raw})

    return results


def _dump_raw(dump_dir: str, stem: str, raw: bytes) -> None:
    """Write raw 'uchr' bytes to <dump_dir>/<stem>.uchr for offline inspection."""

    import os
    import re

    os.makedirs(dump_dir, exist_ok=True)
    safe_stem = re.sub(r'[^A-Za-z0-9._-]', '_', stem)
    path = os.path.join(dump_dir, f'{safe_stem}.uchr')

    with open(path, 'wb') as handle:
        handle.write(raw)

    dbg('tis', f'dumped {len(raw)} bytes -> {path}')


# End of file #
