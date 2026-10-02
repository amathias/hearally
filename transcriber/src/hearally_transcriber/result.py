"""From Parakeet's output to the transcript Hearally's backend reads.

The contract is docs/contracts/transcript-v1.schema.json. Each piece of a
recording is transcribed separately (see chunks.py); here its words and
segments are shifted by the piece's start and joined into one transcript.

Per word: the text, its start and end in seconds, and Parakeet's confidence
(0 to 1) when the decoder reports it. Per segment, roughly a sentence: its
words, and the mean and lowest confidence of them. The judgement refuses to
escalate on a low-confidence segment (backend judgement.py), and redaction
bleeps numbers by the word times, so both are kept.

Pure functions, tested without NeMo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

VERSION = 1
_EPSILON = 0.01


@dataclass(frozen=True)
class Piece:
    """What Parakeet returned for one piece, in the piece's own time."""

    offset: float
    words: Sequence[Mapping[str, Any]]
    segments: Sequence[Mapping[str, Any]]
    word_confidence: Sequence[float] | None = None


@dataclass
class Transcript:
    recording_id: str
    model: str
    duration: float
    segments: list[dict[str, Any]] = field(default_factory=list)
    confidence_available: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "id": self.recording_id,
            "model": self.model,
            "duration_s": round(self.duration, 3),
            "confidence_available": self.confidence_available,
            "segments": self.segments,
        }


def _seconds(value: Any) -> float:
    return round(float(value), 3)


def _words(piece: Piece) -> list[dict[str, Any]]:
    confidences = piece.word_confidence
    usable = confidences is not None and len(confidences) == len(piece.words)
    words = []
    for i, w in enumerate(piece.words):
        text = str(w.get("word", "")).strip()
        if not text:
            continue
        words.append(
            {
                "w": text,
                "start": _seconds(piece.offset + float(w["start"])),
                "end": _seconds(piece.offset + float(w["end"])),
                "c": round(float(confidences[i]), 4)
                if usable and confidences is not None
                else None,
            }
        )
    return words


def assemble(recording_id: str, model: str, duration: float, pieces: Sequence[Piece]) -> Transcript:
    """Join the pieces into one transcript, in time order, with segment ids from 0."""
    transcript = Transcript(recording_id=recording_id, model=model, duration=duration)
    all_have_confidence = bool(pieces)
    for piece in sorted(pieces, key=lambda p: p.offset):
        words = _words(piece)
        if any(w["c"] is None for w in words):
            all_have_confidence = False
        for seg in piece.segments:
            start = _seconds(piece.offset + float(seg["start"]))
            end = _seconds(piece.offset + float(seg["end"]))
            text = str(seg.get("segment", "")).strip()
            if not text:
                continue
            inside = [w for w in words if start - _EPSILON <= w["start"] <= end + _EPSILON]
            scores = [w["c"] for w in inside if w["c"] is not None]
            transcript.segments.append(
                {
                    "id": len(transcript.segments),
                    "start": start,
                    "end": end,
                    "text": text,
                    "confidence": round(sum(scores) / len(scores), 4) if scores else None,
                    "min_confidence": round(min(scores), 4) if scores else None,
                    "words": inside,
                }
            )
    transcript.confidence_available = all_have_confidence and bool(transcript.segments)
    return transcript


def summary(transcript: Transcript) -> dict[str, Any]:
    """What the job may report about a transcript: counts and timings only.

    Never the words. The report is read by people debugging the pipeline, and
    AGENTS.md forbids transcripts in logs.
    """
    words = sum(len(s["words"]) for s in transcript.segments)
    scores = [s["confidence"] for s in transcript.segments if s["confidence"] is not None]
    return {
        "duration_s": round(transcript.duration, 1),
        "segments": len(transcript.segments),
        "words": words,
        "confidence_available": transcript.confidence_available,
        "mean_confidence": round(sum(scores) / len(scores), 3) if scores else None,
        "low_confidence_segments": sum(1 for s in scores if s < 0.8),
    }
