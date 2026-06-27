"""
keylayout_to_xkb/__main__.py

Entry point: python -m keylayout_to_xkb

PHASE 1 SCOPE. This wires together only the extraction and parsing stages so
the binary 'uchr' parser can be validated against real macOS layouts before
the classify and emit stages are built on top of it.

What it does:
  1. Pull all input-source 'uchr' payloads via the TIS bridge (macOS only).
  2. Optionally dump each raw payload to a directory for offline inspection.
  3. Parse each payload into the normalized model.
  4. Print a per-layout summary (key count, dead-state count, approx char
     count) and, with --debug, a great deal of intermediate detail.

Suggested first runs on the MacBook Air:

    # Confirm the ctypes/TIS bridge works and see raw sizes:
    python -m keylayout_to_xkb --dump-raw ./raw_dump --debug

    # Focus on one layout by name substring (e.g. the US layout, no dead keys):
    python -m keylayout_to_xkb --filter "U.S." --debug

    # Then the dead-key oracle:
    python -m keylayout_to_xkb --filter "ABC - Extended" --debug

The US layout is the first oracle (no dead keys, every cell checkable). ABC
Extended is the second (25 dead keys per the optspecialchars appendix; counts
are checked leniently, since layouts shift between macOS versions and the
appendix counts were hand-tallied).
"""

import os
import sys
import argparse

from keylayout_to_xkb.common.debug import set_debug, dbg, warn
from keylayout_to_xkb.extract.tis_source import extract_all_layouts, TISExtractionError
from keylayout_to_xkb.extract.uchr_parse import parse_uchr, UchrParseError


__version__ = '20260622'


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='keylayout_to_xkb',
        description='Extract and parse macOS keyboard layouts (phase 1).',
    )
    parser.add_argument(
        '--dump-raw',
        metavar='DIR',
        default=None,
        help='write each raw uchr payload to DIR before parsing',
    )
    parser.add_argument(
        '--filter',
        metavar='SUBSTR',
        default=None,
        help='only process layouts whose name contains SUBSTR (case-insensitive)',
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='enable verbose diagnostic logging to stderr',
    )
    parser.add_argument(
        '--verify-os',
        action='store_true',
        help='audit the parser against UCKeyTranslate (macOS only) and print '
             'a detailed per-layout diff report',
    )
    parser.add_argument(
        '--make-installer',
        metavar='OUT.py',
        default=None,
        help='generate a self-contained installer file for the processed '
             'layouts (combine with --filter to select which) and write it to '
             'OUT.py',
    )
    parser.add_argument(
        '--uchr-file',
        metavar='FILE',
        default=None,
        help='parse a local .uchr binary instead of extracting from macOS '
             '(use with --make-installer to build from a file off-Mac)',
    )
    return parser


def _summarize(layout, raw_len: int) -> None:
    """Print a one-block summary of a parsed layout to stdout."""

    print(f'layout: {layout.name!r}')
    if layout.source_id:
        print(f'  source_id:      {layout.source_id}')
    print(f'  raw bytes:      {raw_len}')
    print(f'  virtual keys:   {len(layout.keys)}')
    print(f'  dead states:    {layout.dead_key_count()}')
    print(f'  approx chars:   {layout.char_count()}')

    if layout.dead_key_count():
        named = sorted(layout.dead_states.keys(), key=lambda value: (len(value), value))
        preview = ', '.join(named[:12])
        suffix = ' ...' if len(named) > 12 else ''
        print(f'  dead state ids: {preview}{suffix}')

    print()


def main(argv: 'list[str] | None' = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    set_debug(args.debug)

    if args.uchr_file:
        # Off-Mac path: parse a single local .uchr instead of extracting.
        try:
            with open(args.uchr_file, 'rb') as handle:
                file_data = handle.read()
        except OSError as error:
            warn('main', f'cannot read --uchr-file: {error}')
            return 2
        base = os.path.basename(args.uchr_file)
        name = base.replace('com_apple_keylayout_', '').replace('.uchr', '')
        payloads = [{'name': name, 'source_id': '', 'data': file_data}]
    else:
        try:
            payloads = extract_all_layouts(dump_dir=args.dump_raw)
        except TISExtractionError as error:
            warn('main', f'extraction failed: {error}')
            return 2

    filter_substr = args.filter.lower() if args.filter else None

    parsed_count = 0
    skipped_no_data = 0
    failed = 0
    built_records = []                                  # for --make-installer

    for payload in payloads:
        name = payload.get('name') or ''
        source_id = payload.get('source_id') or ''
        data = payload.get('data')

        if filter_substr is not None and (
            filter_substr not in name.lower()
            and filter_substr not in source_id.lower()
        ):
            continue

        if not data:
            skipped_no_data += 1
            dbg('main', f'skip (no uchr data): {name!r}')
            continue

        try:
            layout = parse_uchr(data, layout_name=name, source_id=source_id)
        except UchrParseError as error:
            failed += 1
            warn('main', f'parse failed for {name!r}: {error}')
            continue

        _summarize(layout, raw_len=len(data))
        parsed_count += 1

        if args.make_installer:
            from keylayout_to_xkb.install.build_record import build_record
            built_records.append(build_record(layout))

        if args.verify_os:
            from keylayout_to_xkb.verify.os_oracle import (
                verify_layout, format_report, OSOracleUnavailable,
            )
            try:
                result = verify_layout(data, name)
                print(format_report(result, verbose=True))
                print()
            except OSOracleUnavailable as reason:
                warn('main', f'--verify-os needs macOS: {reason}')
                args.verify_os = False              # stop retrying every layout

    print(
        f'done: parsed={parsed_count} '
        f'skipped_no_data={skipped_no_data} failed={failed}',
        file=sys.stderr,
    )

    if args.make_installer:
        if not built_records:
            warn('main', 'no layouts matched; nothing to put in the installer')
            return 1
        from keylayout_to_xkb.install.generate import generate_installer
        text = generate_installer(built_records)
        try:
            with open(args.make_installer, 'w', encoding='utf-8') as handle:
                handle.write(text)
        except OSError as error:
            warn('main', f'cannot write installer: {error}')
            return 2
        ids = ', '.join(r.identifier for r in built_records)
        print(
            f'wrote installer {args.make_installer} '
            f'({len(built_records)} layout(s): {ids})',
            file=sys.stderr,
        )
        return 0

    if parsed_count == 0:
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())


# End of file #
