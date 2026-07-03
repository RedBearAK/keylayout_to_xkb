"""
keylayout_to_xkb/install/language_data.py

Two lookups that give an installed layout a language grouping and a flag.

  1. _TIS_PRIMARY_LANGUAGE: the baked STATIC table of each Apple layout's PRIMARY
     language (ISO 639, sometimes with a script subtag like 'ff-Adlm'), keyed by
     input-source id. Captured on macOS by probe_layout_languages.py.

     IMPORTANT about Apple's data: kTISPropertyInputSourceLanguages returns every
     language a layout CAN type (for a Latin layout that is dozens -- de, en, sw,
     ...), NOT just its own. Only the FIRST entry is the layout's primary
     language, so this table stores that first entry. To refresh it, run the
     probe and use each layout's first language.

     Used when generating OFF macOS, or for a layout the live TIS read missed.

  2. _LANGUAGE_TO_COUNTRY: ISO 639 language code -> ISO 3166 country code, for the
     flag. Apple gives languages, not countries, so this bridges to the flag.
     Languages with no single sensible country (many minority/indigenous
     languages) are intentionally absent: the layout then gets a correct
     languageList grouping but no flag, rather than a wrong country's flag.

Data-only by design (no logic) so the tables are easy to regenerate and review.
The resolver logic lives in generate._record_to_dict.
"""


