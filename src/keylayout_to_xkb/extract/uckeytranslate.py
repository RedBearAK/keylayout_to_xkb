"""
keylayout_to_xkb/extract/uckeytranslate.py

Deterministic plane resolution via Apple's own UCKeyTranslate, used only when
running on macOS. This is the authoritative counterpart to the content-driven
plane resolver in uchr_parse.py.

WHY THIS EXISTS
The binary 'uchr' modifier section (keyModifiersToTableNum) encodes which char
table each modifier combination selects, but the on-disk index is a compacted,
undocumented encoding we cannot decode into predicates (unlike the .keylayout
XML, whose <modifier> rules ARE explicit predicates). So uchr_parse.py resolves
planes by table CONTENT, which is validated to agree with the XML parser but is
heuristic, not derived.

UCKeyTranslate is the OS function that resolves a uchr deterministically -- it
knows how to read the compacted index, because that is its job. When we are on
macOS we can therefore ask it directly: "for this layout, what does each key
produce at the plain / shift / option / shift+option modifier state?" and map
those answers back to char tables. That yields the exact plane->table map with
no heuristic.

SCOPE (deliberately narrow)
This module resolves ONLY the plane->table assignment, returning the same
{ModifierState: table_index} dict that uchr_parse._resolve_plane_tables returns.
Everything else (dead keys, terminators, compositions) stays on the existing,
validated parser path. We do not build a second parser; we replace one heuristic
input with an authoritative one when we can.

FALLBACK
Every entry point fails safe: if the frameworks or the symbol are unavailable
(i.e. not macOS), or anything goes wrong, the caller falls back to the
content-driven resolver. The chosen path is always logged so it is never silent
which resolver ran.
"""

import ctypes

from ctypes import util as ctypes_util

from keylayout_to_xkb.common.debug import dbg, warn
from keylayout_to_xkb.common.models import (
    ModifierState,
    PLANE_MODIFIER_BYTE,
)


__version__ = '20260702'


def _utf16_units_to_str(out_buffer, length: int) -> str:
    """Combine a UCKeyTranslate UniChar (UTF-16) unit array into a Python str.

    UCKeyTranslate returns UTF-16 code units. A supplementary-plane codepoint
    (Wancho, Adlam, Pahawh, ...) arrives as a surrogate PAIR of two units, so the
    units must be decoded as UTF-16 -- decoding per-unit with chr() would leave
    lone surrogates instead of the real codepoint. Packing to little-endian bytes
    and decoding as utf-16-le combines the pair correctly.
    """

    raw = bytes()
    for i in range(length):
        unit = out_buffer[i]
        raw += bytes((unit & 0xFF, (unit >> 8) & 0xFF))
    return raw.decode('utf-16-le')


_HITOOLBOX_PATH = (
    '/System/Library/Frameworks/Carbon.framework/Versions/A/'
    'Frameworks/HIToolbox.framework/Versions/A/HIToolbox'
)

# kUCKeyActionDown
_KEY_ACTION_DOWN = 0

# The plane -> modifierKeyState byte mapping is the SHARED constant
# PLANE_MODIFIER_BYTE in common/models.py -- all eight typeable planes,
# including the caps quartet. Deliberately NOT redeclared here: this module
# once carried its own four-plane copy, which silently dropped the caps layers
# from every on-Mac plane resolution after the content resolver grew to eight
# planes (while every off-Mac test, taking the fallback path, stayed green).
# See the constant's comment in models.py for the Carbon byte derivation.

# Probe keys: a spread of virtual keys whose outputs identify the matching table
# unambiguously. Includes alphabetic keys (distinctive on plain/shift) AND keys
# that carry distinctive symbols on the Option planes, so symbol-heavy planes
# match as reliably as letter-heavy ones. Requiring several to agree guards
# against a single-key coincidence.
_PROBE_VKS = (0, 1, 2, 13, 14, 15, 17, 31, 32, 38, 40, 45, 18, 19, 20, 35, 41, 47)


