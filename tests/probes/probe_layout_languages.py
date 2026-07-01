"""
keylayout_to_xkb/tests/probes/probe_layout_languages.py

macOS-only probe: capture Apple's AUTHORITATIVE per-layout language list.

Apple assigns each keyboard layout a list of ISO 639 language codes, exposed by
the Text Input Source API as kTISPropertyInputSourceLanguages. That list is the
ground truth for grouping a layout by language and (via a language->country map)
choosing its flag -- far better than guessing from the layout's display name.

This probe enumerates every installed input source, reads its language list, and
prints two things:

  1. A human-readable table (name / source id / languages), so you can eyeball it.
  2. A ready-to-paste Python literal, _TIS_LANGUAGES, mapping each layout's
     source id AND display name to its language list. Paste that into
     keylayout_to_xkb/install/language_data.py (the baked static fallback used
     when generating off-Mac or for a layout TIS did not report).

Run it on your Mac:

    python3 -m keylayout_to_xkb.tests.probes.probe_layout_languages

Add --json PATH to also write the mapping as JSON:

    python3 -m keylayout_to_xkb.tests.probes.probe_layout_languages --json langs.json

It returns a non-zero exit code (and score 0) only if the TIS bridge could not
be set up at all; a layout with an empty language list is reported, not failed
(some layouts genuinely report none).
"""

import sys
import json

from keylayout_to_xkb.extract.tis_source import extract_all_layouts


__version__ = '20260630'


def _collect():
    """Return the list of {name, source_id, languages} for all input sources
    that report either uchr data or a language list."""

    payloads = extract_all_layouts()
    rows = []
    for entry in payloads:
        name = entry.get('name', '')
        source_id = entry.get('source_id', '')
        languages = entry.get('languages', [])
        has_data = entry.get('data') is not None
        # Keep anything that is a keyboard layout (has uchr) OR carries languages.
        if has_data or languages:
            rows.append({'name': name, 'source_id': source_id,
                         'languages': languages, 'has_data': has_data})
    return rows


def _print_table(rows):
    """Human-readable enumeration."""

    print('=' * 72)
    print('%-30s %-28s %s' % ('name', 'source id (tail)', 'languages'))
    print('-' * 72)
    for row in rows:
        id_tail = row['source_id'].split('.')[-1] if row['source_id'] else ''
        langs = ', '.join(row['languages']) if row['languages'] else '(none)'
        marker = '' if row['has_data'] else '  [no uchr]'
        print('%-30s %-28s %s%s'
              % (row['name'][:30], id_tail[:28], langs, marker))
    print('-' * 72)


def _print_literal(rows):
    """Emit a paste-ready Python dict literal for the static fallback table.

    Apple's language list is "languages this layout CAN type" (dozens for a Latin
    layout), and only the FIRST entry is the layout's PRIMARY language. So this
    bakes the PRIMARY (first) language per layout, keyed by source id -- matching
    _TIS_PRIMARY_LANGUAGE in language_data.py. Paste it over that table to refresh.
    """

    mapping = {}
    for row in rows:
        if not row['languages']:
            continue
        if row['source_id'] and row['source_id'].startswith(
                'com.apple.keylayout.'):
            mapping[row['source_id']] = row['languages'][0]     # primary only

    print('\n# ---- paste over _TIS_PRIMARY_LANGUAGE in '
          'keylayout_to_xkb/install/language_data.py ----')
    print('_TIS_PRIMARY_LANGUAGE = {')
    for key in sorted(mapping):
        print('    %r: %r,' % (key, mapping[key]))
    print('}')
    print('# ---- end paste ----\n')
    return mapping


def main():
    try:
        rows = _collect()
    except Exception as error:
        print('TIS bridge failed (are you on macOS?): %s' % error,
              file=sys.stderr)
        print('score: 0')
        return 1

    if not rows:
        print('No input sources with layout data or languages found.',
              file=sys.stderr)
        print('score: 0')
        return 1

    _print_table(rows)
    mapping = _print_literal(rows)

    with_langs = sum(1 for row in rows if row['languages'])
    total = len(rows)
    print('Layouts enumerated: %d | with a language list: %d | without: %d'
          % (total, with_langs, total - with_langs))

    # Optional JSON dump for machine use.
    if '--json' in sys.argv:
        idx = sys.argv.index('--json')
        if idx + 1 < len(sys.argv):
            path = sys.argv[idx + 1]
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(mapping, handle, ensure_ascii=False, indent=2)
            print('wrote JSON mapping to %s' % path)

    # Score: fraction of layouts that reported a language list. Informational --
    # a layout with no language is not a failure, but a high score means TIS gave
    # us good coverage to bake in.
    score = with_langs
    print('score: %d/%d' % (score, total))
    return 0


if __name__ == '__main__':
    sys.exit(main())

# End of file #
