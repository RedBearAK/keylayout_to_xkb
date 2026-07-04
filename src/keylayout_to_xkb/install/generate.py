"""
keylayout_to_xkb/install/generate.py

Generate a SELF-CONTAINED installer file from one or more LayoutRecords.

The output is a single runnable Python file that embeds the records as a data
literal plus all the install logic inline, so it runs on a machine that does NOT
have keylayout_to_xkb installed -- copy the one file, run it. It writes the
layouts into the per-user XKB tree (~/.config/xkb), idempotently.

WHY EMBED THE LOGIC. The installer file cannot import keylayout_to_xkb (the
point is to need nothing else), so the install runtime is carried inline. To
avoid drift from the tested installer.py, the embedded runtime is a compact
dict-based mirror of the same contract (wholesale regeneration, hash-skip,
manifest), exercised by the same kinds of tests.

FILE STRUCTURE (as agreed): logic first (readable when opened), the big record
payloads last, and the single launcher line at the very bottom -- so a reader
sees what it does before the embedded data, and only the final line executes.
"""

import json

from keylayout_to_xkb.common.debug import warn
from keylayout_to_xkb.install.catalog import LayoutRecord


__version__ = '20260704'


def _record_to_dict(record: LayoutRecord) -> 'dict':
    """Serialise a record to the plain dict the embedded runtime consumes.

    Language/country resolution, best source first:
      1. Live TIS languages captured at extraction (record.languages), the
         authoritative Apple list -- may be several for a multilingual layout.
      2. The baked static table (language_data), for off-Mac generation or a
         layout the live read missed.
      3. The name-based heuristic (language_iso_codes), last resort.
    The country list (for flags) is derived by mapping each ISO 639 language to
    its canonical ISO 3166 country; a language with no country mapping simply
    contributes no flag rather than a wrong one.
    """

    from keylayout_to_xkb.install.catalog import language_iso_codes
    from keylayout_to_xkb.install.language_data import (
        tis_languages_for, country_for_language, base_layout_for_language,
        base_layout_is_known,
    )

    languages = []
    if getattr(record, 'source_languages', None):
        languages = list(record.source_languages)              # 1. live TIS (full)
    if not languages:
        staged = tis_languages_for(record.source_id, record.display_name)
        if staged:
            languages = list(staged)                           # 2. static (primary)

    iso_languages = []
    iso_countries = []
    short_desc = None

    def _bare(code):
        # 'ff-Adlm' / 'en_US' -> 'ff' / 'en'
        return code.split('-')[0].split('_')[0].lower()

    if languages:
        # Apple's list is "languages this layout CAN type" (every language in its
        # script) -- long for Latin layouts. The FULL list goes to languageList
        # (XKB's own "can type these" feature), but the FIRST entry is the layout's
        # PRIMARY language and is the ONLY thing that drives the flag + tray label
        # (a flag is a single visual claim; dozens of flags would be nonsense).
        for code in languages:
            bare = _bare(code)
            if bare and bare not in iso_languages:
                iso_languages.append(bare)
        primary = _bare(languages[0])
        short_desc = primary
        country = country_for_language(primary)
        if country:
            iso_countries = [country]                          # primary flag only
    else:
        # 3. name heuristic (off-Mac / XML-sourced). iso3166 may be None for a
        # known language with no single-country flag -- then: grouping, no flag.
        codes = language_iso_codes(record.language)
        if codes:
            iso639, iso3166, short = codes
            iso_languages = [_bare(iso639)]
            if iso3166:
                iso_countries = [iso3166]
            short_desc = short

    # Base layout to register our variants under, from the primary language. Use
    # short_desc (the ISO 639-1 code, e.g. 'pl') as the key -- iso_languages may
    # carry 639-2 ('pol') from the name heuristic, which the base map does not key
    # on. The variant is namespaced (mac-k2x-*) so it never collides with a system
    # variant of that base. 'us' fallback when the language has no system base.
    base_key = short_desc or (iso_languages[0] if iso_languages else 'en')
    base_layout = base_layout_for_language(base_key)
    if not base_layout_is_known(base_key):
        # Neither mapped nor in the verified-baseless set: a language nobody
        # has classified yet (e.g. a layout Apple added after the last
        # registry cross-reference). Grouping under 'us' silently is how the
        # Inuktitut family hid there for weeks -- so be loud about it.
        warn('base', f'{record.display_name!r}: language {base_key!r} is '
             'unclassified (not in the base map nor the known-baseless set); '
             'grouping under us. Classify it in language_data.py.')

    return {
        'identifier': record.identifier,
        'display_name': record.display_name,
        'language': record.language,
        'source_id': record.source_id,
        'variants': [list(v) for v in record.variants],   # JSON-friendly
        'compose_text': record.compose_text,
        'compose_complete': record.compose_complete,
        'dead_key_count': record.dead_key_count,
        'key_count': record.key_count,
        'iso_languages': iso_languages,   # ISO 639 list (may be multiple)
        'iso_countries': iso_countries,   # ISO 3166 list -> flags
        'short_desc': short_desc,         # tray label, or None
        'base_layout': base_layout,       # system layout our variants attach to
    }