class _UCKTUnavailable(Exception):
    """Internal: UCKeyTranslate path cannot be used; caller should fall back."""


def _load_uckeytranslate():
    """Load HIToolbox and bind UCKeyTranslate + LMGetKbdType.

    Raises _UCKTUnavailable on any platform where the framework or symbol is
    not present (i.e. not macOS), so the caller falls back cleanly.
    """

    handle = None
    try:
        handle = ctypes.CDLL(_HITOOLBOX_PATH)
    except OSError:
        found = ctypes_util.find_library('Carbon')
        if found:
            try:
                handle = ctypes.CDLL(found)
            except OSError:
                handle = None
    if handle is None:
        raise _UCKTUnavailable('HIToolbox not loadable (not macOS)')

    if not hasattr(handle, 'UCKeyTranslate'):
        raise _UCKTUnavailable('UCKeyTranslate symbol absent')

    void_p = ctypes.c_void_p
    u16 = ctypes.c_uint16
    u32 = ctypes.c_uint32

    handle.UCKeyTranslate.restype = ctypes.c_int32      # OSStatus
    handle.UCKeyTranslate.argtypes = [
        void_p,                                 # keyLayoutPtr
        u16,                                    # virtualKeyCode
        u16,                                    # keyAction
        u32,                                    # modifierKeyState
        u32,                                    # keyboardType
        u32,                                    # keyTranslateOptions
        ctypes.POINTER(u32),                    # deadKeyState (in/out)
        ctypes.c_ulong,                         # maxStringLength
        ctypes.POINTER(ctypes.c_ulong),         # actualStringLength (out)
        ctypes.POINTER(u16),                    # unicodeString (out)
    ]

    kbd_type = 0
    if hasattr(handle, 'LMGetKbdType'):
        handle.LMGetKbdType.restype = ctypes.c_uint32
        handle.LMGetKbdType.argtypes = []
        try:
            kbd_type = handle.LMGetKbdType()
        except OSError:
            kbd_type = 0

    return handle, kbd_type


def _translate(handle, layout_ptr, kbd_type, virtual_key, modifier_byte):
    """Call UCKeyTranslate for one key+plane; return the output string.

    Returns the produced string (which may be empty), or None if the key enters
    a dead-key state (deadKeyState becomes non-zero with no immediate output) so
    the caller can treat dead keys distinctly. Any OS error returns ''.
    """

    dead_key_state = ctypes.c_uint32(0)
    buffer_len = 8
    actual_len = ctypes.c_ulong(0)
    out_buffer = (ctypes.c_uint16 * buffer_len)()

    status = handle.UCKeyTranslate(
        layout_ptr,
        virtual_key,
        _KEY_ACTION_DOWN,
        modifier_byte,
        kbd_type,
        0,                                  # options: 0 so dead keys are visible
        ctypes.byref(dead_key_state),
        buffer_len,
        ctypes.byref(actual_len),
        out_buffer,
    )
    if status != 0:
        return ''

    if actual_len.value == 0 and dead_key_state.value != 0:
        return None  # dead key: entered a state, produced nothing yet

    return _utf16_units_to_str(out_buffer, actual_len.value)


