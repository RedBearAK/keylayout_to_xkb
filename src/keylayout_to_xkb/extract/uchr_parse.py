"""
keylayout_to_xkb/extract/uchr_parse.py

Parser for the macOS 'uchr' (UCKeyboardLayout) binary keyboard-layout format,
producing a normalized common.models.Layout.

Structure verified byte-for-byte against real macOS Sonoma layouts (US, ABC
Extended, Polish Pro, German). Dead-key decode validated against an independent
oracle: ABC Extended resolves to exactly 25 dead-key states.

All multi-byte values little-endian. Sub-table offsets are absolute (from the
start of the buffer) unless noted. Each sub-table index begins with a u16
marker sentinel from the 0x_001 family; a wrong marker means a wrong offset and
we fail loud rather than parse garbage.
"""

import struct
import unicodedata

from keylayout_to_xkb.common.debug import dbg, warn, hex_window
from keylayout_to_xkb.common.models import (
    Layout,
    DeadState,
    KeyOutput,
    OutputKind,
    ModifierState,
)
from keylayout_to_xkb.common.mac_virtual_keys import vk_name
from keylayout_to_xkb.extract.uckeytranslate import resolve_plane_tables_via_os


__version__ = '20260623'


_EXPECTED_HEADER_FORMAT     = 0x1002
_FEATURE_INFO_MARKER        = 0x2001
_CHAR_INDEX_MARKER          = 0x4001
_STATE_INDEX_MARKER         = 0x5001
_TERMINATOR_INDEX_MARKER    = 0x6001
_SEQUENCE_INDEX_MARKER      = 0x7001

_OUTPUT_EMPTY_A             = 0xFFFE
_OUTPUT_EMPTY_B             = 0xFFFF
_FLAG_SEQUENCE             = 0x8000
_FLAG_STATE                = 0x4000
_FLAG_TEST_MASK            = 0xC000
_INDEX_MASK                = 0x3FFF

_TERMINAL_ENTRY_FORMAT     = 0x0001
_RANGE_ENTRY_FORMAT        = 0x0002

_MOD_MAP_MARKER            = 0x3001

# Modifier-index bits that mark a non-character plane on Linux: cmd (0x01) and
# control (0x10). A table reachable only with one of these set carries shortcut
# or control output, not characters we port.
_MOD_CMD_OR_CONTROL        = 0x11

# A plane-sanity gap of at least this many distinct printable outputs is flagged
# as major (typically a whole missed script); fewer is minor (stray accents).
_PLANE_GAP_MAJOR           = 10

# A char table needs at least this many letters of a script to be considered a
# substantial alphabet (used to decide a layout's primary script vs. a small
# Latin shortcut/fallback layer on a non-Latin keyboard).
_MIN_SCRIPT_LETTERS        = 10

# macOS virtual keycodes of the number-row keys 1..0. The shift plane of a
# standard layout differs from plain on these (digit -> symbol), which
# disambiguates two otherwise-identical uppercase tables whose only difference
# is whether the number row is shifted (seen in Greek Polytonic: two Greek-caps
# tables, one with 1234 and one with !@#$, identical on letters).
_NUMBER_ROW_VKS = (18, 19, 20, 21, 23, 22, 26, 28, 25, 29)


class UchrParseError(RuntimeError):
    """Raised on an unrecoverable structural problem in the 'uchr' buffer."""


def _check_bounds(data: bytes, offset: int, length: int, what: str) -> None:
    """Fail loud if [offset, offset+length) is not within the buffer."""

    if offset < 0 or length < 0 or offset + length > len(data):
        raise UchrParseError(
            f'{what}: out-of-bounds '
            f'(offset={offset}, length={length}, buffer={len(data)})'
        )