# The embedded runtime: the standalone installer's whole body EXCEPT the record
# data and the launcher. Kept as a plain string so the generator can write it
# verbatim. It mirrors installer.py's contract but operates on dicts and needs
# only the standard library. Logic lives here (top of the output file); the
# record literal and launcher are appended after it.
_RUNTIME_PREAMBLE = r'''#!/usr/bin/env python3
"""
Self-contained installer for Apple macOS keyboard layouts converted to XKB.

Generated by keylayout_to_xkb. Not affiliated with or endorsed by Apple.

Installs the embedded layout(s) into the system XKB tree (/usr/share/X11/xkb,
recommended: visible to KDE's settings and previews) or your per-user tree
(~/.config/xkb, rootless; libxkbcommon >= 0.10.0). After installing, LOG OUT
and back in so the compositor rebuilds its keymap and the layout appears in
your keyboard settings picker.

Usage:
  python3 THIS_FILE.py                 interactive menu
  python3 THIS_FILE.py --list          list embedded layouts
  python3 THIS_FILE.py --install ID    install one layout by identifier
  python3 THIS_FILE.py --install-lang L install all layouts in a language group
  python3 THIS_FILE.py --install-all   install every embedded layout (user)
  python3 THIS_FILE.py --install-all-system  install every layout, symbols in
                                       the system location (keyboard preview
                                       works; uses sudo/doas/run0 as needed)
  python3 THIS_FILE.py --dump DIR      write the raw XKB/Compose files to DIR
  python3 THIS_FILE.py --force ...     rewrite even if unchanged
  python3 THIS_FILE.py --preview ID    print a layout's structured summary
"""

import os
import sys
import json
import zlib
import pydoc
import base64
import shutil
import hashlib
import tempfile
import subprocess

from xml.sax.saxutils import escape as _xml_escape


'''


