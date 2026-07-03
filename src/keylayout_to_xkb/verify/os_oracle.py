"""
keylayout_to_xkb/verify/os_oracle.py

Audit the binary 'uchr' parser against Apple's UCKeyTranslate -- the OS ground
truth -- cell by cell. This is the standing confidence mechanism: it runs the
SAME bytes through both the parser and the live OS resolver and reports every
disagreement, localising exactly which keys, planes, dead keys, or compositions
the parser does not yet reproduce faithfully.

Why this is the strongest check available:
  * It works on EVERY layout the parser can read, including the majority of
    Apple's system layouts that ship no .keylayout XML (so the XML cross-check
    cannot reach them).
  * It resolves dead-key compositions by actually running the OS state machine
    (dead key, then base key), so it is ground truth for the composition tables
    the parser reconstructs structurally -- including the chained/flattened
    cases (Greek polytonic) where the parser's flattening is an interpretation.

It runs only on macOS (it needs the live UCKeyTranslate). Off macOS, the OS
reference cannot be built and verify_layout raises OSOracleUnavailable; the
test suite skips the OS portion in that case, and the pure comparison logic
(compare_reference) remains unit-testable anywhere with a synthetic reference.

Two entry points:
  * verify_layout(data, name)         -> VerificationResult  (score + diffs)
  * format_report(result, verbose)    -> str                 (human report)
The CLI's --verify-os flag prints format_report(..., verbose=True); the test
suite asserts result.is_clean() (or an allowed-known-divergence set).
"""

from dataclasses import dataclass, field

from keylayout_to_xkb.common.debug import dbg, warn
from keylayout_to_xkb.common.models import (
    Layout,
    OutputKind,
    ModifierState,
    PLANE_MODIFIER_BYTE,
)
from keylayout_to_xkb.extract.uckeytranslate import (
    build_os_reference,
    OSOracleUnavailable,
)


__version__ = '20260703'


# The planes the audit covers, derived from the SHARED plane constant so the
# audit can never silently agree with a resolver that dropped planes: when the
# oracle resolver and this audit carried separate four-plane lists, a
# four-plane extraction audited at 100% while the caps quartet was missing.
# plane_name strings are the ModifierState values ('plain' .. 'caps_...').
_PLANE_NAME = {plane: plane.value for plane in PLANE_MODIFIER_BYTE}


@dataclass
class CellDiff:
    """One disagreeing (key, plane) cell between parser and OS."""

    virtual_key:    int
    plane:          str
    parser_says:    str
    os_says:        str
    kind:           str        # 'output', 'dead_mismatch', 'missing', 'extra'


@dataclass
class CompositionDiff:
    """One disagreeing composition within a dead-key state."""

    dead_key:       int
    plane:          str
    base_char:      str
    parser_says:    'str | None'
    os_says:        'str | None'


@dataclass
class VerificationResult:
    """Outcome of auditing one layout against the OS oracle."""

    name:               str
    cells_checked:      int = 0
    cells_agree:        int = 0
    cell_diffs:         'list[CellDiff]' = field(default_factory=list)
    comps_checked:      int = 0
    comps_agree:        int = 0
    comp_diffs:         'list[CompositionDiff]' = field(default_factory=list)

    def is_clean(self) -> bool:
        return not self.cell_diffs and not self.comp_diffs

    def cell_agreement(self) -> float:
        return self.cells_agree / self.cells_checked if self.cells_checked else 1.0

    def comp_agreement(self) -> float:
        return self.comps_agree / self.comps_checked if self.comps_checked else 1.0


def _parser_cell(layout: Layout, virtual_key: int, plane: ModifierState):
    """Return (output, is_dead, dead_state_name) for a parser cell, or None."""

    ko = layout.keys.get(virtual_key, {}).get(plane)
    if ko is None:
        return None
    if ko.kind is OutputKind.DEAD:
        return ('', True, ko.dead_state_name)
    if ko.kind is OutputKind.CHARS:
        return (ko.output, False, None)
    return ('', False, None)