def parse_uchr(data: bytes, layout_name: str = '', source_id: str = '') -> Layout:
    """Parse a single 'uchr' byte buffer into a normalized Layout."""

    if len(data) < 40:
        raise UchrParseError(f'buffer too small to be uchr: {len(data)} bytes')

    dbg('uchr', f'parsing layout name={layout_name!r} bytes={len(data)}')
    dbg('uchr', hex_window(data, 0, 32))

    header_format, data_version, feature_info_offset, keyboard_type_count = \
        struct.unpack_from('<HHII', data, 0)

    if header_format != _EXPECTED_HEADER_FORMAT:
        raise UchrParseError(
            f'unexpected header format 0x{header_format:04x}; '
            f'expected 0x{_EXPECTED_HEADER_FORMAT:04x}'
        )

    if keyboard_type_count < 1 or keyboard_type_count > 64:
        raise UchrParseError(
            f'implausible keyboard_type_count={keyboard_type_count}'
        )

    dbg(
        'uchr',
        f'data_version={data_version} feature_info_offset={feature_info_offset} '
        f'keyboard_type_count={keyboard_type_count}'
    )

    type_header_base = 12
    _check_bounds(data, type_header_base, 28, 'keyboard-type header[0]')

    (
        kbd_type_first,
        kbd_type_last,
        mod_to_table_offset,
        char_index_offset,
        state_records_offset,
        state_terminators_offset,
        sequence_data_offset,
    ) = struct.unpack_from('<IIIIIII', data, type_header_base)

    dbg(
        'uchr',
        f'type[0] first={kbd_type_first} last={kbd_type_last} '
        f'char_index_off={char_index_offset} '
        f'state_records_off={state_records_offset} '
        f'seq_data_off={sequence_data_offset}'
    )

    if keyboard_type_count > 1:
        dbg('uchr', f'note: {keyboard_type_count} keyboard-type records; using [0]')

    max_output_char_length = _parse_max_output_char_length(data, feature_info_offset)
    dbg('uchr', f'maxOutputCharLength={max_output_char_length}')

    sequences = _parse_sequence_table(data, sequence_data_offset)
    dbg('uchr', f'sequences: {len(sequences)}')

    state_records = _parse_state_records(data, state_records_offset)
    dbg('uchr', f'state records: {len(state_records)}')

    # The two index flags on a char-table entry are governed independently:
    #
    #   0x4000 (state index) is active whenever the layout actually has state
    #   records. Many single-character layouts (German, Polish, Turkish, etc.)
    #   have maxOutputCharLength == 1 yet still use state indices for their dead
    #   keys, so this must key on state-record presence, NOT on max length.
    #
    #   0x8000 (sequence index) is active only when the layout declares it can
    #   emit multi-character output (maxOutputCharLength >= 2). When length is 1
    #   there is no sequence table, so a high bit is part of a literal codepoint
    #   (e.g. Rejang U+A946, Meetei Mayek U+ABxx), not an index.
    #
    # Both are format-declared facts, not inferences from index range.
    state_indices_active = len(state_records) > 0
    sequence_indices_active = max_output_char_length >= 2

    char_tables = _parse_char_table_index(data, char_index_offset)
    keys_per_table = char_tables[0][1] if char_tables else 0
    dbg('uchr', f'char tables: {len(char_tables)} (each {keys_per_table} keys)')

    table_map, default_table = _parse_modifier_table_map(data, mod_to_table_offset)

    # Prefer Apple's UCKeyTranslate for an authoritative plane->table map when
    # running on macOS; fall back to content-driven resolution everywhere else.
    # The OS path resolves the same four planes deterministically, sidestepping
    # the undocumented on-disk modifier-index encoding. Both paths feed the same
    # downstream machinery (dead keys, terminators, compositions).
    def table_outputs_for(table_index):
        return _table_outputs(
            data, char_tables[table_index], state_records, sequences,
            state_indices_active, sequence_indices_active,
        )

    plane_tables = None
    try:
        plane_tables = resolve_plane_tables_via_os(
            data, char_tables, table_outputs_for
        )
    except Exception as os_error:                       # never let OS path abort
        warn('uchr', f'UCKeyTranslate path errored ({os_error}); using content resolver')
        plane_tables = None

    if plane_tables:
        dbg('uchr', 'plane resolution: UCKeyTranslate (deterministic)')
    else:
        plane_tables = _resolve_plane_tables(
            data, char_tables, table_map, default_table,
            state_records, sequences, state_indices_active, sequence_indices_active,
        )
        dbg('uchr', 'plane resolution: content-driven (fallback)')
    dbg(
        'uchr',
        'plane->table: '
        + ', '.join(f'{p.value}={t}' for p, t in plane_tables.items())
    )

    layout = Layout(name=layout_name, source_id=source_id or None)

    terminators = _parse_terminators(
        data, state_terminators_offset, sequences, sequence_indices_active
    )
    dbg('uchr', f'terminators: {len(terminators)}')

    _build_dead_states(
        layout, state_records, sequences, sequence_indices_active, terminators
    )
    dbg('uchr', f'dead states: {layout.dead_key_count()}')

    _populate_keys(
        layout, data, char_tables, plane_tables, state_records, sequences,
        state_indices_active, sequence_indices_active,
    )

    _verify_plane_assignment(
        layout, data, char_tables, plane_tables, table_map, state_records,
        sequences, state_indices_active, sequence_indices_active,
    )

    dbg(
        'uchr',
        f'parsed: keys={len(layout.keys)} dead_states={layout.dead_key_count()} '
        f'approx_chars={layout.char_count()}'
    )

    return layout


def _parse_max_output_char_length(data: bytes, feature_info_offset: int) -> int:
    """Read maxOutputCharLength from UCKeyboardLayoutFeatureInfo.

    Structure at feature_info_offset:
      u16 keyboardLayoutFeatureInfoFormat   (marker, 0x2001 family)
      u16 (reserved/version)
      u32 maxOutputCharLength
      ...

    Returns the declared maximum output length. A value of 1 means the layout
    cannot emit sequences, so index flags on char-table entries are not active.
    If the feature-info block is absent (offset 0) or unreadable, we
    conservatively assume sequences may exist (return 2), preserving prior
    behaviour rather than risking dropping a real sequence.
    """

    if feature_info_offset == 0:
        return 2

    if feature_info_offset + 8 > len(data):
        warn('uchr', 'feature-info offset out of bounds; assuming sequences active')
        return 2

    marker = struct.unpack_from('<H', data, feature_info_offset)[0]
    max_output = struct.unpack_from('<I', data, feature_info_offset + 4)[0]

    if marker != _FEATURE_INFO_MARKER:
        warn(
            'uchr',
            f'feature-info marker 0x{marker:04x} != 0x{_FEATURE_INFO_MARKER:04x}; '
            f'assuming sequences active'
        )
        return 2

    if max_output < 1 or max_output > 64:
        warn('uchr', f'implausible maxOutputCharLength={max_output}; assuming active')
        return 2

    return max_output


def _parse_char_table_index(data: bytes, offset: int) -> 'list[tuple[int, int]]':
    """Read keyToCharTableIndex; return [(table_offset, table_size), ...].

    Header: u16 marker (0x4001), u16 size, u32 count, then count absolute u32
    table offsets. Each table holds size u16 entries.
    """

    if offset == 0:
        raise UchrParseError('char-table-index offset is 0; layout has no keys')

    _check_bounds(data, offset, 8, 'char-table-index header')
    marker, table_size = struct.unpack_from('<HH', data, offset)
    table_count = struct.unpack_from('<I', data, offset + 4)[0]

    if marker != _CHAR_INDEX_MARKER:
        raise UchrParseError(
            f'char-table-index marker 0x{marker:04x} != 0x{_CHAR_INDEX_MARKER:04x}'
        )
    if table_count < 1 or table_count > 64:
        raise UchrParseError(f'implausible char table count={table_count}')
    if table_size < 1 or table_size > 1024:
        raise UchrParseError(f'implausible char table size={table_size}')

    offsets_base = offset + 8
    _check_bounds(data, offsets_base, 4 * table_count, 'char-table offset array')

    tables = []
    for table_index in range(table_count):
        table_offset = struct.unpack_from('<I', data, offsets_base + 4 * table_index)[0]
        _check_bounds(data, table_offset, 2 * table_size, f'char table[{table_index}]')
        tables.append((table_offset, table_size))

    return tables


