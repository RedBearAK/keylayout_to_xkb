# Fidelity & Normalization: Why the Layouts Come Out This Shape

    docs/FIDELITY_AND_NORMALIZATION.md

A bridge between the two other documents. `UCHR_FORMAT.md` explains *what the
uchr format is*; `UCHR_FORMAT_HANDOFF.md` explains *how the tool decodes it and
which wrong turns were terminated*. This document explains the third thing:
*why the emitted Linux layouts have the exact shape they do* — which macOS
behaviors are reproduced faithfully, which are deliberately normalized away
because they are behaviorally redundant, which are deliberately excluded because
Linux handles them better natively, and the few places where the gap between the
two systems cannot be fully bridged.

It is organized as a ledger. Each entry names the misalignment between what uchr
expresses and what XKB/XCompose can represent, states the choice made, gives the
reasoning, and — this is the point — names the specific language or layout that
forced or exposed the decision. The driving layouts are not incidental; they are
why the decision exists, and they are the regression targets if anyone ever
revisits it.

This document references the other two rather than repeating them. For the byte
grammar, see `UCHR_FORMAT.md`; for the implementation and its verification, see
`UCHR_FORMAT_HANDOFF.md`.


## The shape of the problem

macOS and Linux keyboard input do not describe behavior the same way, and the
mismatch is not cosmetic. Three structural facts drive almost every decision in
this document:

1. **uchr is not a canonical encoding.** Several distinct byte structures
   produce identical `UCKeyTranslate` output. The tool's internal model stores
   *what a key produces*, not *how Apple encoded it*, so any Apple choice that is
   behaviorally inert is normalized away on the way in — it was never
   recoverable from behavior, and reproducing it would add nothing a user could
   observe.
2. **XKB and XCompose split the work.** XKB carries per-key, per-level characters
   and the *identity* of dead keys; XCompose carries what dead keys *compose to*
   and is the only surface that can emit a multi-codepoint string. A faithful
   reproduction needs both, paired.
3. **Some macOS "keyboard" behavior is really OS/desktop behavior.** The Command
   and Control layers are not document typing; Linux produces them natively. What
   looks like dropped content is often content that would be wrong to emit.

Everything below follows from these three.


## Faithfully reproduced (the wins)

These are the behaviors the tool reproduces closely enough that, on the shipped
catalog, the OS oracle finds no divergence.

**The eight typeable planes, including the caps quartet.** All four base planes
(plain / shift / option / shift+option) and all four caps planes map onto XKB
levels 1–8. The option layer is selected by a `LevelThree` modifier (RightAlt);
the caps layer by a `LevelFive` modifier. This is the full character surface a
Mac layout exposes, and dropping the caps quartet — a tempting simplification,
since it often mirrors the base planes — would lose real characters. *Driven by:*
non-Latin layouts that place the Latin alphabet behind caps, and layouts with
unique caps+option glyphs (e.g. Polish, where caps+option reaches Ś/£ that the
naive "caps just re-uppercases" model would never produce).

**Caps as a latch, not a held modifier.** macOS Caps Lock toggles a global caps
state; you tap it on and the ordinary modifiers then select among the caps
planes, and the state persists until you tap it off. The tool reproduces this by
binding the caps layer to a *lockable* `LevelFive` — Caps Lock *locks* the
modifier rather than holding it, backed by the real Lock bit so the Caps LED
tracks the layer and the state carries across layout switches, exactly like the
single global caps on macOS. The plain (non-alphabetic) XKB level types are used
deliberately rather than the `_ALPHABETIC` variants: the alphabetic types give
Lock its classic "shift reverses caps" behavior, which would scramble the Mac
caps+option layers (levels 7–8). *Driven by:* the requirement that caps+option
and caps+shift+option reach their own glyphs instead of collapsing back down.

**Dead keys and their compositions, split across the two surfaces.** Each dead
key becomes its XKB `dead_*` keysym (or a private-use placeholder where no named
`dead_*` keysym fits the accent), and every composition it can produce becomes an
XCompose line. This split is not a convenience; it is forced by the next point.

**Multi-codepoint results.** macOS emits multi-character output for ligatures and
for base-plus-combining-mark results that have no precomposed form. A single XKB
keysym cannot emit a multi-codepoint string, so these results *must* live in
XCompose, which emits a quoted string. An emitter that assumed one keysym per
cell would silently drop exactly these characters. *Driven by:* the presence of
ligatures (fi/fl presentation forms) and combining-mark compositions across many
layouts; this is guarded at the model layer precisely because it is easy to get
wrong.

