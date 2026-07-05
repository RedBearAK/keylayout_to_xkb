# The macOS `uchr` Keyboard-Layout Format

    docs/UCHR_FORMAT.md

A practical, behaviorally-verified map of Apple's `uchr` (`UCKeyboardLayout`)
binary keyboard-layout resource — the format every macOS Unicode keyboard
layout is ultimately compiled into, and the format a `.keylayout` XML file
becomes when the OS loads it.

Apple documents the *XML* `.keylayout` schema (Technical Note TN2056) and the
*runtime API* (`UCKeyTranslate`, the `UCKeyboardLayout` header types), but it
does not document the on-disk binary structure of the `uchr` resource itself.
This document fills that gap. It was written not by reading Apple source — there
is none to read — but by decoding real layouts and then proving the decode
against the operating system's own behavior, layout by layout, across Apple's
entire shipped catalog.

If you are trying to read a `.uchr` file, port Mac layouts to another platform,
build a `mac` variant for an XKB-based system, or just understand why a Mac
keyboard behaves the way it does, this is meant to save you the reverse
engineering it took to write it — and, just as importantly, to save you from the
specific wrong turns that *look* correct and pass every casual test.


## How to read the confidence tags

Every non-obvious claim in this document carries one of three tags. They are the
whole point: a format document you cannot calibrate is worse than none, because
it invites false confidence.

- **`[VERIFIED]`** — Confirmed against the operating system's own output. For
  these, real layouts were decoded and the result was compared, cell by cell,
  against what macOS actually produces for the same keypresses (see *How this
  was verified*). If this document says something is `[VERIFIED]`, it held across
  Apple's full shipped catalog with zero divergences.

- **`[INFERRED]`** — Reasoned from the structure and consistent with every
  layout observed, but not independently *forced* by the verification. Usually
  this means a field exists and is read correctly, but its full generality could
  not be exercised because no shipped layout uses the full range. These are the
  places most likely to hide a surprise in some layout nobody has looked at yet.

- **`[BOUNDARY]`** — A deliberate edge: something the format expresses that is
  intentionally not carried across to a Linux reproduction, or a known corner
  that is correctly *absent* rather than missing. Called out so its absence is
  never mistaken for a bug.

Untagged prose is either uncontroversial framing or standard, externally
documented fact (TN2056, the Carbon `UCKeyTranslate` calling convention).


## The clean-room method, in one paragraph

The binary structure here was recovered without disassembling Apple's code. The
technique is behavioral: drive the *documented* runtime function
`UCKeyTranslate` with every modifier combination on every installed layout,
record what character or dead-key state it returns for each virtual key, and
then find the on-disk structure that reproduces those answers exactly. Where a
structural interpretation and the OS disagreed, the OS won and the
interpretation was wrong — by construction, not by opinion. This matters for two
reasons. First, it means the offsets and grammars below are not guesses that
"looked right in a hex editor"; they are the interpretations that survived
contact with the OS's own decoder. Second, it means the failures were
informative: every time a plausible reading produced *almost* the right answer,
the mismatch pointed at a real structural fact the plausible reading had gotten
subtly wrong. Several of those are preserved later under *Wrong turns worth
keeping*.


## The container at a glance

A `uchr` resource is a single little-endian byte buffer. Everything is reached
by absolute offsets from the start of the buffer. There is no compression and no
alignment padding you can rely on; sub-tables simply sit at the offsets the
header and records point to.

The shape is a small fixed header, then an array of *keyboard-type records*,
then a scattering of sub-tables that those records point into. The sub-tables
are shared and de-duplicated: several keyboard-type records routinely point at
the same character tables.

At the very top (offset 0):

| Field | Type | Meaning |
|---|---|---|
| `headerFormat` | u16 | Format marker; `0x1002` for the layouts in circulation. |
| `dataVersion` | u16 | Version field; not load-bearing for decoding. |
| `featureInfoOffset` | u32 | Absolute offset of the feature-info block (may be 0). |
| `keyboardTypeCount` | u32 | Number of keyboard-type records that follow. |

