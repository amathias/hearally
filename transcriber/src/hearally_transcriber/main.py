"""The transcription job: runs on a Nebius Serverless AI GPU, once per batch.

Input is one environment variable, HEARALLY_MANIFEST_URL: a short-lived signed
link to a manifest in Hearally's S3 bucket. The manifest lists the recordings,
each with a signed link to download its audio and a signed link to upload its
transcript, plus a signed link for the job's report. The job holds no AWS
credentials and can reach nothing else: every link must be HTTPS on S3.

For each recording: download, convert to 16 kHz mono, cut long recordings at
quiet moments (chunks.py), transcribe with Parakeet, join the pieces
(result.py), upload the transcript. The report says what happened in counts
and timings only. Nothing here prints or logs what was said.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from hearally_transcriber.chunks import parse_silences, plan_chunks
from hearally_transcriber.redact import number_spans, silence_filter, transcript_words
from hearally_transcriber.result import Piece, assemble, summary

MODEL = "nvidia/parakeet-tdt-0.6b-v3"
MANIFEST_VERSION = 1
BATCH_SIZE = 8
TIMEOUT = 300
# Exit code when the job failed but wrote its own report. Any other non-zero
# exit means it crashed, and bootstrap.sh reports that instead.
REPORTED_FAILURE = 3

_URL = re.compile(r"https?://\S+")


class ManifestError(ValueError):
    pass


def _allowed(url: Any) -> bool:
    """Only HTTPS links to S3: the job may fetch and send nothing else."""
    if not isinstance(url, str):
        return False
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (
        host == "s3.amazonaws.com" or host.endswith(".amazonaws.com")
    )


def validate_manifest(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        raise ManifestError("manifest version not recognised")
    if not _allowed(data.get("report_url")):
        raise ManifestError("report_url must be an HTTPS S3 link")
    recordings = data.get("recordings")
    if not isinstance(recordings, list) or not recordings:
        raise ManifestError("manifest lists no recordings")
    for r in recordings:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"]:
            raise ManifestError("each recording needs an id")
        if not (_allowed(r.get("audio_url")) and _allowed(r.get("result_url"))):
            raise ManifestError("audio_url and result_url must be HTTPS S3 links")
        if "redacted_url" in r and not _allowed(r["redacted_url"]):
            raise ManifestError("redacted_url must be an HTTPS S3 link")
    return data


def safe_detail(message: str) -> str:
    """An error message fit for the report: links removed, length capped."""
    return _URL.sub("[link]", message)[:300]


def _open(
    url: str,
    *,
    data: bytes | None = None,
    method: str = "GET",
    content_type: str = "application/json",
) -> Any:
    """Every network call goes through here, and only to an HTTPS S3 link."""
    if not _allowed(url):
        raise ManifestError("refusing a link that is not HTTPS on S3")
    headers = {"Content-Type": content_type} if data is not None else {}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
    return urllib.request.urlopen(request, timeout=TIMEOUT)  # noqa: S310 - checked above


def _get(url: str) -> bytes:
    with _open(url) as response:
        return bytes(response.read())


def _download(url: str, path: Path) -> None:
    with _open(url) as response, path.open("wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)


def _put_json(url: str, body: dict[str, Any]) -> None:
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    with _open(url, data=data, method="PUT"):
        pass


def _put_audio(url: str, path: Path) -> None:
    # The signed link names the content type, so only Ogg audio can land there.
    with _open(url, data=path.read_bytes(), method="PUT", content_type="audio/ogg"):
        pass


def _ffmpeg(*args: str) -> str:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({result.returncode})")
    return result.stderr


def _duration(wav: Path) -> float:
    import soundfile  # installed with NeMo

    info = soundfile.info(str(wav))
    return float(info.frames) / float(info.samplerate)


def _gpu_name() -> str:
    try:
        import torch

        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no GPU"
    except Exception as exc:  # pragma: no cover - reported, never fatal
        return f"unknown ({type(exc).__name__})"


def load_model() -> tuple[Any, str]:
    """Parakeet on the GPU, with word confidence switched on if the decoder allows."""
    import nemo.collections.asr as nemo_asr
    from omegaconf import open_dict

    model = nemo_asr.models.ASRModel.from_pretrained(model_name=MODEL)
    model.eval()
    try:
        decoding = model.cfg.decoding
        with open_dict(decoding):
            decoding.confidence_cfg = {
                "preserve_word_confidence": True,
                "exclude_blank": True,
                "aggregation": "min",
                "method_cfg": {
                    "name": "entropy",
                    "entropy_type": "tsallis",
                    "alpha": 0.5,
                    "entropy_norm": "exp",
                },
            }
        model.change_decoding_strategy(decoding)
        confidence = "word"
    except Exception as exc:
        confidence = f"unavailable ({type(exc).__name__})"
    return model, confidence


def transcribe_recording(model: Any, recording: dict[str, Any], workdir: Path) -> dict[str, Any]:
    source = workdir / "audio"
    wav = workdir / "audio.wav"
    _download(recording["audio_url"], source)
    _ffmpeg(
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav),
    )
    duration = _duration(wav)
    silences = parse_silences(
        _ffmpeg("-i", str(wav), "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-")
    )
    bounds = plan_chunks(duration, silences)
    paths = []
    for i, (start, end) in enumerate(bounds):
        piece = workdir / f"piece-{i:03d}.wav"
        _ffmpeg(
            "-y",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(wav),
            "-c",
            "copy",
            str(piece),
        )
        paths.append(str(piece))

    output = model.transcribe(paths, timestamps=True, return_hypotheses=True, batch_size=BATCH_SIZE)
    if isinstance(output, tuple):  # some versions return (best, all)
        output = output[0]
    pieces = [
        Piece(
            offset=start,
            words=hyp.timestamp.get("word", []),
            segments=hyp.timestamp.get("segment", []),
            word_confidence=[float(c) for c in hyp.word_confidence]
            if getattr(hyp, "word_confidence", None)
            else None,
        )
        for (start, _end), hyp in zip(bounds, output, strict=True)
    ]
    transcript = assemble(recording["id"], MODEL, duration, pieces)
    body = transcript.to_json()
    _put_json(recording["result_url"], body)
    result = {"pieces": len(bounds), **summary(transcript)}
    if recording.get("redacted_url"):
        # The copy Hearally keeps: numbers silenced in the audio itself. The
        # backend deletes the original once this exists (AGENTS.md).
        spans = number_spans(transcript_words(body))
        redacted = workdir / "redacted.ogg"
        _ffmpeg(
            "-y",
            "-i",
            str(wav),
            "-af",
            silence_filter(spans),
            "-c:a",
            "libopus",
            "-b:a",
            "24k",
            str(redacted),
        )
        _put_audio(recording["redacted_url"], redacted)
        result["redacted_spans"] = len(spans)
    return result


def report_failure(stage: str, log_path: str | None = None) -> None:
    """For bootstrap.sh: report that the job failed before it could start,
    with the end of the installer's output, links removed.

    Standard library only, since it runs when installing NeMo has failed.
    """
    manifest = validate_manifest(json.loads(_get(os.environ["HEARALLY_MANIFEST_URL"])))
    tail = ""
    if log_path and Path(log_path).exists():
        tail = _URL.sub("[link]", Path(log_path).read_text(encoding="utf-8", errors="replace"))
    _put_json(
        manifest["report_url"],
        {
            "version": 1,
            "job": manifest.get("job"),
            "status": "failed",
            "stage": stage[:40],
            "log_tail": tail[-3000:],
        },
    )


def main() -> int:
    started = time.time()
    manifest = validate_manifest(json.loads(_get(os.environ["HEARALLY_MANIFEST_URL"])))
    report: dict[str, Any] = {
        "version": 1,
        "job": manifest.get("job"),
        "model": MODEL,
        "gpu": _gpu_name(),
        "recordings": [],
    }
    load_started = time.time()
    try:
        model, confidence = load_model()
    except Exception as exc:
        # Without a model nothing can be transcribed; say why, then stop.
        report.update(status="failed", stage="load-model", error=type(exc).__name__)
        report["detail"] = safe_detail(str(exc))
        _put_json(manifest["report_url"], report)
        return REPORTED_FAILURE
    report["confidence"] = confidence
    report["model_load_s"] = round(time.time() - load_started, 1)

    failures = 0
    for recording in manifest["recordings"]:
        entry: dict[str, Any] = {"id": recording["id"]}
        began = time.time()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                entry.update(status="ok", **transcribe_recording(model, recording, Path(tmp)))
        except Exception as exc:
            failures += 1
            entry.update(status="failed", error=type(exc).__name__, detail=safe_detail(str(exc)))
        entry["seconds"] = round(time.time() - began, 1)
        report["recordings"].append(entry)

    report["total_s"] = round(time.time() - started, 1)
    _put_json(manifest["report_url"], report)
    # Counts only, for the job log in the Nebius console.
    print(
        json.dumps(
            {
                "job": report["job"],
                "recordings": len(report["recordings"]),
                "failed": failures,
            }
        )
    )
    return REPORTED_FAILURE if failures else 0


if __name__ == "__main__":
    sys.exit(main())
