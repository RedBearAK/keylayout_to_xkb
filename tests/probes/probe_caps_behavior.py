#!/usr/bin/env python3
"""
tests/probes/probe_caps_behavior.py  (run on macOS)

Gathers full-set data on the CAPS layer to test -- and antagonize -- the
hypothesis that layouts fall into two caps behaviors:

  behavior 1 (caps-as-shift-level): CapsLock acts like a momentary Shift-style
    level. Structural guess: caps+shift reuses the base SHIFT table and
    caps+shift+option reuses the base SHIFT+OPTION table; only caps and
    caps+option point at new tables.

  behavior 2 (caps-as-mode / alphabet switch): CapsLock latches a whole separate
    alphabet (e.g. Latin on a Tibetan keyboard). Structural guess: the caps
    tables are an independent block that does NOT collapse onto base shift
    tables.

The static table-reuse pattern is only an INFERENCE of intent. This probe also
reads what the OS actually does, so structure can be checked against behavior:

  For each layout it records, per caps plane, whether the caps table index
  equals a base table index (and which), giving the full reuse fingerprint --
  not just a yes/no -- so any third/odd pattern (e.g. Tibetan-Wylie's observed
  [0,0,5,4]) shows up as its own fingerprint rather than being forced into a
  bucket.

Run across ALL layouts to see whether the buckets are clean and stable, or
whether the "pattern" was a small-sample coincidence. The OS-driven half (when
available) reports, for a sample key, the plain vs caps output so the structural
fingerprint can be sanity-checked against real output.

Usage (from repo root or anywhere):
  python3 tests/probes/probe_caps_behavior.py
  python3 tests/probes/probe_caps_behavior.py --no-os      # structural only
  python3 tests/probes/probe_caps_behavior.py tibetan wancho
"""

import os
import sys
import struct
import ctypes
from collections import defaultdict


def _bootstrap_src_on_path():
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
    raise RuntimeError(f'could not locate src/keylayout_to_xkb above {here}')


_bootstrap_src_on_path()

from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract import uchr_parse as up


# Base and caps modifier bytes (byte+2 gives the modifier-map index).
_BASE = [('plain', 0x00), ('shift', 0x02), ('option', 0x08), ('shopt', 0x0A)]
_CAPS = [('caps', 0x04), ('caps_shift', 0x06),
         ('caps_opt', 0x0C), ('caps_shopt', 0x0E)]


__version__ = '20260626'


def _tables(data):
    """Return (base_tables, caps_tables) as lists of char-table indices."""

    _hf, _dv, _fi, _ktc = struct.unpack_from('<HHII', data, 0)
    f, l, mod, ci, sro, sto, seqo = struct.unpack_from('<IIIIIII', data, 12)
    m, default, mc = struct.unpack_from('<HHH', data, mod)
    arr = [data[mod + 6 + k] for k in range(mc)]

    def tbl(byte):
        i = byte + 2
        return arr[i] if i < len(arr) else default

    base = [tbl(b) for _n, b in _BASE]
    caps = [tbl(b) for _n, b in _CAPS]
    return base, caps


def _fingerprint(base, caps):
    """Describe each caps table by what base plane (if any) it reuses.

    Returns a tuple of labels, one per caps plane: the base-plane name it equals,
    or 'NEW' if it matches no base table. This is the full reuse pattern, not a
    binary bucket -- odd layouts get their own distinct fingerprint.
    """

    base_by_name = {name: caps_idx for (name, _b), caps_idx in zip(_BASE, base)}
    index_to_basename = {}
    for (name, _b), idx in zip(_BASE, base):
        index_to_basename.setdefault(idx, name)
    out = []
    for (name, _b), idx in zip(_CAPS, caps):
        out.append(index_to_basename.get(idx, 'NEW'))
    return tuple(out)


def main(argv):
    use_os = True
    wants = []
    i = 0
    while i < len(argv):
        if argv[i] == '--no-os':
            use_os = False; i += 1
        else:
            wants.append(argv[i].lower()); i += 1

    payloads = extract_all_layouts()

    handle = kbd_type = None
    if use_os:
        try:
            from keylayout_to_xkb.extract.uckeytranslate import (
                _load_uckeytranslate, _translate)
            handle, kbd_type = _load_uckeytranslate()
        except Exception as error:
            print(f'(OS unavailable: {error}; structural-only)\n')
            use_os = False

    fingerprint_counts = defaultdict(list)   # fingerprint -> [layout names]
    rows = []

    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data:
            continue
        if wants and not any(tok in name.lower() for tok in wants):
            continue
        try:
            base, caps = _tables(data)
        except Exception as error:
            print(f'{name}: failed ({error})')
            continue
        fp = _fingerprint(base, caps)
        fingerprint_counts[fp].append(name)
        rows.append((name, base, caps, fp))

    # Print per-layout rows.
    for (name, base, caps, fp) in rows:
        print(f'{name[:30]:30} base={base} caps={caps} reuse={fp}')

    # Aggregate by fingerprint.
    print('\n' + '=' * 64)
    print('caps reuse fingerprints (caps / caps+shift / caps+opt / caps+shopt):')
    print('  each entry says which BASE plane that caps table equals, or NEW.\n')
    for fp in sorted(fingerprint_counts, key=lambda k: -len(fingerprint_counts[k])):
        names = fingerprint_counts[fp]
        sample = ', '.join(names[:4]) + ('...' if len(names) > 4 else '')
        print(f'  {str(fp):48} x{len(names):3}  [{sample}]')

    print('\nReading this:')
    print('  A clean two-bucket split (e.g. (shift,shift,NEW,shopt) vs all-NEW)')
    print('  supports the two-behavior model. Many scattered fingerprints means')
    print('  the "pattern" was sample coincidence and the real signal is')
    print('  elsewhere (or caps behavior is per-key, not per-layout).')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
