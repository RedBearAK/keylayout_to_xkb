# Known limitations

keylayout_to_xkb path: KNOWN_LIMITATIONS.md

## Caps+Option (and other non-standard modifier) layers are not captured

The emitter models four character planes -- PLAIN, SHIFT, OPTION, SHIFT+OPTION --
which is the faithful mirror of what `UCKeyTranslate` produces for the generic
modifier states a Mac actually feeds the layout (see the modifier-resolution
notes in `extract/`). A small number of macOS layouts place genuinely-typeable
characters on a FIFTH state the four-plane model does not capture, most commonly
**Caps Lock + Option** (`uchr` modifier table reached by `caps anyOption`).

Examples found:
- **Polish (QWERTZ)** (`com.apple.keylayout.Polish`): `o` with double acute
  (`o-double-acute`, U+0151) sits on Caps+Option of the Q-position key.
- **Polish Pro** (`com.apple.keylayout.PolishPro`): the curly quotes
  (U+2018, U+201C) sit on a Caps-modified layer.

These characters are surfaced at parse time by the plane-sanity check, which now
distinguishes two cases:
- characters reachable via **dead-key composition** -> informational note only
  (they remain typeable through the generated XCompose file); and
- characters on **no plane and in no composition** -> a real warning, because the
  four-plane port cannot type them.

### Status / decision

**Decision: capture faithfully as an 8-level layout, WITHOUT classifying caps
behavior.** Design settled; build pending (a focused multi-module task, noted
below).

The caps tables are real distinct character layers, not shift-equivalents. For
Polish Pro the full modifier->table mapping is:

    plain        -> t0      caps              -> t1
    shift        -> t0      caps+shift        -> t2   <- unique chars (quotes)
    option       -> t1      caps+option       -> t4
    shift+option -> t3      caps+shift+option -> t5   <- unique chars

So faithful capture means extending the four-plane model to EIGHT levels: the
existing levels 1-4 (plain/shift/option/shift+option) plus levels 5-8 (the caps
quartet caps/caps+shift/caps+option/caps+shift+option).

**Full-set findings (validated against the OS oracle on all 241 layouts):**
- The caps planes decode with the SAME machinery as the base planes (byte+2 plane
  resolution + the output-reference grammar): a caps-layer validation matched the
  OS at 99.96% (235/241 clean; the 6 misses are the known ISO/PC keyboard-type
  vk50/vk94 variant cells, not a caps bug).
- "Latin behind caps" is common: on many non-Latin layouts (Tibetan, Wancho,
  Rejang, Pahawh, ...) the base planes carry the native script and the Latin
  alphabet lives entirely on the caps layer. 19/27 sampled layouts have unique
  caps-layer characters; explicit caps levels are required broadly, not rarely.
- **Do NOT classify caps "behavior."** A full-set scan of the caps table-reuse
  pattern produced 16 distinct fingerprints across 241 layouts, not two clean
  buckets -- the earlier "caps-as-shift-level vs caps-as-mode" split was a
  small-sample coincidence. Caps table allocation is a per-layout design detail,
  not a declared mode. The decode must simply "follow the groove": read whatever
  table each caps plane points at and emit it at its level. This is uniform
  across all 16 fingerprints and needs no per-layout special-casing.

**The Caps-Lock-toggle problem is solved at the keymapper layer, not in XKB.**
macOS treats Caps+Option as a HELD chord, which fights Caps Lock's toggle
nature. The keymapper (Toshy) can supply a truly-held fake modifier mapped onto
the physical Caps key via a modmap / multipurpose modmap, presenting "caps held"
to XKB as a momentary level shift with no interaction with literal Caps Lock's
lock behaviour. Users without that setup can still reach levels 5-8 by toggling
Caps Lock then pressing the key -- it locks rather than being held, which is an
acceptable alternate path. So the layout is built with an 8-level custom XKB
type; whether the caps-layer modifier arrives held (fake modifier) or locked
(literal Caps) is the user's choice and does not change the layout.

