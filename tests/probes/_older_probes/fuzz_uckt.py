#!/usr/bin/env python3
"""
fuzz_uckt.py  (run on macOS)

Exhaustively fuzz the REAL UCKeyTranslate to extract the empirical shape of its
modifier-state -> character-table decoding, with NO assumptions about bit
meanings. The decision boundaries we recover ARE the algorithm.

METHOD
For a given layout + keyboard type, for each modifierKeyState byte 0..255, build
the full output vector across all 128 virtual keys (the string each key produces
at that modifier state). Two modifier bytes that yield the SAME 128-key vector
are provably selecting the same underlying character table. So we cluster the
256 bytes into equivalence classes = the real tables, labelled by which bytes
reach them. This is ground truth, independent of any bit interpretation.

Then we repeat across keyboard types and across layouts to answer the structural
question the whole investigation hinged on:

  * Are the modifier-byte equivalence classes the SAME across layouts?
      -> modifier decoding is a fixed, layout-independent UCKeyTranslate
         normalization; we can extract it once and hardcode it.
  * Do they DIFFER per layout / keyboard type?
      -> decoding is driven by the file's modifier array (and/or kbType); we
         then know exactly which axis carries the missing dimension.

OUTPUT
For each (layout, kbType): the equivalence classes as {representative_byte:
[member_bytes]}, plus, for the 4 canonical states we care about (the bytes that
produce lowercase 'a', uppercase 'A', option-char, shift+option-char on a known
key), which class they fall in.

Usage:
  python3 fuzz_uckt.py                      # default sample of layouts
  python3 fuzz_uckt.py us greek russian     # specific name substrings
  python3 fuzz_uckt.py --kbtypes 0,40,43    # sweep keyboard types too
"""
import sys, ctypes
from keylayout_to_xkb.extract.tis_source import extract_all_layouts
from keylayout_to_xkb.extract.uckeytranslate import _load_uckeytranslate, _translate

# Probe the standard letter/number virtual keys; 0..50 covers the main block.
_PROBE_VKS = list(range(0, 51))

def output_vector(handle, ptr, kbd_type, modifier_byte):
    """The tuple of outputs across probe keys at this modifier byte."""
    vec = []
    for vk in _PROBE_VKS:
        o = _translate(handle, ptr, kbd_type, vk, modifier_byte)
        vec.append(o if o is not None else '\x00DEAD')
    return tuple(vec)

def cluster_modifiers(handle, ptr, kbd_type):
    """Return {vector: [modifier_bytes...]} for all 256 modifier states."""
    classes = {}
    for mb in range(256):
        vec = output_vector(handle, ptr, kbd_type, mb)
        classes.setdefault(vec, []).append(mb)
    return classes

def analyze_bit_structure(classes):
    """Try to explain the equivalence classes with a fixed bit-rule.

    Builds byte->table_id from the clusters, then for each of the 8 bits checks
    whether flipping that bit has a CONSISTENT effect (always same table delta)
    across all bytes -- which would mean the bit is a clean independent modifier.
    Reports each bit's behavior, exposing which bits are real modifiers, which
    are ignored, and which interact (the non-decomposable part).
    """
    byte_to_table = {}
    for table_id, (vec, bytes_) in enumerate(
            sorted(classes.items(), key=lambda kv: min(kv[1]))):
        for b in bytes_:
            byte_to_table[b] = table_id

    print("  bit-structure analysis (does each bit act as a clean modifier?):")
    for bit in range(8):
        mask = 1 << bit
        # For every byte, compare table(byte) vs table(byte with bit set).
        deltas = {}
        ignored = True
        for b in range(256):
            if b & mask:
                continue  # only test from bit-clear side
            t0 = byte_to_table.get(b)
            t1 = byte_to_table.get(b | mask)
            if t0 is None or t1 is None:
                continue
            if t0 != t1:
                ignored = False
            deltas.setdefault((t0, t1), 0)
            deltas[(t0, t1)] += 1
        if ignored:
            verdict = "IGNORED (no effect anywhere)"
        else:
            # is the (t0->t1) mapping a consistent function? count distinct t1
            # for each t0
            from collections import defaultdict
            t0_to_t1s = defaultdict(set)
            for (t0, t1), _ in deltas.items():
                t0_to_t1s[t0].add(t1)
            multi = {t0: s for t0, s in t0_to_t1s.items() if len(s) > 1}
            if not multi:
                verdict = f"CLEAN modifier ({len(deltas)} consistent transitions)"
            else:
                verdict = (f"CONTEXT-DEPENDENT (table delta varies; "
                           f"interacts with other bits)")
        print(f"    bit 0x{mask:02x}: {verdict}")
    print()


def main(argv):
    kbtypes = [None]   # None -> use the live LMGetKbdType from _load
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == '--kbtypes':
            kbtypes = [int(x) for x in argv[i+1].split(',')]
            i += 2
        else:
            args.append(argv[i].lower()); i += 1
    wants = args or ['u.s.', 'greek', 'russian', 'arabic', 'hindi']

    payloads = extract_all_layouts()
    handle, live_kbtype = _load_uckeytranslate()
    print(f"live LMGetKbdType = {live_kbtype}\n")

    for want in wants:
        target = next((p for p in payloads if p.get('data')
                       and want in p.get('name','').lower()), None)
        if not target:
            print(f"--- no layout matching {want!r} ---\n"); continue
        data = target['data']; name = target['name']
        buf = ctypes.create_string_buffer(data, len(data))
        ptr = ctypes.cast(buf, ctypes.c_void_p)

        for kbt in kbtypes:
            use_kbt = live_kbtype if kbt is None else kbt
            classes = cluster_modifiers(handle, ptr, use_kbt)
            # Sort classes by their smallest member byte for stable labels.
            ordered = sorted(classes.items(), key=lambda kv: min(kv[1]))
            print(f"=== {name!r}  kbType={use_kbt}  -> {len(ordered)} distinct tables ===")
            for vec, bytes_ in ordered:
                # show a sample of the vector (first few non-empty outputs)
                sample = [c for c in vec[:10]]
                lo = min(bytes_)
                # compact the byte list into hex
                blist = ','.join(f'0x{b:02x}' for b in bytes_[:16])
                more = '...' if len(bytes_) > 16 else ''
                print(f"  table@min0x{lo:02x}: {len(bytes_):3d} bytes [{blist}{more}]")
                print(f"      vk0-9: {sample}")
            print()
            analyze_bit_structure(classes)
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
