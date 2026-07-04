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
import re
import sys
import json

from xml.sax.saxutils import escape as _xml_escape


__version__ = '20260704c'


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
        # Symbols now live in one file PER BASE LAYOUT, named '<base>x' (e.g.
        # 'plx', matching k2x_base_name), so the compositor finds our variant
        # sections inside a file named after the registered layout -- while NOT
        # shadowing the real system base (a user 'symbols/pl' would replace the
        # system Polish layout; 'symbols/plx' is a distinct file that shadows
        # nothing). The set of these files is dynamic, so symbols_file is gone;
        # use symbols_file_for().
        self.types_file = os.path.join(self.types_dir, _NAMESPACE)
        self.rules_file = os.path.join(self.rules_dir, 'evdev')
        self.registry_file = os.path.join(self.rules_dir, 'evdev.xml')
        self.compose_file = os.path.join(self.root, 'Compose.%s' % _NAMESPACE)
        self.manifest_file = os.path.join(self.root, '%s.manifest.json' % _NAMESPACE)

    def symbols_file_for(self, base_layout):
        """Path to the per-base symbols file for a base layout ('pl' -> the file
        symbols/plx)."""

        return os.path.join(self.symbols_dir, k2x_base_name(base_layout))


def k2x_base_name(base_layout):
    """The namespaced base-layout name we register under: 'pl' -> 'plx'.

    A distinct file/layout name so our variant sections are findable by KDE's
    preview (which looks in symbols/<layout>) without shadowing the real system
    base layout of the same language.

    The tag is a SINGLE trailing character ('x'), not '-k2x': KDE truncates the
    stored layout identity to a few characters (observed: 'pl-k2x' clipped to
    'pl-' in kxkbrc DisplayNames), and a truncated name no longer matches our
    rules -- so the compositor cannot resolve our types component and every key
    collapses to level 1 (Shift/AltGr/CapsLock stop working). 'plx' is short
    enough to survive the clip while staying distinct from the system 'pl'.
    Three-letter language codes ('ara') make FOUR-letter names ('arax');
    that is inside proven territory, since the truncation victim was the
    DASH form and the system itself ships the five-letter 'latam' working
    everywhere in KDE.
    """

    return '%sx' % (base_layout or 'us')


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

    The emitter now produces ONLY an 'xkb_symbols "v" {...}' block -- every key
    references a STANDARD system type, so there is no custom xkb_types section to
    carry. types_section is therefore empty. (Retained as a tuple for callers that
    still distinguish the two components; the types file is written empty.)
    """

    symbols_index = block_text.index('xkb_symbols')
    types_part = block_text[:symbols_index].strip()
    if not types_part.startswith('xkb_types'):
        types_part = ''
    return (types_part, block_text[symbols_index:].rstrip())


def build_types_file(records):
    """The types/ file, now an EMPTY managed region.

    The emitter switched from custom key types (MAC_KEY_*) to STANDARD system
    types (EIGHT_LEVEL etc.), which the 'complete' component already provides.
    Desktop environments like KDE load the standard types automatically but do
    NOT reliably load a layout's own custom types, which left every key stuck on
    level 1. So we no longer define any types here. The file is still written (as
    an empty managed region) and tracked for snapshot/rollback/uninstall so those
    paths need no special-casing; a stale custom-types file from an older install
    is overwritten to empty.
    """

    _ = records  # no custom types are emitted any more
    return '\n'.join([_BEGIN, '', _END, ''])


def build_symbols_files(records):
    """Return {base_layout: symbols_file_text}, one entry per distinct base.

    Each base's file (written to symbols/<base>x, e.g. symbols/plx) holds the
    xkb_symbols sections for every variant of every record that resolves to that
    base. The section name is the variant name (mac-k2x-ansi/iso), which the
    system rules wildcard resolves as <base>x(<variant>).

    The FIRST section in each file is flagged 'default': the registered layout
    is selectable WITHOUT a variant (the desktop picker always offers the bare
    layout row), and a file with no default section is only tolerated by
    libxkbcommon (first-section fallback, with a warning) -- the X server's own
    compiler is stricter. The default flag makes the bare '<base>x' selection
    well-defined everywhere instead of resolver-dependent.
    """

    by_base = {}
    for record in records:
        base = record.get('base_layout') or 'us'
        by_base.setdefault(base, []).append(record)

    files = {}
    for base in sorted(by_base):
        lines = [_BEGIN, '']
        first_section = True
        for record in sorted(by_base[base], key=lambda r: r['identifier']):
            for variant_name, block_text in record['variants']:
                _types, symbols_section = _split_variant_block(block_text)
                flags = ('default partial alphanumeric_keys' if first_section
                         else 'partial alphanumeric_keys')
                first_section = False
                lines.append('// %s : %s' % (record['identifier'], variant_name))
                lines.append(flags)
                lines.append(symbols_section)
                lines.append('')
        lines.append(_END)
        lines.append('')
        files[base] = '\n'.join(lines)
    return files


def build_rules_file(records):
    """Rules overlay for the <base>x model.

    Our variant sections live in symbols/<base>x, named after the layout, so the
    SYSTEM wildcard rule ('* layout variant = pc+%l%(v)') already resolves
    <base>x(mac-k2x-ansi) to symbols/<base>x section mac-k2x-ansi. The keys now
    reference STANDARD types from the system 'complete' component, so NO custom
    types mapping is needed either -- the whole keymap resolves through the system
    rules. All we must do is include the system ruleset so our registry's layout
    is recognised.

    Structure carried over from the session-breaking bug: the include is
    '%S/evdev' not '%S/rules/evdev' (the doubled path returns no components and
    breaks ALL keymaps).
    """

    _ = records  # symbols + standard types both resolve via the system wildcard
    ns = _NAMESPACE
    lines = ['// %s rules overlay' % ns, '']
    lines.append('! include %S/evdev')
    lines.append('')
    return '\n'.join(lines)


def _tagged_description(text):
    """Append the [kl2xkb] origin tag so custom layouts are distinguishable from
    system layouts in the desktop keyboard picker. The tag is short, unique to
    this tool, and makes the layout's origin unmistakable at a glance."""

    return '%s [kl2xkb]' % text


