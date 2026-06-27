"""
keylayout_to_xkb/extract/keylayout_xml.py

Parse a macOS '.keylayout' XML file into the normalized common.models.Layout.

Second producer of the Layout model, alongside the binary 'uchr' parser. Any
conformant .keylayout file -- Apple's, a Ukelele export, or a hand-authored one
-- can be converted without binary extraction. The .keylayout schema is
documented by Apple (TN2056); this parser targets the categories that schema
defines and that the model represents, and LOUDLY FLAGS anything outside them
rather than guessing.

Document structure (the parts we read):

  <keyboard group=.. id=.. maxout=.. name=..>
    <layouts>                              keyboard-type ranges -> keyMapSet
      <layout first=.. last=.. mapSet=.. modifiers=../>
    <modifierMap id=.. defaultIndex=..>     modifier-combo -> keyMap index
      <keyMapSelect mapIndex=..>
        <modifier keys="anyShift anyOption"/>
    <keyMapSet id=..>                       the actual key tables
      <keyMap index=..>
        <key code=N output=".."/>             literal output, OR
        <key code=N action="ID"/>             triggers an action (dead-key path)
    <actions>                              dead-key state graph
      <action id="ID">
        <when state="none" next="S"/>         pressing this enters state S
        <when state="S" output="X"/>          in state S, this key outputs X
        <when state="S" next="S2"/>           chained: in state S, go to S2
    <terminators>                          bare dead-key output
      <when state="S" output="X"/>

PARSING STRATEGY (kept parallel to the binary parser on purpose):

  * Planes are resolved by inverting the modifierMap rules: each keyMapSelect's
    <modifier> rule is classified by its plane signature (keys="" = plain,
    anyShift = shift, anyOption = option, anyShift anyOption = shift+option),
    ignoring any rule that requires command or control (shortcut/control
    layers, not character planes -- exactly as in uchr_parse.py).
  * Keyboard-type variants come from the <layouts> block: distinct mapSets are
    distinct Variants, tagged by their keyboard-type range. The primary variant
    (lowest keyboard-type range) populates Layout.keys; others go in
    Layout.variants. This is the same ANSI/ISO-vs-JIS split the binary encodes.
  * Dead-key chains are FLATTENED into DeadState.compositions, matching the
    model contract (see models.DeadState).

Standard library only (xml.etree). Does not validate against the DTD; reads the
elements it needs and flags unexpected shapes.
"""

import re

import xml.etree.ElementTree as ET

from keylayout_to_xkb.common.debug import dbg, warn
from keylayout_to_xkb.common.models import (
    Layout,
    Variant,
    KeyOutput,
    DeadState,
    OutputKind,
    Provenance,
    SourceKind,
    ModifierState,
)


__version__ = '20260623'


class KeylayoutParseError(Exception):
    """Raised when a .keylayout file cannot be parsed into a Layout."""


# C0 control characters that are LEGAL as numeric references in XML 1.0.
# Everything else in 0x00-0x1F (and 0x7F-0x9F) is illegal in XML 1.0, which is
# what Python's ElementTree/expat enforces, even though .keylayout files declare
# XML 1.1 and freely reference control chars as control-plane key outputs
# (e.g. Ctrl+H -> &#x0008;). Those outputs are control-plane characters we never
# emit anyway, so neutralising the references is lossless for our purposes.
_XML10_LEGAL_CONTROL = {0x09, 0x0A, 0x0D}

_CHAR_REF_RGX = re.compile(r'&#x([0-9A-Fa-f]+);|&#([0-9]+);')


def _sanitize_control_refs(source_text: str) -> str:
    """Replace illegal-in-XML-1.0 control-char references with U+FFFD.

    .keylayout files declare XML 1.1 and use numeric references to C0 control
    characters (control-plane key outputs). ElementTree only parses XML 1.0 and
    rejects those references outright. We rewrite each illegal control reference
    to the replacement character U+FFFD before parsing; such cells are
    control-plane output we do not port, so nothing of value is lost. Legal
    references (tab, newline, CR, and all normal characters) are left untouched.
    """

    def replace(match: 're.Match') -> str:
        hex_digits, dec_digits = match.group(1), match.group(2)
        codepoint = int(hex_digits, 16) if hex_digits is not None else int(dec_digits)
        is_c0 = codepoint < 0x20
        is_c1_or_del = 0x7F <= codepoint <= 0x9F
        if (is_c0 or is_c1_or_del) and codepoint not in _XML10_LEGAL_CONTROL:
            return '\uFFFD'
        return match.group(0)

    return _CHAR_REF_RGX.sub(replace, source_text)


