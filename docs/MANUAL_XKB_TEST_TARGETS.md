# Manual Test Targets — XKB / XCompose Transfer Verification

    docs/MANUAL_XKB_TEST_TARGETS.md

Targeted checklist for verifying on Linux (Fedora KDE Wayland) that the
oracle-certified extraction capabilities transferred into the emitted XKB
symbols and XCompose files. Every expected output below was machine-verified
against UCKeyTranslate during the 2026-07-03/04 coverage campaign, so a
wrong character here means an EMISSION or INSTALL problem, never extraction.

Before testing:

* Regenerate and install with the current tool (stamp up20260704 or later).
  Everything installed before the campaign predates the chain, orphan,
  identity, and keyboard-type fixes.
* Compose rows require the layout's generated XCompose active in the
  session.

Key names are US physical positions. Plane-to-modifier legend:

* plain            = the key alone
* Shift            = Shift + key
* AltGr            = Right Alt (Option) + key
* Shift+AltGr      = both + key
* Caps ...         = same four with CapsLock engaged (levels 5-8, the Mac
                     caps layer)


## 1. Plane spread (all eight levels on one key)

Polish Pro, the `[` key:

* [ ] plain              → `[`
* [ ] Shift              → `{`
* [ ] AltGr              → `„`
* [ ] Shift+AltGr        → `”`
* [ ] Caps plain         → `[`
* [ ] Caps Shift         → `{`
* [ ] Caps AltGr         → `“`   (differs from non-caps AltGr `„` — the
                                  fastest proof the Mac caps layer is live)
* [ ] Caps Shift+AltGr   → `”`

Finnish Sámi – PC, the `,` key:

* [ ] AltGr              → `‘`
* [ ] Caps AltGr         → `‚`   (the caps-plane difference again)


## 2. Single-level dead keys and compositions (XCompose)

Polish Pro (AltGr+U = diaeresis dead key, terminator `¨`):

* [ ] AltGr+U, A         → `Ä`
* [ ] AltGr+U, E         → `Ť`   (deliberately surprising pairing: proves
                                  the real table transferred)
* [ ] AltGr+U, Space     → `¨`   (dead + space terminator convention)

Latvian (AltGr+4 = diaeresis dead key):

* [ ] AltGr+4, A         → `Ä`
* [ ] AltGr+4, E         → `Ė`

Vietnamese (the A key is a composing base, plane-dependent):

* [ ] a, ^               → `â`
* [ ] Shift+A, ^         → `Â`
* [ ] Caps A, ^          → `Â`


## 3. Identity compositions and collision rank (Tongan)

AltGr+X = acute dead key (terminator `´`):

* [ ] AltGr+X, D         → `D`   (identity composition: bare base, no
                                  accent, no fallback `´D`)
* [ ] AltGr+X, 1         → `¹`
* [ ] AltGr+X, Shift+N   → `Ń`   (collision-rank winner over the identity
                                  records on other planes)

Other Tongan dead keys:

* [ ] AltGr+`, Shift+N   → `Ǹ`
* [ ] AltGr+Q, 1         → `①`


## 4. Chained dead keys (format-2, Tibetan – Wylie)

* [ ] P, H, L (plain, three presses)   → `ཕ` as a SINGLE glyph
      (two glyphs = the chain line is missing, fallbacks fired)
* [ ] Shift+D, L                       → `ཌ` as a SINGLE glyph
* [ ] A alone                          → behaves per its zero (`ཨ`); the
      vowel keys are the zero-bearing composing bases (handoff 1.7)


## 5. Keyboard-type variants (the PC family)

Russian – PC, ANSI variant, the backquote key:

* [ ] plain              → `ё`
* [ ] Shift              → `Ë`
* Seeing `]` / `[` instead means the GENERIC tables were installed rather
  than the ANSI kind variant — this key pair is the single-cell
  discriminator for the whole keyboard-type saga.

Arabic – PC, the backquote key:

* [ ] plain              → `ذ`
* [ ] Shift              → `ّ`   (shadda; same on both variants)


## 6. Negative targets (lines that must NOT exist)

* [ ] Finnish Sámi – PC: `ˀ` dead key, then i   → NOT `ỉ`
      (orphan record removed by the filter; fallback or nothing = pass)
* [ ] Kabyle – QWERTY: cedilla dead key (`̧`), then 2   → NOT a composed
      result (cmd-plane-only record on macOS, correctly absent; an
      undefined-sequence result = pass)
* [ ] Unicode Hex Input, if generated: XCompose contains the
      chain-expansion-skipped comment and no 65k accumulator lines


## 7. Reading failures

* Wrong character on a plain/Shift plane
  → symbols emission or wrong variant installed (check section 5 first)
* Wrong character only with CapsLock
  → levels 5-8 / caps-layer wiring
* Compose row gives terminator + base instead of the expected character
  → XCompose not active, or the line missing
* Compose works but chains (section 4) break at step two
  → chain lines missing from the generated file; regenerate with the
    current tool


# End of file #
