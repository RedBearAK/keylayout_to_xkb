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
from keylayout_to_xkb.install.catalog import fold_name_to_ascii
from keylayout_to_xkb.extract.tis_source import extract_all_layouts, TISExtractionError
from keylayout_to_xkb.extract.uchr_parse import parse_uchr, UchrParseError


__version__ = '20260703'


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
        metavar='OUT',
        nargs='?',
        const='',
        default=None,
        help='generate a self-contained installer for the processed layouts '
             '(combine with --filter to select which). With no value, a '
             'standard name is generated; with --separate, the value is a '
             'directory (also auto-named if omitted)',
    )
    parser.add_argument(
        '--uchr-file',
        metavar='FILE',
        nargs='+',
        default=None,
        help='parse one or more local .uchr binaries instead of extracting from '
             'macOS (use with --make-installer to build from files off-Mac)',
    )
    parser.add_argument(
        '--uchr-dir',
        metavar='DIR',
        default=None,
        help='parse every .uchr file in DIR (off-Mac batch); combine with '
             '--filter to select a subset',
    )
    parser.add_argument(
        '--separate',
        action='store_true',
        help='with --make-installer, emit one installer per layout instead of a '
             'single bundled installer; --make-installer is treated as a '
             'directory in this mode',
    )
    parser.add_argument(
        '--docs',
        metavar='DIR',
        default=None,
        help='write a per-layout Markdown reference (<identifier>_reference.md) '
             'for each processed layout into DIR',
    )
    parser.add_argument(
        '--list-installed',
        action='store_true',
        help='list layouts currently installed in your ~/.config/xkb (Linux)',
    )
    parser.add_argument(
        '--uninstall',
        metavar='ID',
        default=None,
        help='uninstall one installed layout by identifier (Linux)',
    )
    parser.add_argument(
        '--uninstall-all',
        action='store_true',
        help='remove every kl2xkb-installed layout and its files (Linux)',
    )
    return parser


def _summarize(layout, raw_len: int) -> None:
    """Print a one-block summary of a parsed layout to stdout."""

    print(f'layout: {layout.name!r}')
    if layout.source_id:
        print(f'  source_id:      {layout.source_id}')
    print(f'  raw bytes:      {raw_len}')
    print(f'  virtual keys:   {len(layout.keys)}')
    # Distinct planes across all keys: a healthy Latin layout shows 8. This
    # line exists because a four-plane generation once looked identical to an
    # eight-plane one in this summary while the caps layers were missing.
    plane_count = len({
        plane for outputs in layout.keys.values() for plane in outputs
    })
    print(f'  planes:         {plane_count}')
    print(f'  dead states:    {layout.dead_key_count()}')
    print(f'  approx chars:   {layout.char_count()}')

    if layout.dead_key_count():
        named = sorted(layout.dead_states.keys(), key=lambda value: (len(value), value))
        preview = ', '.join(named[:12])
        suffix = ' ...' if len(named) > 12 else ''
        print(f'  dead state ids: {preview}{suffix}')

    print()


def _doc_identifier(layout, fallback_name):
    """The identifier used for a layout's doc filename.

    Reuses build_record's canonical identifier so a layout's reference doc
    (<identifier>_reference.md) lines up with its installer identifier. Falls
    back to a sanitized layout name if that derivation is unavailable.
    """

    try:
        from keylayout_to_xkb.install.build_record import _identifier_from
        return _identifier_from(layout)
    except Exception:
        return (fallback_name or 'layout').lower().replace(' ', '_')


def _sanitize_token(text):
    """Strictest filesystem/shell-safe token: lowercase, only [a-z0-9_].

    Runs of any other character collapse to a single underscore; leading digits
    and underscores are stripped (so the result is also a valid Python module
    name, not just shell-safe). Returns '' if nothing usable remains.
    """

    lowered = text.lower()
    safe = []
    for char in lowered:
        safe.append(char if (char.isascii() and (char.isalnum() or char == '_'))
                    else '_')
    token = ''.join(safe)
    while '__' in token:
        token = token.replace('__', '_')
    token = token.strip('_')
    while token and token[0].isdigit():
        token = token[1:].lstrip('_')
    return token


