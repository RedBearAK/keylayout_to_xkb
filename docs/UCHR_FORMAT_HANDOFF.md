# keylayout_to_xkb — uchr Format & Verification Handoff

    docs/UCHR_FORMAT_HANDOFF.md

State of understanding as of 2026-07-03, at the close of the oracle-coverage
campaign. On this date the full-coverage probe reported, for the first time:

    cells:         94731/94731   (100.00%)
    compositions:  34284/34284   (100.00%)
    sequences:     20691/20691   (100.00%)
    VERDICT: the extraction pathway matches the OS oracle completely.

241 layouts, ~150k machine-verified claims, zero divergences. This document
is the map: how the source format actually works, how the model represents
it, how the verification stack proves it, which wrong turns were taken and
why they were wrong, and what remains open. Module docstrings are the local
truth for each mechanism; this file is where they connect. It supersedes any
earlier scattered notes on limitations.

Component version stamps at this state: rc20260703 / gen20260703c /
br20260623 / up20260703m / uckt20260703d, plus gestalt_keyboard 20260703b,
compose 20260703b, os_oracle 20260703b, models 20260703b.


## 1. The uchr container

Header (offset 0): u16 headerFormat, u16 dataVersion, u32 featureInfoOffset,
u32 keyboardTypeCount. Then keyboardTypeCount 28-byte keyboard-type records
at offset 12, each: u32 first, u32 last (a gestalt keyboard-type range),
u32 modToTableOffset, u32 charIndexOffset, u32 stateRecordsOffset,
u32 stateTerminatorsOffset, u32 sequenceDataOffset. Records may share table
sets (dedupe by charIndexOffset); Russian – PC carries 27 records forming
two sets, Arabic – AZERTY/PC 30 records with ranges as wide as 41-1300.

Sub-table markers (the 0x_001 family, checked to fail loud on a bad offset)
are, across the full shipped catalog, EXACTLY: 0x1002 header, 0x2001 feature
info, 0x3001 modifier map, 0x4001 char index, 0x5001 state records, 0x6001
terminators, 0x7001 sequences. This is the OBSERVED set, not a contiguous
range: the values are not consecutive and none outside this set occurs. In
particular 0x8001 is NOT a marker — 0x8000 is the sequence-index FLAG on a
cell entry (section 1.5), not a table sentinel. Confirmed by the full-catalog
structural walk (reverse_walk probe): no marker outside the seven above
appears in any layout.

### 1.1 Keyboard-type resolution (which record the OS uses)

The rule, probe-derived (probe_kbdtype_resolution and
probe_kbgetlayouttype_dump) and validated against EVERY sweep point of
Russian – PC (47/47) and both Arabic layouts (56/56):

1. MODERN TRANSLATED TYPES (gestalt_keyboard.MODERN_TRANSLATED_TYPES,
   {58, 91, 92, 93, 192, 193}) resolve through their ANSI/ISO/JIS kind's
   representative chain, FINALLY: no covered representative means record 0,
   NEVER a containment retry — the OS never consults the raw number once it
   translates (Russian covers 192 with its own record and no JIS type at
   all, and resolves 192 to record 0). Evidence: Arabic covers 91 inside
   41-194 yet the OS answers with the ANSI tables.
2. Every other type: range CONTAINMENT first (type 16 is the proof: Apple
   classifies it ANSI, yet Arabic resolves it by range into the non-ANSI
   set).
3. Uncovered types fall back to the kind representative
   (gestalt_keyboard.representative_type_for_kind).
4. No kind known: record 0 (the OS default).

The kind table (KIND_BY_TYPE) follows Apple's runtime classification; the
REPRESENTATIVE_TYPES lists are EVIDENCE-FROZEN to the membership the full
241-layout audit validated at 100% — a rebuild that moved 8/16/17 to match
their corrected kinds broke Persian – Standard (see 5.8). The two tables
serve different roles: kinds classify the INCOMING type, the lists encode
the OS's empirical canonical chains.

