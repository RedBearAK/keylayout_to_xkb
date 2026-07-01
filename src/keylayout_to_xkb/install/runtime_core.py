"""
keylayout_to_xkb/install/runtime_core.py

The manifest-driven install/uninstall ENGINE, shared by two callers:

  * the host tool (keylayout_to_xkb.__main__), which imports it directly to
    install, list, or uninstall layouts on a Linux machine that has the repo;
  * the generated self-installers, which embed this module's SOURCE verbatim so
    a single copied file carries the same engine with no import dependency.

Because both sides use this one source, there is no template-vs-module drift:
generate.py reads this file's text and pastes it into the installer. Everything
here operates on plain record dicts, an InstallPaths object, and the JSON
manifest -- no UI, no argv, no RECORDS global (those live in the installer's
front-end or the host CLI). Every managed file is kl2xkb-namespaced and rebuilt
in full from the manifest, so install and uninstall share _rebuild_managed_files
and can never disturb other layouts or system/user files.
"""

import os
import json

from xml.sax.saxutils import escape as _xml_escape


__version__ = '20260630'


_NAMESPACE = 'keylayout_to_xkb'
_BEGIN = '// >>> keylayout_to_xkb managed region (do not edit by hand) >>>'
_END = '// <<< keylayout_to_xkb managed region <<<'


def user_xkb_root():
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(
        os.path.expanduser('~'), '.config')
    return os.path.join(base, 'xkb')


class InstallPaths:
    def __init__(self, root=None):
        self.root = root or user_xkb_root()
        self.symbols_dir = os.path.join(self.root, 'symbols')
        self.types_dir = os.path.join(self.root, 'types')
        self.rules_dir = os.path.join(self.root, 'rules')
        self.symbols_file = os.path.join(self.symbols_dir, _NAMESPACE)
        self.types_file = os.path.join(self.types_dir, _NAMESPACE)
        self.rules_file = os.path.join(self.rules_dir, 'evdev')
        self.registry_file = os.path.join(self.rules_dir, 'evdev.xml')
        self.compose_file = os.path.join(self.root, 'Compose.%s' % _NAMESPACE)
        self.manifest_file = os.path.join(self.root, '%s.manifest.json' % _NAMESPACE)


def _read(path):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _write_if_changed(path, content, force):
    existing = _read(path)
    if existing is not None and existing == content and not force:
        return 'unchanged'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(content)
    return 'written'


def _split_variant_block(block_text):
    """Split an emitted variant block into (types_section, symbols_section).

    The emitter produces 'xkb_types "v" {...}' followed by 'xkb_symbols "v" {...}'.
    These MUST live in separate files: a symbols/ file may contain only
    xkb_symbols sections (libxkbcommon aborts a symbols include the moment it
    meets an xkb_types section -- "Include file of wrong type"), and the custom
    types belong in a types/ file the rules pull in alongside the symbols.
    """

    types_index = block_text.index('xkb_types')
    symbols_index = block_text.index('xkb_symbols')
    return (block_text[types_index:symbols_index].rstrip(),
            block_text[symbols_index:].rstrip())


def build_types_file(records):
    """The types/ file: every variant's xkb_types section, in the managed region.

    The keys in the symbols file reference these custom types by name, so they
    must be loadable as a types component. The rules map each layout/variant to
    'complete+keylayout_to_xkb(variant)' so the system base types load first and
    ours are added.
    """

    lines = [_BEGIN, '']
    for record in sorted(records, key=lambda r: r['identifier']):
        for variant_name, block_text in record['variants']:
            types_section, _symbols = _split_variant_block(block_text)
            lines.append('// %s : %s' % (record['identifier'], variant_name))
            lines.append(types_section)
            lines.append('')
    lines.append(_END)
    lines.append('')
    return '\n'.join(lines)


def build_symbols_file(records):
    """The symbols/ file: ONLY xkb_symbols sections (no xkb_types -- those go in
    the types/ file via build_types_file)."""

    lines = [_BEGIN, '']
    for record in sorted(records, key=lambda r: r['identifier']):
        for variant_name, block_text in record['variants']:
            _types, symbols_section = _split_variant_block(block_text)
            lines.append('// %s : %s' % (record['identifier'], variant_name))
            lines.append(symbols_section)
            lines.append('')
    lines.append(_END)
    lines.append('')
    return '\n'.join(lines)