**Stacked dead-key chains.** A dead key can enter a state from which another dead
key enters a deeper state, several levels down, before anything is emitted. The
tool walks the chain graph and flattens every reachable path into XCompose
sequences. *Driven by:* Tibetan Wylie, which reaches stacking depth four with
dozens of transitions and hundreds of in-state outputs; Greek Polytonic, whose
breathing-plus-accent combinations stack similarly.

**ISO/ANSI/JIS keyboard-type variants.** Where a layout carries per-geometry
tables, each is emitted as its own self-contained variant rather than as an
include-overlay, so an installed layout never depends on a base layout being
present. The ANSI and ISO variants differ only by the documented `TLDE`/`LSGT`
key swap Apple's ISO keyboards use. *Driven by:* the multi-record PC-family
layouts (Russian, Arabic) and any layout Apple ships in both geometries.

**Named keysyms, never invented ones.** A single character is emitted as its
named XKB keysym where one exists, and otherwise as the Unicode `UXXXX` form. The
codepoint-to-name map is parsed from the running system's own `keysymdef.h`, so
names match the actual keysym database rather than a hand-maintained guess. XKB
cannot define new keysyms, so anything without a named keysym uses the `UXXXX`
form. *Driven by:* the general requirement that emitted symbols compile against
the host's real keysym set; the parse-from-`keysymdef.h` approach exists because
the keysym hex value is *not* the codepoint (a subtlety that silently corrupts
naming if guessed).


## Deliberately normalized away (behaviorally redundant encoding)

These are places where uchr carries structure that the tool's model collapses,
because the structure is a choice among behaviorally-equivalent encodings and
reproducing Apple's particular choice would add nothing observable. This is the
category the re-encoding analysis clarified: because uchr is not canonical, these
distinctions cannot be recovered from behavior, and they were correctly discarded
on the way in.

**Terminator staging versus explicit identity compositions.** The format stages a
bare-accent *terminator* (what a dead key produces when followed by a key with no
composition — dead-acute then space yields the bare accent) separately from the
compositions themselves. Some layouts additionally store *identity* compositions
(a composition whose result equals the base character). Behaviorally these are
indistinguishable: the OS emits the same thing whether via terminator fallback or
an explicit identity entry. The model represents the behavior once; it does not
preserve which staging Apple used. *Driven by:* the whole Nordic no-sign group
and the Sámi family, where the correct output is terminator + base and preserving
the distinction would only manufacture false "divergences" against the OS.

**Identity compositions are kept when real, but ranked.** Where a layout genuinely
stores an identity result (the OS really does emit the bare base at that cell),
the tool keeps it — these are not dropped as redundant. A collision rule decides
what happens when several records share a base character: an identity result never
overwrites a differing one, a differing result replaces an identity, and two
*different* non-identity results colliding warns loudly rather than guessing.
*Driven by:* Tongan, whose N key carries a record per plane and ships 183 real
identity entries; the ranking exists so those are neither dropped nor allowed to
mask a real composition.

**Orphan records.** A state record referenced by no cell on any plane describes
compositions no keypress can ever trigger. The OS never produces them, so an
emitted XCompose line for them would be inert. The tool skips them. *Driven by:*
Finnish Sámi PC (which ships orphan `i`/`I` records), Azeri (`I`/`W`/`w` plus
Nordic leftovers), and Tibetan Wylie (orphan Latin vowels). These are real bytes
in Apple's data that correctly produce no output.

**Table sharing and de-duplication.** Multiple keyboard-type records routinely
point at the same character tables; the model treats records with identical
table sets as one variant and emits once. Apple's byte-level record count is not
preserved because it is not behavior. *Driven by:* the multi-record PC-family
layouts, where two or three geometry records collapse to one or two distinct
emitted variants.

**Right-sided modifier rules.** The format can express rules that depend on
right-Shift as distinct from Shift. The OS never feeds the layout the sided bits
during character production, so these rules can never fire; they are vestigial,
inherited from the older KCHR lineage, and are not reproduced. *Driven by:* the
general format, confirmed by the cross-implementation calling convention that
every other consumer of uchr also builds the modifier state from generic bits
only.


## Deliberately excluded (Linux does it better, or it isn't typing)

These are behaviors uchr expresses that are intentionally *not* carried across,
because emitting them would be wrong rather than merely redundant. Their absence
is correct.

**The Command and Control layers are not planes.** Probing their contents showed
the Control tables hold the C0 control codes (Control-A is U+0001, and so on) and
the Command tables hold a Latin shortcut layer so that shortcuts work regardless
of the active script. Both are desktop and modifier behavior that the Linux input
stack produces natively. Capturing them as character levels would actively break
Control and Super handling. They are excluded from the eight-plane surface.
*Driven by:* every layout — this is a structural exclusion, not a per-layout one.

