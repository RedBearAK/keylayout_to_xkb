"""
keylayout_to_xkb/emit/test_classify.py

Standing tests for the emitter's classification stage.

Runnable with pytest, but written in the project style: each test prints what it
checks, returns True/False, and main() accumulates a score. These run anywhere
keysymdef.h is installed (Linux/CI); the keysym-name assertions degrade to the
UXXXX fallback if it is absent, which the keysymdef-presence test reports.

Run directly:  python -m keylayout_to_xkb.emit.test_classify
Under pytest:  pytest keylayout_to_xkb/emit/test_classify.py
"""

from keylayout_to_xkb.emit.classify import (
    char_to_keysym,
    dead_state_keysym,
    unicode_keysym,
    _load_keysymdef,
)


def test_named_keysyms_for_polish() -> bool:
    """Polish letters map to their named keysyms, not UXXXX fallbacks."""

    expected = {
        '\u0105': 'aogonek', '\u0107': 'cacute', '\u0119': 'eogonek',
        '\u0142': 'lstroke', '\u0144': 'nacute', '\u00f3': 'oacute',
        '\u015b': 'sacute',  '\u017a': 'zacute', '\u017c': 'zabovedot',
    }
    ok = True
    for ch, want in expected.items():
        got = char_to_keysym(ch)
        if got != want:
            ok = False
            print(f'  {ch!r} expected {want!r}, got {got!r}')
    print(f'  polish named keysyms: {"all correct" if ok else "MISMATCH"}')
    return ok


def test_ascii_keysyms() -> bool:
    """ASCII letters/digits/symbols map to expected keysym names."""

    cases = {'a': 'a', 'Z': 'Z', '1': '1', '!': 'exclam', '@': 'at',
             '#': 'numbersign', ' ': 'space'}
    ok = all(char_to_keysym(c) == k for c, k in cases.items())
    print(f'  ascii keysyms: {"ok" if ok else "MISMATCH"} '
          f'(! -> {char_to_keysym("!")!r})')
    return ok


def test_unknown_codepoint_falls_back_to_unicode() -> bool:
    """A codepoint with no named keysym uses the UXXXX form."""

    # U+1E9E LATIN CAPITAL SHARP S has no classic short name in many builds;
    # regardless, the function must return SOMETHING valid (named or UXXXX).
    got = char_to_keysym('\U0001F600')          # emoji: definitely no named keysym
    ok = got == 'U1F600'
    print(f'  astral fallback: {got!r} (want U1F600) {"ok" if ok else "FAIL"}')
    return ok


def test_multichar_and_empty_return_none() -> bool:
    """Multi-character and empty outputs have no keysym (go to XCompose)."""

    ok = (char_to_keysym('') is None
          and char_to_keysym('ab') is None
          and char_to_keysym('J\u0301') is None)
    print(f'  multichar/empty -> None: {"ok" if ok else "FAIL"}')
    return ok


def test_unicode_keysym_format() -> bool:
    """unicode_keysym pads to 4 hex digits, uppercase, with U prefix."""

    ok = (unicode_keysym(0x0105) == 'U0105'
          and unicode_keysym(0x41) == 'U0041'
          and unicode_keysym(0x1F600) == 'U1F600')
    print(f'  unicode_keysym format: {"ok" if ok else "FAIL"} '
          f'(0x105 -> {unicode_keysym(0x0105)})')
    return ok


def test_dead_keysyms() -> bool:
    """Bare accents map to the correct dead_* keysyms."""

    cases = {
        '\u00a8': 'dead_diaeresis',
        '\u005e': 'dead_circumflex',
        '\u0060': 'dead_grave',
        '\u00b4': 'dead_acute',
        '\u02db': 'dead_ogonek',
        '\u02c7': 'dead_caron',
    }
    ok = True
    for accent, want in cases.items():
        got = dead_state_keysym(accent)
        if got != want:
            ok = False
            print(f'  accent {accent!r} expected {want!r}, got {got!r}')
    print(f'  dead keysyms: {"all correct" if ok else "MISMATCH"}')
    return ok


def test_dead_keysym_falls_back_to_space_composition() -> bool:
    """When terminator is empty, the space-composition supplies the accent."""

    got = dead_state_keysym('', {' ': '\u00b4', 'a': '\u00e1'})
    ok = got == 'dead_acute'
    print(f'  dead via space-composition: {got!r} {"ok" if ok else "FAIL"}')
    return ok


def test_keysymdef_present() -> bool:
    """Report whether keysymdef.h was found; not a hard failure if absent."""

    n = len(_load_keysymdef())
    print(f'  keysymdef.h: {n} codepoint->name entries '
          f'{"(loaded)" if n else "(NOT FOUND -- UXXXX fallback only)"}')
    return True                                 # informational, never fails


def main() -> int:
    tests = [
        ('named keysyms for polish', test_named_keysyms_for_polish),
        ('ascii keysyms', test_ascii_keysyms),
        ('unknown -> unicode fallback', test_unknown_codepoint_falls_back_to_unicode),
        ('multichar/empty -> none', test_multichar_and_empty_return_none),
        ('unicode_keysym format', test_unicode_keysym_format),
        ('dead keysyms', test_dead_keysyms),
        ('dead via space composition', test_dead_keysym_falls_back_to_space_composition),
        ('keysymdef present', test_keysymdef_present),
    ]
    print('classify tests:\n')
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
