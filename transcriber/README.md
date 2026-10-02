# Transcriber

The Parakeet transcription job. It runs on **Nebius AI Cloud** (Serverless AI, one NVIDIA RTX PRO 6000, preemptible) with **NVIDIA Parakeet TDT 0.6B v3**, NeMo 3.0, 25 European languages, and turns recordings into transcripts with word timings and word confidence.

It is its own deployable unit in the monorepo: a container image in Nebius Container Registry (`cr.us-central1.nebius.cloud/u00cw1b5c26dcmx7vc/transcriber`), started once per batch.

## How a batch works

1. The backend (`backend/src/hearally/transcription.py`) writes a **manifest** to S3 under `jobs/<job>/`. For each recording it holds a signed link to **download** the audio from `incoming/` and a signed link to **upload** the transcript to `transcripts/`; plus a link for the job's **report**.
2. The job is started with one environment variable, `HEARALLY_MANIFEST_URL`, a signed link to that manifest valid for 3 hours (the links inside it last 6). Finding a GPU and pulling the image can take over half an hour, and a 30-minute link once expired before the job could read it.
3. For each recording (`src/hearally_transcriber/main.py`): download, convert to 16 kHz mono, cut long recordings into pieces of about ten minutes at quiet moments (`chunks.py`), transcribe the pieces in one batch, shift and join them (`result.py`), and upload the transcript (`docs/contracts/transcript-v1.schema.json`).
4. It uploads a report of counts and timings, and exits non-zero if any recording failed.

## What it will not do

- **Hold AWS credentials.** It has only signed links, each for one object, for a few hours, and an upload link is signed for JSON only.
- **Talk to anything but S3.** Every link must be HTTPS on `*.amazonaws.com`, checked before any request, including the manifest link itself.
- **Log what was said.** The report and the job log carry counts, timings and error types. Error messages have links stripped.

Transcripts are raw text, so the backend deletes each one as soon as the judgement has read it, and `transcripts/` and `jobs/` expire after a day regardless.

## Build, push, run

Needs Docker, and the Nebius Python SDK for the tooling (`pip install nebius`). The Nebius CLI does not support Windows; `scripts/nebius_jobs.py` uses the SDK with the `hearally-transcriber` service account's authorized key, whose private half never leaves the operator's machine.

```bash
python transcriber/scripts/nebius_jobs.py registry        # prints the registry path
python transcriber/scripts/nebius_jobs.py docker-login
docker build -t cr.us-central1.nebius.cloud/u00cw1b5c26dcmx7vc/transcriber:latest transcriber
docker push cr.us-central1.nebius.cloud/u00cw1b5c26dcmx7vc/transcriber:latest
python backend/scripts/transcribe_recording.py EVENT_ID [--judge]
```

The model is baked into the image, so a job never waits on a download and always runs the tested weights.

### Without a built image

When no image has been built (Docker Desktop would not start on the operator's machine, 2026-09-25), a job can run from PyTorch's official CUDA 12.8 image instead. Nebius injects `bootstrap.sh`, `requirements.txt` and `src/` into the container (each under its 64 KiB limit); the script installs ffmpeg and the pinned NeMo, then runs the job, and reports the failed stage if installing fails. This is the default for `backend/scripts/transcribe_recording.py`; pass `--built` to use the registry image. It costs a few minutes of start-up per job, so the built image, from CI, is the long-term route.

## Tests

```bash
PYTHONPATH=transcriber/src python -m unittest discover -s transcriber/tests
```

The tests cover the parts that need no GPU: where long recordings are cut, how pieces are joined and scored, and that the job only accepts HTTPS S3 links and never reports one. Parakeet itself is exercised by running a job.

## Cost

An RTX PRO 6000 is about $1.80 per GPU-hour on demand, less preemptible, billed only while a job runs. Transcribing takes minutes for hours of audio; most of a job's time is starting the machine and loading the image. Jobs are cancelled after 45 minutes. The Nebius account has a $20 monthly budget with alerts.

## Licence

Parakeet TDT 0.6B v3 is released by NVIDIA under CC-BY-4.0.