# Gestalt keyboard-type values 18 and up are JIS keyboards; below that is
# ANSI/ISO. Used only to tag a non-primary variant meaningfully.
_JIS_TYPE_FLOOR = 18


# --------------------------------------------------------------------------
# Modifier-plane resolution by EXACT PREDICATE EVALUATION
# --------------------------------------------------------------------------
# A <modifier keys="..."> rule is a precise predicate over modifier keys, NOT a
# fuzzy signature. TN2056 documents the semantics, and the OS implements them in
# UCKeyTranslate: the keyMapSelect elements are evaluated in order and the LAST
# one whose rule matches the current modifier state selects the table.
#
# Each rule token constrains one modifier:
#   bare token  X   -> X must be DOWN
#   X?              -> X is don't-care
#   token absent    -> X must be UP
#   anyX            -> (left X or right X); anyX bare means "at least one down"
#
# Crucially, the modifier state UCKeyTranslate is ever given is built from the
# GENERIC (non-sided) Carbon bits only -- shiftKey, optionKey, controlKey,
# cmdKey -- never the right-sided bits (rightShiftKey, rightOptionKey, ...).
# This is the documented, cross-implementation calling convention (Chromium,
# Microsoft node-native-keymap, Opera, et al. all build the modifier byte the
# same way). Therefore right-only rules can never fire during character
# production: they are vestigial, inherited from the format's KCHR lineage.
#
# We reproduce exactly that: planes are resolved by evaluating the rules against
# the four modifier states a Mac can actually present to the layout --
#   {}                       -> PLAIN
#   {shift}                  -> SHIFT
#   {option}                 -> OPTION
#   {shift, option}          -> SHIFT_OPTION
# with no sided bits ever set, last-match-wins. This is deterministic; there is
# nothing fuzzy to classify.

# The generic modifier atoms a plane state may contain. caps/command/control are
# never set for the four character planes, so they stay absent (= must be up).
_PLANE_STATES = {
    ModifierState.PLAIN:        frozenset(),
    ModifierState.SHIFT:        frozenset({'shift'}),
    ModifierState.OPTION:       frozenset({'option'}),
    ModifierState.SHIFT_OPTION: frozenset({'shift', 'option'}),
}

# Map each rule token's base name to the generic atom it tests. Right-sided and
# 'any' forms collapse to the generic atom, because the state we evaluate only
# ever carries generic bits (see above); a bare right-only token therefore tests
# an atom that is never set and so never matches, which is the correct vestigial
# behaviour.
_ATOM_OF_TOKEN = {
    'shift': 'shift',       'rightShift': '__never__',  'anyShift': 'shift',
    'option': 'option',     'rightOption': '__never__', 'anyOption': 'option',
    'control': 'control',   'rightControl': '__never__', 'anyControl': 'control',
    'command': 'command',
    'caps': 'caps',
}


def _rule_matches(keys: str, state: 'frozenset') -> bool:
    """True if a <modifier keys=...> rule matches a generic modifier state.

    'state' is a set of generic atoms currently down (e.g. {'shift', 'option'}).
    The rule matches iff every required (bare) token's atom is down, every
    explicitly-absent atom is up, and don't-care ('X?') tokens are ignored. A
    token naming a right-only modifier maps to the sentinel '__never__', which is
    never in 'state', so a bare right-only requirement fails to match -- exactly
    reproducing UCKeyTranslate being fed only generic bits.
    """

    constrained = set()
    for token in keys.split():
        optional = token.endswith('?')
        name = token[:-1] if optional else token
        atom = _ATOM_OF_TOKEN.get(name)
        if atom is None:
            # Unknown modifier token: be conservative and treat as a required
            # constraint that cannot be satisfied, so the rule does not spuriously
            # match. Flagged once by the caller if it ever happens.
            atom = '__unknown__'
        constrained.add(atom)
        if not optional:
            # Required down.
            if atom not in state:
                return False

    # Every generic atom NOT mentioned by the rule must be UP (absent from state).
    for atom in state:
        if atom not in constrained:
            return False
    return True