**Control-character cells in plane tables.** A handful of cells in Apple's data
produce raw control characters that have no XKB representation. They are not
emitted. In the verification roundtrip they appear as "missing" rows, never as
mismatches, because their absence is intended. *Driven by:* the specific layouts
carrying `\x03`/`\x14`-class cells in ordinary planes.

**The all-of-Unicode hex accumulator.** One layout — Unicode Hex Input — encodes
typing four hex digits as a four-deep dead-key chain expanding to 65,536 leaf
compositions, and (uniquely in the catalog) uses the format's wide range-record
expansion to do it. Enumerating that into XCompose would emit megabytes of
sequences that reproduce nothing a user needs, because Linux already offers
Unicode hex entry natively. It is excluded **by name**, not by any shape
heuristic, so a layout only counts as an accumulator when a human has judged it
one. The single-level content of the layout still emits normally; only the
accumulator graph is skipped. This exclusion is *load-bearing*, not cosmetic: UHI
is the sole user of the wide range-record form, so quarantining it by name is
what lets every other layout be decoded through the ordinary path uniformly.
*Driven by:* Unicode Hex Input, and only Unicode Hex Input.

**Compositions reachable only through Command.** Some layouts define compositions
that the OS produces only with Command held. These live outside the eight-plane
typeable surface and are correctly absent from the emitted layout. *Driven by:*
Kabyle – QWERTY, whose relevant record is referenced only from a command-modifier
table and composes on real hardware only with Command held; on the plain
dead-key path the OS gives the terminator fallback, which is what the emitted
layout reproduces.


## Where the gap cannot be fully bridged (honest limitations)

These are not choices; they are places where the two systems genuinely differ and
the reproduction is as close as it can be rather than exact.

**The lock-bit corner in multi-layout keymaps.** The caps latch is implemented by
locking `LevelFive` through the real Lock bit. On modern libxkbcommon, in a
keymap that merges several layouts, one stock binding cannot be fully neutralized
from the tool's group, so `LevelFive` can compile as `Lock` plus an extra bit
there. In normal single-layout use this is invisible. The one observable corner:
engage caps in this layout, disengage it from a sibling layout, then tap caps
here again, and two bits can flip-flop until one caps tap in a sibling layout
re-syncs. The Caps LED reports every state truthfully throughout, and a
Shift+Caps gesture unconditionally clears the state as a guaranteed escape (the
one deliberate deviation from macOS, where Shift+Caps would toggle). If the
relevant upstream binding is removed in a future xkeyboard-config, the corner
disappears on its own.

**Terminator + base is modeled as behavior, not as Apple's exact staging.** As
noted under normalization, where a dead key is followed by a non-composing key
the OS emits terminator + base, and the tool reproduces that behavior. It does
not reproduce whether Apple encoded a given case as a terminator or as an
explicit identity entry, because that distinction is not observable and not
recoverable. This is a limitation only in the sense that a byte-for-byte
re-encode is impossible; behaviorally it is exact.

**Named-keysym coverage depends on the host.** A character with no named keysym
falls back to the `UXXXX` form, which is always valid but less readable, and the
set of named keysyms is whatever the host's `keysymdef.h` provides. This is not a
loss of function — the character is still produced — but the *naming* is
host-dependent by nature.


## How to read this against the other documents

- If a decision here concerns the *format* (what the bytes mean, why a field
  exists), the authority is `UCHR_FORMAT.md`.
- If a decision here concerns the *implementation* (how a function decodes a
  table, which wrong turn was terminated and why), the authority is
  `UCHR_FORMAT_HANDOFF.md`, whose "resolved wrong turns" section is the vaccine
  against re-deriving the same mistakes.
- This document is the authority only for *why the reproduction has this shape* —
  the fidelity decisions and their driving layouts. When the three disagree, the
  format doc wins on format facts, the handoff wins on implementation facts, and
  this document wins on rationale.

The driving layouts named throughout are the regression set for their decisions.
Anyone revisiting a choice here should confirm the behavior against the specific
layout that forced it — the Nordic group for terminator staging, Tongan for
identity compositions, the Sámi and Azeri layouts for orphans, Tibetan Wylie for
chain depth, Greek Polytonic for near-twin tables and stacked composition, Kabyle
for command-plane compositions, Unicode Hex Input for the accumulator and the wide
range-record form, and the multi-record Russian/Arabic families for keyboard-type
variants.

# End of file #
