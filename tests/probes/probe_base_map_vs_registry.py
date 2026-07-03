#!/usr/bin/env python3
"""
tests/probes/probe_base_map_vs_registry.py  (run on the Linux machine)

Cross-check base-layout classification against the SYSTEM xkeyboard-config
registry, so upstream changes surface instead of rotting our data:

  1. Every base in _LANGUAGE_TO_BASE_LAYOUT must exist as a system layout --
     a removed or renamed base would break registration for its languages.
  2. Every language in _KNOWN_BASELESS_LANGUAGES is re-checked against the
     registry's languageList declarations (layout AND variant level). A hit
     means upstream added a home since our last cross-reference and the
     language should be PROMOTED into the base map.

Check 2 needs ISO 639-1 -> 639-2/3 conversion (the registry speaks 3-letter
codes); it uses the 'iso639-lang' package when available and self-skips with
a note otherwise. Check 1 needs nothing beyond the registry file.

Usage (from repo root or anywhere, on a machine with xkeyboard-config):
  python3 tests/probes/probe_base_map_vs_registry.py
  python3 tests/probes/probe_base_map_vs_registry.py /path/to/evdev.xml
"""

import os
import sys
import xml.etree.ElementTree as ET


def _bootstrap_src_on_path():
    """Locate the package 'src' dir relative to this file and add it to sys.path.

    Lets the probe run from the repo root, from tests/probes/, or from any
    nested probe folder without the caller setting PYTHONPATH. Walks upward
    from this file looking for a 'src/keylayout_to_xkb' package.
    """

    here = os.path.dirname(os.path.abspath(__file__))
    cursor = here
    for _ in range(8):
        candidate = os.path.join(cursor, 'src')
        if os.path.isdir(os.path.join(candidate, 'keylayout_to_xkb')):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return candidate
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    raise RuntimeError('could not locate src/keylayout_to_xkb above this file')


_bootstrap_src_on_path()

from keylayout_to_xkb.install.language_data import (
    _KNOWN_BASELESS_LANGUAGES,
    _LANGUAGE_TO_BASE_LAYOUT,
)


__version__ = '20260703'


_DEFAULT_REGISTRY = '/usr/share/X11/xkb/rules/evdev.xml'


def _load_registry(path):
    """Return (layout_names, iso639_to_targets) from the registry XML."""

    tree = ET.parse(path)
    layout_names = set()
    iso_to_targets = {}
    for layout in tree.getroot().iter('layout'):
        name_el = layout.find('./configItem/name')
        if name_el is None:
            continue
        layout_name = name_el.text
        layout_names.add(layout_name)
        for iso_el in layout.findall('./configItem/languageList/iso639Id'):
            iso_to_targets.setdefault(iso_el.text, set()).add(layout_name)
        for variant in layout.findall('./variantList/variant'):
            vname_el = variant.find('./configItem/name')
            vname = vname_el.text if vname_el is not None else '?'
            for iso_el in variant.findall('./configItem/languageList/iso639Id'):
                iso_to_targets.setdefault(iso_el.text, set()).add(
                    '%s(%s)' % (layout_name, vname))
    return layout_names, iso_to_targets


def check_bases_exist(layout_names):
    """Every mapped base must be a real system layout."""

    missing = sorted({base for base in _LANGUAGE_TO_BASE_LAYOUT.values()
                      if base not in layout_names})
    if missing:
        print('  MISSING system layouts for mapped bases: %s'
              % ', '.join(missing))
        return False
    print('  all %d distinct mapped bases exist in the system registry'
          % len(set(_LANGUAGE_TO_BASE_LAYOUT.values())))
    return True


def check_baseless_still_baseless(iso_to_targets):
    """Flag baseless languages that upstream has since given a home."""

    try:
        from iso639 import Lang
    except ImportError:
        print('  skipped: iso639-lang not installed '
              '(pip install iso639-lang to enable this check)')
        return True

    promoted = []
    for code in sorted(_KNOWN_BASELESS_LANGUAGES):
        try:
            lang = Lang(code)
            isos = [v for v in (lang.pt2b, lang.pt2t, lang.pt3) if v]
        except Exception:
            isos = [code]
        targets = set()
        for iso in dict.fromkeys(isos):
            targets |= iso_to_targets.get(iso, set())
        if targets:
            promoted.append('%s -> %s' % (code, ', '.join(sorted(targets)[:3])))
    if promoted:
        print('  PROMOTE these (upstream now has a home):')
        for line in promoted:
            print('    %s' % line)
        return False
    print('  all %d baseless languages remain baseless in this registry'
          % len(_KNOWN_BASELESS_LANGUAGES))
    return True


def main(argv):
    registry_path = argv[0] if argv else _DEFAULT_REGISTRY
    print('base map vs registry probe (%s)\nregistry: %s\n'
          % (__version__, registry_path))
    if not os.path.isfile(registry_path):
        print('registry file not found (run on Linux, or pass a path)')
        return 1
    layout_names, iso_to_targets = _load_registry(registry_path)

    score = 0
    checks = (
        ('mapped bases exist', check_bases_exist, (layout_names,)),
        ('baseless still baseless', check_baseless_still_baseless,
         (iso_to_targets,)),
    )
    for label, check_fn, args in checks:
        passed = bool(check_fn(*args))
        print('  -> %s: %s\n' % (label, 'PASS' if passed else 'FAIL'))
        score += 1 if passed else 0
    print('score: %d/%d' % (score, len(checks)))
    return 0 if score == len(checks) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


# End of file #