Implemented in uchr_parse._record_for_kbd_type. The parse builds the PRIMARY
layout from record 0 unless the caller passes kbd_type (the audit paths do:
verify and the full-coverage probe pass LMGetKbdType()); emission never
passes it — the ANSI/ISO/JIS kind variants cover per-type tables there.

RESOLVED (was the last open anomaly): the 192/193 containment inversion on
Russian – PC. Apple's runtime classification table (dumped via
probe_kbgetlayouttype_dump; KBGetLayoutType returns FOURCC OSTypes 'ANSI'/
'ISO '/'JIS '/'????') classifies 192 JIS and 193 ANSI, and translation is
FINAL for translated types: no covered representative means record 0, never
a containment retry (Russian covers 192 with its own record and no JIS type
at all; the OS resolves 192 to record 0). The same dump corrected three
legacy kinds vs the Gestalt.h-name reading (8 and 16 are ANSI, 17 is ISO)
and confirmed 58/91 ANSI plus the modern ISO/JIS hardware values 92/93 —
all encoded in gestalt_keyboard. Type 16 replaced type 8 as the proof that
legacy types honor containment (Apple-ANSI, yet Arabic resolves it by range
into the non-ANSI set).

### 1.2 Modifier map (keyModifiersToTableNum)

u16 marker (0x3001), u16 defaultTableNum, u32 modifiersCount, then
modifiersCount u8 tableNum entries at offset +8, indexed DIRECTLY by the
Carbon modifier byte. The eight canonical plane bytes are the shared
PLANE_MODIFIER_BYTE constant (models.py): 0x00 plain, 0x02 shift, 0x04
caps, 0x06 caps_shift, 0x08 option, 0x0A shift_option, 0x0C caps_option,
0x0E caps_shift_option. Out-of-range indices fall to defaultTableNum.
Alignment history in the _parse_modifier_table_map docstring (section 5.2
below); equivalence of the corrected decode was verified byte-for-byte
across all 241 layouts (1401 records) before it landed.

### 1.3 Char tables and plane resolution

charIndexOffset: u32 count, u32 size?, offsets — decode via
_parse_char_table_index only (a hand-rolled read of this header failed once;
do not re-derive it). Each table is size u16 entries indexed by virtual key.

Plane→table resolution is CONTENT-FIRST (the on-disk map is a fully
validated decode), with the OS matcher as the primary path ON the Mac and a
permanent cross-check warn between the two. The matcher is SIGHTED: when
several tables tie on the 18 probe keys, near-twin families (2–6 differing
cells, typically dead keys or characters outside the probe set) are settled
by asking UCKeyTranslate about exactly the differing cells, dead-state
included; a residual tie among non-identical tables warns "decision rule not
understood" and falls to the on-disk pick. Historical context: the blind
matcher's lowest-index tie-break was the sole cause of all 64
OS-vs-content disagreement warnings ever observed.

### 1.4 Cell entry grammar

Entries are u16. 0xFFFE/0xFFFF empty. (entry & 0xC000) == 0x4000 with an
in-range index (& 0x3FFF) is a STATE reference into the state-record list.
Everything else is an output reference (section 1.5). A state-referenced
cell whose record has empty zero and next > 0 decodes as a DEAD key entering
state 'next'; a record with a char zero is a DUAL-IDENTITY key (section
1.7).

### 1.5 The output-reference grammar

One grammar everywhere output is specified — cell literals, record 'zero'
and entry charData, terminators (see _resolve_char_data):

    0xFFFE / 0xFFFF     empty (no output)
    0x8000 + in-range   sequence-table index (multi-codepoint output)
    0x8000 + oor        literal high codepoint (high bit is data)
    0xC000              literal high codepoint (both bits data)
    otherwise           literal BMP codepoint

Never chr() a raw value from any of those sites (section 5.4).

### 1.6 State records: composition and chaining

stateRecordsOffset: u16 marker (0x5001), u16 recordCount, u32 offsets[].
Record header: u16 zeroChar, u16 nextState, u16 entryCount, u16 entryFormat.
State NUMBERS are arbitrary (not record indices); DeadState names are the
string form of the number.

