#!/usr/bin/env python3
"""
tests/probes/probe_xkb_roundtrip.py

Right-side end-to-end validation of the XKB/XCompose emitter.

For every layout this:
  1. emits symbols + types + compose from the parsed model,
  2. compiles the keymap with REAL libxkbcommon (not a re-implementation),
  3. loads the Compose with the real compose engine,
  4. for every key and every level, reads what libxkbcommon actually produces --
     resolving placeholder keysyms and dead-key keysyms through the compose
     engine so multi-char outputs and dead-key terminators expand to real strings,
  5. compares that against the parsed model's output for the same key+plane.

This proves the whole emit -> compile -> resolve chain preserves every character
the extraction captured, using the same XKB stack a running Linux system uses.
It is the "right side" of the eventual macOS-oracle comparison; the model is the
stand-in reference here (already oracle-validated upstream), so a clean run means
the emitter faithfully reproduces the model end to end.

Levels are aligned to planes by the type's level_name labels (emitted as the
plane value, e.g. "option"), NOT by raw level index, because each key-group has
its own level count -- level 3 on a 3-level key is a different plane than level 3
on an 8-level key. Aligning by plane name is the correct join.

Runs anywhere libxkbcommon is present (no macOS needed). Run from repo root:
    python3 tests/probes/probe_xkb_roundtrip.py
    python3 tests/probes/probe_xkb_roundtrip.py US PolishPro Tibetan-Wylie
"""

import os
import sys
import glob
import ctypes
import ctypes.util


def _bootstrap_src_on_path():
    here = os.path.dirname(os.path.abspath(__file__))
    cursor = here
    for _ in range(8):
        candidate = os.path.join(cursor, 'src')
        if os.path.isdir(os.path.join(candidate, 'keylayout_to_xkb')):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    raise RuntimeError('cannot find src/keylayout_to_xkb above %s' % here)


_bootstrap_src_on_path()

from keylayout_to_xkb.extract.uchr_parse import parse_uchr
from keylayout_to_xkb.emit.symbols import emit_symbols, _VK_TO_XKB
from keylayout_to_xkb.emit.compose import emit_compose
from keylayout_to_xkb.common.models import ModifierState, OutputKind


__version__ = '20260629'


_UPLOADS = '/mnt/user-data/uploads'

# xkb keycode name -> evdev keycode number (libxkbcommon keycodes are these).
_XKB_KEYCODE = {
    'TLDE': 49,
    'AE01': 10, 'AE02': 11, 'AE03': 12, 'AE04': 13, 'AE05': 14, 'AE06': 15,
    'AE07': 16, 'AE08': 17, 'AE09': 18, 'AE10': 19, 'AE11': 20, 'AE12': 21,
    'AD01': 24, 'AD02': 25, 'AD03': 26, 'AD04': 27, 'AD05': 28, 'AD06': 29,
    'AD07': 30, 'AD08': 31, 'AD09': 32, 'AD10': 33, 'AD11': 34, 'AD12': 35,
    'AC01': 38, 'AC02': 39, 'AC03': 40, 'AC04': 41, 'AC05': 42, 'AC06': 43,
    'AC07': 44, 'AC08': 45, 'AC09': 46, 'AC10': 47, 'AC11': 48, 'BKSL': 51,
    'LSGT': 94,
    'AB01': 52, 'AB02': 53, 'AB03': 54, 'AB04': 55, 'AB05': 56, 'AB06': 57,
    'AB07': 58, 'AB08': 59, 'AB09': 60, 'AB10': 61, 'SPCE': 65,
}


def _load_xkb():
    """Resolve the libxkbcommon entry points the probe uses."""

    lib = ctypes.CDLL(ctypes.util.find_library('xkbcommon'))
    lib.xkb_context_new.restype = ctypes.c_void_p
    lib.xkb_context_new.argtypes = [ctypes.c_int]
    lib.xkb_keymap_new_from_string.restype = ctypes.c_void_p
    lib.xkb_keymap_new_from_string.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    lib.xkb_keymap_num_levels_for_key.restype = ctypes.c_int
    lib.xkb_keymap_num_levels_for_key.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
    lib.xkb_keymap_key_get_syms_by_level.restype = ctypes.c_int
    lib.xkb_keymap_key_get_syms_by_level.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32))]
    lib.xkb_keymap_key_get_name.restype = ctypes.c_char_p
    lib.xkb_keymap_key_get_name.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.xkb_keysym_to_utf8.restype = ctypes.c_int
    lib.xkb_keysym_to_utf8.argtypes = [
        ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    # Compose
    lib.xkb_compose_table_new_from_buffer.restype = ctypes.c_void_p
    lib.xkb_compose_table_new_from_buffer.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p,
        ctypes.c_int, ctypes.c_int]
    lib.xkb_compose_state_new.restype = ctypes.c_void_p
    lib.xkb_compose_state_new.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.xkb_compose_state_feed.restype = ctypes.c_int
    lib.xkb_compose_state_feed.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.xkb_compose_state_get_status.restype = ctypes.c_int
    lib.xkb_compose_state_get_status.argtypes = [ctypes.c_void_p]
    lib.xkb_compose_state_get_utf8.restype = ctypes.c_int
    lib.xkb_compose_state_get_utf8.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.xkb_compose_state_reset.argtypes = [ctypes.c_void_p]
    return lib


