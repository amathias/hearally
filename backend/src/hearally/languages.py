from __future__ import annotations


class UnsupportedLanguageError(ValueError):
    """Raised when a requested output language is outside the product catalog."""


SUPPORTED_LANGUAGES: tuple[tuple[str, str, str], ...] = (
    ("en", "English", "ltr"),
    ("es", "Spanish", "ltr"),
    ("fr", "French", "ltr"),
    ("de", "German", "ltr"),
    ("it", "Italian", "ltr"),
    ("pt", "Portuguese", "ltr"),
    ("nl", "Dutch", "ltr"),
    ("pl", "Polish", "ltr"),
    ("sv", "Swedish", "ltr"),
    ("no", "Norwegian", "ltr"),
    ("da", "Danish", "ltr"),
    ("fi", "Finnish", "ltr"),
    ("cs", "Czech", "ltr"),
    ("el", "Greek", "ltr"),
    ("tr", "Turkish", "ltr"),
    ("ru", "Russian", "ltr"),
    ("uk", "Ukrainian", "ltr"),
    ("ro", "Romanian", "ltr"),
    ("hu", "Hungarian", "ltr"),
    ("ar", "Arabic", "rtl"),
    ("he", "Hebrew", "rtl"),
    ("hi", "Hindi", "ltr"),
    ("bn", "Bengali", "ltr"),
    ("pa", "Punjabi", "ltr"),
    ("gu", "Gujarati", "ltr"),
    ("mr", "Marathi", "ltr"),
    ("ta", "Tamil", "ltr"),
    ("te", "Telugu", "ltr"),
    ("kn", "Kannada", "ltr"),
    ("ml", "Malayalam", "ltr"),
    ("th", "Thai", "ltr"),
    ("vi", "Vietnamese", "ltr"),
    ("id", "Indonesian", "ltr"),
    ("ms", "Malay", "ltr"),
    ("zh-Hans", "Chinese (Simplified)", "ltr"),
    ("zh-Hant", "Chinese (Traditional)", "ltr"),
    ("ja", "Japanese", "ltr"),
    ("ko", "Korean", "ltr"),
    ("fil", "Filipino", "ltr"),
    ("sw", "Swahili", "ltr"),
)

SUPPORTED_LANGUAGE_TAGS = tuple(language[0] for language in SUPPORTED_LANGUAGES)
_LANGUAGES_BY_LOWER_TAG = {language[0].lower(): language for language in SUPPORTED_LANGUAGES}


def normalize_output_language(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UnsupportedLanguageError("An output language is required")
    candidate = value.strip().replace("_", "-")
    match = _LANGUAGES_BY_LOWER_TAG.get(candidate.lower())
    if match:
        return match[0]

    # Region-specific preferences use their supported base language. Script-specific
    # Chinese remains explicit because Simplified and Traditional are not interchangeable.
    base = candidate.split("-", 1)[0].lower()
    match = _LANGUAGES_BY_LOWER_TAG.get(base)
    if match and base != "zh":
        return match[0]
    raise UnsupportedLanguageError(f"Unsupported output language: {value}")


def language_name(tag: str) -> str:
    normalized = normalize_output_language(tag)
    return _LANGUAGES_BY_LOWER_TAG[normalized.lower()][1]


def text_direction(tag: str) -> str:
    normalized = normalize_output_language(tag)
    return _LANGUAGES_BY_LOWER_TAG[normalized.lower()][2]