def _resolve_modifier_map(keyboard: 'ET.Element') -> 'dict':
    """Map each character plane to its keyMap index by exact predicate eval.

    For each of the four reachable plane states, evaluate every keyMapSelect's
    rules in document order; the LAST keyMapSelect with a matching rule selects
    that plane's table (TN2056 / UCKeyTranslate last-match-wins). Returns
    {ModifierState: mapIndex}. A plane with no matching keyMapSelect is omitted
    (the layout does not define that plane).
    """

    modifier_map = keyboard.find('modifierMap')
    if modifier_map is None:
        raise KeylayoutParseError('no <modifierMap> element')

    selects = []
    for select in modifier_map.findall('keyMapSelect'):
        index_attr = select.get('mapIndex')
        if index_attr is None:
            continue
        rules = [m.get('keys', '') for m in select.findall('modifier')]
        selects.append((int(index_attr), rules))

    plane_to_index = {}
    for plane, state in _PLANE_STATES.items():
        winner = None
        for map_index, rules in selects:
            if any(_rule_matches(rule, state) for rule in rules):
                winner = map_index            # last match wins
        if winner is not None:
            plane_to_index[plane] = winner

    return plane_to_index


def _parse_layouts_block(keyboard: 'ET.Element') -> 'list[dict]':
    """Read <layouts>: keyboard-type ranges mapped to keyMapSet ids.

    Returns a list of {first, last, mapSet} dicts. Empty if there is no
    <layouts> block.
    """

    layouts = keyboard.find('layouts')
    rows = []
    if layouts is not None:
        for layout in layouts.findall('layout'):
            map_set = layout.get('mapSet')
            if map_set is None:
                continue
            rows.append({
                'first': int(layout.get('first', '0')),
                'last': int(layout.get('last', '0')),
                'mapSet': map_set,
            })
    return rows


def _decode_output(text: str) -> str:
    """Decode a key/action output attribute into the literal string.

    ElementTree already resolves XML entities and &#xNNNN; forms, so the
    attribute value is the literal string. A lone U+FFFD is what
    _sanitize_control_refs left in place of an illegal control-char reference;
    it marks a control-plane cell we do not port, so it decodes to empty and the
    caller omits the cell. This wrapper is the single place to add handling if a
    non-standard escape is ever encountered.
    """

    if text == '\uFFFD':
        return ''
    return text


def _parse_actions(keyboard: 'ET.Element') -> 'tuple[dict, dict]':
    """Parse <actions> into the dead-key state graph.

    Returns (action_enters, state_graph):
      action_enters: {action_id: state_name} for actions that, from state
        'none', enter a dead-key state (the dead keys themselves).
      state_graph:   {state_name: {'outputs': {action_id: output_str},
                                    'nexts':   {action_id: next_state}}}.
    """

    actions = keyboard.find('actions')
    action_enters = {}
    state_graph = {}

    if actions is None:
        return action_enters, state_graph

    for action in actions.findall('action'):
        action_id = action.get('id')
        if action_id is None:
            continue
        for when in action.findall('when'):
            state = when.get('state')
            if state is None:
                continue
            output = when.get('output')
            next_state = when.get('next')

            if state == 'none' and next_state:
                action_enters[action_id] = next_state

            entry = state_graph.setdefault(state, {'outputs': {}, 'nexts': {}})
            if output is not None:
                entry['outputs'][action_id] = _decode_output(output)
            if next_state is not None:
                entry['nexts'][action_id] = next_state

    return action_enters, state_graph


def _parse_terminators(keyboard: 'ET.Element') -> 'dict':
    """Parse <terminators>: {state_name: bare_output_str}."""

    terminators = keyboard.find('terminators')
    result = {}
    if terminators is None:
        return result
    for when in terminators.findall('when'):
        state = when.get('state')
        output = when.get('output')
        if state is not None and output is not None:
            result[state] = _decode_output(output)
    return result


def _action_base_char(action_id: str, state_graph: 'dict') -> 'str | None':
    """The literal base character an action produces in the ground state.

    Compositions are keyed by base character (acute + 'e' -> 'e-acute'), so each
    action needs its ground-state output as the key. Returns the 'none'-state
    output, or None if the action has none.
    """

    none_state = state_graph.get('none')
    if none_state is None:
        return None
    return none_state['outputs'].get(action_id)


