#!/usr/bin/env python3
"""
src/keylayout_to_xkb/install/test_language_data.py

Focused tests for base-layout resolution and layout-name ASCII folding.

The base map and the known-baseless set together must classify every language
deliberately: a language in neither is an unclassified fallthrough, which is
how the Inuktitut family hid under 'us' for weeks. These tests pin the two
sets' hygiene and the specific promotions from the 2.47 registry
cross-reference, plus the ASCII folding that keeps en dashes out of emitted
layout names (an en dash in 'Inuktitut - Nunavik' crashed an ASCII-locale
consumer downstream).

Project test style: each test prints, returns True/False, main() scores.
Runs standalone or under pytest.
"""

import sys

from keylayout_to_xkb.install.catalog import fold_name_to_ascii
from keylayout_to_xkb.install.language_data import (
    base_layout_is_known,
    base_layout_for_language,
    _KNOWN_BASELESS_LANGUAGES,
    _LANGUAGE_TO_BASE_LAYOUT,
)


__version__ = '20260703'


def test_sets_are_disjoint():
    """A language must be mapped OR baseless, never both."""

    overlap = set(_LANGUAGE_TO_BASE_LAYOUT) & _KNOWN_BASELESS_LANGUAGES
    if overlap:
        print('  overlap: %s' % ', '.join(sorted(overlap)))
        return False
    print('  map (%d) and baseless (%d) sets are disjoint'
          % (len(_LANGUAGE_TO_BASE_LAYOUT), len(_KNOWN_BASELESS_LANGUAGES)))
    return True


def test_promoted_languages_resolve():
    """Spot-check promotions from the registry cross-reference."""

    expected = {
        'iu': 'ca',    # Inuktitut  -> ca(ike)
        'bo': 'cn',    # Tibetan    -> cn(tib)
        'zh': 'cn',    # Zhuyin etc.
        'chr': 'us',   # Cherokee   -> us(chr): 'us' for the RIGHT reason
        'ga': 'ie',    # Irish      -> ie(CloGaelach)
        'vi': 'vn',
        'ta': 'in',
        'se': 'no',    # Northern Sami: native pick among fi/no/se
    }
    bad = []
    for code, want in expected.items():
        got = base_layout_for_language(code)
        if got != want:
            bad.append('%s -> %s (want %s)' % (code, got, want))
    if bad:
        print('  wrong: %s' % '; '.join(bad))
        return False
    print('  %d promoted languages resolve to their native bases'
          % len(expected))
    return True


def test_classification_is_total_for_baseless():
    """Baseless languages are KNOWN (no warn), unknown codes are not."""

    if not all(base_layout_is_known(code)
               for code in _KNOWN_BASELESS_LANGUAGES):
        print('  a baseless language reads as unclassified')
        return False
    if base_layout_is_known('zz-not-a-language'):
        print('  an unknown code reads as classified')
        return False
    if base_layout_for_language('zz-not-a-language') != 'us':
        print('  unknown code did not fall back to us')
        return False
    print('  baseless set is known; unknown codes are loud fallthroughs')
    return True


def test_name_folding():
    """En dash, quotes, and diacritics fold to readable pure ASCII."""

    cases = (
        ('Inuktitut \u2013 Nunavik', 'Inuktitut - Nunavik'),
        ('Czech \u2014 QWERTY', 'Czech - QWERTY'),
        ('M\u0101ori', 'Maori'),
        ('O\u2019Brien\u2019s \u201cTest\u201d', 'O\'Brien\'s "Test"'),
        ('plain ASCII stays', 'plain ASCII stays'),
        ('\u2013', '-'),
    )
    bad = []
    for raw, want in cases:
        got = fold_name_to_ascii(raw)
        if got != want:
            bad.append('%r -> %r (want %r)' % (raw, got, want))
        if any(ord(ch) > 127 for ch in got):
            bad.append('%r left non-ASCII in %r' % (raw, got))
    if fold_name_to_ascii('\u2603') == '':
        bad.append('empty fold result not guarded')
    if bad:
        print('  ' + '; '.join(bad))
        return False
    print('  %d folding cases correct, all outputs pure ASCII' % len(cases))
    return True


def main():
    print('language data + name folding tests:\n')
    tests = (
        ('sets are disjoint', test_sets_are_disjoint),
        ('promoted languages resolve', test_promoted_languages_resolve),
        ('classification total for baseless',
         test_classification_is_total_for_baseless),
        ('name folding', test_name_folding),
    )
    score = 0
    for label, test_fn in tests:
        passed = bool(test_fn())
        print('  -> %s: %s\n' % (label, 'PASS' if passed else 'FAIL'))
        score += 1 if passed else 0
    print('score: %d/%d' % (score, len(tests)))
    return 0 if score == len(tests) else 1


if __name__ == '__main__':
    sys.exit(main())


# End of file #