Format 1 (terminal): entryCount 4-byte entries (u16 curState, u16 charData).
Format 2 (range): entryCount 8-byte entries (u16 curStateStart,
u8 curStateRange, u8 deltaMultiplier, u16 charData, u16 nextState) — and
NOTHING else: no sentinel, no nested block (section 5.3). charData 0xFFFF
in a format-2 entry is a CHAIN: pressing this record's key in curState
enters nextState (Tibetan stacking, depth ≤ 4 observed; the compose walk
caps at 6 with a per-path cycle guard). The WIDE run-expansion
(curStateRange > 0 and/or deltaMultiplier > 1: charData advances by
deltaMultiplier over curStateRange+1 states) IS exercised in the wild, but
by exactly ONE layout — Unicode Hex Input, whose accumulator entries carry
deltaMultiplier up to 16 and curStateRange up to 255 (sixteen hex digits,
each advancing the accumulated codepoint by a fixed multiple). Every other
layout, including the deep-chaining ones (Greek Polytonic, all three Tibetan
layouts), uses only the degenerate 0/1-delta, 0-range form — one
state→output pair per entry. Confirmed by a full-catalog structural walk
(reverse_walk probe): 576 wide entries across the corpus, ALL in UHI, none
elsewhere. This is why UHI's name-block (section 3) is load-bearing rather
than cosmetic — see there.

Entry semantics, all probe-proven:

* An entry (S, C) in record R means: in state S, pressing R's key produces
  C. The BASE identity for composition keying is R's resolved zero.
