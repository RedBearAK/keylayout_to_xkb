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

from keylayout_to_xkb.install.catalog import LayoutRecord


__version__ = '20260623'


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

Installs the embedded layout(s) into your per-user XKB tree (~/.config/xkb),
which is rootless and works on Wayland sessions (libxkbcommon >= 0.10.0, 2020+).
After installing, LOG OUT and back in so the compositor rebuilds its keymap and
the layout appears in your keyboard settings picker.

Usage:
  python3 THIS_FILE.py                 interactive menu
  python3 THIS_FILE.py --list          list embedded layouts
  python3 THIS_FILE.py --install ID    install one layout by identifier
  python3 THIS_FILE.py --install-lang L install all layouts in a language group
  python3 THIS_FILE.py --install-all   install every embedded layout
  python3 THIS_FILE.py --dump DIR      write the raw XKB/Compose files to DIR
  python3 THIS_FILE.py --force ...     rewrite even if unchanged
  python3 THIS_FILE.py --preview ID    print a layout's structured summary
"""

import os
import sys
import json
import pydoc
import hashlib

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
    """Refuse to run anywhere but Linux.

    This installer writes Linux XKB layouts into the per-user ~/.config/xkb tree
    (libxkbcommon >= 0.10.0). That mechanism does not exist on macOS, Windows, or
    the BSDs, so running elsewhere would create files that do nothing. Generation
    (extracting from macOS) is the Mac-side operation; installation is Linux-only.
    """

    if not sys.platform.startswith('linux'):
        sys.stderr.write(
            'This installer runs on Linux only. It installs XKB keyboard '
            'layouts into your ~/.config/xkb directory, which is a Linux '
            'mechanism.\n')
        if sys.platform == 'darwin':
            sys.stderr.write(
                'On macOS these layouts already exist natively; this tool '
                'is for using them on Linux.\n')
        sys.exit(2)


def _refuse_root():
    """Refuse to run as root (absolute, no override).

    The installer targets your personal ~/.config/xkb. Running as root would
    write root-owned files into the wrong home directory and would not install
    the layout for your normal user. There is intentionally no --allow-root: if
    you want the files in a system location, use --dump and place them by hand.
    Catches plain root, sudo, and a setuid-root binary alike (all give euid 0).
    """

    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        sys.stderr.write(
            'Refusing to run as root. This installer writes to your personal '
            '~/.config/xkb and must run as your normal user.\n')
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user:
            sys.stderr.write(
                'It looks like you used sudo -- run it directly as %s '
                '(no sudo).\n' % sudo_user)
        sys.stderr.write(
            'To install into a system location instead, use --dump DIR and '
            'place the files by hand.\n')
        sys.exit(2)


def main(argv):
    _require_linux()
    _refuse_root()

    force = '--force' in argv
    argv = [a for a in argv if a != '--force']

    if not argv:
        return _menu(force=force)

    cmd = argv[0]
    if cmd == '--list':
        _print_list(); return 0
    if cmd == '--preview' and len(argv) > 1:
        return _print_preview(argv[1])
    if cmd == '--install' and len(argv) > 1:
        record = _by_identifier(argv[1])
        if record is None:
            print('no such layout: %s' % argv[1]); return 1
        _report(install_records([record], force=force)); return 0
    if cmd == '--install-lang' and len(argv) > 1:
        records = _by_language(argv[1])
        if not records:
            print('no layouts in language group: %s' % argv[1]); return 1
        _report(install_records(records, force=force)); return 0
    if cmd == '--install-all':
        _report(install_records(list(RECORDS), force=force)); return 0
    if cmd == '--dump' and len(argv) > 1:
        written = dump_records(list(RECORDS), argv[1])
        print('Wrote %d files to %s' % (len(written), argv[1])); return 0
    if cmd == '--list-installed':
        installed = list_installed()
        if not installed:
            print('No kl2xkb layouts are installed.'); return 0
        print('Installed layouts (%d):' % len(installed))
        for record in sorted(installed, key=lambda r: r['identifier']):
            print('  %-18s %s)' % (record['identifier'], record['display_name']))
        return 0
    if cmd == '--uninstall' and len(argv) > 1:
        result = uninstall_one(argv[1], force=force)
        if result['removed'] is None:
            print('not installed: %s' % argv[1]); return 1
        print('Removed %s (%d remain). Some desktops might need a log out.'
              % (result['removed'], result['installed_count'])); return 0
    if cmd == '--uninstall-all':
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


def _confirm_and_install(records, force=False):
    """Show the selection, confirm, then install via the real machinery."""

    if not records:
        return
    _clear()
    print('About to install %d layout(s):' % len(records))
    print('')
    for record in records:
        variants = ', '.join(v[0] for v in record['variants'])
        print('  %s)  [%s]' % (record['display_name'], variants))
    print('')
    print('Files go under %s' % user_xkb_root())
    answer = _ask('\nProceed? [y/N] ')
    if answer.lower() not in ('y', 'yes'):
        _clear()
        print('Cancelled. Nothing was installed.')
        _pause()
        return
    _clear()
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
    _clear()
    print('Manage installed layouts')
    print('=' * 40)
    if not installed:
        print('Nothing is installed.')
        _pause()
        return
    ordered = sorted(installed, key=lambda r: r['identifier'])
    for index, record in enumerate(ordered, start=1):
        print('  %2d) %-18s %s)'
              % (index, record['identifier'], record['display_name']))
    print('')
    print('Enter a number to uninstall that layout, "all" to remove every')
    print('kl2xkb layout, or blank to go back.')
    answer = _ask('> ').strip().lower()

    if not answer:
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
    installer, as a readable composite like 'rc20260701/gen20260623/br20260623'.

    Read defensively: a module missing __version__ contributes '00000000' so the
    composite is always well-formed. runtime_core (rc) leads because it is the
    embedded engine that most determines installer behavior.
    """

    import os as _os
    import re as _re

    def _ver(module_filename):
        here = _os.path.dirname(_os.path.abspath(__file__))
        text = _read_text(_os.path.join(here, module_filename))
        match = _re.search(r"__version__\s*=\s*'(\d{8})'", text or '')
        return match.group(1) if match else '00000000'

    return 'rc%s/gen%s/br%s' % (
        _ver('runtime_core.py'), _ver('generate.py'), _ver('build_record.py'))


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

    parts = []
    parts.append(code_body)
    parts.append(stamp_block)
    parts.append('\n\n# ---- embedded layout records (data payload) ----\n')
    parts.append('_RECORDS_JSON = r"""\n')
    parts.append(data_blob)
    parts.append('\n"""\n')
    parts.append('RECORDS = json.loads(_RECORDS_JSON)\n')
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
