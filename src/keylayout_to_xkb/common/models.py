"""
keylayout_to_xkb/common/models.py

Normalized, format-agnostic model of a keyboard layout.

This is the single contract that every stage reads or writes. The extract
stage (binary 'uchr' parser, or the '.keylayout' XML parser) produces these
objects. The classify stage annotates them. The emit stage consumes them. No
stage should ever reach back into a source format once a layout has been turned
into one of these objects.

The model deliberately stores *what a key produces*, not *how the source
encoded it*. Dead keys are represented as named states with their own
composition maps, so the graph can be walked without any knowledge of 'uchr'
state-record internals or XML <action>/<when> structure.

--------------------------------------------------------------------------
NOTE FOR FUTURE MAINTAINERS (human or LLM)
--------------------------------------------------------------------------
This file is the load-bearing interface between parsers and the emitter. A
plausible-looking change here can silently corrupt output for whole classes of
layouts that are not in your immediate test set. Two failure points have
already been hit during development and are guarded below; read those comments
before editing:

  1. KeyOutput.output is a STRING, never a single char. It can be multiple
     codepoints (ligatures, base+combining-mark results) and can be high or
     presentation-form codepoints with no named keysym. An emitter that assumes
     "one keysym per cell" WILL drop characters. See KeyOutput.

  2. Dead-key chaining is FLATTENED into DeadState.compositions on purpose.
     There is intentionally no separate 'chained' graph. See DeadState.

Run Layout.validate() after building a layout; it asserts the invariants the
emitter relies on, so a future parser change cannot break them silently.
"""

import enum

from dataclasses import dataclass, field


__version__ = '20260703'


class ModifierState(enum.Enum):
    """The modifier planes we port.

    macOS 'uchr' (and a .keylayout modifierMap) can select many more tables than
    this, but only EIGHT carry user-typeable character output: the four base
    planes (no caps) and the four caps planes (caps held/locked). Both quartets
    are faithful mirrors of what UCKeyTranslate produces, and both decode with
    the same machinery (verified against the OS oracle at ~99.96% on the caps
    quartet across all layouts).

    The command and control tables are deliberately NOT planes here: probing
    their contents showed the control tables hold C0 control characters
    (Ctrl+A = U+0001, ...) and the command tables hold the Latin shortcut layer
    (so Cmd+key shortcuts work regardless of script). Both are desktop/modifier
    behavior Linux handles natively, not document-typeable content, so capturing
    them as levels would break Control/Super handling (see the resolver and the
    caps notes in uchr_parse.py / KNOWN_LIMITATIONS.md).

    The caps quartet is captured WITHOUT classifying "caps behavior": a full-set
    scan found 16 distinct caps table-reuse fingerprints, not two clean modes, so
    the decode simply follows the groove -- reading whatever table each caps
    plane points at and emitting it at its level, uniform across all layouts.

    UNKNOWN is reserved, NOT currently produced. The content-driven resolver
    assigns every plane it emits to one of the eight real planes or omits the
    table; it never yields UNKNOWN. It is kept as an explicit sentinel so that a
    future parser MAY mark "a table that should be a plane but could not be
    classified" rather than dropping it silently. If you start producing
    UNKNOWN, update Layout.validate() and the emitter to handle it, do not let
    it reach output.
    """

    PLAIN               = 'plain'
    SHIFT               = 'shift'
    OPTION              = 'option'
    SHIFT_OPTION        = 'shift_option'
    CAPS                = 'caps'
    CAPS_SHIFT          = 'caps_shift'
    CAPS_OPTION         = 'caps_option'
    CAPS_SHIFT_OPTION   = 'caps_shift_option'
    UNKNOWN             = 'unknown'


