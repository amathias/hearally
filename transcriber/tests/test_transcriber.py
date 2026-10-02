"""Tests for the transcription job's pure logic. No NeMo, no GPU, no network."""

from __future__ import annotations

import unittest

from hearally_transcriber.chunks import parse_silences, plan_chunks
from hearally_transcriber.main import ManifestError, safe_detail, validate_manifest
from hearally_transcriber.result import Piece, assemble, summary

S3 = "https://hearally-prod-media.s3.amazonaws.com/incoming/a/b?X-Amz-Signature=abc"


class ChunkPlanTests(unittest.TestCase):
    def test_a_short_recording_is_one_piece(self) -> None:
        self.assertEqual(plan_chunks(300.0, []), [(0.0, 300.0)])
        self.assertEqual(
            plan_chunks(650.0, []),
            [(0.0, 650.0)],
            "within the window, not worth cutting",
        )

    def test_cuts_move_to_the_nearest_silence_near_each_mark(self) -> None:
        silences = [(580.0, 581.0), (615.0, 616.0), (1190.0, 1192.0)]
        pieces = plan_chunks(1500.0, silences)
        self.assertEqual([round(a, 1) for a, _ in pieces], [0.0, 615.5, 1191.0])
        self.assertEqual(pieces[-1][1], 1500.0)

    def test_without_silence_the_cut_falls_on_the_mark(self) -> None:
        pieces = plan_chunks(1300.0, [])
        self.assertEqual(pieces, [(0.0, 600.0), (600.0, 1200.0), (1200.0, 1300.0)])

    def test_pieces_cover_everything_in_order_with_no_gaps(self) -> None:
        # The real test file: 4 h 42 min, with silences scattered through it.
        duration = 4 * 3600 + 42 * 60
        silences = [(t, t + 1.0) for t in range(30, int(duration), 97)]
        pieces = plan_chunks(duration, silences)
        self.assertEqual(pieces[0][0], 0.0)
        self.assertEqual(pieces[-1][1], duration)
        for (a, b), (c, _) in zip(pieces, pieces[1:], strict=False):
            self.assertEqual(b, c)
            self.assertLessEqual(b - a, 660.0 + 1e-6)
        self.assertEqual(len(pieces), 29)

    def test_a_tiny_last_piece_is_merged(self) -> None:
        pieces = plan_chunks(1215.0, [], target=600.0, window=10.0, min_tail=30.0)
        self.assertEqual(pieces, [(0.0, 600.0), (600.0, 1215.0)])

    def test_silences_are_read_from_ffmpeg_output(self) -> None:
        log = (
            "[silencedetect @ 0x1] silence_start: -0.02\n"
            "[silencedetect @ 0x1] silence_end: 1.5 | silence_duration: 1.52\n"
            "size=N/A time=00:10:00.00\n"
            "[silencedetect @ 0x1] silence_start: 612.25\n"
            "[silencedetect @ 0x1] silence_end: 613.75 | silence_duration: 1.5\n"
            "[silencedetect @ 0x1] silence_start: 900.0\n"
        )
        self.assertEqual(parse_silences(log), [(0.0, 1.5), (612.25, 613.75)])


def piece(offset: float, conf: list[float] | None = None) -> Piece:
    return Piece(
        offset=offset,
        words=[
            {"word": "Hello", "start": 0.5, "end": 0.9},
            {"word": "Grandma.", "start": 1.0, "end": 1.6},
            {"word": "Send", "start": 3.0, "end": 3.3},
            {"word": "money.", "start": 3.4, "end": 3.9},
        ],
        segments=[
            {"segment": "Hello Grandma.", "start": 0.5, "end": 1.6},
            {"segment": "Send money.", "start": 3.0, "end": 3.9},
        ],
        word_confidence=conf,
    )


