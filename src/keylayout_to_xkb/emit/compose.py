"""
keylayout_to_xkb/emit/compose.py

Emit a self-contained XCompose file for a layout's dead-key compositions.

XKB symbols give the dead keys (dead_grave, dead_circumflex, ...) but NOT what
they compose to; that lives here. Each dead-key state becomes a set of XCompose
sequences:

    <dead_grave> <a>    : "ą"

i.e. the dead-key keysym, then the base character's keysym, producing the
composed result. The result is a quoted STRING, so multi-codepoint results
(combining-mark output with no precomposed form) are expressible here -- the
reason compositions go to XCompose rather than XKB single keysyms.

Self-contained on purpose: the emitted file does NOT `include "%L"` the host
locale's Compose, so the layout's behaviour does not depend on which Compose the
host happens to have. (A caller may add an include if they want host sequences
too, but the faithful macOS reproduction is exactly these sequences.)

Grounded on the installed XCompose syntax:
    <dead_X> <base> : "result"  keysym_name # COMMENT
The trailing keysym_name/comment are informational; the quoted result is what
the input method uses. We emit the quoted result plus a comment for readability.

Base keys are named by their XKB keysym via emit/classify.py. A base character
with no single keysym (rare for composition bases) is skipped with a warning
rather than emitted wrong, so a bad base never silently corrupts the file.
"""

from keylayout_to_xkb.common.models import Layout
from keylayout_to_xkb.common.debug import warn
from keylayout_to_xkb.common.policy import layout_is_unicode_accumulator
from keylayout_to_xkb.emit.classify import (
    char_to_keysym,
    dead_state_keysym,
    reserve_placeholders,
)


__version__ = '20260703b'


def _escape_result(text: str) -> str:
    """Escape a result string for an XCompose double-quoted literal.

    Backslash and double-quote must be escaped; everything else (including
    multi-byte UTF-8 and multi-codepoint sequences) passes through literally,
    matching how the system Compose file quotes results.
    """

    return text.replace('\\', '\\\\').replace('"', '\\"')


def _codepoint_comment(text: str) -> str:
    """A short 'U+XXXX' comment describing the result, for human readers."""

    points = ' '.join('U+{:04X}'.format(ord(ch)) for ch in text)
    return points


# Stacked dead-key chains bound: Tibetan Wylie reaches depth 4; the cap only
# guards against a malformed cyclic graph ever looping the walk.
_MAX_CHAIN_DEPTH = 6

# The all-of-Unicode accumulator list (Unicode Hex Input) lives in
# common/policy.py so the reference-doc emitter shares the same decision;
# see layout_is_unicode_accumulator.


def _state_trigger(state_name, dead_state, placeholders):
    """The keysym that TRIGGERS a ground dead state, plus its section header.

    Named dead_* keysym when one matches the state's accent; else the PUA
    placeholder the symbols emitter placed. (None, None) with a warning when
    neither exists (the symbols emitter would also have skipped the key).
    """

    dead_keysym = dead_state_keysym(dead_state.terminator, dead_state.compositions)
    if dead_keysym is not None:
        header = '# %s  (terminator %r)' % (dead_keysym, dead_state.terminator)
        return dead_keysym, header
    trigger = placeholders['deadkey'].get(state_name)
    if trigger is None:
        warn('compose',
             f'dead state {state_name!r} has neither a dead_* keysym nor '
             f'a placeholder; skipping (this should not happen)')
        return None, None
    header = ('# %s  PUA placeholder dead key for terminator %r '
              '(no standard dead_* keysym exists for it)'
              % (trigger, dead_state.terminator))
    return trigger, header


