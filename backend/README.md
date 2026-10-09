# Hearally backend

One Python 3.12 package, `hearally`, deployed as one AWS Lambda function per handler (arm64), each with its own IAM role. How the pieces fit together is in [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md); code comments cite its sections.

## Handlers (`src/hearally/handlers/`)

| Area | Handlers |
|---|---|
| Public | `config` (runtime configuration) |
| Recordings in | `plaud_link` (Plaud sign-in), `plaud_import` (every 15 minutes), `collector` (upload and ingest from a paired computer), `pairing` (pairing codes, the acknowledgement) |
| Transcription | `worker` (dispatch every 30 minutes, collect every 10) |
| Dashboard | `dashboard` (`GET /v1/me/{section}`), `recordings` (listen to the silenced copy) |
| Allies | `allies` (managed by the account holder; the ally's own link; delivery every 10 minutes) |
| Scam library | `scam_refresh` (weekly, with Tavily) |

## Main modules

- `judgement.py`: Nemotron on Nebius Token Factory describes what was said; code applies the grades, the confidence gate and the dignity filter.
- `transcription.py`, `nebius_jobs.py`, `worker.py`: the Parakeet job on Nebius AI Cloud, and storing what it found.
- `learning.py`: what allies said was nothing becomes learned patterns. `topics.py`: topics that never leave.
- `allies.py`: allies, their links and consent, planning and sending email through Amazon SES.
- `scam_library.py`, `scam_discovery.py`: known scam scripts, matched with Qwen3 embeddings and kept current with Tavily.
- `plaud_link.py`, `plaud_import.py`, `pairing.py`, `audio.py`: the two ways recordings arrive.
- `contact_channels.py`, `providers/aws_channels.py`: email and text-message verification codes (Amazon SES and AWS End User Messaging). Tested, but not yet used by any handler.

## Rules

- Never log a request body, a transcript, a summary, a link or anything said. Runs report counts.
- Product rules (the heads-up budget, quiet hours, the dignity filter, learned patterns) live in code and conditional writes, never in a model prompt.

## Checks

From `backend/`:

```powershell
pip install -e ".[dev]"
python -m unittest discover -s tests
ruff check src tests
mypy src
```
