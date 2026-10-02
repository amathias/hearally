# Hearally workspace instructions

## Product boundary

- Hearally passes along things that were **mentioned out loud** near a Plaud recorder to the **allies** the account holder chose. It reports what was said. It does not detect what happened.
- Never describe it as fall detection, health monitoring, a monitoring service, a medical or diagnostic product, an emergency service, or a guaranteed-response service.
- Every outward-facing word stays in the wellness and coordination lane. Never use: emergency, urgent, safety, protect, guardian, detect falls, detects, monitors, 24/7, life-saving, never miss, ensures, guarantees, medical, health monitoring, adherence, compliance, diagnose, assess, risk score, cognitive. The required disclaimers ("not an emergency service", "not a medical device", "does not detect what happened") are the only exception. Check marketing text, message templates, email copy and the demo script against this.
- Every outward-facing message must carry "not an emergency service". Every item must offer "this was nothing".
- The **account holder** owns the data and the rules. **Allies** have no account, no device and no portal. Never frame an ally as able to authorise anything on the account holder's behalf.
- Never call the account holder a patient, a subject or a monitored person. "Caregiver" does not appear in the interface.

## Vocabulary

Use `ALLY` in APIs and data records. Do not use `contact`, `care circle` or `first responder`.

| Concept | Use |
|---|---|
| People who receive things | **your allies** |
| First in the order | **first ally** |
| The no-login page | **ally link** |
| The audit log | **"what your allies heard"** |
| Onboarding step | **"Who are your allies?"** |

## Colour convention

**Amber `#E8934A` is the ally. Sand `#F6F1E8` (on dark) or teal `#1F6F6B` (on light) is the account holder.** This is a product rule, not only a logo rule: use amber anywhere allies appear — ally names, ally links, the "what your allies heard" log. Flag colour `#B4462F` is muted brick, never siren red. Nothing here should look like an alarm. Tokens live in `brand/palette.css`; rules in `brand/README.md`.

## Data boundary

- **Raw transcripts are never stored.** Persist paraphrased items, structured metadata and redacted clips only.
- Redaction happens **in the audio**, on the server after transcription (numbers are located by word timestamp). Unredacted audio lives only under `incoming/`, is deleted by the pipeline as soon as the redacted copy exists. An S3 lifecycle rule is only the backstop and can take up to ~48 hours (S3 rounds to midnight UTC), so it must never be described as a 24-hour guarantee. Nothing but the pipeline may read `incoming/`.
- Speech is transcribed on a model **we run ourselves** (Parakeet, Nebius AI Cloud). Judgement uses Nemotron on **Nebius Token Factory, which is hosted inference**: call it only with Zero Data Retention enabled on the account, and never describe it in public copy as something we run ourselves. Nothing goes to a consumer chatbot. Do not strengthen these claims without a recorded decision; an earlier, stronger version was wrong (ARCHITECTURE.md section 3).
- Apply the topic blocklist to the **original language, before any translation**.
- A low-confidence ASR segment never sends a text. Confidence gating is enforced in code.
- **The dignity filter (ARCHITECTURE.md section 6) runs before anything reaches an ally, and cannot be disabled by an ally.** Media playback never becomes an item. A sensitive item is never quoted verbatim and never carries an evidence clip, at any consent tier. Sensitive and not relevant means dropped.
- Daily transcript copies are per-ally, off by default, dignity-filtered, assembled at delivery and never persisted.
- Never log request bodies, transcripts, audio URLs, pairing codes, device tokens, ally contact details or signed link tokens.
- Enforce product rules — the one-per-24-hours priority budget, suppression patterns, quiet hours — in **code and conditional writes, not in model prompts**.

## Engineering conventions

- Monorepo with independently deployable `collector`, `backend`, `web` and `infra`. See `docs/adr/0001-monorepo-and-copied-engine.md`.
- **Production is the only environment.** Resources are `hearally-prod-*`. No `-dev`, no `-test`, no `-staging`, no environment selector variable. See `docs/adr/0002-production-only.md`.
- `us-east-1` only. Terraform only — no console changes. State in `holds-terraform-state-files` under `hearally/prod.tfstate`.
- **One exception to Terraform-only:** the AWS Notify configuration. The provider ships no `sms-voice` resources, so `backend/src/hearally/configure_notify.py` creates it and Terraform grants the IAM actions. Do not try to move it into Terraform until a resource exists.
- Keep the Python backend a modular monolith: handlers, services, repositories, providers. No internal services, containers or Step Functions without a recorded decision.
- Keep model and delivery providers behind interfaces. Default external messaging to mock mode in development.
- Pin the AWS provider exactly. A log group per Lambda at 30 days.

## Architecture document

- `docs/ARCHITECTURE.md` describes the system as built.
- Every material product, privacy, data, security or architecture change updates it in the same change set.