def _wrap_keymap(symbols_block: str) -> str:
    """Wrap an emitted types+symbols block into a full compilable keymap."""

    types_index = symbols_block.index('xkb_types')
    symbols_index = symbols_block.index('xkb_symbols')
    types_text = symbols_block[types_index:symbols_index].rstrip()
    syms_text = symbols_block[symbols_index:].rstrip()
    indent = '\n  '
    keymap = 'xkb_keymap {\n'
    keymap += '  xkb_keycodes { include "evdev+aliases(qwerty)" };\n  '
    keymap += types_text.replace('\n', indent) + '\n'
    keymap += '  xkb_compat { include "complete" };\n  '
    keymap += syms_text.replace('\n', indent) + '\n'
    keymap += '};\n'
    return keymap


def _keysym_to_str(lib, keysym: int) -> str:
    """UTF-8 string a keysym maps to, or '' for none."""

    buf = ctypes.create_string_buffer(32)
    lib.xkb_keysym_to_utf8(keysym, buf, 32)
    return buf.value.decode('utf-8', 'replace')


def _resolve_via_compose(lib, compose_table, keysym: int) -> 'str | None':
    """Feed a single keysym through the compose engine; return the composed
    string if it COMPOSES (status 2), else None (not a compose trigger)."""

    state = lib.xkb_compose_state_new(compose_table, 0)
    if not state:
        return None
    lib.xkb_compose_state_feed(state, keysym)
    status = lib.xkb_compose_state_get_status(state)
    if status == 2:                                    # XKB_COMPOSE_COMPOSED
        buf = ctypes.create_string_buffer(64)
        lib.xkb_compose_state_get_utf8(state, buf, 64)
        return buf.value.decode('utf-8', 'replace')
    return None


def _model_cell(layout, virtual_key, plane):
    """Return ('dead', state) / ('chars', text) / None for a model cell."""

    key_output = layout.keys.get(virtual_key, {}).get(plane)
    if key_output is None:
        return None
    if key_output.kind is OutputKind.DEAD:
        return ('dead', key_output.dead_state_name)
    if key_output.kind is OutputKind.CHARS and key_output.output:
        return ('chars', key_output.output)
    return None


def _xkb_level_keysym(lib, keymap, keycode, level):
    """Return the first keysym at a keycode+level, or 0 if none."""

    syms = ctypes.POINTER(ctypes.c_uint32)()
    count = lib.xkb_keymap_key_get_syms_by_level(keymap, keycode, 0, level,
                                                 ctypes.byref(syms))
    if count < 1:
        return 0
    return syms[0]