def _parse_modifier_table_map(data: bytes, offset: int) -> 'tuple[list[int], int]':
    """Read keyModifiersToTableNum; return (tableNum list, defaultTableNum).

    Structure at offset:
      u16 marker            (== 0x3001)
      u16 defaultTableNum
      u16 modifiersCount
      u8  tableNum[modifiersCount]

    The array index is a macOS modifierKeyState value; the byte at that index is
    the char-table number to use for that modifier combination. An out-of-range
    plane query falls back to defaultTableNum.
    """

    if offset == 0:
        return [], 0

    _check_bounds(data, offset, 6, 'modifier-table-map header')
    marker, default_table, modifiers_count = struct.unpack_from('<HHH', data, offset)

    if marker != _MOD_MAP_MARKER:
        warn(
            'uchr',
            f'modifier-table-map marker 0x{marker:04x} != 0x{_MOD_MAP_MARKER:04x}; '
            f'falling back to physical table order'
        )
        return [], 0

    if modifiers_count == 0 or modifiers_count > 256:
        warn('uchr', f'implausible modifiersCount={modifiers_count}; ignoring map')
        return [], 0

    _check_bounds(data, offset + 6, modifiers_count, 'modifier-table-map array')
    table_nums = [data[offset + 6 + i] for i in range(modifiers_count)]

    return table_nums, default_table


def _table_letters(
    data: bytes,
    table: 'tuple[int, int]',
    state_records: 'list[dict]',
    sequences: 'list[str]',
    state_indices_active: bool,
    sequence_indices_active: bool,
) -> 'dict':
    """Return {virtual_key: char} for alphabetic single-char outputs of a table.

    Used only for plane classification, so dead keys and multi-char sequences
    are skipped (they don't help judge script/case). Reads the same way the
    populate path does, via _entry_to_key_output, then keeps only single
    alphabetic characters.
    """

    table_offset, table_size = table
    letters = {}
    for virtual_key in range(table_size):
        entry = struct.unpack_from('<H', data, table_offset + 2 * virtual_key)[0]
        ko = _entry_to_key_output(
            entry, state_records, sequences,
            state_indices_active, sequence_indices_active, virtual_key,
        )
        if ko is None or ko.kind is not OutputKind.CHARS or not ko.output:
            continue
        if len(ko.output) == 1 and ko.output.isalpha():
            letters[virtual_key] = ko.output
    return letters


def _table_outputs(
    data: bytes,
    table: 'tuple[int, int]',
    state_records: 'list[dict]',
    sequences: 'list[str]',
    state_indices_active: bool,
    sequence_indices_active: bool,
) -> 'dict':
    """Return {virtual_key: output_str} for ALL single-char outputs of a table.

    Unlike _table_letters (alphabetic only), this keeps every single-character
    output including symbols, so the UCKeyTranslate plane matcher can identify
    symbol-heavy Option planes. Dead-key and multi-char cells are omitted (they
    do not give a stable single-char key for matching).
    """

    table_offset, table_size = table
    outputs = {}
    for virtual_key in range(table_size):
        entry = struct.unpack_from('<H', data, table_offset + 2 * virtual_key)[0]
        ko = _entry_to_key_output(
            entry, state_records, sequences,
            state_indices_active, sequence_indices_active, virtual_key,
        )
        if ko is None or ko.kind is not OutputKind.CHARS or not ko.output:
            continue
        if len(ko.output) == 1:
            outputs[virtual_key] = ko.output
    return outputs


def _is_latin(ch: str) -> bool:
    """True if ch is a Latin-script letter (by Unicode name prefix)."""

    try:
        return unicodedata.name(ch).startswith('LATIN')
    except ValueError:
        return False


def _uppercase_pair_score(lower_letters: 'dict', upper_letters: 'dict') -> int:
    """Count virtual keys where upper_letters is the uppercasing of lower_letters.

    The signature of a shift plane relative to its base: same script, same
    positions, cased up. Cross-script or same-case tables score zero.
    """

    score = 0
    for vk, ch in lower_letters.items():
        other = upper_letters.get(vk)
        if other is not None and ch != other and other == ch.upper():
            score += 1
    return score


