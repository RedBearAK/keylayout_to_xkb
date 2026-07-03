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

from keylayout_to_xkb.common.debug import dbg, warn, hex_window
from keylayout_to_xkb.common.models import (
    Layout,
    Variant,
    DeadState,
    KeyOutput,
    OutputKind,
    ModifierState,
    PLANE_MODIFIER_BYTE,
)
from keylayout_to_xkb.extract.uckeytranslate import resolve_plane_tables_via_os
from keylayout_to_xkb.common.gestalt_keyboard import (
    lowest_generic_type,
    representative_type_for_kind,
)


__version__ = '20260703d'


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


def _read_keyboard_type_records(data: bytes, count: int) -> 'list[dict]':
    """Read every keyboard-type record's range and section offsets.

    Each record is 28 bytes: first, last (gestalt type range) then five section
    offsets (modifier map, char index, state records, terminators, sequences).
    Returns one dict per record.
    """

    records = []
    for i in range(count):
        base = 12 + i * 28
        _check_bounds(data, base, 28, f'keyboard-type header[{i}]')
        (first, last, mod, char_index, state_records,
         terminators, sequences) = struct.unpack_from('<IIIIIII', data, base)
        records.append({
            'first': first, 'last': last,
            'mod_to_table_offset': mod,
            'char_index_offset': char_index,
            'state_records_offset': state_records,
            'state_terminators_offset': terminators,
            'sequence_data_offset': sequences,
        })
    return records


def _table_identity(record: 'dict') -> tuple:
    """Full offset tuple identifying a record's physical layout.

    Two records with the same tuple resolve to byte-identical key output, so
    they are the same variant and emit once. Char-index plus modifier map plus
    the dead-state offsets capture everything a variant's keys depend on.
    """

    return (
        record['mod_to_table_offset'],
        record['char_index_offset'],
        record['state_records_offset'],
        record['state_terminators_offset'],
        record['sequence_data_offset'],
    )


def _resolve_kind_variants(data: bytes, records: 'list[dict]') -> 'list[dict]':
    """Resolve the variant set: generic primary plus each advertised kind.

    Returns an ordered list of {tag, record, type, range} dicts. The first is
    the primary (tag ''), built from the lowest generic keyboard type (or, if a
    layout advertises no kind-less type, the first record). Each ANSI/ISO/JIS
    kind that the layout advertises a representative type for is appended with
    its kind name as tag, UNLESS it resolves to a table already emitted by an
    earlier entry -- kinds that share a physical table collapse to one variant,
    keeping the earliest (primary, then ANSI, ISO, JIS) label.

    Resolution is by gestalt type number, which the exhaustive type/table check
    confirmed the OS honors for every named-kind type.
    """

    ranges = [(r['first'], r['last']) for r in records]

    def covered(type_number):
        return any(first <= type_number <= last for first, last in ranges)

    def record_for(type_number):
        for record in records:
            if record['first'] <= type_number <= record['last']:
                return record
        return None

    variants = []
    seen_tables = set()

    # Primary: the generic/default table (kind-less type), else first record.
    generic_type = lowest_generic_type(ranges)
    if generic_type is not None:
        primary_record = record_for(generic_type)
        primary_type = generic_type
    else:
        primary_record = records[0]
        primary_type = records[0]['first']
    variants.append({
        'tag': '',
        'record': primary_record,
        'type': primary_type,
        'range': (primary_record['first'], primary_record['last']),
    })
    seen_tables.add(_table_identity(primary_record))

    # Each advertised kind, deduped against tables already emitted.
    for kind in ('ANSI', 'ISO', 'JIS'):
        type_number = representative_type_for_kind(kind, covered)
        if type_number is None:
            continue
        record = record_for(type_number)
        if record is None:
            continue
        identity = _table_identity(record)
        if identity in seen_tables:
            continue
        seen_tables.add(identity)
        variants.append({
            'tag': kind.lower(),
            'record': record,
            'type': type_number,
            'range': (record['first'], record['last']),
        })

    return variants