def resolve_plane_tables_via_os(
    data: bytes,
    char_tables: 'list[tuple[int, int]]',
    table_outputs_fn,
) -> 'dict | None':
    """Authoritatively resolve plane -> table index using UCKeyTranslate.

    Returns {ModifierState: table_index}, or None if the OS path is unavailable
    (caller then uses the content-driven resolver). 'table_outputs_fn' is a
    callback (table_index -> {virtual_key: output_str}) giving each table's
    single-character outputs for ALL keys -- letters AND symbols. Using all
    outputs (not just letters) is essential: the Option planes are mostly
    symbols (a, ss, dd, (c)), so a letters-only match would find no overlap and
    silently drop the Option layers (the bug the OS oracle caught on first run).

    Method: for each plane, ask UCKeyTranslate what the probe keys produce, then
    find the table whose probe-key outputs match best. Requiring several probe
    keys to agree makes the match unambiguous.
    """

    try:
        handle, kbd_type = _load_uckeytranslate()
    except _UCKTUnavailable as reason:
        dbg('uckt', f'UCKeyTranslate unavailable: {reason}; using content resolver')
        return None

    # Pin the layout bytes in memory and hand UCKeyTranslate a pointer to them.
    buffer = ctypes.create_string_buffer(data, len(data))
    layout_ptr = ctypes.cast(buffer, ctypes.c_void_p)

    # Precompute each table's probe-key outputs (all single chars) for matching.
    table_probe = {}
    for table_index in range(len(char_tables)):
        outputs = table_outputs_fn(table_index)
        table_probe[table_index] = {
            vk: outputs.get(vk) for vk in _PROBE_VKS
            if outputs.get(vk) and len(outputs[vk]) == 1
        }

    resolved = {}
    for plane, modifier_byte in PLANE_MODIFIER_BYTE.items():
        os_outputs = {}
        for vk in _PROBE_VKS:
            produced = _translate(handle, layout_ptr, kbd_type, vk, modifier_byte)
            # Only single-character, non-dead outputs are useful for matching a
            # table cell; skip empties, dead keys (None), and multi-char.
            if produced and len(produced) == 1:
                os_outputs[vk] = produced

        if not os_outputs:
            dbg('uckt', f'plane {plane.value}: no probe output; skipping')
            continue

        best_index = None
        best_score = 0
        for table_index, probe in table_probe.items():
            shared = [vk for vk in os_outputs if vk in probe]
            if not shared:
                continue
            agree = sum(1 for vk in shared if probe[vk] == os_outputs[vk])
            # Require strong agreement; track the best-matching table.
            if agree > best_score:
                best_score = agree
                best_index = table_index

        # Only accept a confident match (most probe keys agree).
        if best_index is not None and best_score >= max(2, len(os_outputs) - 1):
            resolved[plane] = best_index
        else:
            dbg(
                'uckt',
                f'plane {plane.value}: no confident table match '
                f'(best score {best_score}/{len(os_outputs)})'
            )

    if not resolved:
        warn('uckt', 'UCKeyTranslate produced no plane matches; using content resolver')
        return None

    dbg(
        'uckt',
        'planes via UCKeyTranslate: '
        + ', '.join(f'{p.value}=t{t}' for p, t in resolved.items())
    )
    return resolved


# --------------------------------------------------------------------------
# Full-layout reference via UCKeyTranslate (the OS oracle)
# --------------------------------------------------------------------------
# Beyond plane resolution, UCKeyTranslate can produce the ENTIRE layout
# authoritatively: every key at every plane, dead-key entry, and -- by feeding a
# dead key then a base key -- the composed result. This is the ground truth used
# by the verifier (verify/os_oracle.py) to audit the binary parser cell by cell,
# including for layouts that ship no .keylayout XML (most of Apple's system set).
#
# It runs only on macOS. The builder returns plain dicts (no model dependency)
# so the verifier owns all comparison logic.

# Virtual keys to sweep for a full reference. Covers the alphanumeric block, the
# number row, punctuation, and the ISO/JIS extra keys, i.e. every key a layout
# meaningfully maps. Function/arrow/keypad keys are excluded (not layout chars).
_REFERENCE_VKS = tuple(range(0, 0x35)) + (0x52, 0x5d, 0x5e)


class OSOracleUnavailable(Exception):
    """Raised when the OS oracle cannot run (not macOS, or symbol missing)."""