# Carbon modifier bytes for UCKeyTranslate, one per typeable plane: the
# modifierKeyState parameter is (EventRecord modifiers >> 8), so
# shiftKey=0x0200 -> 0x02, alphaLock=0x0400 -> 0x04, optionKey=0x0800 -> 0x08,
# composed bitwise for the combination planes. The same bytes DIRECTLY index
# the on-disk keyModifiersToTableNum array (entries at struct offset +8; see
# uchr_parse._parse_modifier_table_map for the alignment history).
#
# This dict is THE single source of truth for plane -> modifier byte. The OS
# plane resolver (extract/uckeytranslate.resolve_plane_tables_via_os), the OS
# reference builder (build_os_reference), the verify audit (verify/os_oracle),
# the on-disk plane index in uchr_parse, and the caps-layer probe all derive
# from it. These lists went out of sync once -- the macOS resolver stayed at
# four planes after the content resolver grew to eight, silently dropping the
# caps layers from every on-Mac generation while every off-Mac test stayed
# green -- and a shared constant is what makes that drift structurally
# impossible. Do not redeclare these bytes anywhere else.
PLANE_MODIFIER_BYTE = {
    ModifierState.PLAIN:             0x00,
    ModifierState.SHIFT:             0x02,
    ModifierState.OPTION:            0x08,
    ModifierState.SHIFT_OPTION:      0x0A,
    ModifierState.CAPS:              0x04,
    ModifierState.CAPS_SHIFT:        0x06,
    ModifierState.CAPS_OPTION:       0x0C,
    ModifierState.CAPS_SHIFT_OPTION: 0x0E,
}


class OutputKind(enum.Enum):
    """What a single key-at-modifier-plane resolves to.

    NONE is reserved, NOT currently stored. Parsers OMIT empty cells rather than
    storing an explicit NONE, so absence of a (key, plane) entry means "nothing
    there". NONE exists for the case where an emitter or parser needs to
    distinguish "explicitly blanked" from "absent" (e.g. a variant that masks a
    base key). If you start storing NONE, ensure char_count() and the emitter
    treat it as producing no output.
    """

    CHARS           = 'chars'           # emits one or more literal characters
    DEAD            = 'dead'            # enters a dead-key state
    NONE            = 'none'            # produces nothing (explicitly empty)


class SourceKind(enum.Enum):
    """Which extractor produced a layout. Recorded in provenance."""

    UCHR_BINARY     = 'uchr_binary'     # parsed from a binary 'uchr' resource
    KEYLAYOUT_XML   = 'keylayout_xml'   # parsed from a .keylayout XML file
    UNKNOWN         = 'unknown'


@dataclass
class KeyOutput:
    """The result of one (virtual_key, modifier_state) cell.

    output (CHARS): the literal STRING this cell emits. CRITICAL CONTRACT:
      - It may be MORE THAN ONE codepoint. macOS emits multi-character output
        for ligatures and for base+combining-mark results that have no
        precomposed form (e.g. 'J' + U+0301 COMBINING ACUTE). Real examples
        seen in Apple layouts: ligatures fi/fl (single presentation-form
        codepoints) at shift+option, and multi-codepoint dead-key results.
      - It may contain HIGH or PRESENTATION-FORM codepoints with no named X11
        keysym (U+FB01 fi, U+F8FF Apple logo, native-script letters).
      The emitter MUST therefore:
        * map single named keysyms where they exist,
        * fall back to the XKB Unicode keysym form UXXXX for anything else,
        * route MULTI-codepoint output to an XCompose entry (a single XKB
          keysym/level cannot emit a multi-codepoint string).
      Do NOT assume len(output) == 1, and do NOT assume a codepoint has a named
      keysym. Both assumptions silently drop characters.

    dead_state_name (DEAD): names the state entered; 'output' is unused. The
    compositions reachable from that state live in
    Layout.dead_states[dead_state_name].
    """

    kind:               OutputKind
    output:             str = ''
    dead_state_name:    'str | None' = None

    def is_multi_char(self) -> bool:
        """True if this CHARS cell emits more than one codepoint.

        Cells for which this is True cannot be represented by a single XKB
        keysym and must be emitted via XCompose. Provided so the emitter can
        partition cells without re-deriving the rule.
        """

        return self.kind is OutputKind.CHARS and len(self.output) > 1