def _emit_state_lines(layout, placeholders, dead_state, path_syms, visited,
                      lines):
    """Emit every sequence reachable from 'dead_state' along 'path_syms'.

    Walks the chain graph depth-first: literal compositions first (identical
    to the historical single-level output when the graph is chain-free), then
    dead-key-triggered outputs, then recursion through char and dead
    transitions. 'visited' holds the state names already on THIS path (cycle
    guard); _MAX_CHAIN_DEPTH bounds pathological graphs. Returns the number
    of sequence lines emitted.
    """

    count = 0
    prefix = ' '.join('<%s>' % sym for sym in path_syms)
    state_name = dead_state.name

    for base_char, result in sorted(dead_state.compositions.items()):
        if not base_char:
            continue
        base_keysym = char_to_keysym(base_char)
        if base_keysym is None:
            warn('compose',
                 f'composition base {base_char!r} in state {state_name!r} '
                 f'has no single keysym; skipping that entry')
            continue
        lines.append('%s <%s>\t: "%s"\t# %s'
                     % (prefix, base_keysym, _escape_result(result),
                        _codepoint_comment(result)))
        count += 1

    def _trigger_sym_for(trigger_state_name):
        trigger_state = layout.dead_states.get(trigger_state_name)
        if trigger_state is None or not trigger_state.ground:
            warn('compose',
                 f'in-state trigger references state {trigger_state_name!r} '
                 f'with no ground dead key; skipping')
            return None
        sym, _hdr = _state_trigger(trigger_state_name, trigger_state,
                                   placeholders)
        return sym

    for trigger_state_name, result in sorted(
            dead_state.dead_compositions.items()):
        trigger_sym = _trigger_sym_for(trigger_state_name)
        if trigger_sym is None:
            continue
        lines.append('%s <%s>\t: "%s"\t# %s'
                     % (prefix, trigger_sym, _escape_result(result),
                        _codepoint_comment(result)))
        count += 1

    if len(path_syms) >= _MAX_CHAIN_DEPTH:
        return count

    for base_char, next_name in sorted(dead_state.char_transitions.items()):
        next_state = layout.dead_states.get(next_name)
        if next_state is None or next_name in visited:
            continue
        base_keysym = char_to_keysym(base_char)
        if base_keysym is None:
            continue
        count += _emit_state_lines(
            layout, placeholders, next_state, path_syms + (base_keysym,),
            visited | {next_name}, lines)

    for trigger_state_name, next_name in sorted(
            dead_state.dead_transitions.items()):
        next_state = layout.dead_states.get(next_name)
        if next_state is None or next_name in visited:
            continue
        trigger_sym = _trigger_sym_for(trigger_state_name)
        if trigger_sym is None:
            continue
        count += _emit_state_lines(
            layout, placeholders, next_state, path_syms + (trigger_sym,),
            visited | {next_name}, lines)

    return count


def emit_compose(layout: Layout, header_note: str = '') -> str:
    """Emit a self-contained XCompose file body for a layout's dead keys.

    Returns the file text. Iterates every dead state, names it via its dead_*
    keysym, and emits one sequence per composition. States whose accent maps to
    no dead_* keysym are skipped with a warning (the symbols emitter would also
    have skipped placing that dead key, so there is nothing to compose against).
    """

    lines = []
    lines.append('# XCompose for %s' % (layout.name or 'layout'))
    if header_note:
        lines.append('# %s' % header_note)
    lines.append('# Self-contained: does not include the host locale Compose.')
    lines.append('# Generated by keylayout_to_xkb. Not affiliated with Apple.')
    lines.append('')

    placeholders = reserve_placeholders(layout)

    total = 0
    for state_name, dead_state in sorted(layout.dead_states.items()):
        if not dead_state.ground:
            # Deep chain-target states are reachable only THROUGH paths from a
            # ground state; the walk below emits their content with the full
            # key sequence. They have no level-1 dead key of their own.
            continue
        trigger, header = _state_trigger(state_name, dead_state, placeholders)
        if trigger is None:
            continue

        lines.append(header)

        # The bare placeholder alone produces the terminator (the accent/letter
        # the dead key emits when followed by a non-composing key). Named dead
        # keys get this from the host Compose; placeholder dead keys need it
        # stated explicitly so the bare press is not lost.
        dead_keysym = dead_state_keysym(
            dead_state.terminator, dead_state.compositions)
        if dead_keysym is None and dead_state.terminator:
            escaped_term = _escape_result(dead_state.terminator)
            lines.append('<%s>\t: "%s"\t# %s'
                         % (trigger, escaped_term,
                            _codepoint_comment(dead_state.terminator)))
            total += 1

        if layout_is_unicode_accumulator(layout):
            lines.append('# chain expansion intentionally skipped for this '
                         'layout (all-of-Unicode accumulator)')
        else:
            total += _emit_state_lines(
                layout, placeholders, dead_state, (trigger,), {state_name},
                lines)

        lines.append('')

    # Multi-character DIRECT key outputs: a single keypress that emits a multi-
    # codepoint string with no precomposed form (e.g. a Tibetan stacked vowel,
    # a Manipuri conjunct, a Vietnamese space+combining-mark). The symbols layer
    # places a PUA placeholder keysym on the level; here we emit the one-token
    # XCompose rule that expands that placeholder to the real string. This is the
    # same mechanism the X.Org Khmer Compose uses (<U17ff> : "string"). The
    # placeholder map is the SAME one the symbols emitter consumed, so the two
    # stay in lockstep.
    multichar = placeholders['multichar']
    if multichar:
        lines.append('# Multi-character key outputs (placeholder expansions)')
        for text, placeholder_keysym in sorted(
            multichar.items(), key=lambda item: item[1]
        ):
            escaped = _escape_result(text)
            comment = _codepoint_comment(text)
            lines.append('<%s>\t: "%s"\t# %s'
                         % (placeholder_keysym, escaped, comment))
            total += 1
        lines.append('')

    lines.insert(4, '# %d composition sequences' % total)
    return '\n'.join(lines)


# End of file #
