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
    source_languages:  'list' = field(default_factory=list)  # TIS ISO 639 codes

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


# Language name -> (iso639 language code, iso3166 country code, short label).
# Used to populate the registry's countryList / languageList / shortDescription
# so desktop pickers show a flag and a tray label instead of a blank/placeholder.
# The country code is what drives the flag icon in KDE; the short label is the
# 2-3 char string shown in the layout-switcher applet. Unknown languages fall
# back to no codes (better a missing flag than a WRONG country's flag).
_LANGUAGE_ISO = {
    'Arabic':     ('ara', 'SA', 'ar'),
    'Armenian':   ('hye', 'AM', 'hy'),
    'Bengali':    ('ben', 'IN', 'bn'),
    'Bulgarian':  ('bul', 'BG', 'bg'),
    'Chinese':    ('zho', 'CN', 'zh'),
    'Croatian':   ('hrv', 'HR', 'hr'),
    'Czech':      ('ces', 'CZ', 'cs'),
    'Danish':     ('dan', 'DK', 'da'),
    'Dutch':      ('nld', 'NL', 'nl'),
    'English':    ('eng', 'US', 'en'),
    'Finnish':    ('fin', 'FI', 'fi'),
    'French':     ('fra', 'FR', 'fr'),
    'Georgian':   ('kat', 'GE', 'ka'),
    'German':     ('deu', 'DE', 'de'),
    'Greek':      ('ell', 'GR', 'el'),
    'Hebrew':     ('heb', 'IL', 'he'),
    'Hindi':      ('hin', 'IN', 'hi'),
    'Hungarian':  ('hun', 'HU', 'hu'),
    'Icelandic':  ('isl', 'IS', 'is'),
    'Italian':    ('ita', 'IT', 'it'),
    'Japanese':   ('jpn', 'JP', 'ja'),
    'Korean':     ('kor', 'KR', 'ko'),
    'Norwegian':  ('nor', 'NO', 'no'),
    'Polish':     ('pol', 'PL', 'pl'),
    'Portuguese': ('por', 'PT', 'pt'),
    'Romanian':   ('ron', 'RO', 'ro'),
    'Russian':    ('rus', 'RU', 'ru'),
    'Serbian':    ('srp', 'RS', 'sr'),
    'Slovak':     ('slk', 'SK', 'sk'),
    'Spanish':    ('spa', 'ES', 'es'),
    'Swedish':    ('swe', 'SE', 'sv'),
    'Thai':       ('tha', 'TH', 'th'),
    'Tibetan':    ('bod', 'CN', 'bo'),
    'Turkish':    ('tur', 'TR', 'tr'),
    'Turkmen':    ('tuk', 'TM', 'tk'),
    'Ukrainian':  ('ukr', 'UA', 'uk'),
    'Vietnamese': ('vie', 'VN', 'vi'),

    # Exotic / minority-script layouts. Primary language codes are Apple's own
    # (from the TIS enumeration); country is the reasoned canonical home, or None
    # where the language has no single-country flag (indigenous/cross-border) --
    # None gives a correct language grouping but no (wrong) flag.
    'Adlam':      ('ful', None, 'ff'),    # Fulani (Adlam script), West Africa, no single country
    'Apache':     ('apw', 'US', 'apw'),   # Western Apache (USA)
    'Cherokee':   ('chr', 'US', 'chr'),   # Cherokee (USA)
    'Chickasaw':  ('cic', 'US', 'cic'),   # Chickasaw (USA)
    'Hawaiian':   ('haw', 'US', 'haw'),
    'Manipuri':   ('mni', 'IN', 'mni'),   # Meitei (India)
    'Maori':      ('mri', 'NZ', 'mi'),
    'Pahawh Hmong': ('hmn', None, 'hmn'), # Hmong (Pahawh script), cross-border
    'Rejang':     ('rej', 'ID', 'rej'),   # Rejang (Indonesia)
    'Wancho':     ('nnp', 'IN', 'nnp'),   # Wancho (India)
    'Wolastoqey': ('pqm', 'CA', 'pqm'),   # Wolastoqey/Maliseet-Passamaquoddy (Canada/USA)
    'Yiddish':    ('yid', None, 'yi'),    # cross-border
    'Yoruba':     ('yor', 'NG', 'yo'),
    'Welsh':      ('cym', 'GB', 'cy'),
    'Uyghur':     ('uig', 'CN', 'ug'),
    'Sami':       ('sme', None, 'se'),    # Northern Sami, cross-border
}


# Language-name qualifiers that derive_language may leave attached (e.g.
# 'Tibetan Otani', 'ABC India'). When an exact match fails, the resolver retries
# on the first word so these still resolve. 'ABC' is Apple's generic Latin
# layout family -- not a language -- so it maps to its base script (English).
_LANGUAGE_ALIASES = {
    'ABC':       ('eng', 'US', 'en'),
    'ABC India': ('eng', 'US', 'en'),
}


def language_iso_codes(language: str) -> 'tuple | None':
    """Return (iso639, iso3166, short_label) for a language name, or None.

    Tries an exact match, then known aliases, then the first word of the name (so
    qualified names like 'Tibetan Otani' resolve via 'Tibetan'). None means the
    language is unknown, so the registry omits the country/language/short elements
    rather than guess -- a missing flag is preferable to a wrong country's flag.
    Note iso3166 may itself be None for a known language with no single country;
    callers must treat 'no tuple' and 'tuple with None country' the same for the
    flag (both mean no flag) while still using the language code for grouping.
    """

    if language in _LANGUAGE_ISO:
        return _LANGUAGE_ISO[language]
    if language in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[language]
    first_word = language.split(' ')[0] if language else ''
    if first_word and first_word in _LANGUAGE_ISO:
        return _LANGUAGE_ISO[first_word]
    return None


def group_by_language(records: 'list') -> 'dict':
    """Group records into {language: [records...]} sorted within each group."""

    groups = {}
    for record in records:
        groups.setdefault(record.language, []).append(record)
    for language in groups:
        groups[language].sort(key=lambda r: r.display_name)
    return dict(sorted(groups.items()))


# End of file #