def _resolve_plane_tables(
    data: bytes,
    char_tables: 'list[tuple[int, int]]',
    table_map: 'list[int]',
    default_table: int,
    state_records: 'list[dict]',
    sequences: 'list[str]',
    state_indices_active: bool,
    sequence_indices_active: bool,
) -> 'dict':
    """Map each ModifierState plane to its char-table index, by table CONTENT.

    The on-disk keyModifiersToTableNum index uses an undocumented compaction, so
    rather than trust fixed indices, planes are resolved from what each table
    outputs. Verified against Apple's own .keylayout XML (Ukelele) for US,
    German, Greek, Ukrainian: this reproduces Apple's declared plane->table
    assignment, including native-script layouts whose primary script the fixed
    indices missed.

    Algorithm:
      1. Candidate planes are tables reachable WITHOUT command or control. Those
         modifiers select shortcut/control layers (e.g. the Latin command layer
         on a Cyrillic keyboard), which are not character planes on Linux.
      2. If any candidate holds a substantial non-Latin script, the layout's
         primary script is non-Latin; the Latin candidate(s) are a fallback/
         shortcut layer and are demoted. Otherwise the primary script is Latin.
      3. PLAIN is the primary-script candidate with the most lowercase letters
         (or, for uncased scripts, the most letters). SHIFT is the candidate
         that best reads as the uppercasing of PLAIN. OPTION and SHIFT_OPTION
         are the remaining candidates, paired by the same uppercase relationship
         where it holds, else by letter richness.

    Falls back to physical order only when there is no modifier map at all.
    """

    table_count = len(char_tables)

    if not table_map:
        fallback = {
            ModifierState.PLAIN:        0,
            ModifierState.SHIFT:        1,
            ModifierState.OPTION:       3,
            ModifierState.SHIFT_OPTION: 4,
        }
        return {
            plane: ti for plane, ti in fallback.items()
            if 0 <= ti < table_count
        }

    reach = _modifier_reach_by_table(table_map)

    def letters_of(table_index):
        return _table_letters(
            data, char_tables[table_index], state_records, sequences,
            state_indices_active, sequence_indices_active,
        )

    # Step 1: candidate char-plane tables (reachable without cmd/control).
    candidates = [
        table_index for table_index in range(table_count)
        if any(not (i & _MOD_CMD_OR_CONTROL) for i in reach.get(table_index, []))
    ]
    if not candidates:
        return {}

    letters_by_table = {t: letters_of(t) for t in candidates}

    def nonlatin_count(t):
        return sum(1 for ch in letters_by_table[t].values() if not _is_latin(ch))

    def latin_count(t):
        return sum(1 for ch in letters_by_table[t].values() if _is_latin(ch))

    def lowercase_count(t):
        return sum(1 for ch in letters_by_table[t].values() if ch.islower())

    def letter_count(t):
        return len(letters_by_table[t])

    # Step 2: decide primary script. A layout is non-Latin if some candidate
    # carries a substantial non-Latin alphabet; then Latin tables are demoted.
    has_nonlatin = any(nonlatin_count(t) >= _MIN_SCRIPT_LETTERS for t in candidates)
    if has_nonlatin:
        primary = [t for t in candidates if nonlatin_count(t) >= latin_count(t)
                   and nonlatin_count(t) > 0]
    else:
        primary = [t for t in candidates if latin_count(t) > 0]
    pool = primary if primary else candidates

    # Step 3: assign planes. Cased scripts (Latin, Greek, Cyrillic) are resolved
    # by case relationship; uncased scripts (Tibetan, Thai, Rejang, ...) have no
    # uppercase, so their planes are ordered by how few modifiers reach each
    # table (fewest = most basic plane). The modifier index is used here only to
    # ORDER same-script tables, never to decode which modifier it is, so the
    # opaque on-disk index packing does not matter.
    cased = any(lowercase_count(t) > 0 for t in pool)

    plane_order = [
        ModifierState.PLAIN,
        ModifierState.SHIFT,
        ModifierState.OPTION,
        ModifierState.SHIFT_OPTION,
    ]

    if not cased:
        # Uncased: primary-script tables, ordered by minimal non-cmd/control
        # modifier index. Verified against Apple's Thai .keylayout (plain and
        # shift are distinct Thai character sets, not a case pair) and the
        # Tibetan/Rejang/Meetei binaries.
        def min_char_index(t):
            plain_indices = [i for i in reach.get(t, []) if not (i & _MOD_CMD_OR_CONTROL)]
            return min(plain_indices) if plain_indices else None

        script_pool = pool if pool else candidates
        ordered = sorted(
            (t for t in script_pool if min_char_index(t) is not None),
            key=min_char_index,
        )
        resolved = {}
        for plane, table_index in zip(plane_order, ordered):
            resolved[plane] = table_index
        return resolved

    # Cased path.
    plain = max(pool, key=lambda t: (lowercase_count(t), letter_count(t)))
    plain_letters = letters_by_table[plain]

    def number_row(table_index):
        """Raw output of the number-row keys for a table, for tiebreaking."""
        table_offset, table_size = char_tables[table_index]
        row = []
        for vk in _NUMBER_ROW_VKS:
            if vk >= table_size:
                row.append('')
                continue
            entry = struct.unpack_from('<H', data, table_offset + 2 * vk)[0]
            ko = _entry_to_key_output(
                entry, state_records, sequences,
                state_indices_active, sequence_indices_active, vk,
            )
            row.append(ko.output if (ko and ko.kind is OutputKind.CHARS) else '')
        return row

    plain_numbers = number_row(plain)

    def number_row_shifted(table_index):
        """How many number-row keys differ from plain (digit -> symbol).

        The shift plane of a standard layout shifts the number row; a near-
        duplicate table that leaves digits unchanged does not. Used only to
        break ties between tables with equal letter-pairing scores.
        """
        row = number_row(table_index)
        return sum(
            1 for a, b in zip(plain_numbers, row)
            if a and b and a != b
        )

    # SHIFT: the table that best reads as the shifted plane of plain. Score is
    # uppercase letter-pairing PLUS number-row shifting (digit -> symbol), since
    # two tables can tie on letters and differ only on whether they shift the
    # number row (Greek Polytonic has two Greek-caps tables, 1234 vs !@#$). The
    # number-row term is weighted to break such near-ties decisively without
    # overriding a genuine letter-pairing difference on non-number-shifting
    # layouts (where every candidate scores 0 on the number-row term).
    shift_candidates = [t for t in candidates if t != plain]
    shift = None
    if shift_candidates:
        def shift_score(table_index):
            return (
                _uppercase_pair_score(plain_letters, letters_by_table[table_index])
                + number_row_shifted(table_index)
            )
        best = max(shift_candidates, key=shift_score)
        if _uppercase_pair_score(plain_letters, letters_by_table[best]) > 0:
            shift = best

    # OPTION / SHIFT_OPTION: remaining candidates, paired by uppercase
    # relationship where present, else by letter richness.
    claimed = {plain}
    if shift is not None:
        claimed.add(shift)
    rest = [t for t in candidates if t not in claimed]
    option = shift_option = None
    if rest:
        best_pair = None
        for low in rest:
            for high in rest:
                if low == high:
                    continue
                score = _uppercase_pair_score(letters_by_table[low], letters_by_table[high])
                if score > 0 and (best_pair is None or score > best_pair[0]):
                    best_pair = (score, low, high)
        if best_pair is not None:
            option, shift_option = best_pair[1], best_pair[2]
        else:
            ranked = sorted(rest, key=letter_count, reverse=True)
            option = ranked[0]
            shift_option = ranked[1] if len(ranked) > 1 else None

    resolved = {}
    if plain is not None:
        resolved[ModifierState.PLAIN] = plain
    if shift is not None:
        resolved[ModifierState.SHIFT] = shift
    if option is not None:
        resolved[ModifierState.OPTION] = option
    if shift_option is not None:
        resolved[ModifierState.SHIFT_OPTION] = shift_option
    return resolved


