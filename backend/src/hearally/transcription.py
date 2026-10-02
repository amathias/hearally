"""Transcription with Parakeet on Nebius AI Cloud: the AWS side.

The GPU job itself lives in transcriber/. It holds no AWS credentials. For each
batch, this module writes a manifest to S3 that lists every recording with two
short-lived signed links, one to download its audio and one to upload its
transcript, plus a link for the job's report. The job is started with a single
signed link to that manifest. See docs/contracts/transcript-v1.schema.json for
what comes back, and docs/ARCHITECTURE.md section 3.

Transcripts are raw text of what was said, so they live only under
transcripts/, are deleted as soon as the judgement has read them, and expire
after a day regardless (infra/modules/data). They are never logged.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from hearally.audio import incoming_key
from hearally.judgement import Segment
from hearally.models import ValidationError

TRANSCRIPT_VERSION = 1
MANIFEST_VERSION = 1
# Links must outlive the wait for a GPU, not just the job. Creating a job
# includes finding a machine and pulling the image, which took 20 to 35
# minutes on 2026-09-25, and a 30-minute manifest link expired before the job
# could read it (S3 answered 403). Each link still covers exactly one object.
MANIFEST_LINK_SECONDS = 3 * 60 * 60
RECORDING_LINK_SECONDS = 6 * 60 * 60


def transcript_key(account_id: str, event_id: str) -> str:
    return f"transcripts/{account_id}/{event_id}.json"


def redacted_key(account_id: str, event_id: str) -> str:
    """The copy Hearally keeps: numbers silenced in the audio by the job."""
    return f"redacted/{account_id}/{event_id}.ogg"


@dataclass(frozen=True, slots=True)
class Recording:
    account_id: str
    event_id: str
    # Where the audio is, when not under incoming/ (a manual test run).
    audio_key: str | None = None


@dataclass(frozen=True, slots=True)
class Batch:
    job: str
    manifest_key: str
    manifest_url: str
    report_key: str
    transcript_keys: dict[str, str]


def prepare_batch(s3: Any, bucket: str, job: str, recordings: Sequence[Recording]) -> Batch:
    """Write the manifest for one job and return the link to start it with."""
    if not recordings:
        raise ValueError("a batch needs at least one recording")
    report_key = f"jobs/{job}/report.json"
    manifest_key = f"jobs/{job}/manifest.json"

    def get(key: str) -> str:
        return str(
            s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=RECORDING_LINK_SECONDS,
            )
        )

    def put(key: str, content_type: str = "application/json") -> str:
        # The content type is part of the signature, so the job can only ever
        # upload that type to the one key it was given.
        return str(
            s3.generate_presigned_url(
                "put_object",
                Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
                ExpiresIn=RECORDING_LINK_SECONDS,
            )
        )

    keys = {r.event_id: transcript_key(r.account_id, r.event_id) for r in recordings}
    manifest = {
        "version": MANIFEST_VERSION,
        "job": job,
        "report_url": put(report_key),
        "recordings": [
            {
                "id": r.event_id,
                "audio_url": get(r.audio_key or incoming_key(r.account_id, r.event_id)),
                "result_url": put(keys[r.event_id]),
                "redacted_url": put(redacted_key(r.account_id, r.event_id), "audio/ogg"),
            }
            for r in recordings
        ],
    }
    s3.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=json.dumps(manifest).encode(),
        ContentType="application/json",
    )
    manifest_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": manifest_key},
        ExpiresIn=MANIFEST_LINK_SECONDS,
    )
    return Batch(
        job=job,
        manifest_key=manifest_key,
        manifest_url=str(manifest_url),
        report_key=report_key,
        transcript_keys=keys,
    )


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(f"transcript {name} must be a number")
    return float(value)


def _plain(word: str) -> bool:
    """A word whose score reflects the word itself. Parakeet adds punctuation
    and capitals as separate pieces, and a word scores as its weakest piece,
    so "office." or a sentence-start "And" scores the full stop or the capital.
    "I" and its contractions are always capitalised, so they count as plain."""
    if not word or word[-1] in ".,?!;:":
        return False
    return not word[0].isupper() or word == "I" or word.startswith("I'")


def _plain_word_scores(words: Any) -> tuple[float, ...] | None:
    """The plain words' scores, or None when the segment has no usable word
    scores (then its segment score is used instead)."""
    if not isinstance(words, list) or not words:
        return None
    scores = []
    for w in words:
        if not isinstance(w, dict) or not isinstance(w.get("w"), str):
            return None
        c = w.get("c")
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c):
            return None
        if _plain(w["w"]):
            scores.append(min(1.0, max(0.0, float(c))))
    return tuple(scores)


def parse_transcript(body: bytes, *, expected_id: str) -> tuple[list[Segment], dict[str, Any]]:
    """The transcript as judgement segments, plus facts about it for the log.

    The job's output is validated like any other untrusted input. A segment
    with no confidence is given 0.0: an unmeasured segment can inform the daily
    list but never, on its own, cause a text (judgement.MIN_CONFIDENCE_TO_ESCALATE).
    Parakeet does not tell speakers apart, so every segment's speaker is
    "unknown" until diarization is added.
    """
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("transcript is not valid JSON") from exc
    if not isinstance(data, dict) or data.get("version") != TRANSCRIPT_VERSION:
        raise ValidationError("transcript version not recognised")
    if data.get("id") != expected_id:
        raise ValidationError("transcript is for a different recording")
    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list):
        raise ValidationError("transcript segments must be a list")

    segments: list[Segment] = []
    unmeasured = 0
    for raw in raw_segments:
        if not isinstance(raw, dict):
            raise ValidationError("each transcript segment must be an object")
        text = raw.get("text")
        seg_id = raw.get("id")
        if not isinstance(text, str) or isinstance(seg_id, bool) or not isinstance(seg_id, int):
            raise ValidationError("each transcript segment needs an id and text")
        confidence = raw.get("confidence")
        if confidence is None:
            unmeasured += 1
            score = 0.0
        else:
            score = min(1.0, max(0.0, _number(confidence, "confidence")))
        _number(raw.get("start"), "start")
        _number(raw.get("end"), "end")
        if text.strip():
            segments.append(
                Segment(
                    id=seg_id,
                    speaker="unknown",
                    text=text,
                    confidence=score,
                    word_scores=_plain_word_scores(raw.get("words")),
                    start=float(raw["start"]),
                    end=float(raw["end"]),
                )
            )

    facts = {
        "segments": len(segments),
        "duration_s": _number(data.get("duration_s", 0), "duration"),
        "confidence_available": data.get("confidence_available") is True,
        "unmeasured_segments": unmeasured,
        "model": str(data.get("model", "")),
    }
    return segments, facts
