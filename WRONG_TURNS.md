# Wrong turns, and what replaced them

A record of the incorrect assumptions made while reverse-engineering Apple's
`uchr` format and converting it to XKB, kept so they are not re-derived later.
Each entry: the wrong idea, why it was wrong, and the correct interpretation that
replaced it. Light on detail by design -- just enough to recognize a dead end.

---

## 1. Plane -> table resolved by table *content* (the "content heuristic")

**Wrong:** the on-disk modifier index (`keyModifiersToTableNum`) looked like an
undocumented compaction we could not decode, so the parser guessed which table
was which plane by inspecting what each table produced -- counting letters,
detecting case pairs, judging whether a script was "substantial," and demoting
Latin tables to fallback layers on non-Latin keyboards.

**Why wrong:** it was heuristic, not derived. It needed fuzzy thresholds (e.g.
"a real alphabet has >= 10 letters of a script") that were guesses, and it could
misjudge layouts that did not match the sampled cases.

**Correct:** the modifier byte maps to the table index by a simple fixed
transform -- `index = modifier_byte + 2`. This was extracted by driving Apple's
own UCKeyTranslate across every modifier byte on all installed layouts and
inverting its output. Once known, planes are read directly from the layout's own
modifier map with no content inspection. The content heuristic and its fuzzy
constants became dead code. (UCKeyTranslate is still kept as an on-macOS
cross-check; it is authoritative and worth running when available.)

---

## 2. Output references interpreted only at character-table cells

**Wrong:** the format's 16-bit values carry top-bit flags meaning "this is a
literal char / a sequence index / a state index." The decoder honored those
flags only at the main character-table cells, and used a plain literal reading
everywhere else (state-record fields, the entries map, terminators).

**Why wrong:** the *same* output-reference grammar is used at EVERY output site,
not just character cells. Treating the other sites as plain literals decoded
hundreds of sequence/state references as the wrong (often CJK-looking)
characters -- this is what made some layouts (Tibetan, Vietnamese, Manipuri)
produce garbled output.

**Correct:** one unified output-reference grammar applies at all output sites.
Decode every site through the same primitive.

---

## 3. Output-reference flags gated by per-layout booleans

**Wrong:** whether a flagged value should be treated as a sequence index or a
state index was thought to depend on layout-level conditions -- booleans like
`state_indices_active` (the layout has state records) and
`sequence_indices_active` (max output length >= 2). These were threaded as
parameters into every decode function to gate interpretation.

**Why wrong:** the discriminator is not a layout-level flag at all. It is a
per-VALUE in-range check: if a value flagged as (say) a sequence index points to
an index that actually exists in the sequence table, it is a sequence index;
if the index is out of range, it is a literal. Per value, not per layout.

**Correct:** test each value's index against the actual table length. The
`*_active` booleans became inert (gating nothing) and are vestigial parameters.

---

## 4. "Four planes are all that's typeable" (Caps layers ignored)

**Wrong:** only four modifier planes (plain / shift / option / shift+option)
were treated as typeable; the Caps tables were dismissed as a lock layer the
desktop handles.

**Why wrong:** macOS layouts put genuinely-typeable, often UNIQUE output behind
Caps -- not just uppercasing. Many non-Latin layouts (Tibetan, Wancho, Rejang)
put the entire Latin alphabet on the Caps layer; others put unique symbols on
Caps+Option (Polish `Ś £ Ę`, typographic quotes). Dropping Caps lost real
characters.

**Correct:** there are EIGHT typeable planes -- the four base planes plus the
four Caps planes (caps / caps+shift / caps+option / caps+shift+option). All
eight are captured. (The command and control tables are still correctly
excluded: probing their contents showed they hold shortcut-layer Latin and C0
control codes respectively, which Linux handles natively.)

---

## 5. Caps "behavior" should be classified per layout

**Wrong:** assumed Caps layouts fell into a small number of clean "modes" (e.g.
caps-as-shift vs caps-as-alternate-layer) that we could detect and emit
differently.

**Why wrong:** a full scan found ~16 distinct Caps table-reuse fingerprints
across all layouts, not two or three clean buckets. Any classification would be
a leaky heuristic that is sometimes wrong (e.g. one Tibetan variant breaks the
pattern its siblings follow).

**Correct:** do not classify. "Follow the groove": read whatever table each Caps
plane points at and emit it at its level, uniformly. The collapse of redundant
planes is decided per layout by the actual table routing (`plane_tables`), not
by a behavior guess.

---

## 6. Caps might be a hold-vs-toggle choice we infer from the tables