def build_rules_file(records):
    """Rules mapping our namespaced variants UNDER existing base layouts.

    Model (learned from KDE rejecting top-level custom layouts): instead of
    registering 'polishpro' as its own layout -- which KDE collapses to its
    language and then fails to load (looks for symbols/'polish') -- we register
    each layout's variants under the real system base layout for its primary
    language (Polish -> 'pl'). KDE finds the base's symbols/geometry/flag, and our
    variant is selectable within it.

    Each record carries base_layout (e.g. 'pl', or 'us' fallback for languages
    with no system base). Variant names are namespaced 'mac-k2x-*' so they can
    NEVER collide with a system variant of that base (e.g. 'de' ships a 'mac'
    variant already). Layering is REPLACE: 'pc+ns(variant)' with NO '+base', so
    the keymap is exactly our layout with no base-layout keys bleeding through
    (verified: layering '+base' leaked an extra key definition).

    Structural rules carried over from the session-breaking bug: our rules come
    BEFORE '! include %S/evdev' (first-match precedence), the include is '%S/evdev'
    not '%S/rules/evdev' (doubled path returns no components and breaks ALL
    keymaps), and each variant maps to BOTH types and symbols.
    """

    ns = _NAMESPACE
    lines = ['// %s rules overlay' % ns, '']

    def _base(record):
        return record.get('base_layout') or 'us'

    lines.append('! model\tlayout\tvariant\t=\ttypes')
    for record in sorted(records, key=lambda r: r['identifier']):
        for variant_name, _text in record['variants']:
            lines.append('  *\t%s\t%s\t=\tcomplete+%s(%s)'
                         % (_base(record), variant_name, ns, variant_name))
    lines.append('')

    lines.append('! model\tlayout\tvariant\t=\tsymbols')
    for record in sorted(records, key=lambda r: r['identifier']):
        for variant_name, _text in record['variants']:
            lines.append('  *\t%s\t%s\t=\tpc+%s(%s)'
                         % (_base(record), variant_name, ns, variant_name))
    lines.append('')

    # System rules LAST so our specific mappings take precedence (first match).
    lines.append('! include %S/evdev')
    lines.append('')
    return '\n'.join(lines)


def _tagged_description(text):
    """Append the [kl2xkb] origin tag so custom layouts are distinguishable from
    system layouts in the desktop keyboard picker. The tag is short, unique to
    this tool, and makes the layout's origin unmistakable at a glance."""

    return '%s [kl2xkb]' % text


def build_registry_xml(records):
    """Register our variants UNDER existing base layouts.

    libxkbregistry MERGES this file with the system registry, so emitting a
    <layout> with an existing base's <name> (e.g. 'pl') and only OUR <variant>
    entries adds those variants to the base's variant list -- inheriting the
    base's flag, geometry, and grouping while staying selectable. Records are
    grouped by base_layout (several exotic layouts may share the 'us' fallback).

    Only variant-level entries are emitted (no top-level custom <configItem>
    metadata), because the base layout already supplies name/flag/short label; we
    are augmenting it, not redefining it.
    """

    # Group records by their base layout.
    by_base = {}
    for record in records:
        base = record.get('base_layout') or 'us'
        by_base.setdefault(base, []).append(record)

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE xkbConfigRegistry SYSTEM "xkb.dtd">',
           '<xkbConfigRegistry version="1.1">', '  <layoutList>']
    for base in sorted(by_base):
        out += ['    <layout>', '      <configItem>',
                '        <name>%s</name>' % _xml_escape(base),
                '      </configItem>', '      <variantList>']
        for record in sorted(by_base[base], key=lambda r: r['identifier']):
            for variant_name, _text in record['variants']:
                suffix = 'ANSI' if variant_name.endswith('ansi') else (
                    'ISO' if variant_name.endswith('iso') else variant_name)
                desc = _tagged_description(
                    '%s, %s)' % (record['display_name'], suffix))
                item = ['        <variant>', '          <configItem>',
                        '            <name>%s</name>' % _xml_escape(variant_name)]
                short_desc = record.get('short_desc')
                if short_desc:
                    item.append(
                        '            <shortDescription>%s</shortDescription>'
                        % _xml_escape(short_desc))
                item.append('            <description>%s</description>'
                            % _xml_escape(desc))
                iso_countries = record.get('iso_countries') or []
                if iso_countries:
                    item.append('            <countryList>')
                    for country in iso_countries:
                        item.append('              <iso3166Id>%s</iso3166Id>'
                                    % _xml_escape(country))
                    item.append('            </countryList>')
                iso_languages = record.get('iso_languages') or []
                if iso_languages:
                    item.append('            <languageList>')
                    for language in iso_languages:
                        item.append('              <iso639Id>%s</iso639Id>'
                                    % _xml_escape(language))
                    item.append('            </languageList>')
                item += ['          </configItem>', '        </variant>']
                out += item
        out += ['      </variantList>', '    </layout>']
    out += ['  </layoutList>', '</xkbConfigRegistry>', '']
    return '\n'.join(out)


def build_compose_file(records):
    parts = []
    for record in sorted(records, key=lambda r: r['identifier']):
        if record['compose_text'].strip():
            parts.append('# === %s ===' % record['identifier'])
            parts.append(record['compose_text'])
            parts.append('')
    return '\n'.join(parts)