**XKB emitter mechanism (researched, confirmed available):** standard XKB Lock
CANNOT be used directly -- in the standard alphabetic types Lock does not select
a level, it applies automatic capitalization rules, which will not reproduce
arbitrary caps-layer output (Polish quotes, Tibetan Latin, etc.). Instead the
emitter defines a CUSTOM 8-level key type whose levels 5-8 are selected by a
dedicated modifier on one of the free Mod bits (Mod2-Mod5 minus the conventional
ones are "fair play" per the XKB docs), combined with Shift and LevelThree the
same way levels 1-4 use them; the physical Caps key is bound to that modifier
via LockMods (latching) -- or a held fake modifier supplied by the keymapper.

**TODO (emitter phase): examine the other predefined XKB key types** (ALPHABETIC,
the FOUR_LEVEL/EIGHT_LEVEL family, the *_ALPHABETIC and shift-cancels-caps
variants) to see whether any can be repurposed to change how Caps behaves so a
custom type is unnecessary, or to make the custom type cleaner. Decide this when
writing symbols.py, not before.

### Build plan (next major task)

1. models.py: add the caps-quartet planes (CAPS, CAPS_SHIFT, CAPS_OPTION,
   CAPS_SHIFT_OPTION) alongside the existing four.
2. binary parser: populate the caps quartet from the caps-bearing modifier
   tables (the data is already read; the plane resolver needs to select the
   caps tables for the new planes). Validate against the OS oracle (confirm e.g.
   Caps+Option+Q = o-double-acute on the Mac).
3. symbols.py: emit 8 levels per key and a custom xkb_types entry gating levels
   5-8 on the caps-layer modifier + shift/level3.
4. Validate on a real Linux session that the 8-level layout types the caps-layer
   characters via both the held-fake-modifier and Caps-toggle paths.

### Affected layouts (from the 19-layout sample)

Unique caps-layer characters that the four-plane port currently drops:
- **Turkish-Standard**: a substantial symbol layer (typographic + Western-
  European: section, paragraph, A-ring, AE, O-slash, OE, dashes, quotes, etc.).
- **Polish Pro**: the curly quotes (U+2018, U+201C).
- **Turkish-QWERTY-PC**: A-ring.
- **Greek Polytonic**: the Latin alphabet A-Z on a caps layer -- this is a
  Latin-fallback affordance, NOT a unique-character layer, and should be handled
  thoughtfully (likely NOT dumped onto caps levels) rather than captured naively.

# Not a bug: "Pro"-style layouts have eccentric dead-key compositions

Some macOS layouts -- notably the "Pro" variants (Polish Pro, and likely other
Programmer/Professional layouts) -- are gestalts of commonly-needed
international characters. Their dead keys do NOT compose as a clean accent
progression from the base character. For example, on Polish Pro the umlaut dead
key (Option+U) composes a->ä (expected) but e->Ď and y->ō (a grab-bag, not
"e-with-umlaut"). Dumping these compositions makes the layout look broken; it is
not. This was verified against the real UCKeyTranslate (see
tests/probes/probe_deadkey_oracle.py): every genuine composition the parser
extracts matches the OS exactly. The layout really is that eccentric.

The other thing the OS does, which is NOT a composition and which the parser
correctly does NOT store as one: when a dead key is followed by a key that has
no defined composition, macOS emits the dead key's TERMINATOR (its standalone
accent -- ¨, ^, `, etc.) followed by the unchanged base character (so Option+U
then s -> "¨s"). The parser captures each dead state's terminator
(DeadState.terminator) but stores no composition entry for non-composing
follows. This is the correct model: on Linux/XCompose the dead key's bare output
handles the fallback naturally, so the emitter must NOT enumerate the hundreds of
"¨s", "¨d", ... fallback pairs -- it only emits the real compositions plus the
terminator. Do not "fix" missing fallback pairs; they are intentional.

If a future probe or doc dump appears to show a dead key giving illogical or
"missing" results, re-read this section before treating it as a parser bug.


## Future idea: emit a per-layout documentation Markdown alongside the installer

When generating a layout's auto-installer, consider also emitting a human-readable
Markdown reference for that layout, in the spirit of the hand-made OptSpecialChars
repo files. It would document what each key produces across the planes (base +
caps), the dead keys and their compositions, the keyboard-type variants, and any
caveats (e.g. caps-layer reach, characters only available via XCompose). This
gives users a printable/searchable map of the generated layout without needing
to reverse-engineer it. Discuss scope and format when the emitter phase is
further along (it depends on the final model + emitter output shape).

# End of file #
