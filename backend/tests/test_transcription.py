"""The AWS side of transcription: the job's manifest, and reading transcripts back."""

from __future__ import annotations

import json
import unittest
from typing import Any

from hearally.models import ValidationError
from hearally.transcription import (
    MANIFEST_LINK_SECONDS,
    RECORDING_LINK_SECONDS,
    Recording,
    parse_transcript,
    prepare_batch,
    transcript_key,
)

ACCOUNT = "3f1c2a90-1111-4222-8333-944455556666"
EVENT = "a" * 64


class FakeS3:
    def __init__(self) -> None:
        self.signed: list[dict[str, Any]] = []
        self.objects: dict[str, dict[str, Any]] = {}

    def generate_presigned_url(self, method: str, Params: dict[str, Any], ExpiresIn: int) -> str:
        self.signed.append({"method": method, **Params, "expires": ExpiresIn})
        return f"https://bucket.s3.amazonaws.com/{Params['Key']}?method={method}&sig=x"

    def put_object(self, **kwargs: Any) -> None:
        self.objects[kwargs["Key"]] = kwargs


class BatchTests(unittest.TestCase):
    def test_the_manifest_gives_the_job_one_link_per_object_and_nothing_else(self) -> None:
        s3 = FakeS3()
        batch = prepare_batch(s3, "bucket", "job-1", [Recording(ACCOUNT, EVENT)])

        manifest = json.loads(s3.objects["jobs/job-1/manifest.json"]["Body"])
        [entry] = manifest["recordings"]
        self.assertEqual(entry["id"], EVENT)
        self.assertIn(f"incoming/{ACCOUNT}/{EVENT}", entry["audio_url"])
        self.assertIn(f"transcripts/{ACCOUNT}/{EVENT}.json", entry["result_url"])
        self.assertIn("jobs/job-1/report.json", manifest["report_url"])
        self.assertEqual(batch.transcript_keys[EVENT], transcript_key(ACCOUNT, EVENT))

        by_key = {s["Key"]: s for s in s3.signed}
        self.assertEqual(by_key[f"incoming/{ACCOUNT}/{EVENT}"]["method"], "get_object")
        upload = by_key[f"transcripts/{ACCOUNT}/{EVENT}.json"]
        self.assertEqual(
            (upload["method"], upload["ContentType"]), ("put_object", "application/json")
        )
        self.assertEqual(upload["expires"], RECORDING_LINK_SECONDS)
        self.assertEqual(by_key["jobs/job-1/manifest.json"]["expires"], MANIFEST_LINK_SECONDS)

    def test_an_empty_batch_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            prepare_batch(FakeS3(), "bucket", "job-1", [])


def transcript(**overrides: Any) -> bytes:
    data: dict[str, Any] = {
        "version": 1,
        "id": EVENT,
        "model": "nvidia/parakeet-tdt-0.6b-v3",
        "duration_s": 12.0,
        "confidence_available": True,
        "segments": [
            {
                "id": 0,
                "start": 0.5,
                "end": 1.6,
                "text": "Hello Grandma.",
                "confidence": 0.93,
                "words": [],
            },
            {
                "id": 1,
                "start": 3.0,
                "end": 3.9,
                "text": "Send money.",
                "confidence": 0.61,
                "words": [],
            },
            {"id": 2, "start": 4.0, "end": 4.2, "text": "  ", "confidence": 0.5, "words": []},
        ],
    }
    data.update(overrides)
    return json.dumps(data).encode()


class ParseTests(unittest.TestCase):
    def test_segments_become_judgement_segments(self) -> None:
        segments, facts = parse_transcript(transcript(), expected_id=EVENT)
        self.assertEqual(
            [(s.id, s.speaker, s.confidence) for s in segments],
            [(0, "unknown", 0.93), (1, "unknown", 0.61)],
        )
        self.assertEqual(facts["segments"], 2, "blank segments are dropped")
        self.assertTrue(facts["confidence_available"])

    def test_plain_words_carry_the_score_not_punctuation_or_capitals(self) -> None:
        words = [
            {"w": "To", "s": 0.0, "e": 0.1, "c": 0.33},
            {"w": "clear", "s": 0.1, "e": 0.2, "c": 0.82},
            {"w": "I'm", "s": 0.2, "e": 0.3, "c": 0.9},
            {"w": "today,", "s": 0.3, "e": 0.4, "c": 0.24},
            {"w": "gift", "s": 0.4, "e": 0.5, "c": 0.93},
            {"w": "cards.", "s": 0.5, "e": 0.6, "c": 0.18},
        ]
        segment = {"id": 0, "start": 0, "end": 1, "text": "x", "confidence": 0.5, "words": words}
        [parsed], _ = parse_transcript(transcript(segments=[segment]), expected_id=EVENT)
        self.assertEqual(parsed.word_scores, (0.82, 0.9, 0.93))

    def test_segments_without_word_scores_keep_the_segment_score(self) -> None:
        segments, _ = parse_transcript(transcript(), expected_id=EVENT)
        self.assertIsNone(segments[0].word_scores)
        broken = {"id": 0, "start": 0, "end": 1, "text": "x", "confidence": 0.5}
        broken["words"] = [{"w": "hi", "c": "high"}]  # type: ignore[assignment]
        [parsed], _ = parse_transcript(transcript(segments=[broken]), expected_id=EVENT)
        self.assertIsNone(parsed.word_scores)

    def test_unmeasured_confidence_can_never_escalate(self) -> None:
        body = transcript(
            confidence_available=False,
            segments=[
                {"id": 0, "start": 0, "end": 1, "text": "Buy gift cards.", "confidence": None}
            ],
        )
        segments, facts = parse_transcript(body, expected_id=EVENT)
        self.assertEqual(segments[0].confidence, 0.0)
        self.assertEqual(facts["unmeasured_segments"], 1)

    def test_a_transcript_for_another_recording_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            parse_transcript(transcript(id="b" * 64), expected_id=EVENT)

    def test_malformed_transcripts_are_refused_not_repaired(self) -> None:
        for body in (
            b"not json",
            transcript(version=2),
            transcript(segments="nope"),
            transcript(
                segments=[{"id": "0", "start": 0, "end": 1, "text": "x", "confidence": 0.9}]
            ),
            transcript(
                segments=[{"id": 0, "start": "0", "end": 1, "text": "x", "confidence": 0.9}]
            ),
            transcript(segments=[{"id": 0, "start": 0, "end": 1, "text": "x", "confidence": True}]),
        ):
            with self.subTest(body=body[:60]), self.assertRaises(ValidationError):
                parse_transcript(body, expected_id=EVENT)

    def test_confidence_is_clamped_to_zero_to_one(self) -> None:
        body = transcript(
            segments=[{"id": 0, "start": 0, "end": 1, "text": "x", "confidence": 1.7}]
        )
        self.assertEqual(parse_transcript(body, expected_id=EVENT)[0][0].confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