def _build_dead_states(
    layout: 'Layout',
    action_enters: 'dict',
    state_graph: 'dict',
    terminators: 'dict',
) -> None:
    """Create DeadState objects, flattening chains into base->result maps."""

    for state_name in set(action_enters.values()):
        dead_state = DeadState(name=state_name)
        dead_state.terminator = terminators.get(state_name, '')
        _gather_compositions(
            dead_state, state_name, state_graph, seen_states=set()
        )
        layout.dead_states[state_name] = dead_state


def _gather_compositions(
    dead_state: 'DeadState',
    state_name: str,
    state_graph: 'dict',
    seen_states: 'set',
) -> None:
    """Fill dead_state.compositions for state_name, following chains.

    Each action active in this state: a direct output becomes a base->result
    composition; a 'next' (chain) is followed into the chained state. Cycles are
    guarded via seen_states. Flattening matches the model contract: chained
    dead-key results land in the same compositions map keyed by their own base
    characters, reproducing the flat outputs the binary parser also produces.
    """

    if state_name in seen_states:
        warn('keylayout', f'cycle in dead-key chain at state {state_name!r}')
        return
    seen_states.add(state_name)

    entry = state_graph.get(state_name)
    if entry is None:
        return

    for action_id, output in entry['outputs'].items():
        base = _action_base_char(action_id, state_graph)
        if base is None:
            continue
        dead_state.compositions[base] = output

    for next_state in entry['nexts'].values():
        _gather_compositions(dead_state, next_state, state_graph, seen_states)


def _key_element_to_output(
    key: 'ET.Element',
    action_enters: 'dict',
    action_outputs: 'dict',
) -> 'KeyOutput | None':
    """Turn one <key> element into a KeyOutput, or None to omit it.

    A <key output=..> is a literal CHARS cell. A <key action=..> is either a
    dead key (the action enters a state -> DEAD cell) or an ordinary key
    expressed as an action (very common: a letter that is also a composition
    base is written as an action whose 'none'-state output is the letter). For
    the latter we resolve the action's ground-state output from action_outputs.
    """

    output = key.get('output')
    if output is not None:
        text = _decode_output(output)
        if text == '':
            return None
        return KeyOutput(kind=OutputKind.CHARS, output=text)

    action_id = key.get('action')
    if action_id is not None:
        if action_id in action_enters:
            return KeyOutput(
                kind=OutputKind.DEAD,
                dead_state_name=action_enters[action_id],
            )
        ground = action_outputs.get(action_id)
        if ground:
            return KeyOutput(kind=OutputKind.CHARS, output=ground)
        # An action that neither enters a state nor has ground output: nothing
        # to emit. Rare; omit rather than dangle.
        return None

    return None


def _parse_key_tables(
    keyboard: 'ET.Element',
    map_set_id: str,
    plane_to_index: 'dict',
    action_enters: 'dict',
    action_outputs: 'dict',
) -> 'dict':
    """Build {virtual_key: {ModifierState: KeyOutput}} for one keyMapSet."""

    key_map_set = None
    for candidate in keyboard.findall('keyMapSet'):
        if candidate.get('id') == map_set_id:
            key_map_set = candidate
            break
    if key_map_set is None:
        raise KeylayoutParseError(f'keyMapSet {map_set_id!r} not found')

    tables = {}
    for key_map in key_map_set.findall('keyMap'):
        index_attr = key_map.get('index')
        if index_attr is None:
            continue
        tables[int(index_attr)] = key_map

    keys = {}
    for plane, map_index in plane_to_index.items():
        key_map = tables.get(map_index)
        if key_map is None:
            warn(
                'keylayout',
                f'mapSet {map_set_id} has no keyMap index {map_index} '
                f'for plane {plane.value}'
            )
            continue
        for key in key_map.findall('key'):
            code_attr = key.get('code')
            if code_attr is None:
                continue
            key_output = _key_element_to_output(key, action_enters, action_outputs)
            if key_output is None:
                continue
            keys.setdefault(int(code_attr), {})[plane] = key_output

    return keys