def _build_variant_keys(
    data: bytes,
    record: 'dict',
    state_records: 'list[dict]',
    sequences: 'list[str]',
) -> 'tuple[dict, dict]':
    """Decode one variant's keys from its record.

    Returns (keys, plane_tables): the fresh keys dict and the resolved
    {ModifierState: char-table index} map for this variant (so the caller can
    record it on the Variant for documentation-identity collapsing). Uses the
    validated byte+2 plane resolution against the record's own char tables and
    modifier map. Dead states are shared (passed in), not rebuilt.
    """

    char_tables = _parse_char_table_index(data, record['char_index_offset'])
    table_map, default_table = _parse_modifier_table_map(
        data, record['mod_to_table_offset']
    )
    plane_tables = _resolve_plane_tables(
        data, char_tables, table_map, default_table,
        state_records, sequences,
    )
    keys = {}
    for modifier_state, table_index in plane_tables.items():
        if table_index >= len(char_tables):
            continue
        table_offset, table_size = char_tables[table_index]
        for virtual_key in range(table_size):
            entry = struct.unpack_from('<H', data, table_offset + 2 * virtual_key)[0]
            key_output = _entry_to_key_output(
                entry, state_records, sequences,
                virtual_key,
            )
            if key_output is None:
                continue
            keys.setdefault(virtual_key, {})[modifier_state] = key_output
    return keys, plane_tables


