# keylayout_to_xkb

Convert macOS keyboard layouts into Linux XKB symbols and XCompose files, so
a Mac typist can sit down at a Linux machine and have every key, dead key,
and accent sequence behave the way their fingers already know.

The tool reads both of Apple's layout formats — compiled `uchr` binaries
(the same data macOS itself types with) and `.keylayout` XML — and produces
self-contained installers that set the layouts up on a Linux system. Run it
on a Mac to extract and convert the installed catalog automatically, or on
Linux from layout files directly (see Quick start). It is a standalone
converter, not a runtime component of any keymapper — though it pairs
naturally with projects like Toshy that provide Mac-style shortcuts on
Linux.

## Why it exists

Linux and macOS layouts for the same language are parallel evolutions from
the same needs, not copies of each other: key positions, option-plane
characters, special symbols, and dead-key input techniques routinely differ
between the two. Anyone who learned to type accented characters, phonetic
scripts, or Tibetan stacks on a Mac loses that muscle memory on Linux. This
tool replicates the macOS methods and characters as closely as XKB allows,
by translating Apple's own layout data rather than approximating it.

## What transfers

* All eight character planes per key: plain, Shift, Option, Shift+Option,
  and the same four with CapsLock — the Mac caps layer becomes XKB levels
  5-8.
* Dead keys with their full composition tables, emitted as XCompose
  sequences, including the dead-then-space convention for typing the bare
  accent.
* Chained dead keys (multi-stage compositions such as Tibetan Wylie
  consonant stacking), followed to the end of every chain.
* Per-hardware variants: layouts that carry different tables for ANSI, ISO,
  and JIS keyboards are emitted as separate variants, resolved the same way
  macOS resolves them.
* Multi-codepoint outputs, combining-character results, and the quiet
  oddities of real Apple data (identity compositions, per-plane dead
  states) — preserved because they were measured, not assumed.

## Fidelity

Every capability above is certified against macOS itself. The verification
stack asks UCKeyTranslate — the OS routine that turns keystrokes into
characters — what every cell, composition, and chain sequence should
produce, and compares the tool's model against those answers. As of
2026-07-04 the full catalog audit reports 100% agreement across all 241
layouts shipped with macOS: roughly 150,000 machine-verified claims, zero
divergences. (Two of those 241 catalog entries are literal duplicates --
the Wubihua pair -- so an installer built from the full catalog carries
239 unique layouts, and says so in its name.) A wrong character on Linux therefore points at emission or
installation, never at a misread layout. The gory details, including every
wrong turn taken on the way, live in `docs/UCHR_FORMAT_HANDOFF.md`.

## Quick start

On the Mac (generate):

```
pip install -e .
keylayout-to-xkb --filter 'Polish' --make-installer
```

* `--filter SUBSTR` selects layouts by name (case-insensitive); omit it to
  process the whole catalog.
* `--make-installer` produces a self-contained installer script; add
  `--separate` for one installer per layout.
* `--docs DIR` additionally writes a per-layout Markdown reference showing
  every key, plane, and dead-key table.
* `--verify-os` runs the OS-oracle audit and prints a per-layout diff
  report (macOS only; expect a clean report).

On the Linux machine (install):

```
python3 install_xkb_layouts_<N>.py
```

The installer is a self-contained Python script — invoke it through
`python3` as shown, since the executable bit does not reliably survive
transfers across filesystems. It offers system-wide installation (visible to KDE's keyboard
settings and previews, survives for all users) with automatic privilege
escalation, or user-mode installation under `~/.config/xkb`. Management
afterwards:

```
keylayout-to-xkb --list-installed
keylayout-to-xkb --uninstall <identifier>
keylayout-to-xkb --uninstall-all
```

Generation on Linux works the same way from layout files: `--uchr-file`
and `--uchr-dir` build installers from `.uchr` binaries (dump them once on
a Mac with `--dump-raw`, or supply any `.uchr` you have). The per-layout
reference generator (`--docs`, and `emit/docs.py` standalone) additionally
accepts `.keylayout` XML directly.

## Verifying an installation by hand

`docs/MANUAL_XKB_TEST_TARGETS.md` is a keystroke-by-keystroke checklist of
oracle-verified targets — golden plane-spread keys, dead-key compositions,
chain sequences, hardware-variant discriminators, and lines that must NOT
exist — for confirming that everything transferred to a live system.

## Project layout

`src`-layout Python package at `src/keylayout_to_xkb/`, organized by
pipeline stage:

* `extract/` — enumerating macOS input sources, parsing `uchr` binaries,
  and the UCKeyTranslate bridge used for plane resolution and verification
* `common/` — the shared layout model and keyboard-type tables
* `emit/` — XKB symbols and XCompose generation
* `verify/` — the OS-oracle audit
* `install/` — installer generation and Linux-side management
* `tests/probes/` — standalone investigation probes (coverage,
  keyboard-type resolution, format research); test modules live beside the
  code they test and run standalone or under pytest

## Documentation map

* `docs/UCHR_FORMAT_HANDOFF.md` — the technical map: the `uchr` format as
  understood, the verification stack, resolved wrong turns, open items
* `docs/MANUAL_XKB_TEST_TARGETS.md` — the hands-on transfer checklist
* `docs/FIELD_INCIDENTS.md` — observed-in-the-wild integration incidents
* `--docs` output — per-layout key/plane/dead-key references

## Known limitations

* A handful of control-character cells in Apple's data have no XKB
  representation and are intentionally not emitted.
* Unicode Hex Input's all-of-Unicode accumulator is not expanded into
  XCompose (Linux offers Ctrl+Shift+U natively); its single-level content
  still emits.
* Compositions reachable only through command-modifier planes on macOS are
  outside the emitted eight-plane surface and are correctly absent.
