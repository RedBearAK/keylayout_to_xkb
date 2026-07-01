"""
keylayout_to_xkb/install/installer.py

Idempotent installation of layout records into the per-user XKB tree
(~/.config/xkb), which is rootless and works on Wayland sessions (libxkbcommon
>= 0.10.0). Three files are managed:

  symbols/<NAMESPACE>            our layout symbols blocks (one section/variant)
  rules/evdev                    rules overlay: include system, map our layouts
  rules/evdev.xml                registry XML so layouts appear in the GUI picker

IDEMPOTENCY. We OWN these files (under a fixed namespace), so each install
regenerates them WHOLESALE from the full set of currently-installed records --
never appends. This makes duplicates impossible by construction and makes
re-running converge to identical content. Before writing, we compare the new
content to what is on disk; if identical, we skip the write (reporting
'unchanged') unless force=True. A small JSON manifest records which records are
installed so the wholesale regeneration knows the full set across multiple
install runs (install Polish today, German next week -> both in the rules).

ACTIVATION requires a logout/login for the compositor to rebuild its keymap;
the installer reports this rather than implying an immediate effect.

This module performs NO interactive I/O; the TUI and CLI call into it. It fails
loudly (raises) on real errors rather than degrading silently.
"""

import os
import json
import hashlib

from xml.sax.saxutils import escape as xml_escape

from keylayout_to_xkb.install.catalog import LayoutRecord


__version__ = '20260623'


# Fixed namespace for our owned files under the user XKB tree. A single symbols
# file holds all our layouts' sections; the rules/registry reference it.
_NAMESPACE = 'keylayout_to_xkb'

# Marker lines delimiting our managed region, for transparency in the files.
_BEGIN = '// >>> keylayout_to_xkb managed region (do not edit by hand) >>>'
_END = '// <<< keylayout_to_xkb managed region <<<'


def user_xkb_root() -> str:
    """Return $XDG_CONFIG_HOME/xkb (or ~/.config/xkb), without creating it."""

    base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(
        os.path.expanduser('~'), '.config'
    )
    return os.path.join(base, 'xkb')


class InstallPaths:
    """Resolved file paths under the user XKB tree."""

    def __init__(self, root: 'str | None' = None):
        self.root = root or user_xkb_root()
        self.symbols_dir = os.path.join(self.root, 'symbols')
        self.rules_dir = os.path.join(self.root, 'rules')
        self.symbols_file = os.path.join(self.symbols_dir, _NAMESPACE)
        self.rules_file = os.path.join(self.rules_dir, 'evdev')
        self.registry_file = os.path.join(self.rules_dir, 'evdev.xml')
        self.manifest_file = os.path.join(self.root, '%s.manifest.json' % _NAMESPACE)


def _read(path: str) -> 'str | None':
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _write_if_changed(path: str, content: str, force: bool) -> str:
    """Write content unless identical to disk. Returns 'written'|'unchanged'."""

    existing = _read(path)
    if existing is not None and existing == content and not force:
        return 'unchanged'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(content)
    return 'written'


# -- content builders (pure: record set -> file text) ----------------------

def build_symbols_file(records: 'list') -> str:
    """All records' variant blocks in one symbols file, namespaced region."""

    lines = [_BEGIN, '']
    for record in sorted(records, key=lambda r: r.identifier):
        for variant_name, symbols_text in record.variants:
            lines.append('// %s : %s' % (record.identifier, variant_name))
            lines.append(symbols_text)
            lines.append('')
    lines.append(_END)
    lines.append('')
    return '\n'.join(lines)


def build_rules_file(records: 'list') -> str:
    """Rules overlay: include the system ruleset, then map our layout+variant
    names to sections in our symbols file.

    The leading '! include %S/rules/evdev' pulls in the full system ruleset so
    only our additions are overlaid. Each variant maps the RMLVO (layout,
    variant) pair to '<namespace>(<variant>)' -- our symbols file and section.
    """

    lines = []
    lines.append('// %s rules overlay' % _NAMESPACE)
    lines.append('! include %S/rules/evdev')
    lines.append('')
    lines.append('! layout\tvariant\t=\tsymbols')
    for record in sorted(records, key=lambda r: r.identifier):
        for variant_name in record.variant_names():
            lines.append('  %s\t%s\t=\t%s(%s)'
                         % (record.identifier, variant_name, _NAMESPACE, variant_name))
    lines.append('')
    return '\n'.join(lines)