_RUNTIME_UI = r'''
def _by_identifier(ident):
    for record in RECORDS:
        if record['identifier'] == ident:
            return record
    return None


def _by_language(language):
    return [r for r in RECORDS if r['language'].lower() == language.lower()]


def _print_list():
    groups = {}
    for record in RECORDS:
        groups.setdefault(record['language'], []).append(record)
    for language in sorted(groups):
        print('%s:' % language)
        for record in sorted(groups[language], key=lambda r: r['display_name']):
            variants = ', '.join(v[0] for v in record['variants'])
            flag = '' if record['compose_complete'] else '  [compose partial]'
            print('  %-14s %s)  [%s]%s'
                  % (record['identifier'], record['display_name'], variants, flag))


def _print_preview(ident):
    record = _by_identifier(ident)
    if record is None:
        print('no such layout: %s' % ident)
        return 1
    print('%s)' % record['display_name'])
    print('  identifier:   %s' % record['identifier'])
    print('  language:     %s' % record['language'])
    print('  source:       %s' % (record['source_id'] or '(unknown)'))
    print('  variants:     %s' % ', '.join(v[0] for v in record['variants']))
    print('  keys:         %d' % record['key_count'])
    print('  dead keys:    %d' % record['dead_key_count'])
    print('  compose:      %d bytes, %s'
          % (len(record['compose_text']),
             'complete' if record['compose_complete'] else 'PARTIAL'))
    return 0


def _report(report):
    if report.get('rolled_back'):
        print('INSTALL FAILED -- rolled back; nothing was changed.')
        print('')
        print('The assembled keymap did not compile, so the install was undone')
        print('to protect your session. Affected layout(s):')
        for identifier, variant, message in report.get('failures', []):
            print('  %s (%s): %s' % (identifier, variant, message))
        print('')
        print('Your existing keyboard configuration is untouched.')
        return
    if report['all_unchanged']:
        print('Already up to date (nothing changed); no need to log out.')
        return
    if report['added']:
        print('Installed: %s' % ', '.join(report['added']))
    if report['refreshed']:
        print('Refreshed: %s' % ', '.join(report['refreshed']))
    print('Files written under %s' % report['paths'].root)
    print('')
    print('Some Linux desktop environments might require a log out to allow')
    print('picking the new layout(s).')


def _require_linux():
    """Refuse a mutating verb anywhere but Linux.

    Installation writes XKB layouts into the system tree
    (/usr/share/X11/xkb) or the per-user tree (~/.config/xkb) -- Linux
    mechanisms with no equivalent elsewhere. Called by the verbs that WRITE
    (and by the interactive menu); the inspection verbs (--list, --preview,
    --dump, --help) work on any platform.
    """

    if not sys.platform.startswith('linux'):
        sys.stderr.write(
            'Installation runs on Linux only (system tree or ~/.config/xkb).\n')
        if sys.platform == 'darwin':
            sys.stderr.write(
                'On macOS these layouts already exist natively. You can still '
                'inspect this bundle here:\n'
                '  --list, --preview ID, --dump DIR, --help\n')
        sys.exit(2)


_SYSTEM_VERBS = ('--system-apply', '--system-remove')


def _refuse_root():
    """Refuse to run as root, except for the two narrow system verbs.

    Everything except --system-apply/--system-remove targets your personal
    ~/.config/xkb: running THAT as root would write root-owned files into the
    wrong home and install nothing for your normal user. The system verbs are
    the ONLY root-legitimate operations (they copy pre-staged, marker-verified
    symbols files into /usr/share/X11/xkb/symbols); the interactive menu and
    --install-all-system invoke them through sudo/doas/run0/sudo-rs for you,
    so there is still no reason to launch this installer with sudo yourself.
    Catches plain root, sudo, and a setuid-root binary alike (all give euid 0).
    """

    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        sys.stderr.write(
            'Refusing to run as root. This installer runs as your normal '
            'user and escalates by itself\n'
            'for the system-location step (the menu and '
            '--install-all-system handle it).\n')
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user:
            sys.stderr.write(
                'It looks like you used sudo -- run it directly as %s '
                '(no sudo).\n' % sudo_user)
        sys.exit(2)


def _require_root(verb):
    """The system verbs write to /usr and MUST run as root."""

    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        sys.stderr.write(
            '%s writes to the system XKB tree and must run as root.\n'
            'Use the interactive menu or --install-all-system, which '
            'escalate for you.\n' % verb)
        sys.exit(2)


def main(argv):
    force = '--force' in argv
    argv = [a for a in argv if a != '--force']
    cmd = argv[0] if argv else ''

    # The two root-only verbs dispatch BEFORE the root refusal; everything
    # else keeps the absolute refusal.
    if cmd == '--system-apply' and len(argv) > 1:
        _require_linux()
        _require_root(cmd)
        result = system_apply(argv[1])
        for name in result['written']:
            print('system: wrote symbols/%s' % name)
        for line in result['refused']:
            print('system: REFUSED %s' % line)
        if result['error']:
            print('system: FAILED and rolled back: %s' % result['error'])
            return 1
        return 0 if not result['refused'] else 1
    if cmd == '--system-remove':
        _require_linux()
        _require_root(cmd)
        result = system_remove()
        for name in result['removed']:
            print('system: removed symbols/%s' % name)
        for line in result['skipped']:
            print('system: skipped %s' % line)
        return 0

    if not argv:
        _require_linux()
        _refuse_root()
        return _menu(force=force)

    if cmd == '--list':
        _print_list(); return 0
    if cmd == '--preview' and len(argv) > 1:
        return _print_preview(argv[1])
    if cmd == '--install' and len(argv) > 1:
        _require_linux()
        _refuse_root()
        record = _by_identifier(argv[1])
        if record is None:
            print('no such layout: %s' % argv[1]); return 1
        _report(install_records([record], force=force)); return 0
    if cmd == '--install-lang' and len(argv) > 1:
        _require_linux()
        _refuse_root()
        records = _by_language(argv[1])
        if not records:
            print('no layouts in language group: %s' % argv[1]); return 1
        _report(install_records(records, force=force)); return 0
    if cmd == '--install-all':
        _require_linux()
        _refuse_root()
        _report(install_records(list(RECORDS), force=force)); return 0
    if cmd == '--install-all-system':
        _require_linux()
        _refuse_root()
        return _system_install_records(list(RECORDS), force=force)
    if cmd == '--dump' and len(argv) > 1:
        _refuse_root()
        written = dump_records(list(RECORDS), argv[1])
        print('Wrote %d files to %s' % (len(written), argv[1])); return 0
    if cmd == '--list-installed':
        _require_linux()
        _refuse_root()
        installed = list_installed()
        if not installed:
            print('No kl2xkb layouts are installed.'); return 0
        print('Installed layouts (%d):' % len(installed))
        for record in sorted(installed, key=lambda r: r['identifier']):
            print('  %-18s %s)' % (record['identifier'], record['display_name']))
        return 0
    if cmd == '--uninstall' and len(argv) > 1:
        _require_linux()
        _refuse_root()
        result = uninstall_one(argv[1], force=force)
        if result['removed'] is None:
            print('not installed: %s' % argv[1]); return 1
        print('Removed %s (%d remain). Some desktops might need a log out.'
              % (result['removed'], result['installed_count'])); return 0
    if cmd == '--uninstall-all':
        _require_linux()
        _refuse_root()
        result = uninstall_all()
        if result['removed']:
            print('Removed: %s' % ', '.join(result['removed']))
            print('Some Linux desktops might need a log out.')
        else:
            print('Nothing installed; nothing to remove.')
        return 0

    print(__doc__)
    return 0


def _enter_screen():
    """Switch to the terminal's alternate screen buffer (like less/vim), so the
    menu has its own screen and the user's prompt and scrollback are restored
    untouched on exit. No-op when stdout is not a terminal (piped/redirected)."""

    if sys.stdout.isatty():
        sys.stdout.write('\033[?1049h\033[H')
        sys.stdout.flush()


def _leave_screen():
    """Restore the normal screen buffer (prompt and history come back)."""

    if sys.stdout.isatty():
        sys.stdout.write('\033[?1049l')
        sys.stdout.flush()


def _clear():
    """Clear the alternate screen and home the cursor."""

    if sys.stdout.isatty():
        sys.stdout.write('\033[H\033[2J')
        sys.stdout.flush()


def _pause(message='Press Enter to continue...'):
    """Wait for the user before leaving a screen (so output is readable)."""

    try:
        input('\n' + message)
    except (EOFError, KeyboardInterrupt):
        pass


def _page(text):
    """Show long text through the system pager (less/$PAGER), via the stdlib
    pydoc pager, which falls back to plain printing when no pager exists.

    The pager (less) runs its OWN alternate-screen switch and, on quit, restores
    the NORMAL screen -- which drops us out of the TUI's alternate buffer. Without
    re-entering, the menu would then draw on the real screen and leave remnants
    mixed with the prompt on exit. So re-assert our alternate screen right after
    the pager returns, putting us back in the buffer the TUI's exit will restore.
    """

    pydoc.pager(text)
    _enter_screen()


def _ask(prompt):
    """Prompt for a line of input. Returns '' on EOF/Ctrl-C so callers treat it
    as 'go back' rather than crashing."""

    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ''


def _grouped_records():
    """RECORDS grouped by language, languages sorted, records sorted by name."""

    groups = {}
    for record in RECORDS:
        groups.setdefault(record['language'], []).append(record)
    for language in groups:
        groups[language].sort(key=lambda r: r['display_name'])
    return groups


def _list_text():
    """The full embedded-layout listing as a string (for paging)."""

    lines = ['Layouts in this installer:', '']
    for language in sorted(_grouped_records()):
        lines.append('%s:' % language)
        for record in _grouped_records()[language]:
            variants = ', '.join(v[0] for v in record['variants'])
            flag = '' if record['compose_complete'] else '  [compose partial]'
            lines.append('  %-16s %s)  [%s]%s'
                         % (record['identifier'], record['display_name'],
                            variants, flag))
        lines.append('')
    return '\n'.join(lines)


def _preview_text(ident):
    """A layout's structured summary as a string (for paging)."""

    record = _by_identifier(ident)
    if record is None:
        return 'No such layout: %s' % ident
    lines = [
        '%s)' % record['display_name'],
        '  identifier:   %s' % record['identifier'],
        '  language:     %s' % record['language'],
        '  source:       %s' % (record['source_id'] or '(unknown)'),
        '  variants:     %s' % ', '.join(v[0] for v in record['variants']),
        '  keys:         %d' % record['key_count'],
        '  dead keys:    %d' % record['dead_key_count'],
        '  compose:      %d bytes, %s' % (
            len(record['compose_text']),
            'complete' if record['compose_complete'] else 'PARTIAL'),
    ]
    return '\n'.join(lines)


def _numbered_records():
    """A flat, numbered list of all records (language-grouped order) for
    selection. Returns a list so index N-1 maps to choice N."""

    ordered = []
    for language in sorted(_grouped_records()):
        ordered.extend(_grouped_records()[language])
    return ordered


def _select_records():
    """Selection screen: show a numbered list, accept a number, a comma-list of
    numbers, 'all', or a language name. Returns the chosen records, or [] to go
    back. Loops on invalid input rather than failing."""

    ordered = _numbered_records()
    while True:
        _clear()
        print('Select layouts to install')
        print('=' * 40)
        current_language = None
        for index, record in enumerate(ordered, start=1):
            if record['language'] != current_language:
                current_language = record['language']
                print('\n%s:' % current_language)
            variants = ', '.join(v[0] for v in record['variants'])
            print('  %2d) %-16s %s)  [%s]'
                  % (index, record['identifier'], record['display_name'],
                     variants))
        print('')
        print('Enter a number, a comma-list (e.g. 1,3,4), a language name,')
        print("'all' for everything, or blank to go back.")
        answer = _ask('> ')

        if not answer:
            return []
        if answer.lower() == 'all':
            return list(ordered)

        # A language name?
        by_lang = [r for r in ordered if r['language'].lower() == answer.lower()]
        if by_lang:
            return by_lang

        # A number or comma-list of numbers.
        chosen = []
        ok = True
        for token in answer.replace(' ', '').split(','):
            if not token.isdigit() or not (1 <= int(token) <= len(ordered)):
                ok = False
                break
            chosen.append(ordered[int(token) - 1])
        if ok and chosen:
            # de-duplicate while preserving order
            seen = set()
            unique = []
            for record in chosen:
                if record['identifier'] not in seen:
                    seen.add(record['identifier'])
                    unique.append(record)
            return unique

        _clear()
        print('Did not understand %r. Try a number, comma-list, language, or '
              "'all'." % answer)
        _pause()


def _escalate_run(extra_args):
    """Run this installer with 'extra_args' through the first available
    privilege-escalation tool (Toshy convention: sudo, doas, run0, sudo-rs).

    Runs interactively so the tool can prompt for a password on the user's
    terminal. Returns the child's exit code, or None when no escalation tool
    exists -- in which case the exact command is printed for manual use, so
    the user is never stuck.
    """

    script = os.path.abspath(__file__)
    command_tail = [sys.executable, script] + list(extra_args)
    for tool in ('sudo', 'doas', 'run0', 'sudo-rs'):
        if shutil.which(tool):
            print('Escalating with %s for the system-location step...' % tool)
            return subprocess.call([tool] + command_tail)
    print('No escalation tool found (looked for sudo, doas, run0, sudo-rs).')
    print('Run this yourself as root, then re-run the installer:')
    print('  ' + ' '.join(command_tail))
    return None


def _system_install_records(records, force=False):
    """The system-mode install: validate as the user, escalate for symbols,
    then finish the user-side files.

    Sequence: (1) decline early on read-only /usr (immutable distros);
    (2) build and VALIDATE the complete tree in a throwaway user-style root,
    so nothing broken is ever staged; (3) stage the symbols files and
    escalate for --system-apply; (4) on success, write the user-side pieces
    (rules/registry/compose) WITHOUT symbols -- which also removes any stale
    user-dir symbols files, since a user copy would shadow the system one --
    and validate the final assembly, which now resolves against the system
    files.
    """

    if not system_symbols_writable():
        print('The system XKB location is on a read-only filesystem')
        print('(immutable distro?). System mode is not possible here;')
        print('use the user-location install instead (keyboard preview')
        print('will not work there).')
        return 1

    check_root = tempfile.mkdtemp(prefix='kl2xkb-precheck-')
    try:
        report = install_records(records, paths=InstallPaths(root=check_root),
                                 force=True)
        if report.get('validation') == 'failed':
            print('Pre-validation FAILED; nothing was installed anywhere:')
            _report(report)
            return 1
    finally:
        shutil.rmtree(check_root, ignore_errors=True)

    staged = tempfile.mkdtemp(prefix='kl2xkb-stage-')
    keep_staged = False
    try:
        names = stage_system_symbols(records, staged)
        print('Staged %d symbols file(s): %s' % (len(names), ', '.join(names)))
        code = _escalate_run(['--system-apply', staged])
        if code is None:
            # The printed manual command references the staged dir: keep it,
            # or the instructions would point at a deleted path.
            keep_staged = True
            print('(The staged files are kept at %s until you do.)' % staged)
            return 1
        if code != 0:
            print('System apply did not complete (exit %d); the user-side'
                  % code)
            print('files were left untouched.')
            return code

        report = install_records(records, force=force, write_symbols=False)
        _report(report)
        print('')
        print('Symbols are in the system location: the keyboard preview and')
        print('Xorg sessions can use these layouts. Uninstalling later needs')
        print('the same escalation (the Manage menu handles it).')
        return 0
    finally:
        if not keep_staged:
            shutil.rmtree(staged, ignore_errors=True)


def _confirm_and_install(records, force=False):
    """Show the selection, ask the destination, confirm, then install.

    System mode (symbols in /usr/share/X11/xkb/symbols) is the primary path:
    it is the only placement the X server's own compiler sees, which is what
    makes the KDE keyboard preview and pure Xorg sessions work. User mode
    stays available for machines without escalation or with read-only /usr.
    """

    if not records:
        return
    _clear()
    print('About to install %d layout(s):' % len(records))
    print('')
    for record in records:
        variants = ', '.join(v[0] for v in record['variants'])
        print('  %s)  [%s]' % (record['display_name'], variants))
    print('')
    print('Where should the layouts be installed?')
    print('  1) System location (recommended -- keyboard preview works;')
    print('     needs admin rights for one step) [default]')
    print('  2) User location only (no admin rights, but the keyboard')
    print('     preview will NOT work)')
    print('  blank) Cancel')
    answer = _ask('\nChoice [1]: ').strip().lower()
    if answer in ('', '1', 'system', 's'):
        _clear()
        _system_install_records(records, force=force)
        _pause()
        return
    if answer not in ('2', 'user', 'u'):
        _clear()
        print('Cancelled. Nothing was installed.')
        _pause()
        return
    _clear()
    print('Installing to the user location (keyboard preview will not work).')
    report = install_records(records, force=force)
    _report(report)
    _pause()


def _install_flow(force=False):
    """The Install branch: pick layouts, confirm, install."""

    records = _select_records()
    if records:
        _confirm_and_install(records, force=force)


def _preview_flow():
    """The Preview branch: pick one layout by number, page its summary."""

    ordered = _numbered_records()
    _clear()
    print('Preview a layout')
    print('=' * 40)
    for index, record in enumerate(ordered, start=1):
        print('  %2d) %-16s %s)'
              % (index, record['identifier'], record['display_name']))
    answer = _ask('\nNumber to preview (blank to go back): ')
    if not answer:
        return
    if answer.isdigit() and 1 <= int(answer) <= len(ordered):
        _page(_preview_text(ordered[int(answer) - 1]['identifier']))
    else:
        _clear()
        print('Not a valid number.')
        _pause()


def _dump_flow():
    """The Dump branch: write raw XKB/Compose files to a chosen directory."""

    _clear()
    print('Dump raw XKB/Compose files')
    print('=' * 40)
    print('This writes the symbols, variants, and Compose files to a directory')
    print('for inspection (it does NOT install them).')
    directory = _ask('\nTarget directory (blank to go back): ')
    if not directory:
        return
    _clear()
    try:
        dump_records(RECORDS, directory)
        print('Wrote raw files to %s' % directory)
    except OSError as error:
        print('Could not write to %s: %s' % (directory, error))
    _pause()


def _menu(force=False):
    """The guided, dependency-free interactive menu.

    Runs on bare `python3 install_*.py` with no options. Uses the terminal's
    alternate screen so each stage has its own screen and the user's prompt and
    scrollback return untouched on exit (including Ctrl-C). Every stage accepts a
    blank line / Ctrl-C to go back, and 'q' quits from the top level.
    """

    _enter_screen()
    try:
        while True:
            _clear()
            print('keylayout_to_xkb installer')
            print('  generator %s · build %s'
                  % (_GENERATOR_VERSION, _GENERATOR_BUILD))
            print('  generated %s' % _GENERATED_AT)
            print('=' * 40)
            print('%d layout(s) available in this installer.\n' % len(RECORDS))
            print('  1) Install layouts onto this system')
            print('  2) List layouts in this installer')
            print('  3) Preview a layout in this installer')
            print('  4) Manage layouts already installed on this system')
            print('  5) Dump raw XKB files (no install)')
            print('  q) Quit')
            answer = _ask('\nChoice: ').lower()

            if answer in ('q', 'quit', 'exit', ''):
                break
            if answer == '1':
                _install_flow(force=force)
            elif answer == '2':
                _page(_list_text())
            elif answer == '3':
                _preview_flow()
            elif answer == '4':
                _manage_flow(force=force)
            elif answer == '5':
                _dump_flow()
            else:
                _clear()
                print('Unknown choice: %r' % answer)
                _pause()
    finally:
        _leave_screen()
    return 0


def _manage_flow(force=False):
    """Show installed layouts and offer to uninstall one or all."""

    installed = list_installed()
    system_files = system_installed()
    _clear()
    print('Manage installed layouts')
    print('=' * 40)
    if not installed and not system_files:
        print('Nothing is installed.')
        _pause()
        return
    ordered = sorted(installed, key=lambda r: r['identifier'])
    for index, record in enumerate(ordered, start=1):
        print('  %2d) %-18s %s)'
              % (index, record['identifier'], record['display_name']))
    if system_files:
        print('')
        print('System-location symbols files (need admin rights to remove):')
        for name, present in system_files:
            print('     symbols/%s%s' % (name, '' if present else ' (missing)'))
    print('')
    print('Enter a number to uninstall that layout, "all" to remove every')
    if system_files:
        print('kl2xkb layout, "sys" to remove the system-location symbols')
        print('files (escalates), or blank to go back.')
    else:
        print('kl2xkb layout, or blank to go back.')
    answer = _ask('> ').strip().lower()

    if not answer:
        return
    if answer == 'sys' and system_files:
        confirm = _ask('Remove %d system symbols file(s)? [y/N] '
                       % len(system_files))
        if confirm.lower() in ('y', 'yes'):
            code = _escalate_run(['--system-remove'])
            _clear()
            if code == 0:
                print('System-location symbols removed.')
            else:
                print('System removal did not complete.')
            _pause()
        return
    if answer == 'all':
        confirm = _ask('Remove ALL %d installed layout(s)? [y/N] ' % len(ordered))
        if confirm.lower() in ('y', 'yes'):
            result = uninstall_all()
            _clear()
            print('Removed: %s' % ', '.join(result['removed']))
            print('\nSome Linux desktops might need a log out to update the picker.')
        else:
            _clear()
            print('Cancelled.')
        _pause()
        return
    if answer.isdigit() and 1 <= int(answer) <= len(ordered):
        record = ordered[int(answer) - 1]
        confirm = _ask('Uninstall %s? [y/N] ' % record['identifier'])
        if confirm.lower() in ('y', 'yes'):
            result = uninstall_one(record['identifier'], force=force)
            _clear()
            print('Removed %s (%d remain).'
                  % (result['removed'], result['installed_count']))
            print('\nSome Linux desktops might need a log out to update the picker.')
        else:
            _clear()
            print('Cancelled.')
        _pause()
        return
    _clear()
    print('Not a valid choice.')
    _pause()
'''



