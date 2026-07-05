"""
keylayout_to_xkb/common/policy.py

Cross-cutting emit policy shared by more than one emitter, kept in one place so
the several call sites cannot drift on the same decision.

Currently this is the "Unicode accumulator" list: layouts whose dead-key graph
is an intentional all-of-Unicode hex-digit accumulator rather than a set of
human accents. macOS 'Unicode Hex Input' encodes typing four hex digits as a
four-deep chain (65,536 leaf compositions). Both the XCompose emitter (which
would otherwise expand megabytes of sequences verifying nothing) and the
reference-doc emitter (which would otherwise print thousands of empty dead-key
sections) refuse to enumerate these, and they must agree on which layouts
qualify. Blocked BY NAME per project decision: no shape heuristics, so a layout
counts only when a human has judged it an accumulator.
"""

from keylayout_to_xkb.common.models import Layout


__version__ = '20260705'


# Lowercased, stripped layout names that are all-of-Unicode hex accumulators.
_UNICODE_ACCUMULATOR_LAYOUTS = frozenset((
    'unicode hex input',
))


def layout_is_unicode_accumulator(layout: Layout) -> bool:
    """True if this layout's dead keys are an all-of-Unicode hex accumulator.

    Matches BY NAME (lowercased, stripped) against a fixed list, deliberately
    not by any shape heuristic, so a layout only counts when a human has judged
    it an accumulator. The '(name or '')' guard makes the value a str before
    the string methods run, so an absent name is simply 'not an accumulator'
    rather than an error. Both the XCompose and reference-doc emitters consult
    this, so they never diverge on which layouts to skip.
    """

    return (layout.name or '').strip().lower() in _UNICODE_ACCUMULATOR_LAYOUTS


# End of file #