def _confirm(prompt):
    """Yes/no confirmation. Returns True/False; on no TTY returns None so the
    caller can apply its non-interactive policy instead of guessing."""

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    try:
        return input(prompt).strip().lower() in ('y', 'yes')
    except (EOFError, KeyboardInterrupt):
        return False


def _versioned_path(path):
    """First non-existing variant of path, inserting _2, _3, ... before the
    extension (the original is the implicit 1)."""

    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    counter = 2
    while os.path.exists('%s_%d%s' % (base, counter, ext)):
        counter += 1
    return '%s_%d%s' % (base, counter, ext)


def _versioned_dir(path):
    """First non-existing variant of a directory path, appending _2, _3, ..."""

    if not os.path.exists(path):
        return path
    counter = 2
    while os.path.exists('%s_%d' % (path, counter)):
        counter += 1
    return '%s_%d' % (path, counter)


def _resolve_installer_filename(given, layout_count=0):
    """Resolve the bundled single-file installer path.

    given is None/'' (auto-name) or a user string. Auto: a count-based name,
    'install_xkb_layouts_<N>.py' -- deliberately generic (no filter or language
    hints; the count is the one honest summary of any selection). User-supplied: sanitized strictest, .py
    enforced, NO forced install_ prefix or _kl2xkb suffix (those are only for
    auto-named files). If sanitization changed a user name, confirm it (or use
    it silently when no TTY). Returns the path, or None to abort.
    """

    if not given:
        return 'install_xkb_layouts_%d.py' % layout_count

    # Preserve any directory the user gave; sanitize only the filename part.
    directory = os.path.dirname(given)
    base = os.path.basename(given)
    stem = base[:-3] if base.endswith('.py') else base
    token = _sanitize_token(stem)
    if not token:
        warn('main', f'{given!r} has no usable filename')
        return None
    resolved_base = token + '.py'

    if resolved_base != base:
        answer = _confirm(
            'Using sanitized name %r (from %r). Proceed? [y/N] '
            % (resolved_base, base))
        if answer is False:
            print('Aborted; re-run with a clean name.', file=sys.stderr)
            return None
        # answer True or None (no TTY) -> use the sanitized name.
    return os.path.join(directory, resolved_base) if directory else resolved_base


def _resolve_installer_dir(given):
    """Resolve the --separate output directory.

    given is None/'' (auto-name 'generated_xkb_layouts_<timestamp>') or a user
    string (sanitized strictest, confirmed if changed). Returns the dir, or None
    to abort.
    """

    if not given:
        import time
        return 'generated_xkb_layouts_%s' % time.strftime('%Y%m%d_%H%M%S')

    parent = os.path.dirname(given)
    base = os.path.basename(given)
    token = _sanitize_token(base)
    if not token:
        warn('main', f'{given!r} has no usable folder name')
        return None
    if token != base:
        answer = _confirm(
            'Using sanitized folder %r (from %r). Proceed? [y/N] '
            % (token, base))
        if answer is False:
            print('Aborted; re-run with a clean folder name.', file=sys.stderr)
            return None
    return os.path.join(parent, token) if parent else token