def _parse_sequence_table(data: bytes, offset: int) -> 'list[str]':
    """Read keySequenceDataIndex; return the multi-character output strings.

    Header: u16 marker (0x7001), u16 count, then count+1 u16 offsets RELATIVE to
    this table. UTF-16LE bytes between consecutive offsets form each string.
    """

    if offset == 0:
        return []

    _check_bounds(data, offset, 4, 'sequence-table header')
    marker, sequence_count = struct.unpack_from('<HH', data, offset)

    if marker != _SEQUENCE_INDEX_MARKER:
        warn('uchr', f'sequence marker 0x{marker:04x}; treating as empty')
        return []
    if sequence_count == 0:
        return []
    if sequence_count > 4096:
        warn('uchr', f'large sequence count={sequence_count}; verify offset')

    offsets_base = offset + 4
    _check_bounds(data, offsets_base, 2 * (sequence_count + 1), 'sequence offsets')

    rel_offsets = [
        struct.unpack_from('<H', data, offsets_base + 2 * index)[0]
        for index in range(sequence_count + 1)
    ]

    sequences = []
    for index in range(sequence_count):
        start = offset + rel_offsets[index]
        end = offset + rel_offsets[index + 1]
        if end < start:
            warn('uchr', f'sequence[{index}] negative span; emitting empty')
            sequences.append('')
            continue
        _check_bounds(data, start, end - start, f'sequence[{index}] bytes')
        sequences.append(data[start:end].decode('utf-16-le', 'replace'))

    return sequences


def _parse_terminators(
    data: bytes,
    offset: int,
    sequences: 'list[str]',
    sequence_indices_active: bool,
) -> 'list[str]':
    """Read keyStateTerminators; return the bare output per dead-key state.

    Structure (the same marker/count family as the other tables):
      u16 marker (== 0x6001)
      u16 count
      u16 terminator[count]

    Entry i is the output produced when dead-key state i is active and the next
    key has no composition in that state (e.g. the acute dead key followed by
    space yields a bare identical-character). The entry uses the same encoding
    as char-table and state charData: literal codepoint, sequence index when
    sequence_indices_active, or empty. The list is indexed by state, parallel to
    the state records.
    """

    if offset == 0:
        return []

    _check_bounds(data, offset, 4, 'terminators header')
    marker, count = struct.unpack_from('<HH', data, offset)

    if marker != _TERMINATOR_INDEX_MARKER:
        warn(
            'uchr',
            f'terminators marker 0x{marker:04x} != '
            f'0x{_TERMINATOR_INDEX_MARKER:04x}; treating as empty'
        )
        return []
    if count == 0:
        return []
    if count > 4096:
        warn('uchr', f'large terminator count={count}; verify offset')

    _check_bounds(data, offset + 4, 2 * count, 'terminators array')

    terminators = []
    for i in range(count):
        entry = struct.unpack_from('<H', data, offset + 4 + 2 * i)[0]
        terminators.append(
            _resolve_char_data(entry, sequences, sequence_indices_active)
        )
    return terminators


def _parse_state_records(data: bytes, offset: int) -> 'list[dict]':
    """Read keyStateRecordsIndex; return decoded record dicts indexed by record.

    Header: u16 marker (0x5001), u16 count, then count absolute u32 offsets.
    Each record: u16 zeroChar, u16 nextState, u16 entryCount, u16 entryFormat,
    followed by entryCount terminal entries (format 1) of u16 curState, u16
    charData.
    """

    if offset == 0:
        return []

    _check_bounds(data, offset, 4, 'state-records header')
    marker, record_count = struct.unpack_from('<HH', data, offset)

    if marker != _STATE_INDEX_MARKER:
        raise UchrParseError(
            f'state-records marker 0x{marker:04x} != 0x{_STATE_INDEX_MARKER:04x}'
        )
    if record_count == 0:
        return []
    if record_count > 1024:
        warn('uchr', f'large state record count={record_count}; verify offset')

    offsets_base = offset + 4
    _check_bounds(data, offsets_base, 4 * record_count, 'state-record offsets')

    record_offsets = [
        struct.unpack_from('<I', data, offsets_base + 4 * i)[0]
        for i in range(record_count)
    ]

    # A record's data ends where the next record begins (records are laid out
    # consecutively). The last record ends at the buffer end. This bound keeps
    # the variable-length format-2 decode from ever reading into a neighbour.
    sorted_offsets = sorted(set(record_offsets))

    def _record_end_for(start: int) -> int:
        for candidate in sorted_offsets:
            if candidate > start:
                return candidate
        return len(data)

    records = []
    for record_index in range(record_count):
        record_offset = record_offsets[record_index]
        _check_bounds(data, record_offset, 8, f'state record[{record_index}] header')

        zero_char, next_state, entry_count, entry_format = \
            struct.unpack_from('<HHHH', data, record_offset)

        entries = []
        if entry_format == _TERMINAL_ENTRY_FORMAT and entry_count:
            _check_bounds(
                data, record_offset + 8, 4 * entry_count,
                f'state record[{record_index}] entries',
            )
            for entry_index in range(entry_count):
                cur_state, char_data = struct.unpack_from(
                    '<HH', data, record_offset + 8 + 4 * entry_index
                )
                entries.append((cur_state, char_data))
        elif entry_format == _RANGE_ENTRY_FORMAT:
            entries = _parse_range_record_entries(
                data, record_offset, _record_end_for(record_offset), record_index
            )
        elif entry_format not in (0, _TERMINAL_ENTRY_FORMAT):
            warn(
                'uchr',
                f'state record[{record_index}] entry format '
                f'0x{entry_format:04x} not handled; {entry_count} entries skipped'
            )

        records.append({
            'zero': zero_char,
            'next': next_state,
            'fmt': entry_format,
            'entries': entries,
        })

    return records