def build_registry_xml(records):
    """Register each '<base>x' layout with our variants in its variantList.

    libxkbregistry MERGES this file with the system registry. The layout name is
    the namespaced '<base>x' (see k2x_base_name) -- a NEW layout, distinct from
    the system base, so its symbols resolve from our symbols/<base>x file. (An
    earlier design registered variants UNDER the existing base, but KDE then
    derived the symbols file from the base's group and looked for our sections
    inside e.g. 'symbols/pl', which fails.)

    The layout-level configItem carries a description: the desktop picker always
    shows the layout itself as a selectable row (resolving to the file's
    'default' section -- see build_symbols_files), and without a description
    that row displays the raw identifier ('plx'). Records are grouped by
    base_layout (several exotic layouts may share the 'us' fallback).
    """

    # Group records by their base layout, registered as '<base>x'.
    by_base = {}
    for record in records:
        base = record.get('base_layout') or 'us'
        by_base.setdefault(base, []).append(record)

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE xkbConfigRegistry SYSTEM "xkb.dtd">',
           '<xkbConfigRegistry version="1.1">', '  <layoutList>']
    for base in sorted(by_base):
        layout_name = k2x_base_name(base)
        layout_desc = _tagged_description('Macintosh layouts (%s)' % base)
        out += ['    <layout>', '      <configItem>',
                '        <name>%s</name>' % _xml_escape(layout_name),
                '        <shortDescription>%s</shortDescription>'
                % _xml_escape(base),
                '        <description>%s</description>'
                % _xml_escape(layout_desc),
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
                # NOTE: languageList is intentionally NOT emitted. When present,
                # KDE MERGES our <base>-k2x entry into the system layout group for
                # that language (e.g. Polish) and then derives the base symbols
                # file from the group -- looking for our variant section inside
                # 'symbols/pl' (which fails: "No Symbols named mac-k2x-ansi in
                # include file pl") and, worse, leaving the layout unselectable.
                # Without languageList, KDE treats <base>-k2x as its own group and
                # resolves the section from 'symbols/<base>-k2x' where it actually
                # lives. The flag is kept via countryList above.
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


def _rebuild_managed_files(paths, manifest, force, write_symbols=True):
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

    # All per-base symbols files that currently exist on disk (so we can remove
    # any that a new install no longer needs). They are the '<base>-k2x' files in
    # symbols/, identified by the managed-region marker inside them. We match on
    # the _BEGIN marker (content), NOT a filename suffix: the base tag is a single
    # 'x' now, too generic to filter filenames by, and the marker is authoritative.
    def _existing_k2x_symbols():
        found = []
        if os.path.isdir(paths.symbols_dir):
            for name in os.listdir(paths.symbols_dir):
                full = os.path.join(paths.symbols_dir, name)
                if not os.path.isfile(full):
                    continue
                text = _read(full)
                if text and _BEGIN in text:
                    found.append(full)
        return found

    if not merged:
        # Nothing installed: remove every managed file (clean slate).
        stale = [paths.types_file, paths.rules_file, paths.registry_file,
                 paths.compose_file] + _existing_k2x_symbols()
        for path in stale:
            if os.path.exists(path):
                os.remove(path)
                status[os.path.basename(path)] = 'removed'
        status['manifest'] = _write_if_changed(
            paths.manifest_file,
            json.dumps(manifest, indent=2, ensure_ascii=False), force)
        return status, merged

    # Write one symbols file per base layout; track which we wrote so we can
    # remove any leftover '<base>-k2x' file from a previous install.
    #
    # SYSTEM-MODE NOTE: with write_symbols=False (symbols living in the
    # system location instead), the desired set is empty, so the leftover
    # loop below removes EVERY marked user-side symbols file -- which is
    # exactly the migration a user-mode -> system-mode switch needs, since a
    # user-dir copy would shadow the system one in the compositor's search.
    symbols_by_base = build_symbols_files(merged) if write_symbols else {}
    written_symbol_paths = set()
    for base, content in symbols_by_base.items():
        path = paths.symbols_file_for(base)
        written_symbol_paths.add(path)
        status['symbols:%s' % k2x_base_name(base)] = _write_if_changed(
            path, content, force)
    for path in _existing_k2x_symbols():
        if path not in written_symbol_paths:
            os.remove(path)
            status['symbols:%s' % os.path.basename(path)] = 'removed'

    status['types'] = _write_if_changed(
        paths.types_file, build_types_file(merged), force)
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


def _call_with_fd2_captured(call):
    """Run call() with OS-level stderr redirected into a capture buffer.

    libxkbcommon logs compile diagnostics with fprintf straight to file
    descriptor 2 -- invisible to Python-level redirection, and eaten by the
    TUI's alternate screen before anyone can read it. Duplicating fd 2 onto
    a temp file for the duration captures every byte the C side emits.
    Returns (result, captured_text). tempfile is a lazy import to keep the
    generated installer's import surface unchanged (precedent: ctypes in
    validate_keymap).
    """

    import tempfile

    sys.stderr.flush()
    saved_fd = os.dup(2)
    capture = tempfile.TemporaryFile()
    os.dup2(capture.fileno(), 2)
    try:
        result = call()
    finally:
        sys.stderr.flush()
        os.dup2(saved_fd, 2)
        os.close(saved_fd)
    capture.seek(0)
    captured = capture.read().decode('utf-8', errors='replace')
    capture.close()
    return result, captured


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

    Returns (ok, message, diagnostics): (True, '', diagnostics) on success,
    (False, message, diagnostics) on a compile failure -- diagnostics being
    whatever the C side printed to fd 2 during the compile (possibly empty,
    and possibly non-empty even on success: libxkbcommon reports recoverable
    problems like unknown keysyms as [ERROR] lines while still assembling a
    keymap). If libxkbcommon is unavailable it returns (True, 'unavailable',
    ''); on a real Linux target it is always present (it is the compositor's
    own library).
    """

    import ctypes
    import ctypes.util

    libname = ctypes.util.find_library('xkbcommon')
    if not libname:
        return True, 'unavailable', ''
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

        def _compile():
            context = xkb.xkb_context_new(0)
            names = _Names(b'evdev', b'pc105', identifier.encode(),
                           variant_name.encode(), None)
            return xkb.xkb_keymap_new_from_names(
                context, ctypes.byref(names), 0)

        keymap, diagnostics = _call_with_fd2_captured(_compile)
        if keymap:
            xkb.xkb_keymap_unref(keymap)
            return True, '', diagnostics
        return False, ('libxkbcommon could not assemble keymap for %s(%s)'
                       % (identifier, variant_name)), diagnostics
    except Exception as error:
        # never block on a probe bug
        return True, 'validation error: %s' % error, ''


def validate_installed(records, paths=None):
    """Validate every variant of every record against the on-disk tree.

    Returns (ok, failures, diagnostics): failures is a list of (identifier,
    variant, message); diagnostics is a list of (identifier, variant, text)
    for every compile whose C-side output was non-empty, success or not.
    Call AFTER the files are written (the keymap is assembled from them) and
    BEFORE reporting success.
    """

    paths = paths or InstallPaths()
    failures = []
    diagnostics = []
    for record in records:
        base = k2x_base_name(record.get('base_layout') or 'us')
        for variant_name, _text in record['variants']:
            # Compile the way it is actually selected: <base>-k2x(mac-k2x-variant).
            ok, message, noise = validate_keymap(base, variant_name, paths)
            if noise.strip():
                diagnostics.append(
                    (record['identifier'], variant_name, noise.strip()))
            if not ok:
                failures.append((record['identifier'], variant_name, message))
    return (not failures), failures, diagnostics


def _snapshot_files(paths):
    """Capture current contents of every managed file (or None if absent), so a
    failed install can be rolled back to the exact prior state.

    Includes every per-base '<base>-k2x' symbols file currently on disk, so
    rollback restores or removes them exactly.
    """

    files = [paths.types_file, paths.rules_file, paths.registry_file,
             paths.compose_file, paths.manifest_file]
    if os.path.isdir(paths.symbols_dir):
        for name in os.listdir(paths.symbols_dir):
            full = os.path.join(paths.symbols_dir, name)
            if not os.path.isfile(full):
                continue
            text = _read(full)
            if text and _BEGIN in text:
                files.append(full)
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


def install_records(new_records, paths=None, force=False, validate=True,
                    write_symbols=True):
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

    status, merged = _rebuild_managed_files(paths, manifest, force,
                           write_symbols=write_symbols)

    result = {'paths': paths, 'status': status, 'added': added,
              'refreshed': refreshed,
              'all_unchanged': all(v == 'unchanged' for v in status.values()),
              'installed_count': len(merged), 'validated': False,
              'rolled_back': False, 'failures': [], 'diagnostics': []}

    if validate:
        ok, failures, diagnostics = validate_installed(new_records, paths)
        result['validated'] = ok
        result['failures'] = failures
        result['diagnostics'] = diagnostics
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
    targets = [paths.types_file, paths.rules_file, paths.registry_file,
               paths.compose_file, paths.manifest_file]
    # Every per-base symbols file we manage, matched by our managed-region marker.
    if os.path.isdir(paths.symbols_dir):
        for name in os.listdir(paths.symbols_dir):
            full = os.path.join(paths.symbols_dir, name)
            if not os.path.isfile(full):
                continue
            text = _read(full)
            if text and _BEGIN in text:
                targets.append(full)
    for path in targets:
        if os.path.exists(path):
            try:
                os.remove(path)
                removed.append(os.path.basename(path))
            except OSError:
                pass
    return {'paths': paths, 'removed': removed}


# --- system-location install (symbols only) --------------------------------
#
# System mode moves ONLY the per-base symbols files to the system XKB tree:
# that placement is what the X server's own compiler (and therefore the KDE
# keyboard preview and pure Xorg sessions) can see. Rules/registry overlays
# stay user-side in both modes (libxkbregistry reads XDG paths -- that is
# what makes desktop pickers work), and XCompose is per-user by nature.
# These functions are policy-free engine pieces: root checks, escalation, and
# UI live in the generated installer.

SYSTEM_SYMBOLS_DIR = '/usr/share/X11/xkb/symbols'
SYSTEM_MANIFEST_DIR = '/var/lib/kl2xkb'
SYSTEM_MANIFEST_FILE = os.path.join(SYSTEM_MANIFEST_DIR, 'manifest.json')


def system_symbols_writable():
    """True when the system symbols dir sits on a writable filesystem.

    Detects immutable/read-only-/usr distros BEFORE any escalation attempt:
    ST_RDONLY is a mount property, visible without privileges. A False here
    means system mode should be declined with a pointer to user mode (write
    PERMISSION is root's concern; a read-only MOUNT defeats root too).
    """

    directory = SYSTEM_SYMBOLS_DIR
    while directory and not os.path.isdir(directory):
        directory = os.path.dirname(directory)
    if not directory:
        return False
    try:
        flags = os.statvfs(directory).f_flag
    except OSError:
        return False
    return not (flags & os.ST_RDONLY)


def _load_system_manifest():
    text = _read(SYSTEM_MANIFEST_FILE)
    if not text:
        return {'files': {}}
    try:
        manifest = json.loads(text)
    except ValueError:
        return {'files': {}}
    if not isinstance(manifest, dict) or 'files' not in manifest:
        return {'files': {}}
    return manifest


def stage_system_symbols(records, directory):
    """Write the per-base symbols files into 'directory' for a system apply.

    Returns the list of staged file names ('plx', 'usx', ...). The staged
    content is byte-identical to what user mode would write; only the final
    destination differs.
    """

    os.makedirs(directory, exist_ok=True)
    names = []
    for base, content in build_symbols_files(records).items():
        name = k2x_base_name(base)
        with open(os.path.join(directory, name), 'w', encoding='utf-8') as fh:
            fh.write(content)
        names.append(name)
    return sorted(names)


def system_apply(staged_dir):
    """Apply staged symbols files to the system location (run as root).

    Safety rules, in order: every staged file must carry the managed-region
    marker (refuse to stage arbitrary content into the system tree); an
    existing TARGET that lacks the marker and is not in the system manifest is
    NEVER overwritten (refuse loudly -- it is somebody else's file); writes go
    through a temp file in the target directory plus os.replace (atomic on the
    same filesystem); and a failure mid-batch restores every file already
    replaced in this run (byte-for-byte) before returning.

    Returns {'written': [...], 'refused': [...], 'error': str | None}.
    """

    result = {'written': [], 'refused': [], 'error': None}
    manifest = _load_system_manifest()

    staged = []
    for name in sorted(os.listdir(staged_dir)):
        full = os.path.join(staged_dir, name)
        if not os.path.isfile(full):
            continue
        content = _read(full)
        if not content or _BEGIN not in content:
            result['refused'].append('%s (staged file lacks the managed '
                                     'marker)' % name)
            continue
        staged.append((name, content))

    backups = []                    # (target_path, previous_content | None)
    try:
        for name, content in staged:
            target = os.path.join(SYSTEM_SYMBOLS_DIR, name)
            existing = _read(target)
            if (existing is not None and _BEGIN not in existing
                    and name not in manifest['files']):
                result['refused'].append('%s (existing system file is not '
                                         'kl2xkb-managed)' % name)
                continue
            backups.append((target, existing))
            temp_path = target + '.kl2xkb-new'
            with open(temp_path, 'w', encoding='utf-8') as fh:
                fh.write(content)
            os.chmod(temp_path, 0o644)
            os.replace(temp_path, target)
            manifest['files'][name] = {'bytes': len(content)}
            result['written'].append(name)

        os.makedirs(SYSTEM_MANIFEST_DIR, exist_ok=True)
        with open(SYSTEM_MANIFEST_FILE, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(manifest, indent=2, ensure_ascii=False))
    except Exception as error:
        for target, previous in backups:
            try:
                if previous is None:
                    if os.path.exists(target):
                        os.remove(target)
                else:
                    with open(target, 'w', encoding='utf-8') as fh:
                        fh.write(previous)
            except OSError:
                pass
        result['error'] = str(error)
    return result


def system_remove():
    """Remove every manifest-listed system symbols file (run as root).

    A target is only removed while it still carries the managed marker; a
    file someone replaced by hand is left alone and reported. Returns
    {'removed': [...], 'skipped': [...]}.
    """

    result = {'removed': [], 'skipped': []}
    manifest = _load_system_manifest()
    for name in sorted(manifest['files']):
        target = os.path.join(SYSTEM_SYMBOLS_DIR, name)
        existing = _read(target)
        if existing is None:
            result['skipped'].append('%s (already gone)' % name)
        elif _BEGIN not in existing:
            result['skipped'].append('%s (no longer kl2xkb-managed; left '
                                     'in place)' % name)
        else:
            os.remove(target)
            result['removed'].append(name)
    if os.path.isdir(SYSTEM_MANIFEST_DIR):
        try:
            os.remove(SYSTEM_MANIFEST_FILE)
            os.rmdir(SYSTEM_MANIFEST_DIR)
        except OSError:
            pass
    return result


# Matches our per-record section names inside a symbols file; group(1) is
# the record identifier. Kept as a module-global _rgx per project convention.
_SECTION_ID_RGX = re.compile(r'xkb_symbols "mac-k2x-([a-z0-9]+)-(?:ansi|iso)"')


def system_installed_identifiers():
    """Map each present system symbols file to the layout identifiers whose
    sections it contains, parsed from the section names themselves (possible
    since names carry the record identifier). Returns {file_name: sorted ids}.
    """

    occupancy = {}
    for name, present in system_installed():
        if not present:
            continue
        content = _read(os.path.join(SYSTEM_SYMBOLS_DIR, name))
        if not content:
            continue
        ids = sorted(set(_SECTION_ID_RGX.findall(content)))
        if ids:
            occupancy[name] = ids
    return occupancy


def system_remove_files(names):
    """Remove SPECIFIC manifest-listed system files (run as root).

    The scoped sibling of system_remove, for when a base file's last layout
    is uninstalled. Same safety rules: only manifest-listed names, only
    files carrying the managed-region marker. Returns
    {'removed': [...], 'skipped': [...]}.
    """

    manifest = _load_system_manifest()
    result = {'removed': [], 'skipped': []}
    for name in names:
        if name not in manifest['files']:
            result['skipped'].append('%s (not in system manifest)' % name)
            continue
        full = os.path.join(SYSTEM_SYMBOLS_DIR, name)
        content = _read(full)
        if content is None:
            del manifest['files'][name]
            result['skipped'].append('%s (already absent)' % name)
            continue
        if _BEGIN not in content:
            result['skipped'].append('%s (no marker; left alone)' % name)
            continue
        try:
            os.remove(full)
            del manifest['files'][name]
            result['removed'].append(name)
        except OSError as error:
            result['skipped'].append('%s (%s)' % (name, error))
    if manifest['files']:
        os.makedirs(SYSTEM_MANIFEST_DIR, exist_ok=True)
        with open(SYSTEM_MANIFEST_FILE, 'w', encoding='utf-8') as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=False)
    else:
        # last managed file gone: clear the manifest like system_remove does
        try:
            os.remove(SYSTEM_MANIFEST_FILE)
            os.rmdir(SYSTEM_MANIFEST_DIR)
        except OSError:
            pass
    return result


def system_installed():
    """Return [(name, still_present)] for manifest-listed system files."""

    manifest = _load_system_manifest()
    return [
        (name, os.path.isfile(os.path.join(SYSTEM_SYMBOLS_DIR, name)))
        for name in sorted(manifest['files'])
    ]


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
