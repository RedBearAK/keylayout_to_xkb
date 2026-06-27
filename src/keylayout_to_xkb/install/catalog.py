"""
keylayout_to_xkb/install/catalog.py

The catalog data model: a LayoutRecord is the self-contained unit the generator
embeds into an installer file and the TUI lists/previews/installs. A record
carries everything needed with no external sources -- identity, language group,
the emitted symbols text per variant, and the emitted compose text -- so the
installer file is fully self-describing.

Language grouping is derived from the layout's display/source name (Polish Pro
and Polish both -> "Polish"; the Turkish variants -> "Turkish"), with an
override table for names whose group is not cleanly derivable.
"""

import re

from dataclasses import dataclass, field


__version__ = '20260623'


@dataclass
class LayoutRecord:
    """One installable layout, fully self-contained for embedding.

    identifier:    XKB layout token stem, e.g. 'plpro' (file/registry name).
    display_name:  human label stem, e.g. 'Polish Pro (Macintosh'  (the variant
                   suffix ', ANSI)'/', ISO)' is completed per variant).
    language:      grouping key for the menu, e.g. 'Polish'.
    source_id:     Apple source identity, e.g. 'com.apple.keylayout.PolishPro'.
    variants:      ordered list of (variant_name, symbols_text), e.g.
                   [('mac-ansi', '...'), ('mac-iso', '...')].
    compose_text:  the self-contained XCompose body (may be '' if none).
    compose_complete: False if any dead state was dropped for lack of a dead_*
                   keysym (complex-script layouts); surfaced in previews so a
                   user knows the compose file is partial.
    dead_key_count / key_count: small summary numbers for the preview header.
    """

    identifier:        str
    display_name:      str
    language:          str
    source_id:         str = ''
    variants:          'list' = field(default_factory=list)
    compose_text:      str = ''
    compose_complete:  bool = True
    dead_key_count:    int = 0
    key_count:         int = 0

    def variant_names(self) -> 'list':
        return [name for name, _text in self.variants]

    def symbols_for(self, variant_name: str) -> 'str | None':
        for name, text in self.variants:
            if name == variant_name:
                return text
        return None


# Apple display/source names whose language group is not the obvious first word,
# or which should be grouped specially. Extend as real catalogs need it.
_LANGUAGE_OVERRIDES = {
    'usextended': 'English',
    'us': 'English',
    'abc': 'English',
    'abcextended': 'English',
    'british': 'English',
    'unicodehexinput': 'Special',
    'tibetanwylie': 'Tibetan',
    'tibetanqwerty': 'Tibetan',
    'tibetanotaniu_s': 'Tibetan',
    'manipuribengali': 'Manipuri',
    'manipurimeeteimayek': 'Manipuri',
}

# Trailing descriptors to strip when deriving a language group from a name, so
# 'Polish Pro', 'Polish - QWERTZ', 'Turkish-QWERTY-PC' all reduce to the base.
_DESCRIPTOR_WORDS = (
    'pro', 'qwerty', 'qwertz', 'azerty', 'pc', 'standard', 'extended',
    'legacy', 'old', 'new', 'macintosh', 'mac', 'ansi', 'iso', 'jis',
    'polytonic', 'monotonic', 'wylie', 'us',
)


def derive_language(display_name: str, source_id: str = '') -> str:
    """Derive a language grouping key from a layout's name.

    Strategy: check the override table by a normalised source/display token
    first; otherwise take the leading words of the display name up to the first
    descriptor word (so 'Polish Pro' -> 'Polish', 'Greek Polytonic' -> 'Greek').
    Always returns a non-empty group ('Other' as last resort).
    """

    # Normalised key for override lookup: source_id leaf or display name, lower,
    # alphanumerics only.
    leaf = source_id.rsplit('.', 1)[-1] if source_id else display_name
    norm = re.sub(r'[^a-z0-9]', '', leaf.lower())
    if norm in _LANGUAGE_OVERRIDES:
        return _LANGUAGE_OVERRIDES[norm]

    # Strip a leading 'com.apple.keylayout.' style and split the display name
    # into words; accumulate words until a descriptor word is hit.
    words = re.split(r'[\s\-\u2013\u2014_]+', display_name.strip())
    kept = []
    for word in words:
        if re.sub(r'[^a-z]', '', word.lower()) in _DESCRIPTOR_WORDS:
            break
        if word:
            kept.append(word)
    group = ' '.join(kept).strip()
    return group if group else 'Other'


def group_by_language(records: 'list') -> 'dict':
    """Group records into {language: [records...]} sorted within each group."""

    groups = {}
    for record in records:
        groups.setdefault(record.language, []).append(record)
    for language in groups:
        groups[language].sort(key=lambda r: r.display_name)
    return dict(sorted(groups.items()))


# End of file #