def _parse_range_record_entries(
    data: bytes,
    record_offset: int,
    record_end: int,
    record_index: int,
) -> 'list[tuple[int, int]]':
    """Best-effort decode of a format-2 (range) state record.

    Format-2 records encode polytonic / stacked dead-key chaining (Greek
    Polytonic, Tibetan, a few Native American layouts). Verified layout: after
    the 8-byte record header come 8-byte range entries (u16 curStateStart,
    u8 curStateRange, u8 deltaMultiplier, u16 charData, u16 nextState), a
    0xFFFF-curStateStart sentinel, then a nested format-1 terminal record
    (u16 zeroChar, u16 nextState, u16 count, u16 format==1, then count
    (curState, charData) pairs) carrying the real compositions.

    Some records nest multiple blocks recursively; rather than chase a graph
    that varies per script, this extracts the first cleanly-formed nested
    terminal block (the common high-value case: a base letter's full accent
    set) and is strictly bounded by record_end so it can never read into the
    next record. curStateRange/deltaMultiplier are 0/1 in every observed
    layout, so single-state coverage is assumed. Records whose nested block is
    not a simple terminal are skipped with a warning rather than guessed.
    """

    entries = []
    offset = record_offset + 8

    while offset + 8 <= record_end:
        cur_state_start = struct.unpack_from('<H', data, offset)[0]
        if cur_state_start == _OUTPUT_EMPTY_B:
            offset += 8
            break
        char_data = struct.unpack_from('<H', data, offset + 4)[0]
        if char_data not in (_OUTPUT_EMPTY_A, _OUTPUT_EMPTY_B):
            entries.append((cur_state_start, char_data))
        offset += 8

    if offset + 8 <= record_end:
        n_zero, n_next, n_count, n_fmt = struct.unpack_from('<HHHH', data, offset)
        if n_fmt == _TERMINAL_ENTRY_FORMAT and offset + 8 + 4 * n_count <= record_end:
            for entry_index in range(n_count):
                cur_state, char_data = struct.unpack_from(
                    '<HH', data, offset + 8 + 4 * entry_index
                )
                entries.append((cur_state, char_data))
        else:
            warn(
                'uchr',
                f'state record[{record_index}] format-2 nested block not a simple '
                f'terminal (fmt=0x{n_fmt:04x}); partial decode'
            )

    return entries


def _resolve_char_data(
    char_data: int,
    sequences: 'list[str]',
    sequence_indices_active: bool,
) -> str:
    """Turn a state-entry charData into an output string.

    A 0x8000-flagged value is a sequence index only when sequence_indices_active
    (the layout declares maxOutputCharLength >= 2). Otherwise the high bit is
    part of a literal codepoint. Values with both high bits set (0xC000) are
    always literal high codepoints.
    """

    if char_data in (_OUTPUT_EMPTY_A, _OUTPUT_EMPTY_B):
        return ''
    if sequence_indices_active and (char_data & _FLAG_TEST_MASK) == _FLAG_SEQUENCE:
        seq_index = char_data & _INDEX_MASK
        if 0 <= seq_index < len(sequences):
            return sequences[seq_index]
        warn('uchr', f'sequence index {seq_index} out of range')
        return ''
    return chr(char_data)


def _build_dead_states(
    layout: Layout,
    state_records: 'list[dict]',
    sequences: 'list[str]',
    sequence_indices_active: bool,
    terminators: 'list[str]',
) -> None:
    """Create DeadState objects and fill their compositions and terminators.

    A record is a dead key when zeroChar is 0xFFFF. Such a record activates the
    state given by its 'next' field (states are 1-based). Compositions for a
    state live in OTHER records' terminal entries keyed by that state number: an
    entry (curState=S, charData=C) in a record whose zeroChar is base B means
    "state S then base B yields C". The dead state is therefore keyed by its
    state NUMBER, not by the record index, so that compositions (keyed by
    curState) and terminators (indexed by state-1) line up.

    The terminator for state S is terminators[S - 1], the bare output when the
    dead key is followed by a non-composing key (e.g. acute then space -> the
    bare accent).
    """

    base_char_for_record = {}
    for record_index, record in enumerate(state_records):
        zero = record['zero']
        if zero not in (_OUTPUT_EMPTY_A, _OUTPUT_EMPTY_B):
            base_char_for_record[record_index] = chr(zero)

    for record in state_records:
        if record['zero'] != _OUTPUT_EMPTY_B:
            continue
        state_number = record['next']
        if state_number <= 0:
            continue
        name = str(state_number)
        if name in layout.dead_states:
            continue
        dead_state = DeadState(name=name)
        terminator_index = state_number - 1
        if 0 <= terminator_index < len(terminators):
            dead_state.terminator = terminators[terminator_index]
        layout.dead_states[name] = dead_state

    for record_index, record in enumerate(state_records):
        base_char = base_char_for_record.get(record_index)
        if base_char is None:
            continue
        for cur_state, char_data in record['entries']:
            dead_state = layout.dead_states.get(str(cur_state))
            if dead_state is None:
                continue
            dead_state.compositions[base_char] = _resolve_char_data(
                char_data, sequences, sequence_indices_active
            )