def _variant_tag(map_set_id: str, type_range: 'tuple | None') -> str:
    """A short stable tag for a non-primary variant.

    JIS keyboards are gestalt type 18+; if the range starts there, tag 'jis'.
    Otherwise fall back to the mapSet id lowercased, stable per file.
    """

    if type_range is not None and type_range[0] >= _JIS_TYPE_FLOOR:
        return 'jis'
    return (map_set_id or 'alt').lower()


def _build_provenance(
    keyboard: 'ET.Element',
    source_text: str,
) -> 'Provenance':
    """Populate Provenance from the XML's metadata for the emitted file header."""

    maxout = keyboard.get('maxout')
    layout_id = keyboard.get('id')

    source_tool = None
    source_edited = None
    edit_match = re.search(r'edited by\s+(.+?)\s+on\s+([0-9-]+)', source_text)
    if edit_match:
        source_tool = edit_match.group(1).strip()
        source_edited = edit_match.group(2).strip()

    return Provenance(
        source_kind=SourceKind.KEYLAYOUT_XML,
        source_name=keyboard.get('name', ''),
        source_id=layout_id,
        apple_layout_id=layout_id,
        source_max_output=int(maxout) if maxout and maxout.lstrip('-').isdigit() else None,
        source_tool=source_tool,
        source_edited=source_edited,
        tool_version=__version__,
    )


def parse_keylayout_xml(path: str) -> 'Layout':
    """Parse a .keylayout XML file at 'path' into a normalized Layout.

    Produces the same common.models.Layout the binary parser produces, so the
    classify and emit stages are shared. Raises KeylayoutParseError on a file
    that does not conform; warns and continues best-effort on shapes outside the
    categories this parser maps.
    """

    with open(path, 'r', encoding='utf-8') as handle:
        source_text = handle.read()

    sanitized_text = _sanitize_control_refs(source_text)

    try:
        root = ET.fromstring(sanitized_text)
    except ET.ParseError as error:
        raise KeylayoutParseError(f'XML parse error: {error}') from error

    keyboard = root if root.tag == 'keyboard' else root.find('keyboard')
    if keyboard is None:
        raise KeylayoutParseError('no <keyboard> root element')

    name = keyboard.get('name', '') or path
    dbg('keylayout', f'parsing {name!r} from {path}')

    plane_to_index = _resolve_modifier_map(keyboard)
    dbg(
        'keylayout',
        'planes -> mapIndex: '
        + ', '.join(f'{p.value}={i}' for p, i in plane_to_index.items())
    )

    action_enters, state_graph = _parse_actions(keyboard)
    terminators = _parse_terminators(keyboard)

    # Ground-state output of each action (the 'none' state), used to resolve
    # ordinary keys that are written as actions because they are composition
    # bases (e.g. <key action="a"> whose 'none' output is 'a').
    none_state = state_graph.get('none', {'outputs': {}})
    action_outputs = dict(none_state['outputs'])

    layout = Layout(name=name, source_id=keyboard.get('id'))
    layout.provenance = _build_provenance(keyboard, source_text)

    _build_dead_states(layout, action_enters, state_graph, terminators)
    dbg('keylayout', f'dead states: {layout.dead_key_count()}')

    layout_rows = _parse_layouts_block(keyboard)
    if layout_rows:
        seen_sets = {}
        for row in sorted(layout_rows, key=lambda r: r['first']):
            seen_sets.setdefault(row['mapSet'], (row['first'], row['last']))
        ordered_sets = list(seen_sets.items())
    else:
        only = keyboard.find('keyMapSet')
        if only is None:
            raise KeylayoutParseError('no <keyMapSet> element')
        ordered_sets = [(only.get('id'), None)]

    for position, (map_set_id, type_range) in enumerate(ordered_sets):
        keys = _parse_key_tables(
            keyboard, map_set_id, plane_to_index, action_enters, action_outputs
        )
        if position == 0:
            layout.keys = keys
            tag = ''
        else:
            tag = _variant_tag(map_set_id, type_range)
        layout.variants.append(
            Variant(tag=tag, keys=keys, keyboard_type_range=type_range)
        )

    dbg(
        'keylayout',
        f'variants: {len(layout.variants)} '
        f'(mapSets {[s for s, _ in ordered_sets]})'
    )

    for problem in layout.validate()[:10]:
        warn('keylayout', f'validate: {problem}')

    return layout


# End of file #
