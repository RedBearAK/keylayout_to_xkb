"""
keylayout_to_xkb/install/build_record.py

Bridge from a parsed Layout to a self-contained catalog LayoutRecord: run the
three emitters (symbols variants + compose) and package their text plus the
identity/grouping metadata into one record the generator can embed and the TUI
can list, preview, and install.

This is the single place that turns "a layout the parser produced" into "an
installable catalog unit", so the emitter wiring lives in one auditable spot.
The identifier and display stem follow the naming settled for the project:
a short layout token (e.g. 'plpro') and a '(Macintosh' display stem that the
installer completes per variant.
"""

import re

from keylayout_to_xkb.common.models import Layout, OutputKind
from keylayout_to_xkb.emit.symbols import emit_symbols_variants
from keylayout_to_xkb.emit.compose import emit_compose
from keylayout_to_xkb.emit.classify import dead_state_keysym
from keylayout_to_xkb.install.catalog import LayoutRecord, derive_language


__version__ = '20260623'


def _identifier_from(layout: Layout) -> str:
    """Derive a short XKB-token identifier from the layout's source identity.

    Prefers the Apple source_id leaf (com.apple.keylayout.PolishPro -> polishpro),
    falling back to the name. Lowercased, alphanumerics only, so it is a valid
    XKB layout token. Kept deterministic so re-generating yields the same id.
    """

    provenance = layout.provenance
    leaf = ''
    if provenance is not None and provenance.source_id:
        leaf = provenance.source_id.rsplit('.', 1)[-1]
    if not leaf:
        leaf = layout.name or 'layout'
    token = re.sub(r'[^a-z0-9]', '', leaf.lower())
    return token or 'layout'


def _humanize_name(raw: str) -> str:
    """Turn a possibly-camelCase internal name into spaced words.

    The binary 'uchr' carries no display name, so a test/extraction may pass the
    internal identifier ('PolishPro', 'USExtended'). Split camelCase / digit
    boundaries into words ('Polish Pro', 'US Extended') so the display stem reads
    naturally. A name that already has spaces is returned trimmed. On a real Mac
    extraction the TIS localized name ('Polish') is used instead and this is a
    no-op, so it only helps the provenance-poor binary path.
    """

    if not raw:
        return ''
    if ' ' in raw.strip():
        return raw.strip()
    # Insert spaces at lowercase->uppercase and letter<->digit boundaries.
    spaced = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', raw)
    spaced = re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', ' ', spaced)
    spaced = re.sub(r'(?<=[A-Za-z])(?=[0-9])', ' ', spaced)
    return spaced.strip()


def _display_stem(layout: Layout) -> str:
    """Human display stem ending in ' (Macintosh' (open paren, completed per
    variant by the installer/registry with ', ANSI)' / ', ISO)').

    Uses the source/display name as the base. The '(Macintosh' tag advertises
    that the macOS Option / Shift+Option special-character layers are present.
    """

    base = _humanize_name((layout.name or '').strip())
    # Strip an existing trailing '(Macintosh...' if the name already has one, so
    # we do not double it.
    base = re.sub(r'\s*\(mac(intosh)?.*\)?\s*$', '', base, flags=re.IGNORECASE).strip()
    return '%s (Macintosh' % base if base else 'Layout (Macintosh'


def _compose_is_complete(layout: Layout) -> bool:
    """True if every dead state maps to a dead_* keysym (so none is dropped).

    Mirrors emit_compose's own decision: a state whose accent has no dead_*
    keysym is skipped, making the compose file partial. Complex-script layouts
    (Tibetan, polytonic extras) commonly come back partial; Latin layouts like
    Polish Pro are complete.
    """

    for dead_state in layout.dead_states.values():
        if dead_state_keysym(dead_state.terminator, dead_state.compositions) is None:
            return False
    return True


def _counts(layout: Layout) -> 'tuple':
    """(key_count, dead_key_count) for the preview header."""

    key_count = sum(1 for _vk, planes in layout.keys.items() if planes)
    dead_key_count = len(layout.dead_states)
    return key_count, dead_key_count


def build_record(layout: Layout) -> LayoutRecord:
    """Build a fully-populated, self-contained LayoutRecord from a Layout.

    Runs the symbols and compose emitters and assembles the record. The record
    carries the emitted text verbatim, so once built it needs neither the parser
    nor the source file again -- it is ready to embed in an installer.
    """

    identifier = _identifier_from(layout)
    display_stem = _display_stem(layout)
    language = derive_language(
        _humanize_name(layout.name or ''),
        layout.provenance.source_id if layout.provenance else '',
    )

    variants = emit_symbols_variants(layout, 'mac-k2x', display_stem)

    note = ''
    if layout.provenance is not None and layout.provenance.source_id:
        note = 'Source: %s' % layout.provenance.source_id
    compose_text = emit_compose(layout, header_note=note)

    key_count, dead_key_count = _counts(layout)
    source_id = ''
    source_languages = []
    if layout.provenance is not None:
        if layout.provenance.source_id:
            source_id = layout.provenance.source_id
        if getattr(layout.provenance, 'source_languages', None):
            source_languages = list(layout.provenance.source_languages)

    return LayoutRecord(
        identifier=identifier,
        display_name=display_stem,
        language=language,
        source_id=source_id,
        variants=variants,
        compose_text=compose_text,
        compose_complete=_compose_is_complete(layout),
        dead_key_count=dead_key_count,
        key_count=key_count,
        source_languages=source_languages,
    )


# End of file #