`[VERIFIED]` — the header layout and the `0x1002` marker held for every layout
decoded.

Immediately after the header (at offset 12) sits `keyboardTypeCount` records,
each 28 bytes. Each record describes one range of physical keyboard types and
gives the offsets of the five sub-tables that define behavior for that range:

| Field | Type | Meaning |
|---|---|---|
| `first` | u32 | Lowest keyboard "gestalt" type this record covers. |
| `last` | u32 | Highest keyboard type this record covers. |
| `modifiersToTableOffset` | u32 | Offset of the modifier→table map. |
| `charIndexOffset` | u32 | Offset of the character-table index. |
| `stateRecordsOffset` | u32 | Offset of the dead-key state records. |
| `stateTerminatorsOffset` | u32 | Offset of the dead-key terminators. |
| `sequenceDataOffset` | u32 | Offset of the multi-character sequence table. |

`[VERIFIED]` — the 28-byte record layout and its five section offsets.

Most layouts carry a small number of records. Some carry many: a layout that
supports several physical keyboard geometries can carry two or three *sets* of
tables, expressed as a couple of dozen records whose `first..last` ranges tile
the space of keyboard types and whose offsets fall into a handful of distinct
groups. Records that share the same `charIndexOffset` (and the same other four
offsets) describe byte-identical behavior and can be treated as one.

### The sub-table markers

Each sub-table begins with its own 16-bit marker. Checking the marker is the
cheapest way to fail loudly instead of silently parsing garbage: a wrong offset
lands on the wrong marker. Across Apple's full shipped catalog, exactly these
markers occur:

