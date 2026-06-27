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

Documented, not yet captured. Caps-as-a-layer is rare and its behaviour on Linux
is contested (Caps Lock is frequently remapped or treated purely as a lock), so
faithfully reproducing a Caps+Option layer as additional XKB levels (5-6) is real
emitter work for a narrow case. The first time the four-plane model has been shown
insufficient for a genuinely-reachable DIRECT character (prior "fifth plane"
candidates -- sided right-Option rules -- were vestigial; this one is not).

Affected characters are few and tend to be typographic or cross-language extras
(curly quotes, a stray Hungarian letter on a Polish layout). The primary typing
of every affected layout (its alphabet, number row, Option layer, and dead-key
compositions) is unaffected and, for layouts verified against the OS oracle,
exact.

# End of file #
