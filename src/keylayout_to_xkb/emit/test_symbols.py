"""
keylayout_to_xkb/emit/test_symbols.py

Standing tests for the XKB symbols emitter, validated against the OS-confirmed
Polish Pro layout.

Project test style: each test prints, returns True/False, main() scores. Runs
anywhere keysymdef.h is present. Requires the Polish Pro test fixture at
/mnt/user-data/uploads; if absent, fixture-dependent tests report and skip.

Run directly:  python -m keylayout_to_xkb.emit.test_symbols
Under pytest:  pytest keylayout_to_xkb/emit/test_symbols.py
"""

import io
import os
import re
import contextlib

from keylayout_to_xkb.extract.uchr_parse import parse_uchr
from keylayout_to_xkb.emit.symbols import emit_symbols


_FIXTURE = '/mnt/user-data/uploads/com_apple_keylayout_PolishPro.uchr'
_KEYSYMDEF = '/usr/include/X11/keysymdef.h'


def _polish_pro_symbols():
    with open(_FIXTURE, 'rb') as handle:
        data = handle.read()
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        layout = parse_uchr(data, layout_name='PolishPro')
    return emit_symbols(layout, 'pl_mac', 'Polish (Macintosh)')


def _valid_keysym_names():
    names = set()
    with open(_KEYSYMDEF, 'r', encoding='utf-8', errors='replace') as handle:
        for line in handle:
            match = re.match(r'#define XK_(\w+)', line)
            if match:
                names.add(match.group(1))
    return names


def _keysym_tokens(symbols_text):
    tokens = []
    for match in re.finditer(r'\{\[([^\]]+)\]\}', symbols_text):
        for token in match.group(1).split(','):
            token = token.strip()
            if token and token != 'NoSymbol':
                tokens.append(token)
    return tokens


def test_all_keysyms_valid() -> bool:
    """Every emitted keysym is a real XKB keysym name or a UXXXX form."""

    if not (os.path.isfile(_FIXTURE) and os.path.isfile(_KEYSYMDEF)):
        print('  skipped (fixture or keysymdef missing)')
        return True
    text = _polish_pro_symbols()
    valid = _valid_keysym_names()
    bad = [t for t in set(_keysym_tokens(text))
           if not re.fullmatch(r'U[0-9A-F]{4,6}', t) and t not in valid]
    print(f'  invalid keysyms: {bad if bad else "none"}')
    return not bad


def test_polish_letters_positioned() -> bool:
    """The Polish letters land on the expected keys at the option level."""

    if not os.path.isfile(_FIXTURE):
        print('  skipped (fixture missing)')
        return True
    text = _polish_pro_symbols()
    # (xkb_key, expected option-level keysym)
    expect = {
        'AC01': 'aogonek', 'AB03': 'cacute', 'AD03': 'eogonek',
        'AC09': 'lstroke', 'AB06': 'nacute', 'AD09': 'oacute',
        'AC02': 'sacute',  'AB02': 'zacute', 'AB01': 'zabovedot',
    }
    ok = True
    for key, want in expect.items():
        m = re.search(r'<%s>\s*\{\[([^\]]+)\]\}' % key, text)
        if not m:
            ok = False
            print(f'  {key}: not emitted')
            continue
        levels = [t.strip() for t in m.group(1).split(',')]
        got = levels[2] if len(levels) > 2 else '(none)'
        if got != want:
            ok = False
            print(f'  {key} level3: want {want!r}, got {got!r}')
    print(f'  polish letter positions: {"all correct" if ok else "MISMATCH"}')
    return ok


def test_dead_keys_placed() -> bool:
    """The three dead keys appear at their expected keys/levels."""

    if not os.path.isfile(_FIXTURE):
        print('  skipped (fixture missing)')
        return True
    text = _polish_pro_symbols()
    checks = [('AD07', 'dead_diaeresis'), ('AD08', 'dead_circumflex'),
              ('TLDE', 'dead_grave')]
    ok = True
    for key, want in checks:
        if not re.search(r'<%s>[^\n]*%s' % (key, want), text):
            ok = False
            print(f'  {key}: expected {want} not found')
    print(f'  dead-key placement: {"ok" if ok else "MISMATCH"}')
    return ok


def test_structure() -> bool:
    """Header, name, level3 include, and four-level rows are present."""

    if not os.path.isfile(_FIXTURE):
        print('  skipped (fixture missing)')
        return True
    text = _polish_pro_symbols()
    ok = ('xkb_symbols "pl_mac"' in text
          and 'name[Group1]= "Polish (Macintosh)"' in text
          and 'include "level3(ralt_switch)"' in text
          and text.rstrip().endswith('};'))
    # At least one full four-level key row.
    ok = ok and bool(re.search(r'<AC01>\s*\{\[ a, A, aogonek, Aogonek \]\}', text))
    print(f'  structure: {"ok" if ok else "MALFORMED"}')
    return ok


def test_iso_ansi_variants_and_swap() -> bool:
    """emit_symbols_variants produces mac-ansi and mac-iso with TLDE/LSGT swapped."""

    if not os.path.isfile(_FIXTURE):
        print('  skipped (fixture missing)')
        return True
    from keylayout_to_xkb.emit.symbols import emit_symbols_variants
    with open(_FIXTURE, 'rb') as handle:
        data = handle.read()
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        layout = parse_uchr(data, layout_name='PolishPro')
    variants = emit_symbols_variants(layout, 'mac', 'Polish Pro (Macintosh')

    names = [v[0] for v in variants]
    if names != ['mac-ansi', 'mac-iso']:
        print(f'  unexpected variant names: {names}')
        return False

    def tlde_lsgt(text):
        t = re.search(r'<TLDE>\s*\{\[ ([^\]]+) \]\}', text)
        l = re.search(r'<LSGT>\s*\{\[ ([^\]]+) \]\}', text)
        return (t.group(1) if t else None, l.group(1) if l else None)

    a_tl, a_ls = tlde_lsgt(variants[0][1])
    i_tl, i_ls = tlde_lsgt(variants[1][1])
    swapped = (a_tl == i_ls and a_ls == i_tl and a_tl is not None)
    # Everything else identical: the AC01 row must match across variants.
    a_ac01 = re.search(r'<AC01>[^\n]+', variants[0][1]).group(0)
    i_ac01 = re.search(r'<AC01>[^\n]+', variants[1][1]).group(0)
    others_same = a_ac01 == i_ac01
    print(f'  variants={names} tlde/lsgt swapped={swapped} other-keys-identical={others_same}')
    return swapped and others_same


def main() -> int:
    tests = [
        ('all keysyms valid', test_all_keysyms_valid),
        ('polish letters positioned', test_polish_letters_positioned),
        ('dead keys placed', test_dead_keys_placed),
        ('structure', test_structure),
        ('iso/ansi variants and swap', test_iso_ansi_variants_and_swap),
    ]
    print('symbols emitter tests:\n')
    passed = 0
    for label, fn in tests:
        try:
            ok = fn()
        except Exception as error:
            ok = False
            print(f'  {label}: EXCEPTION {error}')
        print(f'  -> {label}: {"PASS" if ok else "FAIL"}\n')
        passed += 1 if ok else 0
    print(f'score: {passed}/{len(tests)}')
    return 0 if passed == len(tests) else 1


def test_suite() -> None:
    assert main() == 0


if __name__ == '__main__':
    import sys
    sys.exit(main())


# End of file #
