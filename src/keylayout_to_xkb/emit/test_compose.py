"""
keylayout_to_xkb/emit/test_compose.py

Standing tests for the XCompose emitter, with a round-trip check against the
OS-confirmed Polish Pro layout: every composition the parser read must appear in
the emitted file with an identical result. Because the parser's Polish Pro
compositions are OS-oracle-clean, a perfect round-trip means the emitted Compose
is faithful to current macOS.

Project test style: print, return True/False, main() scores. Runs anywhere; the
fixture-dependent tests skip if the Polish Pro .uchr is absent.

Run directly:  python -m keylayout_to_xkb.emit.test_compose
Under pytest:  pytest keylayout_to_xkb/emit/test_compose.py
"""

import io
import os
import re
import contextlib

from keylayout_to_xkb.extract.uchr_parse import parse_uchr
from keylayout_to_xkb.emit.compose import emit_compose
from keylayout_to_xkb.emit.classify import char_to_keysym, dead_state_keysym


_FIXTURE = '/mnt/user-data/uploads/com_apple_keylayout_PolishPro.uchr'


def _polish_pro_layout():
    with open(_FIXTURE, 'rb') as handle:
        data = handle.read()
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        return parse_uchr(data, layout_name='PolishPro')


def _parse_emitted(text):
    entries = {}
    for match in re.finditer(r'<(\w+)> <(\w+)>\s*: "([^"]*)"', text):
        entries[(match.group(1), match.group(2))] = match.group(3)
    return entries


def test_round_trip() -> bool:
    """Every parser composition appears in the emitted file, result-identical."""

    if not os.path.isfile(_FIXTURE):
        print('  skipped (fixture missing)')
        return True
    layout = _polish_pro_layout()
    emitted = _parse_emitted(emit_compose(layout))

    expected = {}
    for dead_state in layout.dead_states.values():
        dead_keysym = dead_state_keysym(dead_state.terminator, dead_state.compositions)
        for base, result in dead_state.compositions.items():
            if not base:
                continue
            base_keysym = char_to_keysym(base)
            if base_keysym is None:
                continue
            expected[(dead_keysym, base_keysym)] = result

    missing = [k for k in expected if k not in emitted]
    mismatch = [k for k in expected if k in emitted and emitted[k] != expected[k]]
    ok = not missing and not mismatch
    print(f'  round-trip: {len(expected)} compositions, '
          f'{len(missing)} missing, {len(mismatch)} mismatched')
    return ok


def test_canonical_polish() -> bool:
    """The canonical Polish dead-key results are correct."""

    if not os.path.isfile(_FIXTURE):
        print('  skipped (fixture missing)')
        return True
    emitted = _parse_emitted(emit_compose(_polish_pro_layout()))
    checks = {
        ('dead_grave', 'a'): '\u0105',          # ą
        ('dead_diaeresis', 'o'): '\u00f6',      # ö
        ('dead_circumflex', 'o'): '\u00f4',     # ô
    }
    ok = True
    for key, want in checks.items():
        got = emitted.get(key)
        if got != want:
            ok = False
            print(f'  {key}: want {want!r}, got {got!r}')
    print(f'  canonical polish: {"all correct" if ok else "MISMATCH"}')
    return ok


def test_syntax_wellformed() -> bool:
    """Emitted sequence lines match the XCompose grammar shape."""

    if not os.path.isfile(_FIXTURE):
        print('  skipped (fixture missing)')
        return True
    text = emit_compose(_polish_pro_layout())
    seq_lines = [ln for ln in text.splitlines()
                 if ln.startswith('<') and ':' in ln]
    bad = [ln for ln in seq_lines
           if not re.match(r'^(<\w+>\s+)+:\s*"([^"]|\\.)*"', ln)]
    print(f'  {len(seq_lines)} sequence lines, {len(bad)} malformed')
    if bad:
        print(f'    e.g. {bad[0]!r}')
    return not bad


def test_multicodepoint_result_supported() -> bool:
    """A multi-codepoint composition result is emitted as a quoted string.

    Polish Pro has none, so this uses a synthetic state to prove the emitter
    does not choke on (and does not drop) a multi-codepoint result -- the whole
    reason compositions go to XCompose.
    """

    from keylayout_to_xkb.common.models import Layout, DeadState
    layout = Layout(name='synthetic')
    state = DeadState(name='s1', terminator='\u00b4')   # acute
    state.compositions = {'j': 'J\u0301'}               # J + combining acute (2 cp)
    layout.dead_states = {'s1': state}
    text = emit_compose(layout)
    ok = '<dead_acute> <j>' in text and 'J\u0301' in text
    print(f'  multi-codepoint result emitted: {"ok" if ok else "FAIL"}')
    return ok


def main() -> int:
    tests = [
        ('round trip', test_round_trip),
        ('canonical polish', test_canonical_polish),
        ('syntax well-formed', test_syntax_wellformed),
        ('multi-codepoint supported', test_multicodepoint_result_supported),
    ]
    print('compose emitter tests:\n')
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