def _populate_keys(
    layout: Layout,
    data: bytes,
    char_tables: 'list[tuple[int, int]]',
    plane_tables: 'dict',
    state_records: 'list[dict]',
    sequences: 'list[str]',
    state_indices_active: bool,
    sequence_indices_active: bool,
) -> None:
    """Fill layout.keys, one modifier plane at a time.

    Each plane (plain / shift / option / shift+option) is resolved to its
    physical char table via keyModifiersToTableNum, so the assignment is read
    from the layout rather than assumed from table order.
    """

    for modifier_state, table_index in plane_tables.items():
        table_offset, table_size = char_tables[table_index]

        for virtual_key in range(table_size):
            entry = struct.unpack_from('<H', data, table_offset + 2 * virtual_key)[0]
            key_output = _entry_to_key_output(
                entry, state_records, sequences,
                state_indices_active, sequence_indices_active, virtual_key,
            )
            if key_output is None:
                continue
            layout.keys.setdefault(virtual_key, {})[modifier_state] = key_output


def _entry_to_key_output(
    entry: int,
    state_records: 'list[dict]',
    sequences: 'list[str]',
    state_indices_active: bool,
    sequence_indices_active: bool,
    virtual_key: int,
) -> 'KeyOutput | None':
    """Resolve one char-table entry into a KeyOutput (or None for empty).

    The two index flags are governed independently:

      0x4000 (state index) is honored when state_indices_active, i.e. the layout
      has state records. Single-character layouts with dead keys (German,
      Polish, Turkish, ...) rely on this even though they have no sequences.

      0x8000 (sequence index) is honored only when sequence_indices_active, i.e.
      maxOutputCharLength >= 2. Otherwise the high bit belongs to a literal
      codepoint (Rejang U+A946, Meetei Mayek U+ABxx).

    The 0xC000 (both-bits) case is always a literal high codepoint (ligatures,
    Apple private-use). When a flag is active but the index lands out of range,
    the entry falls back to a literal codepoint and is surfaced via a warning,
    since that indicates a real structural surprise rather than ordinary data.
    """

    if entry in (_OUTPUT_EMPTY_A, _OUTPUT_EMPTY_B):
        return None

    masked = entry & _FLAG_TEST_MASK

    if masked == _FLAG_SEQUENCE and sequence_indices_active:
        seq_index = entry & _INDEX_MASK
        if 0 <= seq_index < len(sequences):
            return KeyOutput(kind=OutputKind.CHARS, output=sequences[seq_index])
        warn('uchr', f'{vk_name(virtual_key)} sequence index {seq_index} out of range')
        return KeyOutput(kind=OutputKind.CHARS, output=chr(entry))

    if masked == _FLAG_STATE and state_indices_active:
        state_index = entry & _INDEX_MASK
        if state_index < len(state_records):
            record = state_records[state_index]
            if record['zero'] == _OUTPUT_EMPTY_B:
                # A dead-key record activates the state given by its 'next'
                # field; dead states are keyed by that state number so that
                # compositions and terminators line up.
                #
                # next == 0 means "no new state from ground": these are
                # chain-continuation dead keys that only act when another state
                # is already active (e.g. Tibetan subjoined-consonant keys that
                # operate inside state N). From the ground state they enter no
                # usable dead state, so emit nothing rather than a DEAD cell that
                # names a state _build_dead_states intentionally does not create.
                # _build_dead_states applies the same next > 0 guard, so this
                # keeps the two in agreement (Layout.validate() checks it).
                if record['next'] <= 0:
                    return None
                return KeyOutput(
                    kind=OutputKind.DEAD,
                    dead_state_name=str(record['next']),
                )
            return KeyOutput(kind=OutputKind.CHARS, output=chr(record['zero']))
        warn('uchr', f'{vk_name(virtual_key)} state index {state_index} out of range')
        return KeyOutput(kind=OutputKind.CHARS, output=chr(entry))

    return KeyOutput(kind=OutputKind.CHARS, output=chr(entry))


# Virtual keycodes for letter keys with a clear plain/shift distinction, used
# to sanity-check that the resolved planes are not degenerate. These are the
# macOS virtual keycodes for A, S, D, F (all lowercase->uppercase under shift).
_SANITY_LETTER_VKS = (0x00, 0x01, 0x02, 0x03)


def _modifier_reach_by_table(table_map: 'list[int]') -> 'dict':
    """Invert keyModifiersToTableNum: table index -> list of modifier indices.

    Lets the sanity check ask, for a given char table, which modifier
    combinations reach it (so cmd/control-only tables can be treated as
    non-character planes).
    """

    reach = {}
    for modifier_index, table_index in enumerate(table_map):
        reach.setdefault(table_index, []).append(modifier_index)
    return reach


# Control characters and the empty string never count as "printable content"
# that a plane would be expected to carry.
def _is_printable_output(text: str) -> bool:
    """True if text has at least one non-control, non-space printable char."""

    for ch in text:
        if ch.isprintable() and not ch.isspace():
            return True
    return False