* IDENTITY entries (result == R's zero) are REAL: the OS emits the bare
  base at the referencing cell (183 of them in Tongan alone). Their one
  special property is collision rank (section 2).
* Entries resolving to the EMPTY string are non-emitting (the OS falls back
  to terminator + base).
* ORPHAN records — referenced by no cell — are vestigial; their entries are
  skipped (Finnish Sámi PC ships 'i'/'I' orphans, Azeri 'I'/'W'/'w' plus
  Nordic leftovers, Wylie the Latin vowels).
* Reference counting is scoped to CANONICAL-PLANE tables (the eight plane
  bytes) per record, unioned across keyboard-type records for generation and
  scoped to the resolved record for kbd_type-aware audit parses. The
  canonical scoping is load-bearing: Kabyle – QWERTY's '2' record is
  referenced only from a command-modifier table, and the OS composes there
  only with cmd held.
* Fallback behavior (no entry matches): the OS emits terminator + base,
  including bare base when the terminator is empty. The audit counts those
  as expected, never as parser gaps.

### 1.7 Zero-bearing state cells (composition base keys)

A state-referencing cell whose record carries a char zero decodes as
CHARS(resolved zero) — the zero IS the ground output, and therefore the
XCompose base keysym for that record's compositions is exactly what the key
produces. The composing base keys of every layout are this construction
(PolishPro's letter keys, Wylie's vowels alike; a nonzero 'next' on such a
record does not make the cell dead — the DEAD decode applies only to
EMPTY-zero records). XCompose reachability of char-keyed lines was
investigated and proven vacuous at catalog scale: 1953 composition bases
across all seventeen fixtures, zero without a producing canonical cell (see
section 5.8 for the scare this check ended).

### 1.8 Terminators, sequences, and the tables after the records

stateTerminatorsOffset: u16 marker (0x6001), count, per-state terminator
values in the output-reference grammar (indexed state-1). The terminators
table sits flush after the last state record — which is how the format-2
"nested terminal" myth died (section 5.3). Sequence table: multi-codepoint
outputs referenced by the 0x8000+in-range grammar.


## 2. Model semantics (common/models.py)

DeadState carries the chain graph: compositions (char pressed → result),
dead_compositions (dead key pressed → result, keyed by the pressed key's
GROUND STATE NAME — also how the compose emitter derives its keysym),
char_transitions and dead_transitions (→ deeper state names), ground (False
for lazily-created chain targets, never emitted as level-1), terminator,
and the classify-stage xkb_keysym.

Composition COLLISION RANK (several referenced records sharing a base
char — Tongan's N key carries a record per plane): an identity result never
overwrites a differing one; a differing result replaces an identity; two
DIFFERENT non-identity results colliding warns loudly ("decision rule not
understood") and keeps the first. None exist in current data.

The shared PLANE_MODIFIER_BYTE constant is consumed by the on-disk resolver,
the UCKeyTranslate matcher, the audit, and the probes, so the plane sets
cannot drift apart.


## 3. Emission notes touched by the campaign

* compose.py walks the chain graph depth-first (_MAX_CHAIN_DEPTH = 6,
  per-path visited set); its depth-1 output is byte-identical to the
  historical single-level emitter, which is the standing regression proof.
* _CHAIN_BLOCKED_LAYOUTS = {'unicode hex input'}: UHI's graph is an
  intentional all-of-Unicode accumulator (16 transitions per state, four
  deep, 65536 leaves). Blocked BY NAME per project decision — no shape
  heuristics; every other layout's chains are followed to the end. The
  emitted file carries an explanatory comment; the probe imports the same
  constant. The block is LOAD-BEARING, not merely a convenience: UHI is the
  ONLY layout that uses the wide format-2 run-expansion (section 1.6), so
  quarantining it by name is what lets every other layout be decoded through
  the degenerate-only path uniformly.
* Control characters (\x03, \x14 classes) in plane cells are correctly
  unemitted; they appear as roundtrip "missing" rows, never mismatches.
* Known cosmetic debt: the unused 'types' parameter in
  emit/symbols._render_layout.


## 4. The verification stack

Layered, in increasing depth; the standing workflow is the first two for
routine changes and the third for releases or after macOS updates.

1. Test modules (pytest-runnable, plain-main style): emit/test_symbols,
   emit/test_classify, emit/test_compose (includes the fabricated-chain and
   Wylie chain-count tests), extract/test_uckt_matcher (fake-OS matcher with
   dead-state-accurate answers; the Latvian near-twin acceptance test),
   install/test_language_data, verify/test_os_oracle.
2. tests/probes/probe_xkb_roundtrip.py: parsed model vs emitted XKB across
   every fixture in uploads; expect mismatch=0, missing = only the
   control-char rows.
3. keylayout-to-xkb --verify (Mac): OS-oracle audit per layout — every cell
   plus OS-first compositions, with the fallback (terminator+base, empty
   terminator included) and X-convention (model stores terminator-only where
   the OS emits terminator+base, canonically dead+space) allowances built
   in.
4. tests/probes/probe_oracle_full_coverage.py (Mac): the
   everything-everywhere probe — cells and compositions via the audit
   machinery, plus every chain SEQUENCE the compose walk would emit,
   executed by threading dead state through _translate_step. It presses the
   COMPOSING cell for each base (raw-table state-flag preference), resolves
   dual-identity chars, parses with the real kbd_type, honors the UHI
   block, and reports unaddressables (ground states no key enters) honestly.
   (A _dual_identity_chars helper existed briefly for a key class that
   turned out not to exist; removed as dead code.)
5. tests/probes/probe_kbdtype_resolution.py (Mac): prints LMGetKbdType()
   and sweeps candidate types over an auto-found discriminating cell of any
   multi-record layout; the resolution rule reads off the grouped mapping.
   Run it on new hardware classes (ISO/JIS Macs) to extend
   MODERN_TRANSLATED_TYPES with evidence, never analogy.

UCKeyTranslate facts the stack depends on: deadKeyState equals the uchr
state number for single-level states and (previous_state << 16 |
current_state) for chained states — compare the LOW HALF only. Dead keys
return no output with a nonzero state. The dead-state value is threaded via
_translate_step (uckeytranslate), of which _translate_full and
_compose_after are special cases.


## 5. Resolved wrong turns (the vaccine section)

Kept deliberately: each of these was plausible, some were "confirmed" by
partial evidence, and every one fell to a focused probe against the native
tool. The pattern is the lesson.

### 5.1 "The OS resolved a different table" (near-twin blindness)

The plane matcher's 18 fixed probe keys could not distinguish near-twin
tables differing only in dead keys and off-probe characters, so it tied and
took the lowest index — while the on-disk map pointed at the right twin.
Every historical OS-vs-content disagreement warning was this. The warns said
"OS resolved table X"; the OS never resolved anything wrong — our inference
layer did. Fix: the sighted tie-settling (section 1.3). Probe:
probe_table_disagreement_cells (the OS agreed with the on-disk pick at every
askable cell; 190:0 after decoding representational NEITHERs).

### 5.2 The '+2' compensation

The modifier-map parser read entries two bytes early (u16 count instead of
u32), swallowing the count's high half as two phantom entries — and a '+2'
in the plane indexing exactly canceled it for every queried byte, hiding the
misread for the project's whole life and costing an investigation to
re-understand. Equivalence of the aligned decode was probe-verified across
the full catalog before the fix landed. Lesson: a compensation constant is a
misread wearing a disguise.

### 5.3 The format-2 "nested terminal block"

The original format-2 decoder assumed a 0xFFFF sentinel plus a nested
format-1 terminal — so it read past each record's true end and absorbed the
NEXT record's header and entries as compositions. The fmt=0x04ce warn on
Tibetan – Wylie was the decoder reading the global terminators table as a
record. Proof of the real grammar: 27 of Wylie's 28 format-2 records tile
flush against their successors at exactly 8 + 8*entryCount bytes; the 28th
tiles flush against the 0x6001 terminators table. The models.py docstring
that once said a chaining graph "was tried, never populated, and removed"
is the fossil record: chains were structurally invisible under the
misdecoded grammar.

### 5.4 Raw chr() on record zeros

base_char_for_record used chr(zero) without the output-reference grammar,
turning flagged sequence references into garbage base characters (the
<U8000> compose bases). The grammar applies to EVERY output site.

### 5.5 The identity-sentinel overcorrection

Kabyle – QWERTY's identity '2' entry audited as a fallback, and identity
entries were briefly dropped as "no-composition sentinels" — which turned
197 real compositions across six layouts into divergences on the next run.
The data adjudicated: identity entries are real (the OS emits the bare base
at their referencing cells); Kabyle's row was actually the canonical-plane
scoping issue (its record is command-plane-only). Lesson: three rows of
testimony lost to 197; and an anomaly should trigger a specimen request, not
a semantics change.

### 5.6 "deadKeyState == uchr state number" (universally)

Proven on Latvian and Kildin Sámi — both single-level. Chained states pack a
stack: (previous << 16 | current). The strict equality check condemned every
sequence on chain-heavy layouts (22% coverage) until the low-half comparison
landed (99%+). Lesson: an identity proven on two specimens is proven for
their class, not the format.

### 5.7 The dual-identity reachability scare

The first handoff draft claimed zero-bearing records with next > 0 decode
as DEAD cells, making their char-keyed XCompose lines unreachable, and
proposed an emit-side change. Reading the decoder disproved the premise
(the DEAD branch is empty-zero-only), and a catalog-wide check confirmed
it: every one of 1953 composition bases has a producing canonical cell.
Lesson: handoff documents are code too — every mechanism claim in one must
be source-grounded at writing time, or the document manufactures work.

### 5.8 The representative-list purity rebuild

After Apple's dump corrected three legacy kinds (8/16 ANSI, 17 ISO), the
representative lists were rebuilt to match — and Persian – Standard promptly
lost 10 cells: adding 16/8 to the ANSI chain resolved type 91 to a record
the OS does not use. The old membership had been validated at 100% across
the whole catalog; kind purity was an assumption, catalog agreement is the
evidence. Membership changes now require a fresh full-coverage run. (The
same batch fixed a FourCC mis-transcription: 194 is JIS, caught by the
decoded rerun of the dump probe.)

### 5.9 The variant-name collision (every multi-layout language served
the wrong layout)

Emission named every record's XKB variant sections with the same constant
stem: 'mac-k2x-ansi' / 'mac-k2x-iso', passed as a literal at the
build_record call site. Harmless for a language with one layout -- and
catastrophically silent for every language with several, because all of a
language's layouts share one <base>x symbols file, and XKB resolves a
variant reference to the FIRST section bearing that name. Records are
grouped sorted by identifier, so selecting 'Polish Pro (Macintosh, ANSI)'
in KDE loaded plain Polish's tables ('polish' < 'polishpro'), Russian - PC
loaded Russian's, and so on across the Arabics, Tibetans, Kabyles, and
Zhuyins. The duplicate section names are also the prime suspect for the
keymap-compile failures seen during the first full-catalog install.

Why it survived so long: every test until the full-catalog install used
single layouts or layouts from different languages -- the collision needs
two same-language records in one file to exist at all, and a NAME
collision produces no parse error, no compile diff on the first record,
and no wrong character anywhere except under the OTHER records' labels.
The fix names sections 'mac-k2x-<identifier>-<kind>' (prefix retained so a
raw variant name is recognizably a Mac layout from this tool). Lesson: any
constant passed where per-record identity belongs is a collision waiting
for the first shared namespace; and full-catalog testing exercises sharing
that spot checks structurally cannot.

The same investigation exposed a second, adjacent gap, now closed: the
emitted ANSI/ISO variants were physical-shape swaps of the primary tables
only, while the oracle-certified keyboard-type kind tables (Variant tag
'ansi'/'iso' -- the tables that make Russian - PC type Cyrillic io on an
ANSI backquote) were never wired into emission, despite models.py
documenting that intent. Variants now carry their kind's tables, falling
back to the primary when a layout has no table set for that kind -- which
is correct by construction, since an absent tag means the OS resolves
that hardware kind to the primary tables.

### 5.10 Probe-side artifacts masquerading as divergences

Three separate rounds: the tie-settling running blind inside a probe that
predated its kwargs; the probe pressing literal twin cells instead of
composing cells; the OS-reference builder probing plain+shift bases only
with last-writer-wins char collisions. Each produced walls of "real"
divergences. Lesson: a verification tool must have production parity with
the thing it verifies, and its own new code paths need fabricated-object
exercises before shipping (the CellDiff field-name crash shipped without
one).


## 6. Open items

* Keyboard types: fully resolved (section 1.1). Extend
  MODERN_TRANSLATED_TYPES only with probe evidence
  (probe_kbdtype_resolution behaviorally, probe_kbgetlayouttype_dump for
  Apple's own table).
* 'types' parameter cleanup in _render_layout — cosmetic.
* Possible upstream xkeyboard-config report: pc's legacy <LVL5> lines
  (precedent: <HYPR> removed in 2.47); removal would erase the caps-layer
  corner on old libs entirely.
* Informational plane-fill warns (Lao, Thai, Wancho, Yiddish, Rejang, UHI):
  UCKeyTranslate leaves shift_option planes unresolved there and the content
  resolver fills them — proven benign by the 100% audit, retained as
  visibility.
* 148 unaddressable sequence claims: ground states no key enters; honestly
  untestable, not gaps.


## 7. File map

    common/models.py            shared model; PLANE_MODIFIER_BYTE; DeadState
                                chain graph and collision rank
    common/gestalt_keyboard.py  type→kind tables; MODERN_TRANSLATED_TYPES;
                                kind representatives
    extract/uchr_parse.py       the format decode end to end; record
                                resolution; orphan/identity/collision rules;
                                canonical-plane reference scoping
    extract/uckeytranslate.py   the native-tool bridge: matcher (sighted),
                                _translate_step/_translate_full/
                                _compose_after, build_os_reference
                                (all-plane bases, composition-shape
                                preference)
    extract/test_uckt_matcher.py fake-OS matcher tests incl. Latvian
                                near-twin acceptance
    verify/os_oracle.py         the audit: compare_reference with fallback
                                and X-convention allowances; kbd_type-aware
                                parse
    emit/compose.py             chain-walk emitter; UHI name block
    emit/symbols.py             plane-cell emitter (types param debt)
    tests/probes/               probe_xkb_roundtrip,
                                probe_oracle_full_coverage,
                                probe_kbdtype_resolution,
                                probe_table_disagreement_cells,
                                probe_modmap_alignment_equivalence (both
                                historical, self-aware post-fix)


# End of file #