def _translate_full(handle, layout_ptr, kbd_type, virtual_key, modifier_byte):
    """Translate one key+plane, returning (output, dead_state).

    output is the produced string (possibly multi-char, possibly empty).
    dead_state is the non-zero UInt32 the OS set if this key entered a dead-key
    state (in which case output is typically empty). Returns ('', 0) on error.
    """

    dead_key_state = ctypes.c_uint32(0)
    buffer_len = 16
    actual_len = ctypes.c_ulong(0)
    out_buffer = (ctypes.c_uint16 * buffer_len)()

    status = handle.UCKeyTranslate(
        layout_ptr, virtual_key, _KEY_ACTION_DOWN, modifier_byte, kbd_type,
        0, ctypes.byref(dead_key_state), buffer_len,
        ctypes.byref(actual_len), out_buffer,
    )
    if status != 0:
        return '', 0
    output = _utf16_units_to_str(out_buffer, actual_len.value)
    return output, dead_key_state.value


def _compose_after(handle, layout_ptr, kbd_type, dead_state, base_vk, base_mod):
    """Given an active dead_state, translate base_vk to get the composed result.

    Feeds the prior dead_key_state into UCKeyTranslate so the OS composes the
    dead key with the base key, returning the resulting string ('' on error).
    """

    state = ctypes.c_uint32(dead_state)
    buffer_len = 16
    actual_len = ctypes.c_ulong(0)
    out_buffer = (ctypes.c_uint16 * buffer_len)()

    status = handle.UCKeyTranslate(
        layout_ptr, base_vk, _KEY_ACTION_DOWN, base_mod, kbd_type,
        0, ctypes.byref(state), buffer_len,
        ctypes.byref(actual_len), out_buffer,
    )
    if status != 0:
        return ''
    return _utf16_units_to_str(out_buffer, actual_len.value)


def build_os_reference(data: bytes) -> 'dict':
    """Build a complete reference layout from UCKeyTranslate.

    Returns a dict:
      {
        'cells': { (virtual_key, plane_name): {'output': str, 'dead': bool} },
        'compositions': { (virtual_key, plane_name): { base_char: result } },
      }
    where plane_name is a ModifierState value ('plain' .. 'caps_shift_option'):
    every plane in the shared PLANE_MODIFIER_BYTE, so the reference covers the
    caps quartet as well as the base four.

    'cells' is every key at every plane: its produced string and whether it is a
    dead key. 'compositions' is, for each dead-key cell, the result of following
    it with every plain/shift base key -- the OS-composed output, which is the
    ground truth for the parser's reconstructed composition tables.

    Raises OSOracleUnavailable off macOS.
    """

    try:
        handle, kbd_type = _load_uckeytranslate()
    except _UCKTUnavailable as reason:
        raise OSOracleUnavailable(str(reason)) from None

    buffer = ctypes.create_string_buffer(data, len(data))
    layout_ptr = ctypes.cast(buffer, ctypes.c_void_p)

    plane_bytes = [(p.value, b) for p, b in PLANE_MODIFIER_BYTE.items()]

    cells = {}
    compositions = {}

    for plane_name, modifier_byte in plane_bytes:
        for vk in _REFERENCE_VKS:
            output, dead_state = _translate_full(
                handle, layout_ptr, kbd_type, vk, modifier_byte
            )
            is_dead = dead_state != 0 and output == ''
            cells[(vk, plane_name)] = {'output': output, 'dead': is_dead}

            if is_dead:
                # Probe the composition table: this dead key followed by every
                # plain and shift base key. Record only non-empty results.
                comp = {}
                for base_plane, base_mod in (
                    ('plain', PLANE_MODIFIER_BYTE[ModifierState.PLAIN]),
                    ('shift', PLANE_MODIFIER_BYTE[ModifierState.SHIFT]),
                ):
                    for base_vk in _REFERENCE_VKS:
                        base_char, _ = _translate_full(
                            handle, layout_ptr, kbd_type, base_vk, base_mod
                        )
                        if not base_char or len(base_char) != 1:
                            continue
                        result = _compose_after(
                            handle, layout_ptr, kbd_type, dead_state,
                            base_vk, base_mod
                        )
                        if result:
                            comp[base_char] = result
                if comp:
                    compositions[(vk, plane_name)] = comp

    return {'cells': cells, 'compositions': compositions}


# End of file #