**Wrong:** thought the table structure (a few extra chars vs a whole alternate
alphabet behind Caps) signaled whether macOS intended Caps as a momentary HOLD
or a TOGGLE, and that we should reproduce the inferred intent.

**Why wrong:** physical testing on macOS showed Caps is a pure toggle/latch on
every layout -- a held Caps-combo leaves Caps toggled on, exactly like a tap.
There is no hold mode to infer, and the layout data does not encode such intent
anyway. (The activation delay on the Apple Caps key is just debounce, not a
hold-vs-tap discriminator.)

**Correct:** one uniform mechanism -- Caps toggles a modifier (XKB `LockMods` on
a free Mod bit) that selects the upper levels; Shift/Option then select within
the Caps layer. Self-contained in the layout, no keymapper needed.

---

## 7. Polish Pro's dead keys looked broken

**Wrong:** Polish Pro's dead-key compositions looked scrambled (one dead key
appearing to yield several unrelated accents), which read as an extraction bug.

**Why wrong:** it is not a bug. Polish Pro is an eccentric gestalt of
commonly-needed international characters; its dead keys genuinely do not compose
as a clean accent progression. Verified against UCKeyTranslate: every real
composition matches the OS exactly. ("Pro"-style layouts in general can be like
this -- see KNOWN_LIMITATIONS.md.)

**Correct:** the compositions are faithful. Separately, the apparent "missing"
compositions for non-composing keys are the dead key's terminator + base
character (standard dead-key fallback), which the parser correctly models via
the dead-state terminator and does NOT store as compositions.

---

## 8. XKB Caps Lock could select the upper levels directly

**Wrong:** assumed the standard XKB `Lock` modifier could be used to reach the
Caps levels.

**Why wrong:** standard `Lock` applies automatic capitalization rules, not
arbitrary level selection. That works only when Caps = uppercase; it cannot
reproduce arbitrary Caps-layer output (Polish quotes, Tibetan Latin, etc.).

**Correct:** define a custom key type whose upper levels are selected by a
dedicated modifier on a free Mod bit, and bind physical Caps to that modifier
via `LockMods` (not the predefined `Lock`/capitalization behavior).

---

## 9. A custom keysym name (e.g. `dead_mac_numero`) could be defined

**Wrong:** for dead keys whose accent has no standard `dead_*` keysym, thought we
could define a new aliased keysym name in the symbols file.

**Why wrong:** XKB has no mechanism to declare new keysym names. A name resolves
against libxkbcommon's built-in registry or it silently becomes `NoSymbol`.
There is nowhere to define the alias.

**Correct:** two tiers. Use a real, registry-verified `dead_*` keysym wherever
one exists (xkbcommon's set is large and covers most diacritics). For the
genuinely non-standard dead keys (numero sign, glottal-stop letters, Vietnamese
base-vowel tone keys), use a Private-Use placeholder keysym on the level plus
explicit XCompose rules -- the same placeholder mechanism used for multi-
character key outputs. No dead key is dropped; no name is invented.

---

## 10. One uniform key type for a whole layout

**Wrong:** leaned toward emitting a single N-level key type for the whole layout
and padding every key to that width with `NoSymbol`.

**Why wrong:** in XKB a key's type is a PER-KEY fact -- it declares that key's
level count and which modifier selects each level. Forcing a uniform type onto a
key that only responds to Shift falsely asserts it reacts to other modifiers.
Padding with `NoSymbol` is a claim about the key's behavior, not a neutral
filler.

**Correct:** play the record for each key -- read its actual populated planes,
route each to its modifier combination -- then GROUP keys with identical
signatures under one generated type. Uniform Latin layouts collapse to one type;
varied layouts (Tibetan) naturally yield several types of different widths. The
level count falls out of the data, not a fixed tier.

---

## 11. Fake-modifier carrier keys in the 195-199 gap

**Wrong:** the keymapper's internal fake modifiers were placed in the
kernel-undefined 195-199 keycode gap, assuming that gap was free.

**Why wrong:** 195-199 are within XKB's range AND pre-bound by the default `pc`
include (195 -> Level5 shift, 196-199 -> Alt/Meta/Super/Hyper). A fake there only
stays harmless while the keymapper consumes it and the layout has no content on
that modifier's level -- both circumstantial. On an 8-level layout a leaked 195
would shift to level 5.

**Correct:** place purely-internal fake modifiers ABOVE 255 (e.g. 755-759). XKB's
keycode space is hard-capped at 255, so anything higher is structurally
invisible to XKB on any layout -- it cannot accidentally drive a level or a
modifier. (This concerns the keymapper, not this converter, but the reasoning
lives here.)

# End of file #
