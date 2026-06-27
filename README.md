# keylayout_to_xkb

Convert macOS keyboard layouts to XKB symbols and self-contained Compose
output, so Linux users can get layouts that look and behave like the macOS
layouts they already use (including the rich dead-key layouts such as ABC
Extended).

This is a standalone macOS-side tool: it reads Apple's compiled keyboard
layouts and emits XKB artifacts that can be shipped or installed on Linux. It
is not a runtime component of any keymapper.

## Status

Phase 1: extraction and parsing. The tool can enumerate macOS input sources,
pull their raw `uchr` layout data, and parse it into a normalized model. The
classify and emit stages (XKB symbols + Compose generation) come next, once
the parser is validated against real layouts.

## Layout

`src`-layout. The importable package is at `src/keylayout_to_xkb/`. The repo
root and `src/` are not packages.

## Install (development)

```
pip install -e .
```

After that, run from anywhere:

```
python -m keylayout_to_xkb --help
keylayout-to-xkb --help
```

Or run in place without installing:

```
cd src && python -m keylayout_to_xkb --help
```

## First validation runs (on macOS)

```
# Prove the TIS bridge and dump raw payloads:
python -m keylayout_to_xkb --dump-raw ./raw_dump --debug

# US layout first (no dead keys, every cell verifiable):
python -m keylayout_to_xkb --filter "U.S." --debug

# ABC Extended (the dead-key oracle):
python -m keylayout_to_xkb --filter "ABC - Extended" --debug
```

## License

GPL-3.0-or-later.