def compare_reference(layout: Layout, reference: 'dict', name: str) -> VerificationResult:
    """Compare a parsed Layout against an OS reference dict. Pure; no OS calls.

    Separated from the OS-bound build so it is unit-testable anywhere: a test
    can hand-craft a reference dict and assert the diff logic behaves.
    """

    result = VerificationResult(name=name)
    cells = reference['cells']
    os_comps = reference['compositions']

    for (vk, plane_name), os_cell in cells.items():
        plane = ModifierState(plane_name)
        parser = _parser_cell(layout, vk, plane)
        os_output = os_cell['output']
        os_dead = os_cell['dead']

        # Skip cells the OS produces nothing for AND the parser omits: agreement
        # by mutual absence is not interesting and not counted.
        if not os_output and not os_dead and parser is None:
            continue

        result.cells_checked += 1

        if parser is None:
            # OS produces something here; parser has no cell.
            result.cell_diffs.append(CellDiff(
                vk, plane_name, parser_says='(absent)',
                os_says='(dead)' if os_dead else os_output, kind='missing',
            ))
            continue

        p_output, p_dead, _ = parser

        if os_dead or p_dead:
            if os_dead == p_dead:
                result.cells_agree += 1
            else:
                result.cell_diffs.append(CellDiff(
                    vk, plane_name,
                    parser_says='(dead)' if p_dead else p_output,
                    os_says='(dead)' if os_dead else os_output,
                    kind='dead_mismatch',
                ))
            continue

        if p_output == os_output:
            result.cells_agree += 1
        else:
            result.cell_diffs.append(CellDiff(
                vk, plane_name, parser_says=p_output or '(empty)',
                os_says=os_output or '(empty)', kind='output',
            ))

    # Compositions: for each OS dead-key cell, compare the parser's dead state.
    for (vk, plane_name), os_comp in os_comps.items():
        plane = ModifierState(plane_name)
        parser = _parser_cell(layout, vk, plane)
        if parser is None or not parser[1]:
            continue                                # parser does not see a dead key here
        state_name = parser[2]
        dead_state = layout.dead_states.get(state_name)
        parser_comp = dead_state.compositions if dead_state else {}
        terminator = dead_state.terminator if dead_state else ''

        for base_char, os_result in os_comp.items():
            parser_result = parser_comp.get(base_char)

            # The OS produces a result for the dead key followed by EVERY base
            # key: when the layout defines no special composition, it falls
            # back to terminator + base (e.g. acute then 'd' yields "'d") --
            # INCLUDING when the terminator is empty, where the fallback is
            # the bare base (the "passthrough" rows that once made Tibetan
            # Wylie look 85% broken). The parser faithfully stores only the
            # layout's real compositions, so a parser 'None' against the OS
            # fallback is expected, not a disagreement.
            if parser_result is None and os_result == terminator + base_char:
                continue
            # X-convention rows: the model stores terminator-only where the
            # OS emits terminator + base (canonically dead + space -> bare
            # accent vs accent + space). Deliberate convention difference in
            # the emitted XCompose, counted as agreement.
            if (parser_result == terminator
                    and os_result == terminator + base_char):
                result.comps_checked += 1
                result.comps_agree += 1
                continue
            result.comps_checked += 1
            if parser_result == os_result:
                result.comps_agree += 1
            else:
                result.comp_diffs.append(CompositionDiff(
                    vk, plane_name, base_char,
                    parser_says=parser_result, os_says=os_result,
                ))

    return result


def verify_layout(data: bytes, name: str) -> VerificationResult:
    """Build the OS reference for 'data' and compare the parser's output to it.

    Raises OSOracleUnavailable off macOS. Imports the parser lazily to avoid a
    circular import (uchr_parse imports uckeytranslate).
    """

    from keylayout_to_xkb.extract.uchr_parse import parse_uchr

    reference = build_os_reference(data)            # raises OSOracleUnavailable off mac
    layout = parse_uchr(data, layout_name=name)

    # Plane-coverage cross-check: the audit must be ABLE to disagree with the
    # resolver about which planes exist. A parser plane the OS reference never
    # probed (or vice versa) is a sync bug in the making, not a passing grade.
    model_planes = {
        plane.value for outputs in layout.keys.values() for plane in outputs
    }
    audited_planes = {plane_name for _vk, plane_name in reference['cells']}
    unaudited = sorted(model_planes - audited_planes)
    if unaudited:
        warn('oracle', '%s: parser plane(s) not covered by the OS reference: %s'
             % (name, ', '.join(unaudited)))
    unparsed = sorted(audited_planes - model_planes)
    if unparsed:
        warn('oracle', '%s: OS reference plane(s) the parser produced nothing '
             'for: %s' % (name, ', '.join(unparsed)))

    result = compare_reference(layout, reference, name)
    dbg(
        'verify',
        f'{name}: cells {result.cells_agree}/{result.cells_checked}, '
        f'comps {result.comps_agree}/{result.comps_checked}'
    )
    return result


def format_report(result: VerificationResult, verbose: bool = False) -> str:
    """Human-readable audit report. verbose lists every disagreeing cell."""

    lines = []
    lines.append(f'=== OS-oracle audit: {result.name} ===')
    lines.append(
        f'cells:        {result.cells_agree}/{result.cells_checked} agree '
        f'({result.cell_agreement() * 100:.1f}%), {len(result.cell_diffs)} differ'
    )
    lines.append(
        f'compositions: {result.comps_agree}/{result.comps_checked} agree '
        f'({result.comp_agreement() * 100:.1f}%), {len(result.comp_diffs)} differ'
    )

    # Category summary: group cell diffs by (plane, kind) so a systematic issue
    # (a whole plane wrong) stands out from one-off cells.
    if result.cell_diffs:
        by_group = {}
        for diff in result.cell_diffs:
            key = (diff.plane, diff.kind)
            by_group[key] = by_group.get(key, 0) + 1
        lines.append('  cell diff groups:')
        for (plane, kind), count in sorted(by_group.items()):
            lines.append(f'    {plane:13} {kind:14} x{count}')

    if verbose and result.cell_diffs:
        lines.append('  cell diffs (vk, plane: parser -> os):')
        for diff in result.cell_diffs:
            lines.append(
                f'    vk{diff.virtual_key:<3} {diff.plane:13} '
                f'{diff.parser_says!r} -> {diff.os_says!r} [{diff.kind}]'
            )

    if verbose and result.comp_diffs:
        lines.append('  composition diffs (dead vk, base: parser -> os):')
        for diff in result.comp_diffs:
            lines.append(
                f'    vk{diff.dead_key:<3} {diff.plane:13} base {diff.base_char!r}: '
                f'{diff.parser_says!r} -> {diff.os_says!r}'
            )

    if result.is_clean():
        lines.append('  CLEAN: parser matches the OS exactly.')

    return '\n'.join(lines)


# End of file #
