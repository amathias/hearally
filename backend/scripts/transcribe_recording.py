"""Transcribe one received recording on Nebius, by hand, end to end.

    python backend/scripts/transcribe_recording.py EVENT_ID [--judge] [--scores]
    python backend/scripts/transcribe_recording.py --local FILE [--private-copy] ...

Finds the recording under incoming/, writes the job's manifest, launches the
Parakeet job on Nebius (transcriber/scripts/nebius_jobs.py), waits, and reads
back the report and the transcript. With --judge, runs the judgement (Nemotron
with the known scam scripts) on it.

Privacy: this prints counts, timings and decision grades only, never what was
said. With --judge, the full decisions are written to a private file under
~/.hearally/ (outside the repository) for the account holder to read. The
transcript is deleted from S3 once read. AWS credentials come from the default
profile; the Nebius key from HEARALLY_NEBIUS_KEY or its default path.

This is the manual precursor of the transcription worker, which will do the
same on a schedule.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "transcriber" / "scripts"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import boto3  # noqa: E402

from hearally.transcription import Recording, parse_transcript, prepare_batch  # noqa: E402

BUCKET = "hearally-prod-media"


def find_recording(s3: object, event_id: str) -> Recording:
    paginator = s3.get_paginator("list_objects_v2")  # type: ignore[attr-defined]
    for page in paginator.paginate(Bucket=BUCKET, Prefix="incoming/"):
        for obj in page.get("Contents", []):
            parts = obj["Key"].split("/")
            if len(parts) == 3 and parts[2] == event_id:
                return Recording(account_id=parts[1], event_id=event_id)
    raise SystemExit("No recording with that id under incoming/ (it may have expired).")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("event_id", nargs="?", help="a recording under incoming/")
    parser.add_argument(
        "--local",
        type=Path,
        help="transcribe this file instead: uploaded under jobs/ (one-day expiry), then deleted",
    )
    parser.add_argument(
        "--private-copy",
        action="store_true",
        help="keep the transcript in ~/.hearally/ (outside the repository), only for a "
        "recording made for testing",
    )
    parser.add_argument("--judge", action="store_true")
    parser.add_argument(
        "--built",
        action="store_true",
        help="run our built image from the registry, not the stock image with the code injected",
    )
    parser.add_argument(
        "--scores",
        action="store_true",
        help="print each segment's timing, word count and confidence (numbers, never words)",
    )
    parser.add_argument(
        "--on-demand",
        action="store_true",
        help="a regular GPU instead of a cheaper preemptible one that the platform may reclaim",
    )
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name="us-east-1")
    job = f"manual-{int(time.time())}"
    uploaded_key = None
    if args.local:
        # A test file from this computer. It goes under jobs/, which expires
        # after a day like incoming/, and is deleted at the end regardless.
        data = args.local.read_bytes()
        event_id = hashlib.sha256(data).hexdigest()
        uploaded_key = f"jobs/{job}/audio"
        s3.put_object(Bucket=BUCKET, Key=uploaded_key, Body=data)
        recording = Recording(account_id="local", event_id=event_id, audio_key=uploaded_key)
        print(f"uploaded {len(data) / 1e6:.1f} MB for this run")
    elif args.event_id:
        recording = find_recording(s3, args.event_id)
    else:
        parser.error("give an event id or --local FILE")
    batch = prepare_batch(s3, BUCKET, job, [recording])
    print(f"batch {job}: 1 recording")

    import nebius_jobs  # the operator tooling; needs `pip install nebius`

    async def launch() -> int:
        s = nebius_jobs.sdk()
        try:
            image = (
                f"{await nebius_jobs.registry_path(s)}/{nebius_jobs.IMAGE_NAME}:latest"
                if args.built
                else None
            )
            return await nebius_jobs.run(s, batch.manifest_url, image, on_demand=args.on_demand)
        finally:
            await s.close()

    status = asyncio.run(launch())

    try:
        report = json.loads(s3.get_object(Bucket=BUCKET, Key=batch.report_key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        print("No report: the job stopped before writing one. See its logs in the Nebius console.")
        return 1
    print("report:", json.dumps(report, indent=1))
    if status != 0 or any(r.get("status") != "ok" for r in report.get("recordings", [])):
        return 1

    key = batch.transcript_keys[recording.event_id]
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    segments, facts = parse_transcript(body, expected_id=recording.event_id)
    print("transcript:", facts)
    if args.scores:
        # For calibrating the confidence threshold. Numbers only: nothing said.
        for seg in json.loads(body)["segments"]:
            words = [w.get("c") for w in seg.get("words", [])]
            print(
                f"{seg['start']:8.1f}-{seg['end']:7.1f}s  {len(words):3d} words"
                f"  mean {seg.get('confidence')}  min {seg.get('min_confidence')}"
                f"  per word {[round(c, 2) for c in words if c is not None]}"
            )

    if args.judge:
        from embed_scam_library import nebius_key

        from hearally.judgement import NemotronClient, judge
        from hearally.scam_library import EmbeddingClient, ScamRetriever, load_library

        token_factory_key = nebius_key()
        decisions = judge(
            segments,
            NemotronClient(lambda: token_factory_key, timeout=600.0),
            priority_available=True,
            patterns=ScamRetriever(load_library(), EmbeddingClient(lambda: token_factory_key)),
        )
        grades = Counter((d.outcome, d.grade, d.candidate.category) for d in decisions)
        print("decisions:", {" ".join(str(x) for x in k): n for k, n in grades.items()})
        print("named scripts:", [d.pattern.pattern_id for d in decisions if d.pattern])
        private = Path.home() / ".hearally" / f"decisions-{recording.event_id[:12]}.json"
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text(
            json.dumps(
                [
                    {
                        "outcome": d.outcome,
                        "grade": d.grade,
                        "category": d.candidate.category,
                        "summary": d.candidate.summary,
                        "segments": list(d.candidate.segment_ids),
                        "reasons": list(d.reasons),
                        "named_script": d.pattern.name if d.pattern else None,
                    }
                    for d in decisions
                ],
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"full decisions written privately to {private}")

    if args.private_copy:
        copy = Path.home() / ".hearally" / f"transcript-{recording.event_id[:12]}.json"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(body)
        print(f"transcript copied privately to {copy}")

    # Raw transcripts are never kept. The lifecycle rule is only the backstop.
    s3.delete_object(Bucket=BUCKET, Key=key)
    s3.delete_object(Bucket=BUCKET, Key=batch.manifest_key)
    if uploaded_key:
        s3.delete_object(Bucket=BUCKET, Key=uploaded_key)
    print("transcript deleted from S3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