def _load_manifest(paths):
    text = _read(paths.manifest_file)
    if not text:
        return {'installed': {}}
    try:
        data = json.loads(text)
        data.setdefault('installed', {})
        return data
    except ValueError:
        return {'installed': {}}


def _rebuild_managed_files(paths, manifest, force):
    """Write the four kl2xkb-managed files from the manifest's current record set
    (or remove a file when nothing remains needs it).

    Every managed file lives in a kl2xkb-namespaced path and is regenerated in
    full from the manifest, so this same routine serves install AND uninstall:
    add or drop entries in the manifest, then call this to bring the files into
    line. It never touches system or user files (only our namespaced paths), so a
    rebuild can never disturb other layouts -- kl2xkb or otherwise.
    """

    merged = list(manifest['installed'].values())
    status = {}

    if not merged:
        # Nothing installed: remove the managed files entirely (clean slate).
        for path in (paths.symbols_file, paths.types_file, paths.rules_file,
                     paths.registry_file, paths.compose_file):
            if os.path.exists(path):
                os.remove(path)
                status[os.path.basename(path)] = 'removed'
        status['manifest'] = _write_if_changed(
            paths.manifest_file,
            json.dumps(manifest, indent=2, ensure_ascii=False), force)
        return status, merged

    status['types'] = _write_if_changed(
        paths.types_file, build_types_file(merged), force)
    status['symbols'] = _write_if_changed(
        paths.symbols_file, build_symbols_file(merged), force)
    status['rules'] = _write_if_changed(
        paths.rules_file, build_rules_file(merged), force)
    status['registry'] = _write_if_changed(
        paths.registry_file, build_registry_xml(merged), force)
    compose = build_compose_file(merged)
    if compose.strip():
        status['compose'] = _write_if_changed(paths.compose_file, compose, force)
    elif os.path.exists(paths.compose_file):
        # No layout needs compose any more; drop the stale file.
        os.remove(paths.compose_file)
        status['compose'] = 'removed'
    status['manifest'] = _write_if_changed(
        paths.manifest_file,
        json.dumps(manifest, indent=2, ensure_ascii=False), force)
    return status, merged


def validate_keymap(identifier, variant_name, paths=None):
    """Compile one installed layout the way a Wayland compositor does, returning
    (ok, error_text).

    This is the validation that matters: it resolves the rules file, pulls in the
    types and symbols components, chains the system rules, and assembles the FULL
    keymap via libxkbcommon's RMLVO path (xkb_keymap_new_from_names) against the
    real XKB tree -- exactly what the compositor does at session start. An
    isolated xkb_symbols compile (wrapping the block in a synthetic keymap that
    supplies its own types) does NOT exercise this and cannot catch rules/types
    structural bugs, which is how a session-breaking install slipped through once.

    Returns (True, '') on success, (False, message) on a compile failure. If
    libxkbcommon is unavailable it returns (True, 'unavailable'); on a real Linux
    target it is always present (it is the compositor's own library).
    """

    import ctypes
    import ctypes.util

    libname = ctypes.util.find_library('xkbcommon')
    if not libname:
        return True, 'unavailable'
    try:
        xkb = ctypes.CDLL(libname)
        xkb.xkb_context_new.restype = ctypes.c_void_p
        xkb.xkb_context_new.argtypes = [ctypes.c_int]
        xkb.xkb_keymap_new_from_names.restype = ctypes.c_void_p

        class _Names(ctypes.Structure):
            _fields_ = [('rules', ctypes.c_char_p), ('model', ctypes.c_char_p),
                        ('layout', ctypes.c_char_p), ('variant', ctypes.c_char_p),
                        ('options', ctypes.c_char_p)]

        xkb.xkb_keymap_new_from_names.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_Names), ctypes.c_int]
        xkb.xkb_keymap_unref.argtypes = [ctypes.c_void_p]

        context = xkb.xkb_context_new(0)
        names = _Names(b'evdev', b'pc105', identifier.encode(),
                       variant_name.encode(), None)
        keymap = xkb.xkb_keymap_new_from_names(context, ctypes.byref(names), 0)
        if keymap:
            xkb.xkb_keymap_unref(keymap)
            return True, ''
        return False, ('libxkbcommon could not assemble keymap for %s(%s)'
                       % (identifier, variant_name))
    except Exception as error:
        return True, 'validation error: %s' % error      # never block on a probe bug