class AssembleTests(unittest.TestCase):
    def test_pieces_are_shifted_joined_and_numbered(self) -> None:
        t = assemble(
            "rec",
            "m",
            700.0,
            [piece(600.0, [0.9, 0.8, 0.95, 0.5]), piece(0.0, [1, 1, 1, 1])],
        )
        body = t.to_json()
        self.assertEqual([s["id"] for s in body["segments"]], [0, 1, 2, 3])
        self.assertEqual(body["segments"][2]["start"], 600.5, "second piece shifted by its offset")
        self.assertEqual(
            body["segments"][3]["words"][1],
            {"w": "money.", "start": 603.4, "end": 603.9, "c": 0.5},
        )
        self.assertEqual(body["segments"][3]["confidence"], 0.725)
        self.assertEqual(body["segments"][3]["min_confidence"], 0.5)
        self.assertTrue(body["confidence_available"])
        self.assertEqual(body["version"], 1)

    def test_missing_or_mismatched_confidence_is_reported_not_invented(self) -> None:
        for conf in (None, [0.9, 0.9]):
            with self.subTest(conf=conf):
                body = assemble("rec", "m", 10.0, [piece(0.0, conf)]).to_json()
                self.assertFalse(body["confidence_available"])
                self.assertIsNone(body["segments"][0]["confidence"])
                self.assertIsNone(body["segments"][0]["words"][0]["c"])

    def test_the_summary_holds_counts_and_never_words(self) -> None:
        t = assemble("rec", "m", 700.0, [piece(0.0, [0.9, 0.8, 0.95, 0.5])])
        s = summary(t)
        self.assertEqual((s["segments"], s["words"], s["low_confidence_segments"]), (2, 4, 1))
        self.assertNotIn("Grandma", repr(s))


class ManifestTests(unittest.TestCase):
    def manifest(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "version": 1,
            "job": "j1",
            "report_url": S3,
            "recordings": [{"id": "e1", "audio_url": S3, "result_url": S3}],
        }
        data.update(overrides)
        return data

    def test_a_valid_manifest_passes(self) -> None:
        self.assertEqual(validate_manifest(self.manifest())["job"], "j1")

    def test_the_job_only_talks_to_s3_over_https(self) -> None:
        for bad in (
            "http://hearally-prod-media.s3.amazonaws.com/x",
            "https://evil.example.com/x",
            "https://amazonaws.com.evil.example/x",
            "file:///etc/passwd",
            None,
        ):
            with self.subTest(url=bad), self.assertRaises(ManifestError):
                validate_manifest(
                    self.manifest(recordings=[{"id": "e1", "audio_url": bad, "result_url": S3}])
                )
        with self.assertRaises(ManifestError):
            validate_manifest(self.manifest(report_url="https://evil.example.com/r"))

    def test_malformed_manifests_are_refused(self) -> None:
        for bad in (
            self.manifest(version=2),
            self.manifest(recordings=[]),
            self.manifest(recordings=[{"id": ""}]),
            [],
        ):
            with self.subTest(bad=bad), self.assertRaises(ManifestError):
                validate_manifest(bad)

    def test_error_details_never_carry_a_signed_link(self) -> None:
        detail = safe_detail(f"HTTP Error 403 for {S3} while downloading")
        self.assertNotIn("Signature", detail)
        self.assertIn("[link]", detail)


if __name__ == "__main__":
    unittest.main()


def said(*items: tuple[str, float, float]) -> list[dict[str, object]]:
    return [{"w": w, "start": s, "end": e} for w, s, e in items]


class RedactTests(unittest.TestCase):
    def test_a_card_number_read_as_digits_is_silenced_with_a_margin(self) -> None:
        from hearally_transcriber.redact import number_spans

        words = said(("It", 0, 0.2), ("is", 0.2, 0.4), ("4532", 0.5, 1.2), ("8891.", 1.3, 2.0))
        self.assertEqual(number_spans(words), [(0.35, 2.15)])

    def test_spoken_digits_count_and_joiners_do_not_break_the_run(self) -> None:
        from hearally_transcriber.redact import number_spans

        words = said(
            ("four", 1.0, 1.2), ("five", 1.2, 1.4), ("dash", 1.4, 1.6),
            ("three", 1.6, 1.8), ("two,", 1.8, 2.0), ("thanks", 2.1, 2.4),
        )  # fmt: skip
        self.assertEqual(number_spans(words), [(0.85, 2.15)])

    def test_times_and_small_numbers_are_left_alone(self) -> None:
        from hearally_transcriber.redact import number_spans

        for words in (
            said(("Thursday", 0, 0.5), ("at", 0.5, 0.6), ("2", 0.6, 0.8)),
            said(("the", 0, 0.1), ("3", 0.1, 0.2), ("digits", 0.2, 0.5)),
            said(("I", 0, 0.1), ("owe", 0.1, 0.3), ("twenty", 0.3, 0.6), ("dollars", 0.6, 1)),
        ):
            with self.subTest(words=[w["w"] for w in words]):
                self.assertEqual(number_spans(words), [])

    def test_the_filter_silences_each_span_or_passes_audio_through(self) -> None:
        from hearally_transcriber.redact import silence_filter

        self.assertEqual(silence_filter([]), "anull")
        self.assertEqual(
            silence_filter([(0.35, 2.15), (5.0, 6.5)]),
            "volume=0:enable='between(t,0.350,2.150)+between(t,5.000,6.500)'",
        )
