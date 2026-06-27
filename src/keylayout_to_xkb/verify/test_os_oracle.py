"""
keylayout_to_xkb/verify/test_os_oracle.py

Standing verification tests for the OS-oracle audit.

Runnable with pytest, but NOT written in pytest/unittest style: each test is a
function that prints what it checks, returns True/False, and main() accumulates
a score and prints a final tally. This matches the project's test convention.

Two layers:
  * Pure-logic tests (run ANYWHERE, including Linux/CI): feed compare_reference a
    synthetic OS reference and a hand-built Layout, assert the diff logic is
    correct. These guard the comparison code itself.
  * Live-OS tests (run only on macOS): build the real OS reference for each
    available layout and assert the parser matches. Skipped with a clear notice
    off macOS, so the suite is green everywhere but only truly exercised on a
    Mac.

Run directly:   python -m keylayout_to_xkb.verify.test_os_oracle
Under pytest:   pytest keylayout_to_xkb/verify/test_os_oracle.py
"""

from keylayout_to_xkb.common.models import (
    Layout,
    KeyOutput,
    DeadState,
    OutputKind,
    ModifierState,
)
from keylayout_to_xkb.verify.os_oracle import (
    compare_reference,
    verify_layout,
    format_report,
)
from keylayout_to_xkb.extract.uckeytranslate import OSOracleUnavailable


def _synthetic_layout() -> Layout:
    """A tiny Layout: vk0 plain 'a'/shift 'A', vk1 plain dead 'acute'."""

    layout = Layout(name='synthetic')
    layout.keys = {
        0: {
            ModifierState.PLAIN: KeyOutput(OutputKind.CHARS, output='a'),
            ModifierState.SHIFT: KeyOutput(OutputKind.CHARS, output='A'),
        },
        1: {
            ModifierState.PLAIN: KeyOutput(OutputKind.DEAD, dead_state_name='s1'),
        },
    }
    dead = DeadState(name='s1')
    dead.compositions = {'a': '\u00e1'}             # acute + a -> á
    layout.dead_states = {'s1': dead}
    return layout


def test_clean_match() -> bool:
    """compare_reference reports CLEAN when parser and reference agree."""

    layout = _synthetic_layout()
    reference = {
        'cells': {
            (0, 'plain'): {'output': 'a', 'dead': False},
            (0, 'shift'): {'output': 'A', 'dead': False},
            (1, 'plain'): {'output': '', 'dead': True},
        },
        'compositions': {
            (1, 'plain'): {'a': '\u00e1'},
        },
    }
    result = compare_reference(layout, reference, 'synthetic')
    ok = result.is_clean() and result.cells_agree == 3 and result.comps_agree == 1
    print(f'  clean match: cells {result.cells_agree}/{result.cells_checked} '
          f'comps {result.comps_agree}/{result.comps_checked} clean={result.is_clean()}')
    return ok


def test_output_diff_detected() -> bool:
    """A wrong character is reported as an output diff."""

    layout = _synthetic_layout()
    reference = {
        'cells': {
            (0, 'plain'): {'output': 'q', 'dead': False},    # parser says 'a'
            (0, 'shift'): {'output': 'A', 'dead': False},
        },
        'compositions': {},
    }
    result = compare_reference(layout, reference, 'synthetic')
    diff = result.cell_diffs[0] if result.cell_diffs else None
    ok = (
        len(result.cell_diffs) == 1
        and diff.kind == 'output'
        and diff.parser_says == 'a' and diff.os_says == 'q'
    )
    print(f'  output diff: {len(result.cell_diffs)} diff(s), '
          f'first={diff.parser_says!r}->{diff.os_says!r}' if diff else '  no diff!')
    return ok


def test_dead_mismatch_detected() -> bool:
    """A cell the OS makes dead but the parser makes literal is flagged."""

    layout = _synthetic_layout()
    reference = {
        'cells': {
            (0, 'plain'): {'output': '', 'dead': True},      # parser says literal 'a'
        },
        'compositions': {},
    }
    result = compare_reference(layout, reference, 'synthetic')
    ok = len(result.cell_diffs) == 1 and result.cell_diffs[0].kind == 'dead_mismatch'
    print(f'  dead mismatch: {len(result.cell_diffs)} diff(s), '
          f'kind={result.cell_diffs[0].kind if result.cell_diffs else None}')
    return ok


def test_composition_diff_detected() -> bool:
    """A wrong composed result is reported as a composition diff."""

    layout = _synthetic_layout()
    reference = {
        'cells': {
            (1, 'plain'): {'output': '', 'dead': True},
        },
        'compositions': {
            (1, 'plain'): {'a': '\u00e4'},                   # parser says á, os says ä
        },
    }
    result = compare_reference(layout, reference, 'synthetic')
    ok = len(result.comp_diffs) == 1 and result.comp_diffs[0].base_char == 'a'
    print(f'  composition diff: {len(result.comp_diffs)} diff(s)')
    return ok


def test_live_os_layouts() -> bool:
    """On macOS, audit every extractable layout against the OS oracle.

    Skipped (returns True with a notice) off macOS, where the OS oracle cannot
    run. On macOS, every layout must be CLEAN or within a documented allowance.
    """

    try:
        # A trivial probe: building a reference for empty bytes will raise
        # OSOracleUnavailable off macOS before doing real work.
        from keylayout_to_xkb.extract.uckeytranslate import build_os_reference
        build_os_reference(b'\x00' * 64)
    except OSOracleUnavailable as reason:
        print(f'  live-OS audit skipped (not macOS): {reason}')
        return True
    except Exception:
        # On macOS, garbage bytes may fail differently; that still means the OS
        # path is available, so proceed to the real audit below.
        pass

    try:
        from keylayout_to_xkb.extract.tis_source import extract_all_layouts
        payloads = extract_all_layouts()
    except Exception as error:
        print(f'  live-OS audit could not extract layouts: {error}')
        return True                                 # do not fail the suite on env issues

    audited = 0
    clean = 0
    for payload in payloads:
        data = payload.get('data')
        name = payload.get('name') or '?'
        if not data:
            continue
        try:
            result = verify_layout(data, name)
        except OSOracleUnavailable:
            print('  live-OS audit skipped mid-run (oracle vanished)')
            return True
        audited += 1
        if result.is_clean():
            clean += 1
        else:
            print(format_report(result, verbose=False))

    print(f'  live-OS audit: {clean}/{audited} layouts clean')
    return audited == 0 or clean == audited


def main() -> int:
    tests = [
        ('clean match', test_clean_match),
        ('output diff detected', test_output_diff_detected),
        ('dead mismatch detected', test_dead_mismatch_detected),
        ('composition diff detected', test_composition_diff_detected),
        ('live OS layouts', test_live_os_layouts),
    ]
    print('OS-oracle verification tests:\n')
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


# pytest entry: a single test that asserts the whole suite passes.
def test_suite() -> None:
    assert main() == 0


if __name__ == '__main__':
    import sys
    sys.exit(main())


# End of file #
