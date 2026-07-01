"""
keylayout_to_xkb/tests/probes/probe_base_resolution.py

Diagnostic: dump the ACTUAL values that flow through base-layout resolution for a
layout, on a real Mac, so we stop guessing what TIS returns.

For each matching input source it prints, in order:
  1. RAW from tis_source.extract_all_layouts(): name, source_id, languages
  2. After parse_uchr + build_record: record.source_id, record.source_languages
  3. After _record_to_dict: iso_languages, short_desc, base_layout  <-- the answer

Run on the Mac (only there does the live TIS bridge work):
    python -m keylayout_to_xkb.tests.probes.probe_base_resolution PolishPro

The single argument is a case-insensitive substring matched against the layout
name or source_id (omit to dump every layout).

__version__ = '20260701'
"""

import sys


def main(argv):
    filter_substr = (argv[0].lower() if argv else None)

    from keylayout_to_xkb.extract.tis_source import (
        extract_all_layouts, TISExtractionError,
    )
    from keylayout_to_xkb.extract.uchr_parse import parse_uchr, UchrParseError
    from keylayout_to_xkb.install.build_record import build_record
    from keylayout_to_xkb.install.generate import _record_to_dict

    try:
        payloads = extract_all_layouts()
    except TISExtractionError as error:
        print('TIS extraction failed (this probe only runs on macOS): %s' % error)
        return 1

    shown = 0
    for payload in payloads:
        name = payload.get('name') or ''
        source_id = payload.get('source_id') or ''
        languages = payload.get('languages') or []
        data = payload.get('data')

        if filter_substr is not None and (
            filter_substr not in name.lower()
            and filter_substr not in source_id.lower()
        ):
            continue

        print('=' * 60)
        print('1. RAW from tis_source:')
        print('   name        = %r' % name)
        print('   source_id   = %r' % source_id)
        print('   languages   = %r' % (languages,))
        print('   (len languages = %d)' % len(languages))

        if not data:
            print('   [no uchr data; skipping downstream]')
            shown += 1
            continue

        try:
            layout = parse_uchr(data, layout_name=name, source_id=source_id,
                                languages=languages)
        except UchrParseError as error:
            print('   parse failed: %s' % error)
            shown += 1
            continue

        record = build_record(layout)
        print('2. After build_record:')
        print('   record.source_id        = %r' % record.source_id)
        print('   record.source_languages = %r'
              % (getattr(record, 'source_languages', None),))
        print('   record.display_name     = %r' % record.display_name)
        print('   record.language         = %r' % record.language)

        result = _record_to_dict(record)
        print('3. After _record_to_dict (the resolution result):')
        print('   iso_languages = %r' % (result.get('iso_languages'),))
        print('   short_desc    = %r' % result.get('short_desc'))
        print('   base_layout   = %r   <-- REGISTERS UNDER <base>-k2x' %
              result.get('base_layout'))
        shown += 1

    print('=' * 60)
    if shown == 0:
        print('no layout matched %r' % filter_substr)
        return 1
    print('done: %d layout(s) probed' % shown)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