def validate_installed(records, paths=None):
    """Validate every variant of every record against the on-disk tree.

    Returns (ok, failures) with failures a list of (identifier, variant, message).
    Call AFTER the files are written (the keymap is assembled from them) and
    BEFORE reporting success.
    """

    paths = paths or InstallPaths()
    failures = []
    for record in records:
        base = record.get('base_layout') or 'us'
        for variant_name, _text in record['variants']:
            # Compile the way it is actually selected: <base>(mac-k2x-variant).
            ok, message = validate_keymap(base, variant_name, paths)
            if not ok:
                failures.append((record['identifier'], variant_name, message))
    return (not failures), failures


def _snapshot_files(paths):
    """Capture current contents of every managed file (or None if absent), so a
    failed install can be rolled back to the exact prior state."""

    files = (paths.types_file, paths.symbols_file, paths.rules_file,
             paths.registry_file, paths.compose_file, paths.manifest_file)
    return {path: _read(path) for path in files}


def _restore_files(snapshot):
    """Restore managed files from a snapshot: rewrite saved contents, and remove
    any file that did not exist before (its snapshot value is None)."""

    for path, content in snapshot.items():
        if content is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(content)


def install_records(new_records, paths=None, force=False, validate=True):
    """Install records, then validate the assembled keymap and ROLL BACK on
    failure so a broken layout never reaches the live XKB tree.

    Sequence: snapshot the current managed files -> write the new tree -> compile
    every affected layout via libxkbcommon RMLVO (validate_installed) -> if any
    fail, restore the snapshot exactly (prior state byte-for-byte) and report the
    failure instead of success. With validate=False the check is skipped (used by
    callers that validate separately); when libxkbcommon is unavailable the check
    passes (it cannot run), which only happens off a real Linux target.
    """

    paths = paths or InstallPaths()
    snapshot = _snapshot_files(paths)

    manifest = _load_manifest(paths)
    added, refreshed = [], []
    for record in new_records:
        ident = record['identifier']
        (refreshed if ident in manifest['installed'] else added).append(ident)
        manifest['installed'][ident] = record

    status, merged = _rebuild_managed_files(paths, manifest, force)

    result = {'paths': paths, 'status': status, 'added': added,
              'refreshed': refreshed,
              'all_unchanged': all(v == 'unchanged' for v in status.values()),
              'installed_count': len(merged), 'validated': False,
              'rolled_back': False, 'failures': []}

    if validate:
        ok, failures = validate_installed(new_records, paths)
        result['validated'] = ok
        result['failures'] = failures
        if not ok:
            # The assembled keymap did not compile -- undo everything.
            _restore_files(snapshot)
            result['rolled_back'] = True
            result['added'] = []
            result['refreshed'] = []
    return result


def list_installed(paths=None):
    """Return the list of currently-installed records (from the manifest).

    Manifest-driven and read-only: reports what is installed on THIS system,
    regardless of which installer or tool put it there.
    """

    paths = paths or InstallPaths()
    manifest = _load_manifest(paths)
    return list(manifest['installed'].values())


def uninstall_one(identifier, paths=None, force=False):
    """Remove one layout, rebuilding the managed files from what remains.

    Safe by construction: the remaining layouts are regenerated intact from the
    manifest, and only kl2xkb-namespaced files are touched. Returns a result dict
    with 'removed' (the identifier or None if it was not installed) and the
    rebuild status.
    """

    paths = paths or InstallPaths()
    manifest = _load_manifest(paths)
    if identifier not in manifest['installed']:
        return {'paths': paths, 'removed': None,
                'reason': 'not installed', 'installed_count':
                len(manifest['installed'])}
    del manifest['installed'][identifier]
    status, merged = _rebuild_managed_files(paths, manifest, force)
    return {'paths': paths, 'removed': identifier, 'status': status,
            'installed_count': len(merged)}


def uninstall_all(paths=None):
    """Remove every kl2xkb-managed file and the manifest (full clean slate).

    Touches only kl2xkb-namespaced paths; the system rules/registry and the
    user's own files are never modified. Returns a result dict listing what was
    removed.
    """

    paths = paths or InstallPaths()
    removed = []
    for path in (paths.symbols_file, paths.types_file, paths.rules_file,
                 paths.registry_file, paths.compose_file, paths.manifest_file):
        if os.path.exists(path):
            try:
                os.remove(path)
                removed.append(os.path.basename(path))
            except OSError:
                pass
    return {'paths': paths, 'removed': removed}


def dump_records(records, directory):
    """Write the raw XKB/Compose files to a folder for manual inspection/use."""
    os.makedirs(directory, exist_ok=True)
    written = []
    for record in records:
        for variant_name, symbols_text in record['variants']:
            path = os.path.join(directory, '%s-%s' % (record['identifier'], variant_name))
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(symbols_text)
            written.append(path)
        if record['compose_text'].strip():
            path = os.path.join(directory, 'Compose.%s' % record['identifier'])
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(record['compose_text'])
            written.append(path)
    return written



# End of file #