_TIS_PRIMARY_LANGUAGE = {
    'com.apple.keylayout.2SetHangul': 'ko',
    'com.apple.keylayout.ABC': 'en',
    'com.apple.keylayout.ABC-AZERTY': 'fr',
    'com.apple.keylayout.ABC-India': 'en',
    'com.apple.keylayout.ABC-QWERTZ': 'de',
    'com.apple.keylayout.Adlam-QWERTY': 'ff-Adlm',
    'com.apple.keylayout.AfghanDari': 'fa',
    'com.apple.keylayout.AfghanPashto': 'ps',
    'com.apple.keylayout.AfghanUzbek': 'uz-Arab',
    'com.apple.keylayout.Akan': 'ak',
    'com.apple.keylayout.Albanian': 'sq',
    'com.apple.keylayout.Anjal': 'ta',
    'com.apple.keylayout.Apache': 'apw',
    'com.apple.keylayout.Arabic': 'ar',
    'com.apple.keylayout.Arabic-AZERTY': 'ar',
    'com.apple.keylayout.Arabic-NorthAfrica': 'ar',
    'com.apple.keylayout.Arabic-QWERTY': 'ar',
    'com.apple.keylayout.ArabicPC': 'ar',
    'com.apple.keylayout.Armenian-HMQWERTY': 'hy',
    'com.apple.keylayout.Armenian-WesternQWERTY': 'hy',
    'com.apple.keylayout.Assamese': 'as',
    'com.apple.keylayout.Australian': 'en',
    'com.apple.keylayout.Austrian': 'de',
    'com.apple.keylayout.Azeri': 'az',
    'com.apple.keylayout.Bangla': 'bn',
    'com.apple.keylayout.Bangla-QWERTY': 'bn',
    'com.apple.keylayout.Belgian': 'nl',
    'com.apple.keylayout.Bodo': 'brx',
    'com.apple.keylayout.Brazilian': 'pt',
    'com.apple.keylayout.Brazilian-ABNT2': 'pt',
    'com.apple.keylayout.Brazilian-Pro': 'pt',
    'com.apple.keylayout.British': 'en',
    'com.apple.keylayout.British-PC': 'en',
    'com.apple.keylayout.Bulgarian': 'bg',
    'com.apple.keylayout.Bulgarian-Phonetic': 'bg',
    'com.apple.keylayout.Byelorussian': 'be',
    'com.apple.keylayout.Canadian': 'en',
    'com.apple.keylayout.Canadian-CSA': 'fr',
    'com.apple.keylayout.CanadianFrench-PC': 'fr',
    'com.apple.keylayout.CangjieKeyboard': 'zh-Hant',
    'com.apple.keylayout.Cherokee-Nation': 'chr',
    'com.apple.keylayout.Cherokee-QWERTY': 'chr',
    'com.apple.keylayout.Chickasaw': 'cic',
    'com.apple.keylayout.Choctaw': 'cho',
    'com.apple.keylayout.Chuvash': 'cv',
    'com.apple.keylayout.Colemak': 'en',
    'com.apple.keylayout.Croatian': 'hr',
    'com.apple.keylayout.Croatian-PC': 'hr',
    'com.apple.keylayout.Czech': 'cs',
    'com.apple.keylayout.Czech-QWERTY': 'cs',
    'com.apple.keylayout.DVORAK-QWERTYCMD': 'en',
    'com.apple.keylayout.Danish': 'da',
    'com.apple.keylayout.Devanagari': 'hi',
    'com.apple.keylayout.Devanagari-QWERTY': 'hi',
    'com.apple.keylayout.Dhivehi-QWERTY': 'dv',
    'com.apple.keylayout.Dogri': 'doi',
    'com.apple.keylayout.Dutch': 'nl',
    'com.apple.keylayout.Dvorak': 'en',
    'com.apple.keylayout.Dvorak-Left': 'en',
    'com.apple.keylayout.Dvorak-Right': 'en',
    'com.apple.keylayout.Dzongkha': 'dz',
    'com.apple.keylayout.Estonian': 'et',
    'com.apple.keylayout.Faroese': 'fo',
    'com.apple.keylayout.Finnish': 'fi',
    'com.apple.keylayout.FinnishExtended': 'fi',
    'com.apple.keylayout.FinnishSami-PC': 'fi',
    'com.apple.keylayout.French': 'fr',
    'com.apple.keylayout.French-PC': 'fr',
    'com.apple.keylayout.French-numerical': 'fr',
    'com.apple.keylayout.Geez-QWERTY': 'am',
    'com.apple.keylayout.Georgian-QWERTY': 'ka',
    'com.apple.keylayout.German': 'de',
    'com.apple.keylayout.German-DIN-2137': 'de',
    'com.apple.keylayout.Greek': 'el',
    'com.apple.keylayout.GreekPolytonic': 'el',
    'com.apple.keylayout.Gujarati': 'gu',
    'com.apple.keylayout.Gujarati-QWERTY': 'gu',
    'com.apple.keylayout.Gurmukhi': 'pa',
    'com.apple.keylayout.Gurmukhi-QWERTY': 'pa',
    'com.apple.keylayout.Hanifi-Rohingya-QWERTY': 'rhg',
    'com.apple.keylayout.Hausa': 'ha',
    'com.apple.keylayout.Hawaiian': 'haw',
    'com.apple.keylayout.Hebrew': 'he',
    'com.apple.keylayout.Hebrew-PC': 'he',
    'com.apple.keylayout.Hebrew-QWERTY': 'he',
    'com.apple.keylayout.Hungarian': 'hu',
    'com.apple.keylayout.Hungarian-QWERTY': 'hu',
    'com.apple.keylayout.Icelandic': 'is',
    'com.apple.keylayout.Igbo': 'ig',
    'com.apple.keylayout.InariSami': 'smn',
    'com.apple.keylayout.Ingush': 'inh',
    'com.apple.keylayout.Inuktitut-Nattilik': 'iu',
    'com.apple.keylayout.Inuktitut-Nunavut': 'iu',
    'com.apple.keylayout.Inuktitut-Nutaaq': 'iu',
    'com.apple.keylayout.Inuktitut-QWERTY': 'iu',
    'com.apple.keylayout.InuttitutNunavik': 'iu',
    'com.apple.keylayout.Irish': 'en',
    'com.apple.keylayout.IrishExtended': 'ga',
    'com.apple.keylayout.Italian': 'it',
    'com.apple.keylayout.Italian-Pro': 'it',
    'com.apple.keylayout.Jawi-QWERTY': 'ms-Arab',
    'com.apple.keylayout.JulevSami': 'smj',
    'com.apple.keylayout.JulevSami-Norway': 'smj',
    'com.apple.keylayout.KANA': 'ja',
    'com.apple.keylayout.Kabyle-AZERTY': 'kab',
    'com.apple.keylayout.Kabyle-QWERTY': 'kab',
    'com.apple.keylayout.Kannada': 'kn',
    'com.apple.keylayout.Kannada-QWERTY': 'kn',
    'com.apple.keylayout.Kashmiri-Devanagari': 'ks-Deva',
    'com.apple.keylayout.Kazakh': 'kk',
    'com.apple.keylayout.Khmer': 'km',
    'com.apple.keylayout.KildinSami': 'sjd',
    'com.apple.keylayout.Konkani': 'kok',
    'com.apple.keylayout.Kurdish-Kurmanji': 'ku',
    'com.apple.keylayout.Kurdish-Sorani': 'ckb',
    'com.apple.keylayout.Kyrgyz-Cyrillic': 'ky',
    'com.apple.keylayout.Lao': 'lo',
    'com.apple.keylayout.LatinAmerican': 'es',
    'com.apple.keylayout.Latvian': 'lv',
    'com.apple.keylayout.Lithuanian': 'lt',
    'com.apple.keylayout.Macedonian': 'mk',
    'com.apple.keylayout.Maithili': 'mai',
    'com.apple.keylayout.Malayalam': 'ml',
    'com.apple.keylayout.Malayalam-QWERTY': 'ml',
    'com.apple.keylayout.Maltese': 'mt',
    'com.apple.keylayout.Mandaic-Arabic': 'mid',
    'com.apple.keylayout.Mandaic-QWERTY': 'mid',
    'com.apple.keylayout.Manipuri-Bengali': 'mni-Beng',
    'com.apple.keylayout.Manipuri-MeeteiMayek': 'mni-Mtei',
    'com.apple.keylayout.Maori': 'mi',
    'com.apple.keylayout.Marathi': 'mr',
    'com.apple.keylayout.Mikmaw': 'mic',
    'com.apple.keylayout.Mongolian-Cyrillic': 'mn',
    'com.apple.keylayout.Myanmar': 'my',
    'com.apple.keylayout.Myanmar-QWERTY': 'my',
    'com.apple.keylayout.NKo': 'nqo',
    'com.apple.keylayout.NKo-QWERTY': 'nqo',
    'com.apple.keylayout.Navajo': 'nv',
    'com.apple.keylayout.Nepali': 'ne',
    'com.apple.keylayout.Nepali-IS16350': 'ne',
    'com.apple.keylayout.NorthernSami': 'se',
    'com.apple.keylayout.Norwegian': 'nb',
    'com.apple.keylayout.NorwegianExtended': 'nb',
    'com.apple.keylayout.NorwegianSami-PC': 'nb',
    'com.apple.keylayout.Oriya': 'or',
    'com.apple.keylayout.Oriya-QWERTY': 'or',
    'com.apple.keylayout.Osage-QWERTY': 'osa',
    'com.apple.keylayout.Pahawh-Hmong': 'hmn-Hmng',
    'com.apple.keylayout.Persian': 'fa',
    'com.apple.keylayout.Persian-ISIRI2901': 'fa',
    'com.apple.keylayout.Persian-QWERTY': 'fa',
    'com.apple.keylayout.PiteSami': 'sje',
    'com.apple.keylayout.Polish': 'pl',
    'com.apple.keylayout.PolishPro': 'pl',
    'com.apple.keylayout.Portuguese': 'pt',
    'com.apple.keylayout.Rejang-QWERTY': 'rej-Rjng',
    'com.apple.keylayout.Romanian': 'ro',
    'com.apple.keylayout.Romanian-Standard': 'ro',
    'com.apple.keylayout.Russian': 'ru',
    'com.apple.keylayout.Russian-Phonetic': 'ru',
    'com.apple.keylayout.RussianWin': 'ru',
    'com.apple.keylayout.Sami-PC': 'se',
    'com.apple.keylayout.Samoan': 'sm',
    'com.apple.keylayout.Sanskrit': 'sa',
    'com.apple.keylayout.Santali-Devanagari': 'sat-Deva',
    'com.apple.keylayout.Santali-OlChiki': 'sat-Olck',
    'com.apple.keylayout.Serbian': 'sr',
    'com.apple.keylayout.Serbian-Latin': 'sr-Latn',
    'com.apple.keylayout.Sindhi': 'sd',
    'com.apple.keylayout.Sindhi-Devanagari': 'sd-Deva',
    'com.apple.keylayout.Sinhala': 'si',
    'com.apple.keylayout.Sinhala-QWERTY': 'si',
    'com.apple.keylayout.SkoltSami': 'sms',
    'com.apple.keylayout.Slovak': 'sk',
    'com.apple.keylayout.Slovak-QWERTY': 'sk',
    'com.apple.keylayout.Slovenian': 'sl',
    'com.apple.keylayout.SouthernSami': 'sma',
    'com.apple.keylayout.Spanish': 'es',
    'com.apple.keylayout.Spanish-ISO': 'es',
    'com.apple.keylayout.Swedish': 'sv',
    'com.apple.keylayout.Swedish-Pro': 'sv',
    'com.apple.keylayout.SwedishSami-PC': 'sv',
    'com.apple.keylayout.SwissFrench': 'fr',
    'com.apple.keylayout.SwissGerman': 'de',
    'com.apple.keylayout.Syriac-Arabic': 'syr',
    'com.apple.keylayout.Syriac-QWERTY': 'syr',
    'com.apple.keylayout.Tajik-Cyrillic': 'tg',
    'com.apple.keylayout.Tamil99': 'ta',
    'com.apple.keylayout.Telugu': 'te',
    'com.apple.keylayout.Telugu-QWERTY': 'te',
    'com.apple.keylayout.Thai': 'th',
    'com.apple.keylayout.Thai-PattaChote': 'th',
    'com.apple.keylayout.Tibetan-QWERTY': 'bo',
    'com.apple.keylayout.Tibetan-Wylie': 'bo',
    'com.apple.keylayout.TibetanOtaniUS': 'bo',
    'com.apple.keylayout.Tifinagh-AZERTY': 'zgh-Tfng',
    'com.apple.keylayout.Tongan': 'to',
    'com.apple.keylayout.Transliteration-bn': 'bn-Latn',
    'com.apple.keylayout.Transliteration-gu': 'gu-Latn',
    'com.apple.keylayout.Transliteration-hi': 'hi-Latn',
    'com.apple.keylayout.Transliteration-kn': 'kn-Latn',
    'com.apple.keylayout.Transliteration-ml': 'ml-Latn',
    'com.apple.keylayout.Transliteration-mr': 'mr-Latn',
    'com.apple.keylayout.Transliteration-pa': 'pa-Latn',
    'com.apple.keylayout.Transliteration-ta': 'ta-Latn',
    'com.apple.keylayout.Transliteration-te': 'te-Latn',
    'com.apple.keylayout.Transliteration-ur': 'ur-Latn',
    'com.apple.keylayout.Turkish': 'tr',
    'com.apple.keylayout.Turkish-QWERTY': 'tr',
    'com.apple.keylayout.Turkish-QWERTY-PC': 'tr',
    'com.apple.keylayout.Turkish-Standard': 'tr',
    'com.apple.keylayout.Turkmen': 'tk',
    'com.apple.keylayout.US': 'en',
    'com.apple.keylayout.USExtended': 'en',
    'com.apple.keylayout.USInternational-PC': 'en',
    'com.apple.keylayout.Ukrainian': 'uk',
    'com.apple.keylayout.Ukrainian-PC': 'uk',
    'com.apple.keylayout.Ukrainian-QWERTY': 'uk',
    'com.apple.keylayout.UmeSami': 'sju',
    'com.apple.keylayout.UnicodeHexInput': 'af',
    'com.apple.keylayout.Urdu': 'ur',
    'com.apple.keylayout.Uyghur': 'ug',
    'com.apple.keylayout.Uzbek-Cyrillic': 'uz-Cyrl',
    'com.apple.keylayout.Vietnamese': 'vi',
    'com.apple.keylayout.Wancho-QWERTY': 'nnp-Wcho',
    'com.apple.keylayout.Welsh': 'cy',
    'com.apple.keylayout.Wolastoqey': 'pqm',
    'com.apple.keylayout.Yiddish-QWERTY': 'yi',
    'com.apple.keylayout.Yoruba': 'yo',
    'com.apple.keylayout.ZhuyinBopomofo': 'zh-Hant',
}