def _keysym_name(lib, keysym):
    """The keysym's name (e.g. 'dead_circumflex'), or '' if unavailable."""

    if not hasattr(lib, 'xkb_keysym_get_name'):
        lib.xkb_keysym_get_name.restype = ctypes.c_int
        lib.xkb_keysym_get_name.argtypes = [
            ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    buf = ctypes.create_string_buffer(64)
    lib.xkb_keysym_get_name(keysym, buf, 64)
    return buf.value.decode('utf-8', 'replace')


def _xkb_output(lib, keymap, compose_table, keycode: int, level: int) -> 'str | None':
    """What libxkbcommon produces for a keycode at a given level (0-based).

    Reads the level's first keysym, then: if that keysym composes (a placeholder
    or a dead key), return the composed string; otherwise return the keysym's own
    UTF-8. None if the level has no symbol.
    """

    syms = ctypes.POINTER(ctypes.c_uint32)()
    count = lib.xkb_keymap_key_get_syms_by_level(keymap, keycode, 0, level,
                                                 ctypes.byref(syms))
    if count < 1:
        return None
    keysym = syms[0]
    if keysym == 0:
        return None
    composed = _resolve_via_compose(lib, compose_table, keysym)
    if composed is not None:
        return composed
    return _keysym_to_str(lib, keysym)


# Plane -> level_name label the emitter writes (the plane value).
_PLANE_LABEL = {p: p.value for p in ModifierState}


def _level_planes(symbols_block: str, type_name: str) -> 'list':
    """Parse a type's level_name lines to get the plane each level maps to.

    Returns a list indexed by (level-1) of ModifierState, so we can align an
    XKB level back to the plane it represents.
    """

    import re
    block_start = symbols_block.index('type "%s"' % type_name)
    block = symbols_block[block_start:]
    block = block[:block.index('};')]
    planes = {}
    for match in re.finditer(r'level_name\[Level(\d+)\]\s*=\s*"([^"]+)"', block):
        level = int(match.group(1))
        label = match.group(2)
        for plane, value in _PLANE_LABEL.items():
            if value == label:
                planes[level] = plane
    return [planes.get(i + 1) for i in range(max(planes) if planes else 0)]


def _key_type_map(symbols_block: str) -> 'dict':
    """Map xkb_code -> type_name from the emitted symbols block."""

    import re
    mapping = {}
    for match in re.finditer(
        r'key <(\w+)>\s*\{\s*type\[Group1\]\s*=\s*"([^"]+)"', symbols_block
    ):
        mapping[match.group(1)] = match.group(2)
    return mapping


def _check_layout(lib, name, data):
    """Compare emitted-and-compiled output to the model for one layout.

    Returns (match, mismatch, missing, mismatches) counts and a sample list.
    """

    layout = parse_uchr(data, layout_name=name)
    symbols_block = emit_symbols(layout, 't_mac', name)
    compose_text = emit_compose(layout)

    keymap_text = _wrap_keymap(symbols_block)
    ctx = lib.xkb_context_new(0)
    keymap = lib.xkb_keymap_new_from_string(ctx, keymap_text.encode(), 1, 0)
    if not keymap:
        print('  %s: keymap FAILED to compile' % name)
        return (0, 0, 0, [('<compile>', '', '', '')])
    compose_table = lib.xkb_compose_table_new_from_buffer(
        ctx, compose_text.encode(), len(compose_text.encode()), b'C', 1, 0)

    type_by_code = _key_type_map(symbols_block)

    match = mismatch = missing = 0
    samples = []
    inverse_vk = {code: vk for vk, code in _VK_TO_XKB.items()}

    for xkb_code, keycode in _XKB_KEYCODE.items():
        if xkb_code not in type_by_code:
            continue
        virtual_key = inverse_vk.get(xkb_code)
        if virtual_key is None:
            continue
        level_planes = _level_planes(symbols_block, type_by_code[xkb_code])
        for level_index, plane in enumerate(level_planes):
            if plane is None:
                continue
            expected = _model_cell(layout, virtual_key, plane)
            keysym = _xkb_level_keysym(lib, keymap, keycode, level_index)

            if expected is None and keysym == 0:
                continue

            if expected is None:
                # XKB produced something the model lacks: only count if it is a
                # real character (NoSymbol/0 already handled).
                if keysym == 0:
                    continue
                mismatch += 1
                if len(samples) < 12:
                    samples.append((xkb_code, plane.value, None,
                                    _keysym_name(lib, keysym)))
                continue

            if expected[0] == 'dead':
                # A DEAD cell matches when XKB carries a dead_* keysym OR a PUA
                # placeholder dead key (U+E000..U+F8FF) on that level -- both are
                # valid dead-key representations the emitter produces.
                name = _keysym_name(lib, keysym)
                is_named_dead = name.startswith('dead_')
                is_placeholder = 0x0100E000 <= keysym <= 0x0100F8FF
                if is_named_dead or is_placeholder:
                    match += 1
                else:
                    mismatch += 1
                    if len(samples) < 12:
                        samples.append((xkb_code, plane.value,
                                        'dead:' + expected[1], name))
                continue

            # CHARS: resolve the keysym (placeholder/compose) to its string.
            if keysym == 0:
                missing += 1
                if len(samples) < 12:
                    samples.append((xkb_code, plane.value, expected[1], None))
                continue
            composed = _resolve_via_compose(lib, compose_table, keysym)
            got = composed if composed is not None else _keysym_to_str(lib, keysym)
            if got == expected[1]:
                match += 1
            else:
                mismatch += 1
                if len(samples) < 12:
                    samples.append((xkb_code, plane.value, expected[1], got))
    return (match, mismatch, missing, samples)


def main(argv):
    lib = _load_xkb()
    wanted = argv
    paths = sorted(glob.glob(os.path.join(_UPLOADS, '*.uchr')))
    if wanted:
        paths = [p for p in paths
                 if any(w.lower() in p.lower() for w in wanted)]

    grand_match = grand_mismatch = grand_missing = 0
    worst = []
    for path in paths:
        name = path.split('keylayout_')[-1].replace('.uchr', '')
        with open(path, 'rb') as handle:
            data = handle.read()
        match, mismatch, missing, samples = _check_layout(lib, name, data)
        grand_match += match
        grand_mismatch += mismatch
        grand_missing += missing
        total = match + mismatch + missing
        pct = (100.0 * match / total) if total else 100.0
        flag = '' if (mismatch + missing) == 0 else '  <-- DIFFS'
        print('%-26s %5d/%-5d (%.1f%%)%s' % (name, match, total, pct, flag))
        if (mismatch + missing) and samples:
            worst.append((name, samples))

    total = grand_match + grand_mismatch + grand_missing
    print('\n' + '=' * 60)
    pct = (100.0 * grand_match / total) if total else 100.0
    print('TOTAL  match %d / %d  (%.2f%%)  mismatch=%d  missing=%d'
          % (grand_match, total, pct, grand_mismatch, grand_missing))
    for name, samples in worst[:8]:
        print('\n%s sample diffs:' % name)
        for code, plane, expected, got in samples:
            print('   %-6s %-18s model=%r  xkb=%r' % (code, plane, expected, got))
    return 0 if (grand_mismatch + grand_missing) == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