| Marker | Sub-table |
|---|---|
| `0x1002` | header format (the buffer's first field) |
| `0x2001` | feature info (read positionally; carries `maxOutputCharLength`) |
| `0x3001` | modifier→table map |
| `0x4001` | character-table index |
| `0x5001` | dead-key state records |
| `0x6001` | dead-key terminators |
| `0x7001` | multi-character sequence table |

`[VERIFIED]` — these seven and no others appear in any shipped layout. Note this
is the *observed* set, not a contiguous numeric range: there is a family
resemblance in the low nibble (`0x_001`), but the values are not consecutive and
you should treat the set as exactly the entries above. In particular `0x8001`
does **not** occur — `0x8000` is a *flag bit* on character-table cells (the
sequence-index flag, below), not a sub-table marker, and no `0x8001` marker is
used. Do not assume markers you have not observed. `[VERIFIED]`


## Which keyboard-type record the OS actually uses

If a layout has more than one record set, you have to pick the right one, and
the rule is not "take the first" or "take the one whose range contains the
type." It is a specific priority order, and getting it wrong produces a layout
that is correct on most keys and subtly wrong on the few keys that differ
between keyboard geometries (typically the ISO/JIS extra keys). That is the
worst kind of wrong: it passes every test that doesn't happen to press those
keys.

The resolution order, established by driving the OS across a full sweep of
keyboard types and observing which tables it answered with:

1. **Modern "translated" keyboard types** (a specific small set of high type
   numbers) do not get resolved by numeric range at all. They resolve through
   their ANSI/ISO/JIS *kind* to a representative type, and if no representative
   is covered, they fall to the first record — the OS never falls back to a raw
   range containment for these. `[VERIFIED]`
2. **Every other type** resolves by **range containment first**: find the record
   whose `first..last` covers the type. `[VERIFIED]`
3. **A type covered by no record** falls back to the representative type for its
   kind. `[VERIFIED]`
4. **A type of no known kind** falls back to the first record — the OS default.
   `[VERIFIED]`

The evidence for the ordering is layouts where the two rules disagree: a type
that Apple classifies as ANSI but whose *range* falls into a non-ANSI record set
resolves by range, proving containment outranks classification for ordinary
types; and a translated type that is numerically inside a wide range still
resolves through its kind, proving the translated set is exempt from
containment. The safe way to reproduce this is to freeze the kind/representative
tables to the membership that the full-catalog verification actually validated,
rather than "correcting" them to match Apple's nominal classification — at least
one such well-meaning correction broke a real layout. `[VERIFIED]`

For the common single-set layout, none of this matters: there is one record and
you use it.


## The eight typeable planes and the modifier→table map

A Mac layout does not store one symbol per key. It stores several *character
tables*, and a modifier state selects which table is active. The set of modifier
states that actually produce typed characters is exactly eight — four with Caps
off and four with Caps on:

| Plane | Selected by |
|---|---|
| plain | (nothing) |
| shift | Shift |
| option | Option |
| shift+option | Shift + Option |
| caps | Caps latched on |
| caps+shift | Caps latched on, then Shift |
| caps+option | Caps latched on, then Option |
| caps+shift+option | Caps latched on, then Shift + Option |

`[VERIFIED]` — all eight planes carry genuinely typeable output in real layouts,
including the caps quartet. On non-Latin layouts the caps planes frequently hold
the Latin letters; some layouts place unique symbols on caps+option. Treating
the caps planes as "just the shift planes again" drops real characters.

**Caps is a latch, not a held modifier.** `[VERIFIED]` This is a real behavioral
distinction, not pedantry. On macOS, Caps Lock *toggles* a global caps state:
you tap it on, and then the ordinary modifiers (Shift, Option) select among the
four caps planes; releasing a Shift/Option chord leaves you still in the latched
caps state until you tap Caps again. It is not a chord member you hold down
alongside the others. The table above is written as "Caps latched on, then …" for
exactly this reason — while Caps is technically part of the modifier state the
layout sees, it got there by a toggle, and it persists. A faithful reproduction
must model it as a lock, which is precisely what a correct XKB port does: it
binds the caps layer to a *lockable* level modifier (the caps planes become XKB
levels 5–8, selected by a `LevelFive` modifier that Caps Lock *locks* rather than
holds), so that Caps toggles the whole caps layer on and off, the Caps LED tracks
it, and the state carries across layout switches — the same single global caps
macOS presents.

**Command and Control are not planes.** `[BOUNDARY]` The layout does contain
tables reached with Command or Control held, but their contents are not
document-typeable characters: the Control tables hold the C0 control codes
(Control-A is U+0001 and so on) and the Command tables hold a Latin shortcut
layer so that keyboard shortcuts work regardless of the active script. On Linux
these are handled natively by the input stack and the desktop, so capturing them
as character levels would actively break Control and Super handling. They are
deliberately excluded from the eight-plane surface.

The mapping from a modifier state to a character table is a small on-disk array,
the modifier→table map. Its structure:

| Field | Type | Meaning |
|---|---|---|
| marker | u16 | `0x3001`. |
| `defaultTableNum` | u16 | Table to use when a query falls outside the array. |
| `modifiersCount` | u32 | Number of entries in the array that follows. |
| `tableNum[]` | u8 × `modifiersCount` | Table number for each modifier index. |

Each plane has a Carbon modifier byte — the value the OS forms as
`(EventRecord modifiers >> 8)`, so Shift's `0x0200` becomes `0x02`, Caps Lock's
`0x0400` becomes `0x04`, Option's `0x0800` becomes `0x08`, and the combinations
are the bitwise combination of those:

| Plane | Modifier byte |
|---|---|
| plain | `0x00` |
| shift | `0x02` |
| caps | `0x04` |
| caps+shift | `0x06` |
| option | `0x08` |
| shift+option | `0x0A` |
| caps+option | `0x0C` |
| caps+shift+option | `0x0E` |

**That modifier byte indexes the `tableNum` array directly.** `[VERIFIED]` The
byte selects the entry; the entry is the character-table number for that plane.
There is no scaling and no offset applied to the index. (If you have seen a "+2"
here, read *Wrong turns worth keeping* below — it is the single most instructive
mistake in this whole format, and it is a trap, not a feature.)

Two facts make this array smaller than it looks. First, only these eight
indices are ever queried for character production, so the entries at
command/control indices are irrelevant to typed output. Second, right-sided
modifier bits (right-Shift as distinct from Shift, and so on) never participate:
the OS builds the modifier state it passes to the layout from the *generic*
Carbon bits only, never the sided bits. Rules and entries that depend on
right-only modifiers are vestigial — inherited from the format's older KCHR
lineage — and can never fire during character production. `[VERIFIED]`

The caps quartet deserves one honest note. `[VERIFIED]` for the *mechanism* — the
caps planes read through the same array with the same direct indexing and
reproduce the OS's output. But the caps planes were captured *without* trying to
classify "what caps does" on a layout. A scan of the full catalog found sixteen
distinct patterns of how caps tables are reused relative to the non-caps tables,
not two or three clean "caps modes." So the correct approach is mechanical:
follow the array to whatever table each caps plane points at and emit it, rather
than modeling a caps *policy* that does not exist as a small enumerable thing.


## Character tables and the cell encoding

The character-table index is the list of tables the modifier map points into:

| Field | Type | Meaning |
|---|---|---|
| marker | u16 | `0x4001`. |
| `tableSize` | u16 | Number of entries (virtual keys) in every table. |
| `tableCount` | u32 | Number of tables. |
| `offsets[]` | u32 × `tableCount` | Absolute offset of each table. |

`[VERIFIED]` Every table has the same `tableSize`, and each table is that many
16-bit entries — one per virtual key. So a "cell" is one 16-bit value at
`table_offset + 2 * virtualKey`.

Decoding a 16-bit cell:

- `0xFFFE` and `0xFFFF` mean **empty** — the key produces nothing on this plane.
  `[VERIFIED]`
- Otherwise the top two bits are flags and the low 14 bits are an index or a
  literal, governed by two independently-controlled flags:
  - **`0x4000` — state index.** The cell enters a dead-key state; the low 14 bits
    are the state number. This flag is active whenever the layout actually *has*
    dead-key state records. Crucially, many single-character layouts (German,
    Polish, Turkish, and so on) still use dead keys, so you must key this on the
    *presence of state records*, not on whether the layout can emit long output.
    `[VERIFIED]`
  - **`0x8000` — sequence index.** The cell emits a multi-character string; the
    low 14 bits index the sequence table. This flag is active only when the
    layout declares it can emit sequences at all, i.e. its maximum output length
    is at least 2. When the maximum is 1, there is no sequence table, and a set
    high bit is simply part of a literal codepoint (some native-script letters
    live at codepoints with the high bit set), not an index. `[VERIFIED]`
- With neither flag set, the 14-bit value is a literal codepoint. `[VERIFIED]`

The "maximum output length" comes from the feature-info block:

| Field | Type | Meaning |
|---|---|---|
| marker | u16 | `0x2001`. |
| (reserved) | u16 | — |
| `maxOutputCharLength` | u32 | Longest string any key can emit. |

`[VERIFIED]` A value of 1 tells you the sequence flag is inert for this layout —
which is what disambiguates a high-bit literal from a sequence index. If the
block is absent, assume sequences are possible rather than risk dropping one.


## Dead keys: states, terminators, compositions, and chains

Dead keys are where the format earns its complexity, and where the most
characters hide. A dead key does not emit; it enters a *state*. The next key is
interpreted in that state, either composing to a result or falling through to a
default.

Three tables cooperate:

**State records** (`stateRecordsOffset`, marker `0x5001`) describe what happens
to subsequent keys while a state is active. The table is a marker, a record
count, then that many absolute offsets to individual records; the records lie
consecutively, so each one runs until the next begins (the last runs to the end
of its region). Records come in two formats. `[VERIFIED]`

- A **terminal** record (format `0x0001`) directly gives outputs: a header
  (`zeroChar`, `nextState`, `entryCount`, `entryFormat`) then `entryCount`
  four-byte entries of `(curState, charData)`.
- A **range** record (format `0x0002`) is a compact run: the same header, then
  `entryCount` **eight**-byte entries, each carrying `curStateStart`,
  `curStateRange`, `deltaMultiplier`, `charData`, and `chainNextState`. An entry
  whose `charData` is the empty sentinel does not emit — it **chains** to a
  deeper state. Range records tile flush against one another (each is exactly
  `8 + 8 * entryCount` bytes), and the last one tiles flush against the following
  table. `[VERIFIED]`

The run-expansion in a range entry — `curStateStart..+curStateRange`, with
`charData` advancing by `deltaMultiplier` per step — is **real and exercised**,
but by exactly one layout. `[VERIFIED]` Every ordinary layout, including the
deep-chaining ones (Greek Polytonic, the Tibetan family), uses only the
degenerate case: `deltaMultiplier` 0 or 1 and `curStateRange` 0, i.e. one
state-to-output pair per entry. The **one** layout that uses the wide form is
Unicode Hex Input, whose accumulator entries carry `deltaMultiplier` up to 16 and
`curStateRange` up to 255 — the format's way of encoding "sixteen hex digits,
each advancing the accumulated codepoint by a fixed multiple." This is
significant for two reasons. It means the wide expansion is not a hypothetical
you can ignore — a decoder that hard-codes the degenerate case will misread the
hex accumulator. And it means the safety of *not* fully expanding that accumulator
rests entirely on recognizing that one layout by name (see the boundary note
under *chains*), because it is the sole place the wide semantics actually run.

**Terminators** (`stateTerminatorsOffset`, marker `0x6001`) give, per state, the
output produced when a dead key is followed by a key that has *no* composition
in that state. `[VERIFIED]` The canonical example is a dead accent followed by
space yielding the bare accent. This table is indexed by state and uses the same
output-reference grammar as everything else (literal codepoint, sequence index,
or empty).

**Compositions** are the reachable results: from a given state, pressing a
particular base character yields a particular string. These are derived by
walking the state records. A composition result may be **multi-codepoint** —
a base plus a combining mark with no precomposed form — which is exactly why
compositions cannot always live in a single keysym and why the Linux
reproduction needs a second surface (next section).

**Chains** are dead keys stacked on dead keys. `[VERIFIED]` A state can transition
to a deeper state rather than emitting, so pressing two or three dead keys in
sequence walks a graph before producing output. Real layouts reach depth four
(Tibetan Wylie is the standing example, with dozens of transitions and hundreds
of in-state outputs). The graph has four kinds of edge worth naming: a base
character can compose to a result or transition to a deeper state, and a further
*dead* key can likewise compose or transition. In-state behavior triggered by a
dead key is keyed by the ground state that dead key would enter on its own —
which is also how a faithful reproduction knows which Compose sequence to emit.

One accumulator is a deliberate exception. `[BOUNDARY]` A "hex input" layout
models all of Unicode as a dead-key accumulator — a graph with sixteen
transitions per state, several levels deep, expanding to tens of thousands of
leaves, and (as noted above) the sole user of the wide range-record expansion.
That is not a composition table to be enumerated; it is a numeric input method,
and it is excluded by identity rather than by any shape heuristic. Every *other*
layout's chains are followed to the end. Excluding it by name is load-bearing,
not cosmetic: it is the one layout whose range-record semantics differ from the
rest of the format, so quarantining it by name is what lets every other layout be
decoded uniformly.


## Reproducing the behavior on Linux: why it takes two surfaces

This is the design fact worth stating plainly, because it is the thing most
conversions get incomplete. Faithfully reproducing a Mac layout on an
XKB-based system requires **two paired outputs**, not one, because no single
Linux mechanism covers the whole of what a Mac layout does.

**XKB symbols** carry the per-key, per-level characters and the *identity* of
the dead keys. A key that types `å` on option gets `å` at the option level; a
key that is a grave dead key gets `dead_grave`. XKB is the right home for
everything that is "one keysym at one level." `[VERIFIED]`

**XCompose** carries what the dead keys *compose to*. XKB names the dead key but
does not say that grave-then-a yields `à`; the Compose file does. This is also
the only surface that can express a **multi-codepoint** result, because a Compose
sequence produces a quoted string rather than a single keysym. So every dead-key
composition — and in particular every base-plus-combining-mark result with no
precomposed form — belongs in the Compose file. `[VERIFIED]`

The division of labor, concretely:

- Single characters with a named keysym → XKB, by name.
- Single characters with no named keysym → XKB, as a Unicode keysym.
- Dead keys → XKB, as the matching `dead_*` keysym (or a private-use placeholder
  where no `dead_*` name fits the accent).
- Every composition and every chained result → XCompose, as a quoted string.

The eight planes map onto XKB levels directly: plain/shift/option/shift+option
are levels 1–4 (Shift and a `LevelThree` modifier), and the four caps planes are
levels 5–8, selected by a `LevelFive` modifier that Caps Lock *locks* rather than
holds — the latch behavior described earlier. That lock is what makes caps toggle
the whole upper layer on and off and persist across layout switches, matching
macOS.

A faithful Compose file should be **self-contained** — it should not depend on
whichever Compose data the host locale happens to ship, because the point is to
reproduce *this* layout's behavior, not to inherit the host's. `[VERIFIED]`

Three things intentionally do not cross over, and their absence is correct, not
missing:

- A handful of control-character cells in Apple's data have no XKB
  representation and are not emitted. `[BOUNDARY]`
- The all-of-Unicode hex accumulator is not expanded; Linux offers Unicode hex
  entry natively. Its ordinary single-level content still emits. `[BOUNDARY]`
- Compositions reachable only with Command held live outside the eight-plane
  surface and are correctly absent. `[BOUNDARY]`


## Wrong turns worth keeping

The verification did not just confirm the right structure; it *terminated*
several plausible readings that were wrong in ways no casual test would catch.
These are preserved deliberately. If you are re-deriving this format, you are
likely to walk into at least one of them, ship it, and never notice until a
specific layout bites you.

### The canceling offsets: a "+2" that was hiding a misread

This is the important one.

The modifier→table map, above, has its `modifiersCount` as a **32-bit** field,
so the `tableNum` array begins **8 bytes** into the block. An earlier, entirely
reasonable reading took the count as **16-bit** and started the array **6 bytes**
in — two bytes early. Because real counts are small, the high half of the true
32-bit count is two zero bytes, and reading two bytes early swallowed those two
zeros as **two phantom leading entries** at the front of the array. Every real
table number was therefore shifted two positions later than it should have been.

Here is why this survived for so long: the plane-indexing code compensated with
a **"+2"** — it added two to the modifier byte before indexing. A two-slot-late
array indexed two slots too far lands on exactly the right byte. The two errors
annihilated each other on every one of the eight plane queries the code actually
made. The output was correct. Nothing failed. The format was being read through
*two* bugs that precisely canceled.

The cancellation was perfect only within the range of indices that get queried.
It was invisible until a layout with **two nearly-identical character tables**
forced a sharper question: *which* plane resolves to *which* of the twins? That
investigation walked into the modifier map, and the misalignment surfaced —
partly because a correct read also preserves the **tail** of the array (the last
couple of real entries) that the two-bytes-early read silently drops off the
end. Those tail entries never mattered to the eight plane queries, which is
exactly why nothing broke earlier, but they are real data the misaligned read
was losing.

Aligning the read to the true structure (32-bit count, array at offset 8)
removed the two phantom entries, which meant the compensating "+2" had to go too.
The plane byte now indexes the array directly. The corrected read was checked to
be byte-for-byte identical to the old compensated read on everything the old
read got right — across the entire catalog and every keyboard-type record — so
the fix provably lost nothing, while additionally recovering the dropped tail.

The lesson generalizes beyond this format: **a too-early or too-late structural
read can hide indefinitely behind a compensating index offset, as long as your
tests only ever exercise the indices where the two errors cancel.** The thing
that exposes it is data that forces you to query *outside* that comfortable
range — here, near-identical tables that make the exact index matter. If you
find yourself adding a small constant to an index to "make it line up," treat it
as a red flag that a nearby field is being read at the wrong width or offset.
The constant is not a feature of the format; it is the shadow of a bug you have
not found yet.

### The nested-terminator myth in range records

A range state record ends flush against its neighbor at `8 + 8 * entryCount`
bytes. An earlier reading assumed instead that each record ended with a sentinel
followed by a nested terminal record, and so it read *past* each record's true
end and absorbed the following record's header and entries as if they were
compositions. The symptom was output misattributed to the wrong state in every
layout that used range records, plus spurious "unknown format" warnings wherever
the next bytes did not happen to look like a plausible terminal record. The fix
is to trust the flush tiling: the records pack tight, and the last one packs
tight against the following table. `[VERIFIED]`

### Dead cells that must stay visible to the matcher

When resolving which plane maps to which of two near-twin tables by comparing
against the OS, it is tempting to compare only the *literal character* outputs
and skip the dead-key cells. That makes near-twin tables that differ *only* in
their dead keys indistinguishable, and the match becomes a coin flip. The state
a dead cell enters is itself a discriminator the OS exposes, so dead cells must
participate in the comparison. `[VERIFIED]`

### The content heuristic that non-Latin layouts defeated

Before the modifier map was read correctly, planes were guessed from *table
contents* — "the table with the most lowercase Latin letters is plain." That
works for Latin layouts and fails quietly for scripts whose primary plane has no
Latin letters at all, mislabeling the primary script's own plane. Once the
modifier→table map was read correctly (see the canceling-offsets story), planes
could be read from the layout's own map directly, and the content heuristic —
and every script-detection threshold it needed — was retired. Reading the
structure beats inferring from the contents. `[VERIFIED]`


## How this was verified

The confidence tags rest on a layered comparison against the operating system,
run over Apple's full shipped catalog.

The core idea is an **oracle**: for a given layout, the OS's own
`UCKeyTranslate` is the ground truth for "what does this key, with these
modifiers, in this dead-key state, actually produce?" The decode is correct if
and only if it produces the same answers. Three things were checked this way:

- **Cells** — every virtual key on every one of the eight planes, compared to the
  OS's character or dead-key answer.
- **Compositions** — every dead-key result, including chained results, compared to
  what the OS emits when you actually press the sequence.
- **Sequences** — every multi-character output, compared to the OS.

At the close of the verification campaign, the full-coverage comparison reported
complete agreement: on the order of 150,000 individual machine-checked claims
across 241 layouts, with zero divergences on cells, compositions, and sequences.
`[VERIFIED]`

Two details make the number trustworthy rather than merely large. First, the
comparison parses each layout from the record covering the Mac's *real* keyboard
type, so multi-geometry layouts are checked against the same tables the OS is
using, not a default set. Second, a class of apparent "mismatches" was correctly
recognized as *not* mismatches: where the model stores a terminator and the OS
emits terminator-plus-base for a non-composing follow, that is the documented
fallback, not a divergence — distinguishing the two is itself part of getting the
dead-key model right.


## Confidence ledger

A consolidated view of what rests on what.

**Proven against the OS (`[VERIFIED]`):** the container header and marker; the
28-byte keyboard-type record and its five section offsets; the exact set of
sub-table markers (and the absence of `0x8001`); keyboard-type resolution order;
the eight typeable planes; Caps as a latch rather than a held modifier; the
exclusion of Command/Control as character planes; the modifier→table map
structure with its 32-bit count and offset-8 array; **direct** indexing of that
array by the plane's modifier byte; the character-table index and the 16-bit cell
encoding with its two independently-governed flags; the empty sentinels;
`maxOutputCharLength` gating the sequence flag; state-record presence gating the
state flag; the two state-record formats and their flush tiling; the range-record
wide expansion (used only by Unicode Hex Input); terminators; compositions
including multi-codepoint results; chained dead keys to depth four; and the whole
verification result.

**Inferred but unforced (`[INFERRED]`):** nothing material remains in this class
after the full-catalog review. The one field previously carried here — the
range-record wide expansion — was found to be exercised in the wild (by Unicode
Hex Input) and is now verified rather than inferred.

**Deliberate boundaries (`[BOUNDARY]`):** unrepresentable control-character cells
left unemitted; the hex-input accumulator not expanded (and its exclusion by name
being load-bearing, since it is the sole user of the wide range-record form);
Command-plane-only compositions correctly absent; and, on the reproduction side,
the two-surface split where anything multi-codepoint or composed must live in
Compose rather than in a single keysym.


## Appendix: byte-level quick reference

All values little-endian. Offsets are absolute from the buffer start unless a
field is explicitly relative.

**Header** (offset 0):
`u16 headerFormat` · `u16 dataVersion` · `u32 featureInfoOffset` ·
`u32 keyboardTypeCount`.

**Keyboard-type record** (28 bytes each, starting at offset 12):
`u32 first` · `u32 last` · `u32 modifiersToTableOffset` · `u32 charIndexOffset` ·
`u32 stateRecordsOffset` · `u32 stateTerminatorsOffset` · `u32 sequenceDataOffset`.

**Feature info** (marker `0x2001`):
`u16 marker` · `u16 reserved` · `u32 maxOutputCharLength`.

**Modifier→table map** (marker `0x3001`):
`u16 marker` · `u16 defaultTableNum` · `u32 modifiersCount` ·
`u8 tableNum[modifiersCount]` (array begins at offset 8; the plane modifier byte
indexes it directly).

**Character-table index** (marker `0x4001`):
`u16 marker` · `u16 tableSize` · `u32 tableCount` ·
`u32 offsets[tableCount]`. Each table is `tableSize` × `u16` cells; cell for a
virtual key is at `tableOffset + 2 * virtualKey`.

**Cell (u16):** `0xFFFE`/`0xFFFF` empty; else top bits `0x8000` sequence index /
`0x4000` state index, low 14 bits the index or literal codepoint.

**State records** (marker `0x5001`): `u16 marker` · `u16 count` ·
`u32 recordOffsets[count]`. Each record: header `u16 zeroChar` ·
`u16 nextState` · `u16 entryCount` · `u16 entryFormat`, then either terminal
(format `0x0001`, `entryCount` × 4-byte `(curState, charData)`) or range
(format `0x0002`, `entryCount` × 8-byte `(curStateStart, curStateRange,
deltaMultiplier, charData, chainNextState)`, tiling flush at `8 + 8 * entryCount`).

**Terminators** (marker `0x6001`):
`u16 marker` · `u16 count` · `u16 terminator[count]`, indexed by state.

**Sequences** (marker `0x7001`):
`u16 marker` · `u16 count` · `u16 relativeOffsets[count + 1]`; each string is the
UTF-16LE bytes between consecutive relative offsets.

**Plane → modifier byte (direct index):**
plain `0x00` · shift `0x02` · caps `0x04` · caps+shift `0x06` · option `0x08` ·
shift+option `0x0A` · caps+option `0x0C` · caps+shift+option `0x0E`.

---

*This document describes Apple's `uchr` format as recovered by behavioral
verification against the operating system. It is not affiliated with or endorsed
by Apple. Corrections are welcome.*

# End of file #