def build_registry_xml(records: 'list') -> str:
    """The evdev.xml the GUI picker reads (libxkbregistry), listing our layouts
    and their variants with human descriptions.

    One <layout> per record (its identifier + base display name), with a
    <variantList> of its variants. Mirrors the system evdev.xml structure so the
    picker lists them normally.
    """

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<!DOCTYPE xkbConfigRegistry SYSTEM "xkb.dtd">')
    out.append('<xkbConfigRegistry version="1.1">')
    out.append('  <layoutList>')
    for record in sorted(records, key=lambda r: r.identifier):
        # Base layout description: complete the display stem with a closing
        # paren if it is an open '... (Macintosh' stem.
        base_desc = record.display_name
        if base_desc.count('(') > base_desc.count(')'):
            base_desc = base_desc + ')'
        out.append('    <layout>')
        out.append('      <configItem>')
        out.append('        <name>%s</name>' % xml_escape(record.identifier))
        out.append('        <description>%s</description>' % xml_escape(base_desc))
        out.append('      </configItem>')
        out.append('      <variantList>')
        for variant_name in record.variant_names():
            suffix = 'ANSI' if variant_name.endswith('ansi') else (
                'ISO' if variant_name.endswith('iso') else variant_name)
            out.append('        <variant>')
            out.append('          <configItem>')
            out.append('            <name>%s</name>' % xml_escape(variant_name))
            out.append('            <description>%s</description>'
                       % xml_escape('%s, %s)' % (record.display_name, suffix)))
            out.append('          </configItem>')
            out.append('        </variant>')
        out.append('      </variantList>')
        out.append('    </layout>')
    out.append('  </layoutList>')
    out.append('</xkbConfigRegistry>')
    out.append('')
    return '\n'.join(out)


def build_compose_file(records: 'list') -> str:
    """Concatenate the records' compose bodies into one self-contained file."""

    parts = []
    for record in sorted(records, key=lambda r: r.identifier):
        if record.compose_text.strip():
            parts.append('# === %s ===' % record.identifier)
            parts.append(record.compose_text)
            parts.append('')
    return '\n'.join(parts)


# -- manifest (the installed set) ------------------------------------------

def _load_manifest(paths: 'InstallPaths') -> 'dict':
    text = _read(paths.manifest_file)
    if not text:
        return {'installed': {}}
    try:
        data = json.loads(text)
        if 'installed' not in data:
            data['installed'] = {}
        return data
    except json.JSONDecodeError:
        return {'installed': {}}


def _record_payload(record: LayoutRecord) -> 'dict':
    """Serialise a record for the manifest so the installed set survives across
    runs without needing the original installer file present again."""

    return {
        'identifier': record.identifier,
        'display_name': record.display_name,
        'language': record.language,
        'source_id': record.source_id,
        'variants': record.variants,
        'compose_text': record.compose_text,
        'compose_complete': record.compose_complete,
        'dead_key_count': record.dead_key_count,
        'key_count': record.key_count,
    }


def _payload_to_record(payload: 'dict') -> LayoutRecord:
    return LayoutRecord(
        identifier=payload['identifier'],
        display_name=payload['display_name'],
        language=payload.get('language', 'Other'),
        source_id=payload.get('source_id', ''),
        variants=[tuple(v) for v in payload.get('variants', [])],
        compose_text=payload.get('compose_text', ''),
        compose_complete=payload.get('compose_complete', True),
        dead_key_count=payload.get('dead_key_count', 0),
        key_count=payload.get('key_count', 0),
    )


def installed_records(paths: 'InstallPaths') -> 'list':
    """The records currently installed, from the manifest."""

    manifest = _load_manifest(paths)
    return [_payload_to_record(p) for p in manifest['installed'].values()]


# -- the install operation -------------------------------------------------

def install_records(
    new_records: 'list',
    paths: 'InstallPaths | None' = None,
    force: bool = False,
) -> 'dict':
    """Install (or refresh) the given records into the user XKB tree.

    Merges new_records into the installed set (by identifier), regenerates all
    four managed files WHOLESALE from the merged set, and writes each only if
    changed (unless force). Returns a report dict with per-file status and the
    list of identifiers that were newly added vs already present.
    """

    paths = paths or InstallPaths()

    manifest = _load_manifest(paths)
    before = set(manifest['installed'].keys())

    added = []
    refreshed = []
    for record in new_records:
        if record.identifier in manifest['installed']:
            refreshed.append(record.identifier)
        else:
            added.append(record.identifier)
        manifest['installed'][record.identifier] = _record_payload(record)

    merged = [_payload_to_record(p) for p in manifest['installed'].values()]

    status = {}
    status['symbols'] = _write_if_changed(
        paths.symbols_file, build_symbols_file(merged), force)
    status['rules'] = _write_if_changed(
        paths.rules_file, build_rules_file(merged), force)
    status['registry'] = _write_if_changed(
        paths.registry_file, build_registry_xml(merged), force)
    compose_text = build_compose_file(merged)
    if compose_text.strip():
        status['compose'] = _write_if_changed(
            os.path.join(paths.root, 'Compose.%s' % _NAMESPACE), compose_text, force)

    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False)
    status['manifest'] = _write_if_changed(paths.manifest_file, manifest_text, force)

    all_unchanged = all(v == 'unchanged' for v in status.values())

    return {
        'paths': paths,
        'status': status,
        'added': added,
        'refreshed': refreshed,
        'all_unchanged': all_unchanged,
        'installed_count': len(merged),
    }


# End of file #