def _resolve_collision_file(path):
    """Handle an existing target file. TTY: ask overwrite/rename/abort. No TTY:
    version it. Returns the path to write (possibly new), or None to abort."""

    if not os.path.exists(path):
        return path
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _versioned_path(path)            # non-interactive: version
    while True:
        try:
            choice = input(
                '%s exists. [o]verwrite, [r]ename, [a]bort? ' % path
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if choice in ('o', 'overwrite'):
            return path
        if choice in ('a', 'abort', ''):
            return None
        if choice in ('r', 'rename'):
            new = input('New filename: ').strip()
            resolved = _resolve_installer_filename(new)
            if resolved is None:
                return None
            if not os.path.exists(resolved):
                return resolved
            path = resolved                     # loop to re-resolve collision


def _resolve_collision_dir(path):
    """Handle an existing target directory. TTY: ask. No TTY: version it."""

    if not os.path.exists(path):
        return path
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _versioned_dir(path)
    while True:
        try:
            choice = input(
                '%s/ exists. [u]se anyway, [r]ename, [a]bort? ' % path
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if choice in ('u', 'use'):
            return path
        if choice in ('a', 'abort', ''):
            return None
        if choice in ('r', 'rename'):
            new = input('New folder name: ').strip()
            resolved = _resolve_installer_dir(new)
            if resolved is None:
                return None
            if not os.path.exists(resolved):
                return resolved
            path = resolved


def _write_installer(path, text):
    """Write a generated installer to path and mark it executable.

    The installer carries a #!/usr/bin/env python3 shebang, so setting the
    execute bit lets it run as ./installer.py directly. The chmod is best-effort:
    on filesystems that do not support Unix permissions it is silently dropped,
    and the file still runs via `python3 installer.py`, so a chmod failure warns
    but does not abort. Returns True on a successful write (regardless of chmod).
    """

    try:
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
    except OSError as error:
        warn('main', f'cannot write {path}: {error}')
        return False
    try:
        current_mode = os.stat(path).st_mode
        os.chmod(path, current_mode | 0o111)        # add execute for u/g/o
    except OSError as error:
        warn('main', f'wrote {path} but could not set execute bit: {error} '
                     f'(run it with `python3 {os.path.basename(path)}`)')
    return True


def _manage(args) -> int:
    """Handle the host-side management actions: list / uninstall / uninstall-all.

    Uses the shared runtime_core engine against the local ~/.config/xkb tree.
    Linux-only (that tree is a Linux mechanism); refuses politely elsewhere.
    """

    import sys as _sys
    if not _sys.platform.startswith('linux'):
        warn('main', 'install management works on Linux only '
                     '(it operates on ~/.config/xkb)')
        return 2

    from keylayout_to_xkb.install import runtime_core as rc

    if args.list_installed:
        installed = rc.list_installed()
        if not installed:
            print('No kl2xkb layouts are installed.')
            return 0
        print('Installed layouts (%d):' % len(installed))
        for record in sorted(installed, key=lambda r: r['identifier']):
            variants = ', '.join(v[0] for v in record['variants'])
            print('  %-18s %s)  [%s]'
                  % (record['identifier'], record['display_name'], variants))
        return 0

    if args.uninstall_all:
        result = rc.uninstall_all()
        if result['removed']:
            print('Removed: %s' % ', '.join(result['removed']))
            print('Log out and back in for the change to take effect.')
        else:
            print('Nothing was installed; nothing to remove.')
        return 0

    if args.uninstall:
        result = rc.uninstall_one(args.uninstall)
        if result['removed'] is None:
            warn('main', f'{args.uninstall!r} is not installed')
            return 1
        print('Removed %s (%d layout(s) remain).'
              % (result['removed'], result['installed_count']))
        print('Log out and back in for the change to take effect.')
        return 0

    return 0


def main(argv: 'list[str] | None' = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    set_debug(args.debug)

    # Management actions operate on the local Linux install via the shared
    # runtime_core engine (the same code the generated installers embed). They
    # need no extraction or source layouts, so they run and return early.
    if args.list_installed or args.uninstall or args.uninstall_all:
        return _manage(args)

    if args.uchr_file or args.uchr_dir:
        # Off-Mac path: parse local .uchr files instead of extracting.
        file_paths = list(args.uchr_file or [])
        if args.uchr_dir:
            try:
                for entry in sorted(os.listdir(args.uchr_dir)):
                    if entry.endswith('.uchr'):
                        file_paths.append(os.path.join(args.uchr_dir, entry))
            except OSError as error:
                warn('main', f'cannot read --uchr-dir: {error}')
                return 2
        if not file_paths:
            warn('main', 'no .uchr files found to process')
            return 2
        payloads = []
        for path in file_paths:
            try:
                with open(path, 'rb') as handle:
                    file_data = handle.read()
            except OSError as error:
                warn('main', f'cannot read {path!r}: {error}')
                return 2
            base = os.path.basename(path)
            name = base.replace('com_apple_keylayout_', '').replace('.uchr', '')
            payloads.append({'name': name, 'source_id': '', 'data': file_data})
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
    built_pairs = []                                    # (record, layout) pairs

    for payload in payloads:
        # Fold to pure ASCII at the single entry point: everything downstream
        # (summaries, records, name[Group1], the registry) inherits it, so an
        # en dash in a TIS name can never again reach an ASCII-locale consumer.
        name = fold_name_to_ascii(payload.get('name') or '')
        source_id = payload.get('source_id') or ''
        data = payload.get('data')
        languages = payload.get('languages') or []

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
            layout = parse_uchr(data, layout_name=name, source_id=source_id,
                                languages=languages)
        except UchrParseError as error:
            failed += 1
            warn('main', f'parse failed for {name!r}: {error}')
            continue

        _summarize(layout, raw_len=len(data))
        parsed_count += 1

        if args.make_installer is not None:
            from keylayout_to_xkb.install.build_record import build_record
            from keylayout_to_xkb.install.generate import _record_to_dict
            record = build_record(layout)
            print('  base layout:    %s'
                  % _record_to_dict(record).get('base_layout', '?'))
            built_records.append(record)
            built_pairs.append((record, layout))

        if args.docs:
            from keylayout_to_xkb.emit.docs import generate_layout_doc
            identifier = _doc_identifier(layout, name)
            try:
                os.makedirs(args.docs, exist_ok=True)
                doc_path = os.path.join(args.docs, '%s_reference.md' % identifier)
                with open(doc_path, 'w', encoding='utf-8') as handle:
                    handle.write(generate_layout_doc(layout))
                dbg('main', f'wrote doc {doc_path}')
            except OSError as error:
                warn('main', f'cannot write doc for {name!r}: {error}')

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

    if args.make_installer is not None:
        if not built_records:
            warn('main', 'no layouts matched; nothing to put in the installer')
            return 1
        from keylayout_to_xkb.install.generate import (
            generate_installer, installer_stamp_line,
        )
        _stamp = installer_stamp_line(built_records)

        if args.separate:
            # One installer per layout in an auto-named (or given) directory.
            directory = _resolve_installer_dir(args.make_installer)
            if directory is None:
                return 2
            directory = _resolve_collision_dir(directory)
            if directory is None:
                print('Aborted.', file=sys.stderr)
                return 2
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as error:
                warn('main', f'cannot create installer dir: {error}')
                return 2
            written = []
            for record in built_records:
                text = generate_installer([record])
                # Auto-named per-layout files: install_<id>_kl2xkb.py
                out_path = os.path.join(
                    directory, 'install_%s_kl2xkb.py' % record.identifier)
                out_path = _resolve_collision_file(out_path)
                if out_path is None:
                    print('Aborted.', file=sys.stderr)
                    return 2
                if not _write_installer(out_path, text):
                    return 2
                written.append(os.path.basename(out_path))
            print(
                f'wrote {len(written)} installer(s) to {directory}/ '
                f'({", ".join(written)})',
                file=sys.stderr,
            )
            print(f'  [{_stamp}]', file=sys.stderr)
            return 0

        # Single bundled installer.
        out_path = _resolve_installer_filename(
            args.make_installer, layout_count=len(built_records))
        if out_path is None:
            return 2
        out_path = _resolve_collision_file(out_path)
        if out_path is None:
            print('Aborted.', file=sys.stderr)
            return 2
        text = generate_installer(built_records)
        if not _write_installer(out_path, text):
            return 2
        ids = ', '.join(r.identifier for r in built_records)
        print(
            f'wrote installer {out_path} '
            f'({len(built_records)} layout(s): {ids})',
            file=sys.stderr,
        )
        print(f'  [{_stamp}]', file=sys.stderr)
        return 0

    if parsed_count == 0:
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())


# End of file #