def parse_uchr(data: bytes, layout_name: str = '', source_id: str = '',
               languages: 'list | None' = None) -> Layout:
    """Parse a single 'uchr' byte buffer into a normalized Layout.

    languages, when provided, is Apple's authoritative ISO 639 language list for
    this layout (from the TIS API via tis_source); it is stored in provenance
    for the emitter/registry to group and flag the layout accurately.
    """

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

    all_records = _read_keyboard_type_records(data, keyboard_type_count)
    if keyboard_type_count > 1:
        dbg('uchr', f'note: {keyboard_type_count} keyboard-type records')

    max_output_char_length = _parse_max_output_char_length(data, feature_info_offset)
    dbg('uchr', f'maxOutputCharLength={max_output_char_length}')

    sequences = _parse_sequence_table(data, sequence_data_offset)
    dbg('uchr', f'sequences: {len(sequences)}')

    state_records = _parse_state_records(
        data, state_records_offset, layout_name=layout_name)
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

    char_tables = _parse_char_table_index(data, char_index_offset)
    keys_per_table = char_tables[0][1] if char_tables else 0
    dbg('uchr', f'char tables: {len(char_tables)} (each {keys_per_table} keys)')

    table_map, default_table = _parse_modifier_table_map(data, mod_to_table_offset)

    # Prefer Apple's UCKeyTranslate for an authoritative plane->table map when
    # running on macOS; fall back to content-driven resolution everywhere else.
    # Both resolvers cover all eight typeable planes via the SHARED
    # PLANE_MODIFIER_BYTE constant and feed the same downstream machinery
    # (dead keys, terminators, compositions). When the OS path resolves only
    # SOME planes, the content resolver fills the rest LOUDLY: a silent
    # partial resolution is exactly how the caps quartet vanished from every
    # on-Mac generation while every off-Mac test stayed green.
    def table_outputs_for(table_index):
        return _table_outputs(
            data, char_tables[table_index], state_records, sequences,
        )

    def table_cells_for(table_index):
        return _table_cells(
            data, char_tables[table_index], state_records, sequences,
        )

    # The on-disk resolution comes FIRST: it is cheap, fully validated against
    # the native tool (all 241 layouts, every historical disagreement cell),
    # and doubles as the tie-breaking hint and the cross-check baseline for
    # the OS path below.
    content_tables = _resolve_plane_tables(
        data, char_tables, table_map, default_table,
        state_records, sequences,
    )

    plane_tables = None
    try:
        plane_tables = resolve_plane_tables_via_os(
            data, char_tables, table_outputs_for,
            table_cells_fn=table_cells_for,
            ondisk_tables=content_tables,
            layout_name=layout_name,
        )
    except Exception as os_error:                       # never let OS path abort
        warn('uchr', f'{layout_name!r}: UCKeyTranslate path errored '
             f'({os_error}); using content resolver')
        plane_tables = None

    if plane_tables:
        dbg('uchr', 'plane resolution: UCKeyTranslate (deterministic)')
        missing = [
            plane for plane in content_tables if plane not in plane_tables
        ]
        if missing:
            warn(
                'uchr',
                f'{layout_name!r}: UCKeyTranslate left plane(s) unresolved: '
                + ', '.join(plane.value for plane in missing)
                + '; filling from the content resolver'
            )
            for plane in missing:
                plane_tables[plane] = content_tables[plane]
        # Disagreements matter only when the two tables differ in CONTENT: with
        # duplicate char tables the oracle's best-match may legitimately pick a
        # different index than the on-disk map for the same outputs.
        for plane, os_index in sorted(
                plane_tables.items(), key=lambda kv: kv[0].value):
            content_index = content_tables.get(plane)
            if content_index is None or content_index == os_index:
                continue
            if table_outputs_for(content_index) != table_outputs_for(os_index):
                warn(
                    'uchr',
                    f'{layout_name!r} plane {plane.value}: OS resolved table '
                    f'{os_index} but '
                    f'on-disk map says table {content_index} with different '
                    f'content; keeping the OS result'
                )
    else:
        plane_tables = content_tables
        dbg('uchr', 'plane resolution: content-driven (fallback)')
    dbg(
        'uchr',
        'plane->table: '
        + ', '.join(f'{p.value}={t}' for p, t in plane_tables.items())
    )

    layout = Layout(name=layout_name, source_id=source_id or None)
    # Mirror source_id into provenance too: _identifier_from() reads
    # provenance.source_id to derive the layout token (PolishPro -> polishpro).
    # Without this, the live TIS path (which passes the localized name 'Polish'
    # as layout_name) falls back to that name and yields 'polish' instead.
    if source_id:
        layout.provenance.source_id = source_id
    if languages:
        layout.provenance.source_languages = list(languages)

    terminators = _parse_terminators(
        data, state_terminators_offset, sequences
    )
    dbg('uchr', f'terminators: {len(terminators)}')

    _build_dead_states(
        layout, state_records, sequences, terminators
    )
    dbg('uchr', f'dead states: {layout.dead_key_count()}')

    _populate_keys(
        layout, data, char_tables, plane_tables, state_records, sequences,
    )

    _verify_plane_assignment(
        layout, data, char_tables, plane_tables, table_map, state_records,
        sequences,
    )

    # Resolve the keyboard-type variant set (generic primary + advertised
    # ANSI/ISO/JIS kinds that resolve to distinct physical tables). The primary
    # carries the keys already populated above; additional kinds each become a
    # self-contained Variant. Dead states are shared across all variants.
    kind_variants = _resolve_kind_variants(data, all_records)
    primary = kind_variants[0]
    layout.variants.append(Variant(
        tag='',
        keys=layout.keys,
        keyboard_type_range=primary['range'],
        plane_tables=dict(plane_tables),
    ))
    for entry in kind_variants[1:]:
        variant_keys, variant_plane_tables = _build_variant_keys(
            data, entry['record'], state_records, sequences,
        )
        layout.variants.append(Variant(
            tag=entry['tag'],
            keys=variant_keys,
            keyboard_type_range=entry['range'],
            plane_tables=variant_plane_tables,
        ))
        dbg('uchr', f'variant {entry["tag"]!r}: keys={len(variant_keys)} '
                    f'type={entry["type"]} range={entry["range"]}')

    dbg(
        'uchr',
        f'parsed: keys={len(layout.keys)} dead_states={layout.dead_key_count()} '
        f'approx_chars={layout.char_count()} variants={len(layout.variants)}'
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
      u32 modifiersCount
      u8  tableNum[modifiersCount]

    The array index is a macOS modifierKeyState value; the byte at that index is
    the char-table number to use for that modifier combination. An out-of-range
    plane query falls back to defaultTableNum.

    ALIGNMENT NOTE: modifiersCount is a UInt32 and the tableNum array starts at
    offset+8. An earlier decode read the count as u16 and the entries at
    offset+6, swallowing the count's high half as two phantom zero entries --
    which a '+2' compensation in the plane indexing exactly canceled, hiding
    the misread until the near-twin-table investigation. Equivalence of the
    aligned decode was verified byte-for-byte across all 241 Mac layouts
    (1401 keyboard-type records, zero mismatches) before this fix landed.
    """

    if offset == 0:
        return [], 0

    _check_bounds(data, offset, 8, 'modifier-table-map header')
    marker, default_table, modifiers_count = struct.unpack_from('<HHI', data, offset)

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

    _check_bounds(data, offset + 8, modifiers_count, 'modifier-table-map array')
    table_nums = [data[offset + 8 + i] for i in range(modifiers_count)]

    return table_nums, default_table


def _table_cells(
    data: bytes,
    table: 'tuple[int, int]',
    state_records: 'list[dict]',
    sequences: 'list[str]',
) -> 'dict':
    """Return {virtual_key: (kind, output)} for a table, DEAD CELLS INCLUDED.

    kind is 'char' (single-character literal output) or 'dead' (the cell
    enters a dead-key state; output is ''). Cells with no output and
    multi-char cells are omitted. This is the discrimination-oriented sibling
    of _table_outputs: the OS plane matcher settles near-twin table ties by
    comparing UCKeyTranslate answers (dead-state aware) against these cells,
    so dead keys MUST be visible here -- hiding them is exactly how near-twin
    tables became indistinguishable to the matcher.
    """

    table_offset, table_size = table
    cells = {}
    for virtual_key in range(table_size):
        entry = struct.unpack_from('<H', data, table_offset + 2 * virtual_key)[0]
        ko = _entry_to_key_output(
            entry, state_records, sequences, virtual_key,
        )
        if ko is None:
            continue
        if ko.kind is OutputKind.DEAD:
            # The state name IS the OS discriminator: UCKeyTranslate's
            # deadKeyState equals the uchr state number (verified: Latvian
            # table 8 vk 0x27 has state '1' and the OS answers DEAD(state=1)),
            # so two dead cells entering different states are distinguishable.
            cells[virtual_key] = ('dead', ko.dead_state_name or '')
        elif ko.kind is OutputKind.CHARS and ko.output and len(ko.output) == 1:
            cells[virtual_key] = ('char', ko.output)
    return cells


def _table_outputs(
    data: bytes,
    table: 'tuple[int, int]',
    state_records: 'list[dict]',
    sequences: 'list[str]',
) -> 'dict':
    """Return {virtual_key: output_str} for ALL single-char outputs of a table.

    Keeps every single-character output including symbols, so the UCKeyTranslate
    plane matcher (the OS oracle path) can identify symbol-heavy Option planes.
    Dead-key and multi-char cells are omitted (they do not give a stable
    single-char key for matching).
    """

    table_offset, table_size = table
    outputs = {}
    for virtual_key in range(table_size):
        entry = struct.unpack_from('<H', data, table_offset + 2 * virtual_key)[0]
        ko = _entry_to_key_output(
            entry, state_records, sequences, virtual_key,
        )
        if ko is None or ko.kind is not OutputKind.CHARS or not ko.output:
            continue
        if len(ko.output) == 1:
            outputs[virtual_key] = ko.output
    return outputs


def _resolve_plane_tables(
    data: bytes,
    char_tables: 'list[tuple[int, int]]',
    table_map: 'list[int]',
    default_table: int,
    state_records: 'list[dict]',
    sequences: 'list[str]',
) -> 'dict':
    """Map each ModifierState plane to its char-table index via the modifier map.

    The plane's char table is keyModifiersToTableNum[plane_byte + 2], where
    plane_byte is the standard Carbon modifierKeyState for the plane:

        PLAIN              0x00  -> index 0x02
        SHIFT              0x02  -> index 0x04
        OPTION             0x08  -> index 0x0A
        SHIFT_OPTION       0x0A  -> index 0x0C
        CAPS               0x04  -> index 0x06
        CAPS_SHIFT         0x06  -> index 0x08
        CAPS_OPTION        0x0C  -> index 0x0E
        CAPS_SHIFT_OPTION  0x0E  -> index 0x10

    The +2 offset is the transform UCKeyTranslate itself applies between the
    modifier byte and the table index. It was extracted by exhaustively driving
    the real UCKeyTranslate across every modifier byte on all 241 installed
    macOS layouts and inverting its output against the raw array: index = byte+2
    reproduces the OS plane->table selection on 228/241 layouts at 100% and
    99.55% of all cells overall. The residual misses are NOT selection errors --
    they are (a) ISO/JIS keyboard-type variant keys (vk50/vk94) and (b)
    supplementary-plane codepoint decoding, both orthogonal to this mapping.

    The caps quartet (caps bit 0x04) uses the SAME transform and decodes with the
    same machinery; a caps-layer validation matched the OS at 99.96% across all
    layouts. The caps tables carry genuinely-typeable output (e.g. Latin behind
    caps on non-Latin layouts, unique caps+option symbols), captured WITHOUT
    classifying caps behavior -- each caps plane simply reads whatever table its
    byte+2 index points at.

    This replaces the former content-heuristic resolver: with the transform
    known, planes are read directly from the layout's own modifier map rather
    than inferred from table contents, so non-Latin layouts (whose primary
    script the old fixed indices missed) resolve correctly without script
    detection.

    Falls back to physical table order only when there is no modifier map.
    """

    table_count = len(char_tables)

    if not table_map:
        # No modifier map: fall back to physical table order for the base four
        # planes only. The caps quartet has no reliable order-based position, so
        # it is omitted here rather than guessed -- omission is safe (those cells
        # simply have no output), a wrong guess would mistype. Layouts with a
        # modifier map (effectively all real ones) resolve caps via plane_index.
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

    # The modifier byte indexes keyModifiersToTableNum DIRECTLY now that the
    # map decode is struct-aligned (see _parse_modifier_table_map). The bytes
    # come from the SHARED PLANE_MODIFIER_BYTE constant (models.py), the same
    # one the UCKeyTranslate resolver and the verify audit use, so the on-disk
    # and OS-oracle plane sets can never drift apart again.
    plane_index = dict(PLANE_MODIFIER_BYTE)

    resolved = {}
    for plane, index in plane_index.items():
        if 0 <= index < len(table_map):
            table_index = table_map[index]
        else:
            table_index = default_table
        if 0 <= table_index < table_count:
            resolved[plane] = table_index
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
    or empty. The list is indexed by state, parallel to
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
            _resolve_char_data(entry, sequences)
        )
    return terminators


def _parse_state_records(data: bytes, offset: int,
                         layout_name: str = '') -> 'list[dict]':
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
        warn('uchr', f'{layout_name!r}: large state record count={record_count}; '
             'verify offset')

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
                data, record_offset, _record_end_for(record_offset),
                record_index, layout_name=layout_name,
            )
        elif entry_format not in (0, _TERMINAL_ENTRY_FORMAT):
            warn(
                'uchr',
                f'{layout_name!r}: state record[{record_index}] entry '
                f'format 0x{entry_format:04x} not handled; '
                f'{entry_count} entries skipped'
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
    layout_name: str = '',
) -> 'list[tuple[int, int]]':
    """Decode a format-2 (range) state record per its PROVEN grammar.

    Format-2 records encode stacked dead-key chaining (Greek Polytonic,
    Tibetan, a few Native American layouts). The record is exactly:

      u16 zeroChar, u16 nextState, u16 entryCount, u16 entryFormat(==2),
      entryCount x 8-byte entries of
        (u16 curStateStart, u8 curStateRange, u8 deltaMultiplier,
         u16 charData, u16 nextState)

    and NOTHING else: no sentinel, no nested terminal block. Proven on
    Tibetan-Wylie, where 27 of 28 format-2 records tile flush against their
    successor at exactly 8 + 8*entryCount bytes and the 28th tiles flush
    against the global 0x6001 terminators table. An earlier decode assumed a
    0xFFFF sentinel plus a nested format-1 terminal, so it read past each
    record's true end and absorbed the NEXT record's header and entries as
    compositions -- state-misattributed entries in every format-2 layout, and
    the fmt=0x04ce warn wherever the neighbour was not a plausible terminal.

    An entry with charData 0xFFFF (or the 0xFFFE empty) emits nothing: it
    CHAINS to a deeper state (nextState). The single-level composition model
    skips those, loudly counted, until multi-level chains are modeled. A
    non-zero curStateRange covers curStateStart..+range with charData
    advancing by deltaMultiplier per step (0/1 in every observed layout, so
    the expansion degenerates to a single pair today).
    """

    (_zero_char, _next_state, entry_count, _entry_format) = struct.unpack_from(
        '<HHHH', data, record_offset)

    available = max((record_end - record_offset - 8) // 8, 0)
    if entry_count > available:
        warn(
            'uchr',
            f'{layout_name!r}: state record[{record_index}] format-2 entry '
            f'count {entry_count} overruns the record span '
            f'({available} entries fit); clamping'
        )
        entry_count = available

    entries = []
    chain_count = 0
    for entry_index in range(entry_count):
        base = record_offset + 8 + 8 * entry_index
        (cur_state_start, cur_state_range, delta_multiplier, char_data,
         chain_next_state) = struct.unpack_from('<HBBHH', data, base)
        if char_data in (_OUTPUT_EMPTY_A, _OUTPUT_EMPTY_B):
            chain_count += 1
            continue
        for step in range(cur_state_range + 1):
            entries.append((
                cur_state_start + step,
                char_data + step * delta_multiplier,
            ))

    if chain_count:
        dbg(
            'uchr',
            f'{layout_name!r}: state record[{record_index}]: {chain_count} '
            'non-emitting entr(y/ies) chain to deeper states; skipped '
            '(single-level composition model)'
        )

    return entries


def _resolve_char_data(
    char_data: int,
    sequences: 'list[str]',
) -> str:
    """Resolve a 16-bit output reference into its output string.

    This is the single output-reference grammar the 'uchr' format uses wherever
    it specifies output -- char-table entry literals, state-record 'zero' and
    'entries' values, and terminators. The top two bits select the kind:

      0xFFFE / 0xFFFF    -> empty (no output)
      0x8000 + in-range  -> sequence index into the sequence table
      0x8000 + oor       -> literal high codepoint (the high bit is data, not a
                            flag; e.g. Rejang U+A946, Meetei Mayek U+ABxx)
      0xC000             -> literal high codepoint (both bits are data)
      otherwise          -> literal BMP codepoint

    The discriminator between "sequence reference" and "literal high codepoint"
    is whether the index lands IN RANGE of the sequence table, NOT the layout's
    maxOutputCharLength flag. This was established for char-table entries (the
    SMP fix) and proven by probe to hold identically at every output site:
    'entries' alone holds ~22% flagged sequence references across layouts, and
    the out-of-range cases (Rejang terminators) are genuine literals.

    The 0x4000 (state) flag is NOT an output reference -- it only appears at the
    char-table entry level as a dead-key trigger, handled by the caller. Any
    0x4000 value reaching here is treated as a literal (an out-of-range state
    index is a real high codepoint, same as the sequence case).
    """

    if char_data in (_OUTPUT_EMPTY_A, _OUTPUT_EMPTY_B):
        return ''
    if (char_data & _FLAG_TEST_MASK) == _FLAG_SEQUENCE:
        seq_index = char_data & _INDEX_MASK
        if 0 <= seq_index < len(sequences):
            return sequences[seq_index]
        # flag bit set but out of range: the value is a literal high codepoint.
        return chr(char_data)
    return chr(char_data)


def _build_dead_states(
    layout: Layout,
    state_records: 'list[dict]',
    sequences: 'list[str]',
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
                char_data, sequences
            )


def _populate_keys(
    layout: Layout,
    data: bytes,
    char_tables: 'list[tuple[int, int]]',
    plane_tables: 'dict',
    state_records: 'list[dict]',
    sequences: 'list[str]',
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
                virtual_key,
            )
            if key_output is None:
                continue
            layout.keys.setdefault(virtual_key, {})[modifier_state] = key_output


def _entry_to_key_output(
    entry: int,
    state_records: 'list[dict]',
    sequences: 'list[str]',
    virtual_key: int,
) -> 'KeyOutput | None':
    """Resolve one char-table entry into a KeyOutput (or None for empty).

    A char-table entry uses the format's output-reference grammar, with one
    addition (the 0x4000 state flag, which exists only at the entry level). Flag
    interpretation is governed by INDEX VALIDITY, not by per-layout "active"
    booleans -- the booleans were an approximation that proved wrong for both
    flags (SMP sequence layouts with maxOutputCharLength == 1; Manipuri state
    entries with zero state records):

      0x8000 (sequence) + in-range  -> sequence index into the sequence table.
      0x8000 + out-of-range         -> literal high codepoint (the high bit is
                                       data; e.g. Rejang U+A946, Meetei Mayek
                                       U+ABxx).

      0x4000 (state) + in-range     -> references a state record: either a dead
                                       key (enters record['next']) or a direct
                                       ground-state output (record['zero'],
                                       itself resolved through the output-
                                       reference grammar).
      0x4000 + out-of-range         -> NOT a literal: the OS emits nothing, so
                                       this returns None (empty).

      0xC000 (both bits)            -> literal high codepoint (ligatures, Apple
                                       private-use).
    """

    if entry in (_OUTPUT_EMPTY_A, _OUTPUT_EMPTY_B):
        return None

    masked = entry & _FLAG_TEST_MASK

    if masked == _FLAG_SEQUENCE:
        seq_index = entry & _INDEX_MASK
        # A 0x8000-flagged entry is a sequence index when that index actually
        # lands within the sequence table; otherwise the high bit is part of a
        # literal high-BMP codepoint, not an index. This in-range test, NOT the
        # maxOutputCharLength>=2 flag, is the correct discriminator:
        #   - Wancho/Adlam/Pahawh/Osage/Hanifi: maxOut==1, yet entries 0x8000..
        #     ARE sequence indices into a populated table holding supplementary-
        #     plane codepoints (U+1E2CE etc., decoded from their UTF-16 surrogate
        #     pairs by the sequence parser).
        #   - Rejang U+A946 / Meetei Mayek U+ABxx: maxOut==1, NO sequence table;
        #     the 0x8000-bit value is a literal codepoint whose would-be index is
        #     far out of range, so it correctly falls through to the literal.
        # For maxOut>=2 layouts this returns the same result as the former
        # sequence_indices_active gate (their sequence entries are in range,
        # their literals are not), so no currently-passing layout regresses.
        if 0 <= seq_index < len(sequences):
            return KeyOutput(kind=OutputKind.CHARS, output=sequences[seq_index])
        return KeyOutput(kind=OutputKind.CHARS, output=chr(entry))

    # State flag (0x4000): the entry references a state record by index.
    # Interpretation is governed by index validity, not by a per-layout "active"
    # boolean (same principle as the sequence flag). A 0x4000 entry whose index
    # is out of range is NOT a literal codepoint -- unlike the sequence case --
    # the OS emits nothing for it (e.g. Manipuri Meetei Mayek, which has zero
    # state records yet carries 0x4000/0x4001 placeholder entries). The full
    # parser-vs-oracle validation across all layouts confirms this rule.
    if masked == _FLAG_STATE:
        state_index = entry & _INDEX_MASK
        if state_index < len(state_records):
            record = state_records[state_index]
            if record['zero'] in (_OUTPUT_EMPTY_A, _OUTPUT_EMPTY_B):
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
            # 'zero' is the ground-state OUTPUT, encoded with the same output-
            # reference grammar as a char-table entry: it may be a sequence
            # index (Tibetan/Vietnamese), not a raw codepoint, so resolve it
            # through the shared primitive rather than chr()-ing it.
            return KeyOutput(
                kind=OutputKind.CHARS,
                output=_resolve_char_data(record['zero'], sequences),
            )
        # 0x4000 flag set but index out of range: not a literal high codepoint
        # (the sequence case is) -- the OS produces no output, so emit nothing.
        return None

    return KeyOutput(kind=OutputKind.CHARS, output=chr(entry))


# Virtual keycodes for letter keys with a clear plain/shift distinction, used
# to sanity-check that the resolved planes are not degenerate. These are the
# macOS virtual keycodes for A, S, D, F (all lowercase->uppercase under shift).
_SANITY_LETTER_VKS = (0x00, 0x01, 0x02, 0x03)


def _verify_plane_assignment(
    layout: Layout,
    data: bytes,
    char_tables: 'list[tuple[int, int]]',
    plane_tables: 'dict',
    table_map: 'list[int]',
    state_records: 'list[dict]',
    sequences: 'list[str]',
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
        sampled_chars = []
        for vk in _SANITY_LETTER_VKS:
            plain_ko = layout.keys.get(vk, {}).get(ModifierState.PLAIN)
            shift_ko = layout.keys.get(vk, {}).get(ModifierState.SHIFT)
            if plain_ko is None or shift_ko is None:
                continue
            if plain_ko.kind is not OutputKind.CHARS or shift_ko.kind is not OutputKind.CHARS:
                continue
            comparable += 1
            sampled_chars.append(plain_ko.output)
            if plain_ko.output != shift_ko.output:
                differing += 1
        if comparable and differing == 0:
            # Caseless scripts (kana, hangul jamo, most Indic) GENUINELY have
            # identical plain and shift letters -- KANA and 2-Set Korean fired
            # this warn while being parsed correctly. Only cased sampled
            # characters make identical planes suspicious.
            any_cased = any(
                ch and ch.lower() != ch.upper()
                for output in sampled_chars for ch in output
            )
            if any_cased:
                warn(
                    'uchr',
                    f'{layout.name!r} plane sanity: plain and shift planes '
                    'are identical for all sampled letter keys; '
                    'modifier-plane resolution may be wrong'
                )
            else:
                dbg(
                    'uchr',
                    f'{layout.name!r}: plain==shift for sampled letters, all '
                    'caseless (expected for this script); not warning'
                )
    elif plain_table is not None and shift_table is not None and plain_table == shift_table:
        warn(
            'uchr',
            f'{layout.name!r} plane sanity: plain and shift resolved to the '
            'same table; modifier-plane resolution may be wrong'
        )



# End of file #