# The launcher that must come LAST (the only line that executes at run time).
_LAUNCHER = '''

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
'''


def _embedded_core_source() -> str:
    """The body of runtime_core.py, ready to splice into an installer.

    Reads the shared engine module's source and strips its module docstring,
    imports, and footer -- the installer's preamble already provides the imports,
    and the engine's functions/constants are pasted in directly. This is what
    makes the installer carry the SAME engine the host tool imports, with no
    hand-maintained copy to drift.
    """

    import os as _os
    here = _os.path.dirname(_os.path.abspath(__file__))
    source = open(_os.path.join(here, 'runtime_core.py'),
                  encoding='utf-8').read()
    lines = source.split('\n')
    kept = []
    skipping_header = True
    for line in lines:
        if skipping_header:
            # Skip until the first real definition (the _NAMESPACE constant),
            # dropping the docstring, imports, and __version__.
            if line.startswith('_NAMESPACE'):
                skipping_header = False
                kept.append(line)
            continue
        if line.strip() == '# End of file #':
            continue
        kept.append(line)
    return '\n'.join(kept).strip('\n')


def _module_versions():
    """Collect the __version__ dates of the modules whose code shapes an
    installer, as a readable composite like
    'rc20260701/gen20260702/br20260623/up20260702/uckt20260702'.

    Read defensively: a module missing __version__ contributes '00000000' so the
    composite is always well-formed. runtime_core (rc) leads because it is the
    embedded engine that most determines installer behavior. The EXTRACTION
    modules (up = uchr_parse, uckt = uckeytranslate) are stamped too: the
    records baked into an installer are only as good as the parse that built
    them, and an earlier stamp that omitted extraction hid a four-plane
    uckeytranslate behind an apparently current rc/gen/br composite.
    """

    import os as _os
    import re as _re

    def _ver(module_filename):
        here = _os.path.dirname(_os.path.abspath(__file__))
        text = _read_text(_os.path.join(here, module_filename))
        # House version format is YYYYMMDD with an optional letter suffix
        # ('20260703', '20260703b'). The pattern must accept the suffix: a
        # digits-only match once stamped letter-bumped modules as 00000000.
        match = _re.search(r"__version__\s*=\s*'(\d{8}[a-z]?)'", text or '')
        return match.group(1) if match else '00000000'

    return 'rc%s/gen%s/br%s/up%s/uckt%s' % (
        _ver('runtime_core.py'), _ver('generate.py'), _ver('build_record.py'),
        _ver(_os.path.join('..', 'extract', 'uchr_parse.py')),
        _ver(_os.path.join('..', 'extract', 'uckeytranslate.py')))