@dataclass
class DeadState:
    """A dead-key state and everything reachable from it.

    'name' is the source state identifier, rendered as a string. For 'uchr' it
    is the numeric state number (NOT the record index; see uchr_parse.py, which
    keys dead states by the record's 'next' state number so that compositions
    and terminators line up). For XML it is the action/state name (e.g.
    'acute'). Names are opaque keys; do not parse meaning out of them.

    'terminator' is the output if the dead key is followed by a key that has no
    composition in this state (e.g. acute then space yields the bare accent).
    May be empty if the source declares none.

    'compositions' maps the next literal base character to the composed result
    string. Example for an acute state: {'e': 'e-acute', 'a': 'a-acute'}. The
    result MAY be multi-codepoint (same contract as KeyOutput.output).

    CHAINED DEAD KEYS ARE FLATTENED HERE ON PURPOSE. Some layouts (Greek
    Polytonic) build a result through several dead keys in sequence
    (breathing + accent). Both the 'uchr' parser and the XML parser resolve
    those chains down to direct base->result entries in 'compositions'. There is
    deliberately NO separate chaining graph on this model: it was tried, never
    populated, and removed, because flattening produces correct output and a
    second representation only invites the two to disagree. If you think you
    need chaining state here, first confirm the parser is not already flattening
    it (it is) before adding a field the emitter would also have to learn.

    'xkb_keysym' is filled by the classify stage: the XKB dead_* keysym whose
    diacritic matches this state's terminator/compositions. None until then.
    """

    name:               str
    terminator:         str = ''
    compositions:       'dict[str, str]' = field(default_factory=dict)
    xkb_keysym:         'str | None' = None     # filled in by classify stage


@dataclass
class Provenance:
    """Where a layout came from and how it was converted.

    Populated best-effort by the parser and enriched by the extractor/CLI with
    facts only the running tool knows (the macOS version extracted on, the
    conversion timestamp, this tool's version). The emitter writes these as a
    comment header in every generated XKB/Compose file so each shipped artifact
    is self-documenting and traceable back to its source.

    Fields are best-effort; any may be None/'' when the source does not carry
    them. The binary 'uchr' format carries almost no provenance (no name, id,
    date, or per-layout version), so for SourceKind.UCHR_BINARY most of the
    source_* fields will be empty and the generation_* fields carry the weight.
    The XML format is rich: Apple layout id, name, maxout, and an edit-history
    comment (tool + version + date) are all available.
    """

    source_kind:            SourceKind = SourceKind.UNKNOWN
    source_name:            str = ''            # layout name as the source gave it
    source_id:              'str | None' = None # TIS id, or XML <keyboard id=...>
    apple_layout_id:        'str | None' = None # XML negative resource id, if any
    source_max_output:      'int | None' = None # XML maxout, if any
    source_tool:            'str | None' = None # e.g. 'Ukelele 3.0.1.47' from comment
    source_edited:          'str | None' = None # source edit date, if any

    extracted_macos:        'str | None' = None # macOS product version/build extracted on
    extraction_date:        'str | None' = None # when extraction ran

    tool_version:           'str | None' = None # keylayout_to_xkb __version__
    conversion_date:        'str | None' = None # when this conversion ran

    # Static attribution lines the emitter may include verbatim. Kept here so
    # the text lives in one place and every emitted file is consistent.
    attribution:            str = (
        'Faithful conversion of an Apple macOS keyboard layout. '
        'Not affiliated with or endorsed by Apple. '
        'Generated by keylayout_to_xkb.'
    )


@dataclass
class Variant:
    """One keyboard-type variant of a layout (e.g. ANSI/ISO vs JIS).

    macOS 'uchr' encodes per-keyboard-type char tables (gestalt type ranges);
    a .keylayout encodes them as separate named keyMapSets. They differ only in
    a few keys (JIS adds yen and underscore), but each is emitted as a full,
    self-contained XKB file rather than an include-overlay, so an installed
    layout never depends on a base layout being present (see project design
    notes). The shared dead_states live on the Layout, not here, because
    compositions do not vary by keyboard type.

    'tag' is a short stable identifier for the variant used in the emitted file
    name and XKB variant name (e.g. '' for the primary/ANSI-ISO, 'jis' for
    JIS). 'keys' has the same shape and contract as Layout.keys.

    'plane_tables' records which char-table index each ModifierState plane
    resolved to (the layout's own modifier-map routing). It is authoritative for
    detecting when two planes are documentation-identical: macOS routes, say,
    Caps+Shift to the very same table as Shift when caps adds nothing there, so
    plane_tables[CAPS_SHIFT] == plane_tables[SHIFT]. Consumers (the doc generator
    and potentially the emitter) use this to collapse redundant planes without
    comparing outputs cell-by-cell. It is the resolved index, NOT the raw
    modifier byte. May be empty if a parser cannot determine it (then consumers
    fall back to treating every populated plane as distinct).
    """

    tag:                str
    keys:               'dict[int, dict[ModifierState, KeyOutput]]' = field(default_factory=dict)
    keyboard_type_range: 'tuple[int, int] | None' = None  # gestalt first..last, if known
    plane_tables:       'dict[ModifierState, int]' = field(default_factory=dict)


