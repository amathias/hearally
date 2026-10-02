"""Silence account and card numbers in the audio itself.

AGENTS.md: redaction happens in the audio, after transcription, with numbers
located by word timestamp. A run of number words worth at least four digits,
read out as digits ("4532 8891") or spoken ("four five three two"), is
silenced from just before its first word to just after its last. Times and
small numbers ("at 2", "the 3 digits") are left alone: they carry meaning and
identify nothing.

Pure functions, tested without NeMo. main.py runs the ffmpeg filter this
builds and uploads the silenced copy; the original is deleted by the backend
as soon as the copy exists.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MIN_DIGITS = 4
PAD_SECONDS = 0.15

_SPOKEN = {
    "zero": 1, "oh": 1, "o": 1, "one": 1, "two": 1, "three": 1, "four": 1, "five": 1,
    "six": 1, "seven": 1, "eight": 1, "nine": 1, "ten": 2, "eleven": 2, "twelve": 2,
    "thirteen": 2, "fourteen": 2, "fifteen": 2, "sixteen": 2, "seventeen": 2,
    "eighteen": 2, "nineteen": 2, "twenty": 2, "thirty": 2, "forty": 2, "fifty": 2,
    "sixty": 2, "seventy": 2, "eighty": 2, "ninety": 2, "hundred": 2, "thousand": 3,
    "double": 0, "triple": 0,
}  # fmt: skip
# Words that may sit inside a number read aloud without ending it.
_JOINERS = {"dash", "-", "and"}
_STRIP = re.compile(r"^[^\w$]+|[^\w]+$")


def _digits(word: str) -> int | None:
    """How many digits a word stands for, or None if it is not part of a number."""
    token = _STRIP.sub("", word.lower())
    if not token:
        return None
    if any(c.isdigit() for c in token):
        return sum(c.isdigit() for c in token)
    for part in token.split("-"):
        if part not in _SPOKEN:
            return None
    return sum(_SPOKEN[p] for p in token.split("-"))


def number_spans(
    words: Sequence[Mapping[str, Any]], *, min_digits: int = MIN_DIGITS, pad: float = PAD_SECONDS
) -> list[tuple[float, float]]:
    """(start, end) seconds to silence, merged and padded."""
    spans: list[tuple[float, float]] = []
    run_start: float | None = None
    run_end = 0.0
    digits = 0

    def close() -> None:
        if run_start is not None and digits >= min_digits:
            spans.append((max(0.0, run_start - pad), run_end + pad))

    for word in words:
        text = str(word.get("w", ""))
        count = _digits(text)
        if count is not None:
            if run_start is None:
                run_start, digits = float(word["start"]), 0
            run_end = float(word["end"])
            digits += count
        elif run_start is not None and _STRIP.sub("", text.lower()) in _JOINERS:
            continue
        else:
            close()
            run_start, digits = None, 0
    close()
    return _merge(spans)


def _merge(spans: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def silence_filter(spans: Sequence[tuple[float, float]]) -> str:
    """An ffmpeg audio filter that silences every span, or passes audio through."""
    if not spans:
        return "anull"
    windows = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in spans)
    return f"volume=0:enable='{windows}'"


def transcript_words(transcript: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every word of a transcript (contract v1), in order."""
    return [w for seg in transcript.get("segments", []) for w in seg.get("words", [])]