# ISO 639 language -> ISO 3166 country (flag). Canonical/most-common country for
# languages that span several. Minority/indigenous/script-only codes with no
# clear single country are omitted on purpose (better no flag than a wrong one).
_LANGUAGE_TO_COUNTRY = {
    'af': 'ZA', 'ak': 'GH', 'am': 'ET', 'ar': 'SA', 'as': 'IN', 'az': 'AZ',
    'be': 'BY', 'bg': 'BG', 'bn': 'BD', 'bo': 'CN', 'brx': 'IN', 'cs': 'CZ',
    'cy': 'GB', 'da': 'DK', 'de': 'DE', 'doi': 'IN', 'dv': 'MV', 'dz': 'BT',
    'el': 'GR', 'en': 'US', 'es': 'ES', 'et': 'EE', 'fa': 'IR', 'fi': 'FI',
    'fo': 'FO', 'fr': 'FR', 'ga': 'IE', 'gu': 'IN', 'ha': 'NG', 'haw': 'US',
    'he': 'IL', 'hi': 'IN', 'hr': 'HR', 'hu': 'HU', 'hy': 'AM', 'ig': 'NG',
    'is': 'IS', 'it': 'IT', 'ja': 'JP', 'ka': 'GE', 'kk': 'KZ', 'km': 'KH',
    'kn': 'IN', 'ko': 'KR', 'ks': 'IN', 'ku': 'TR', 'ky': 'KG', 'lo': 'LA',
    'lt': 'LT', 'lv': 'LV', 'mai': 'IN', 'mi': 'NZ', 'mk': 'MK', 'ml': 'IN',
    'mn': 'MN', 'mni': 'IN', 'mr': 'IN', 'ms': 'MY', 'mt': 'MT', 'my': 'MM',
    'nb': 'NO', 'ne': 'NP', 'nl': 'NL', 'nn': 'NO', 'no': 'NO', 'or': 'IN',
    'pa': 'IN', 'pl': 'PL', 'ps': 'AF', 'pt': 'PT', 'ro': 'RO', 'ru': 'RU',
    'sa': 'IN', 'sd': 'PK', 'si': 'LK', 'sk': 'SK', 'sl': 'SI', 'sq': 'AL',
    'sr': 'RS', 'sv': 'SE', 'syr': 'IQ', 'ta': 'IN', 'te': 'IN', 'tg': 'TJ',
    'th': 'TH', 'tk': 'TM', 'to': 'TO', 'tr': 'TR', 'ug': 'CN', 'uk': 'UA',
    'ur': 'PK', 'uz': 'UZ', 'vi': 'VN', 'yi': 'IL', 'yo': 'NG', 'zh': 'CN',
}