@dataclass
class Layout:
    """A complete normalized layout.

    'name' is whatever the source could tell us (TIS localized name, XML name,
    or a fallback). 'source_id' is a stable identifier when available; the full
    source story lives in 'provenance'.

    'keys' maps virtual_keycode -> {ModifierState: KeyOutput} for the PRIMARY
    variant. The keycode here is still the *macOS virtual keycode*; translation
    to XKB/evdev key names happens in the emit stage via the keycode map, so the
    model stays faithful to the source and the translation is auditable in one
    place. Additional keyboard-type variants live in 'variants'.

    'dead_states' holds every DeadState by name. KeyOutput.dead_state_name
    indexes into this dict. Dead states are shared across variants.

    'provenance' records source and conversion metadata for the emitted file
    header.
    """

    name:               str
    source_id:          'str | None' = None
    keys:               'dict[int, dict[ModifierState, KeyOutput]]' = field(default_factory=dict)
    dead_states:        'dict[str, DeadState]' = field(default_factory=dict)
    variants:           'list[Variant]' = field(default_factory=list)
    provenance:         Provenance = field(default_factory=Provenance)

    def char_count(self) -> int:
        """Count literal-character positions reachable, for sanity checks.

        Counts CHARS cells across all key/modifier cells (primary variant) plus
        all composition results in every dead state. Used by the validation
        oracle against the optspecialchars appendix counts. Intentionally
        approximate; counts POSITIONS not unique codepoints, and a multi-char
        cell still counts once (it is one position), matching how the appendix
        counts.
        """

        total = 0

        for modmap in self.keys.values():
            for key_output in modmap.values():
                if key_output.kind is OutputKind.CHARS and key_output.output:
                    total += 1

        for dead_state in self.dead_states.values():
            total += len(dead_state.compositions)

        return total

    def dead_key_count(self) -> int:
        """Number of distinct dead-key states, for the validation oracle."""

        return len(self.dead_states)

    def validate(self) -> 'list[str]':
        """Check the invariants the emitter relies on; return a list of problems.

        Empty list means the layout is internally consistent. This is a
        VERIFIER, not a fixer: it never mutates. Call it after building a layout
        (in tests, and optionally in the CLI with a strict flag) so that a
        future parser change cannot silently violate a contract the emitter
        depends on. Each returned string is a human-readable problem.

        Checks:
          - every DEAD cell names a state that exists in dead_states
          - no CHARS cell has empty output (would be a NONE/omission, not CHARS)
          - no DEAD cell carries stray literal output
          - dead-state names referenced anywhere all resolve
          - every variant's DEAD cells also resolve
          - plane keys are real ModifierState members (guards enum drift)
        """

        problems = []

        def check_cell(where, ko):
            if ko.kind is OutputKind.DEAD:
                if not ko.dead_state_name:
                    problems.append(f'{where}: DEAD cell with no state name')
                elif ko.dead_state_name not in self.dead_states:
                    problems.append(
                        f'{where}: DEAD cell names missing state '
                        f'{ko.dead_state_name!r}'
                    )
                if ko.output:
                    problems.append(
                        f'{where}: DEAD cell carries stray output {ko.output!r}'
                    )
            elif ko.kind is OutputKind.CHARS:
                if not ko.output:
                    problems.append(
                        f'{where}: CHARS cell with empty output '
                        f'(should be omitted, not stored)'
                    )

        for vk, modmap in self.keys.items():
            for ms, ko in modmap.items():
                if not isinstance(ms, ModifierState):
                    problems.append(f'key {vk}: non-ModifierState plane {ms!r}')
                check_cell(f'key {vk} {getattr(ms, "value", ms)}', ko)

        for variant in self.variants:
            for vk, modmap in variant.keys.items():
                for ms, ko in modmap.items():
                    check_cell(f'variant {variant.tag!r} key {vk} '
                               f'{getattr(ms, "value", ms)}', ko)

        return problems


# End of file #