def _verify_plane_assignment(
    layout: Layout,
    data: bytes,
    char_tables: 'list[tuple[int, int]]',
    plane_tables: 'dict',
    table_map: 'list[int]',
    state_records: 'list[dict]',
    sequences: 'list[str]',
    state_indices_active: bool,
    sequence_indices_active: bool,
) -> None:
    """Sanity-check the resolved modifier planes against the raw tables.

    The canonical plane query indices are empirical, not from a committed spec,
    so this converts "verified on a sample" into a per-input self-check. Two
    things are checked, both warn-only (never abort a parse):

    1. Plane distinctness: for several letter keys, the plain plane and the
       shift plane should differ (lowercase vs uppercase). If they are identical
       the plane resolution likely landed on the wrong tables.

    2. Unreached printable content: every char table NOT chosen for one of the
       four planes is scanned. If such a table carries printable output that is
       absent from all four planes, it is surfaced, since that means the port
       would drop characters a user could type. (Caps-lock duplicates of shift,
       cmd+option duplicates of option, and the control-character plane are
       expected here and do not trigger this, because their printable output is
       already present in a plane or is purely control codes.)
    """

    plain_table = plane_tables.get(ModifierState.PLAIN)
    shift_table = plane_tables.get(ModifierState.SHIFT)

    # Check 1: plain and shift should differ for ordinary letter keys.
    if plain_table is not None and shift_table is not None and plain_table != shift_table:
        differing = 0
        comparable = 0
        for vk in _SANITY_LETTER_VKS:
            plain_ko = layout.keys.get(vk, {}).get(ModifierState.PLAIN)
            shift_ko = layout.keys.get(vk, {}).get(ModifierState.SHIFT)
            if plain_ko is None or shift_ko is None:
                continue
            if plain_ko.kind is not OutputKind.CHARS or shift_ko.kind is not OutputKind.CHARS:
                continue
            comparable += 1
            if plain_ko.output != shift_ko.output:
                differing += 1
        if comparable and differing == 0:
            warn(
                'uchr',
                'plane sanity: plain and shift planes are identical for all '
                'sampled letter keys; modifier-plane resolution may be wrong'
            )
    elif plain_table is not None and shift_table is not None and plain_table == shift_table:
        warn(
            'uchr',
            'plane sanity: plain and shift resolved to the same table; '
            'modifier-plane resolution may be wrong'
        )

    # Check 2: scan tables NOT chosen for a plane. A table reachable only with
    # cmd or control set is not a character plane on Linux (those are shortcut
    # modifiers), so its content is expected to be unmapped and is ignored here.
    # A table reachable WITHOUT cmd/control that carries printable output absent
    # from all four planes is a real gap: the port would drop characters the
    # user can type with shift/option/companion alone. The most consequential
    # case is native-script layouts (Greek, Cyrillic, Tibetan, ...) whose primary
    # script sits behind the 0x02 companion bit; the fixed plane query indices,
    # which are correct for Latin layouts, resolve those to a Latin fallback
    # table and miss the native alphabet. This is a known limitation of the
    # fixed-index plane resolution, surfaced rather than hidden.
    covered = set()
    for plane in plane_tables:
        for vk in range(char_tables[plane_tables[plane]][1]):
            ko = layout.keys.get(vk, {}).get(plane)
            if ko is not None and ko.kind is OutputKind.CHARS and ko.output:
                covered.add(ko.output)

    # Characters reachable via dead-key composition are NOT lost by the four-
    # plane port: they go into the XCompose file and remain typeable as accent
    # sequences. So a character absent from the planes but present as a
    # composition result is fine; only a character on NO plane AND in NO
    # composition is a genuine gap (e.g. a character on a Caps+Option layer the
    # four-plane model does not capture). We separate the two so the warning
    # reflects reality instead of alarming about compose-available characters.
    composable = set()
    for dead_state in layout.dead_states.values():
        for result in dead_state.compositions.values():
            if result:
                composable.add(result)

    chosen_tables = set(plane_tables.values())
    reach = _modifier_reach_by_table(table_map)
    missed_outputs = set()
    for table_index, (table_offset, table_size) in enumerate(char_tables):
        if table_index in chosen_tables:
            continue
        reaching_indices = reach.get(table_index, [])
        # Benign if every reaching modifier index has cmd (0x01) or control
        # (0x10) set; those are not character planes.
        if reaching_indices and all(
            (index & _MOD_CMD_OR_CONTROL) for index in reaching_indices
        ):
            continue
        for vk in range(table_size):
            entry = struct.unpack_from('<H', data, table_offset + 2 * vk)[0]
            ko = _entry_to_key_output(
                entry, state_records, sequences,
                state_indices_active, sequence_indices_active, vk,
            )
            if ko is None or ko.kind is not OutputKind.CHARS or not ko.output:
                continue
            if not _is_printable_output(ko.output):
                continue
            if ko.output not in covered:
                missed_outputs.add(ko.output)

    # Split: compose-available (informational) vs genuinely unreachable (real).
    via_compose = {ch for ch in missed_outputs if ch in composable}
    unreachable = missed_outputs - composable

    if via_compose:
        sample = ' '.join(sorted(via_compose)[:12])
        dbg(
            'uchr',
            f'plane note: {len(via_compose)} character(s) are not on a direct '
            f'plane but remain typeable via dead-key composition (XCompose): '
            f'{sample}'
        )

    if unreachable:
        sample = ' '.join(sorted(unreachable)[:12])
        severity = 'major' if len(unreachable) >= _PLANE_GAP_MAJOR else 'minor'
        warn(
            'uchr',
            f'plane sanity ({severity}): {len(unreachable)} printable '
            f'output(s) are on NO direct plane and in NO composition, so the '
            f'four-plane port cannot type them (e.g. a Caps+Option layer): '
            f'{sample}'
        )


# End of file #