def _read_text(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return handle.read()
    except OSError:
        return ''


def _generator_stamp(code_body):
    """Return (code_version, build_hash, generated_at) identifying the generation
    of code that emitted an installer.

    code_version is the human-readable composite of module dates. build_hash is a
    short sha256 of the CODE body only (preamble + engine + UI) -- NOT the records
    or timestamp -- so it is identical across installers built from the same code
    but different layouts, and changes the moment any generator code changes.
    generated_at is when this specific file was produced.
    """

    import hashlib as _hashlib
    import datetime as _datetime

    code_version = _module_versions()
    build_hash = _hashlib.sha256(code_body.encode('utf-8')).hexdigest()[:8]
    generated_at = _datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return code_version, build_hash, generated_at


def generate_installer(records: 'list') -> str:
    """Return the full text of a self-contained installer file for the records.

    Assembled from three pieces: the preamble (shebang, docstring, imports), the
    shared engine source from runtime_core.py (so there is no drift from the host
    tool's logic), and the installer-only UI layer (menu, guards, argv handling).
    Then a generator stamp, the embedded RECORDS data literal, and the launcher
    line. The records are embedded as a JSON string parsed at startup, keeping the
    payload readable and avoiding quoting hazards.

    The generator stamp (code version + build hash + timestamp) is computed over
    the CODE body only, so any generator-code change shifts the hash while the
    same code across different layouts keeps a stable hash -- making it obvious at
    a glance (in the terminal and the menu) exactly which generation emitted a
    given installer.
    """

    payload = [_record_to_dict(record) for record in records]
    data_blob = json.dumps(payload, ensure_ascii=False, indent=1)

    # Assemble the CODE body first (preamble + engine + UI); hash that.
    code_body = ''.join([
        _RUNTIME_PREAMBLE, '\n\n\n',
        _embedded_core_source(), '\n\n',
        _RUNTIME_UI,
    ])
    code_version, build_hash, generated_at = _generator_stamp(code_body)

    stamp_block = (
        "\n\n# ---- generator stamp ----\n"
        "_GENERATOR_VERSION = %r\n"
        "_GENERATOR_BUILD = %r\n"
        "_GENERATED_AT = %r\n"
        % (code_version, build_hash, generated_at))

    # The JSON payload is zlib-compressed and base64-wrapped: the full
    # 241-layout catalog embeds several times smaller, and the installer
    # decodes with stdlib only. Wrapped at 76 columns via implicit string
    # concatenation so the generated file stays diffable and scrollable.
    import zlib
    import base64
    compressed = base64.b64encode(
        zlib.compress(data_blob.encode('utf-8'), 9)).decode('ascii')
    wrapped = '\n'.join(
        "    '%s'" % compressed[i:i + 76]
        for i in range(0, len(compressed), 76))

    parts = []
    parts.append(code_body)
    parts.append(stamp_block)
    parts.append('\n\n# ---- embedded layout records (compressed payload) ----\n')
    parts.append('_RECORDS_B64 = (\n')
    parts.append(wrapped)
    parts.append('\n)\n')
    parts.append('RECORDS = json.loads(\n'
                 '    zlib.decompress(base64.b64decode(_RECORDS_B64))'
                 ".decode('utf-8'))\n")
    parts.append('# normalise variant tuples (JSON gives lists)\n')
    parts.append("for _r in RECORDS:\n")
    parts.append("    _r['variants'] = [tuple(v) for v in _r['variants']]\n")
    parts.append(_LAUNCHER)
    return ''.join(parts)


def installer_stamp_line(records: 'list') -> str:
    """The one-line stamp for the terminal at generation time (host tool prints
    this alongside the 'wrote installer' message)."""

    code_body = ''.join([
        _RUNTIME_PREAMBLE, '\n\n\n',
        _embedded_core_source(), '\n\n',
        _RUNTIME_UI,
    ])
    code_version, build_hash, generated_at = _generator_stamp(code_body)
    return 'generator %s · build %s · %s' % (
        code_version, build_hash, generated_at)


# End of file #