def tis_languages_for(source_id: str, name: str) -> 'list[str] | None':
    """Primary language (as a 1-element list) for a layout, by source id, or None.

    Returns a list for API symmetry with the live TIS path (which is also a list,
    just longer). Only the primary is stored/returned.
    """

    if source_id and source_id in _TIS_PRIMARY_LANGUAGE:
        return [_TIS_PRIMARY_LANGUAGE[source_id]]
    return None


def country_for_language(iso639: str) -> 'str | None':
    """ISO 3166 country for an ISO 639 language, or None if not mapped."""

    return _LANGUAGE_TO_COUNTRY.get(iso639)


# ISO 639 language -> the xkeyboard-config BASE LAYOUT NAME to register our
# variant under. Usually the language code, but several differ: Greek's base is
# 'gr' (not 'el'), Ukrainian's is 'ua' (not 'uk'). Languages with no system base
# layout (Vietnamese and every minority/indigenous script) are absent and fall
# back to 'us' so the layout is still selectable in the desktop picker.
#
# WHY register under a base at all: KDE's keyboard UI collapses a top-level
# custom layout to its language and then tries to load symbols/<language>, which
# does not exist (e.g. it looks for 'polish', the real file is 'pl'). Registering
# our variant under the REAL base layout ('pl') makes KDE find the base's
# symbols, geometry, and flag, and lets the variant be selected. The variant name
# is namespaced ('mac-k2x-...') so it can never collide with a system variant
# (e.g. 'de' already ships a 'mac' variant).
_LANGUAGE_TO_BASE_LAYOUT = {
    'ar': 'ara', 'hy': 'am', 'bn': 'in', 'bg': 'bg', 'hr': 'hr', 'cs': 'cz',
    'da': 'dk', 'nl': 'nl', 'en': 'us', 'et': 'ee', 'fi': 'fi', 'fr': 'fr',
    'ka': 'ge', 'de': 'de', 'el': 'gr', 'he': 'il', 'hi': 'in', 'hu': 'hu',
    'is': 'is', 'it': 'it', 'ja': 'jp', 'ko': 'kr', 'lv': 'lv', 'lt': 'lt',
    'mk': 'mk', 'no': 'no', 'nb': 'no', 'nn': 'no', 'pl': 'pl', 'pt': 'pt',
    'ro': 'ro', 'ru': 'ru', 'sr': 'rs', 'sk': 'sk', 'sl': 'si', 'es': 'es',
    'sv': 'se', 'th': 'th', 'tr': 'tr', 'uk': 'ua', 'be': 'by', 'kk': 'kz',
    'mn': 'mn', 'fa': 'ir', 'ur': 'pk', 'ug': 'cn', 'tk': 'tm',
    # Entries below were derived by cross-referencing every TIS primary
    # language against xkeyboard-config 2.47's registry (evdev.xml), scanning
    # languageList declarations at BOTH layout and variant level with all ISO
    # 639-1/2B/2T/3 code forms. Multi-home languages picked the native or
    # dominant-population base. Keys are BARE primary subtags (the resolver
    # strips '-Latn'/'-Cyrl' script tags via _bare()).
    'ak': 'gh', 'am': 'et', 'as': 'in', 'az': 'az', 'bo': 'cn', 'cv': 'ru',
    'dv': 'mv', 'dz': 'bt', 'ff': 'gh', 'fo': 'fo', 'ga': 'ie', 'gu': 'in',
    'ha': 'ng', 'ig': 'ng', 'iu': 'ca', 'km': 'kh', 'kn': 'in', 'ku': 'tr',
    'ky': 'kg', 'lo': 'la', 'mi': 'nz', 'ml': 'in', 'mr': 'in', 'ms': 'my',
    'mt': 'mt', 'my': 'mm', 'ne': 'np', 'or': 'in', 'pa': 'in', 'ps': 'af',
    'sa': 'in', 'sd': 'pk', 'se': 'no', 'si': 'lk', 'sq': 'al', 'ta': 'in',
    'te': 'in', 'tg': 'tj', 'uz': 'uz', 'vi': 'vn', 'yo': 'ng', 'zh': 'cn',
    'brx': 'in', 'chr': 'us', 'doi': 'in', 'haw': 'us', 'kab': 'dz',
    'kok': 'in', 'mai': 'in', 'mni': 'in', 'nqo': 'gn', 'sat': 'in',
    'syr': 'sy',
}


# Languages verified (against the same registry scan) to have NO
# xkeyboard-config home at any level: the 'us' fallback is the CORRECT and
# deliberate result for these, so generation must not warn about them. If a
# future xkeyboard-config release adds a home, the registry-consistency probe
# (tests/probes/probe_base_map_vs_registry.py) flags the entry for promotion
# into _LANGUAGE_TO_BASE_LAYOUT.
_KNOWN_BASELESS_LANGUAGES = frozenset((
    'af', 'apw', 'cho', 'cic', 'ckb', 'cy', 'hmn', 'inh', 'ks', 'mic',
    'mid', 'nnp', 'nv', 'osa', 'pqm', 'rej', 'rhg', 'sjd', 'sje', 'sju',
    'sm', 'sma', 'smj', 'smn', 'sms', 'to', 'yi', 'zgh',
))


def base_layout_is_known(iso639: str) -> bool:
    """True when the language resolves deliberately: mapped, baseless, or en.

    Generation warns loudly for any language outside all three cases -- a new
    Apple layout whose language nobody has classified yet -- so a fallthrough
    to 'us' can never again happen silently.
    """

    return (iso639 in _LANGUAGE_TO_BASE_LAYOUT
            or iso639 in _KNOWN_BASELESS_LANGUAGES
            or iso639 == 'en')


def base_layout_for_language(iso639: str, symbols_dir=None) -> str:
    """Return the xkeyboard-config base layout name to register a variant under.

    Resolves purely from the language->base map (data, not filesystem). This is
    deliberately NOT gated on the base file existing, because the installer is
    generated on macOS -- where /usr/share/X11/xkb/symbols does not exist -- but
    RUNS on Linux. An earlier version checked os.path.isfile() against the system
    XKB dir at generation time; on the Mac that check failed for EVERY language,
    so every layout silently fell back to 'us'. The map only ever contains real
    xkeyboard-config base names, and the install-time validate-and-rollback on the
    target machine catches the rare case where a base is genuinely absent.

    Falls back to 'us' only when the language has no mapping at all (e.g. an
    indigenous/minority language with no system base layout). symbols_dir is
    accepted for backward compatibility but ignored.
    """

    return _LANGUAGE_TO_BASE_LAYOUT.get(iso639, 'us')


# End of file #
