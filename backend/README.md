# Hearally backend

The first backend slice implements two Lambda handlers:

- `hearally.handlers.config.lambda_handler` for public runtime configuration.
- `hearally.handlers.ingest.lambda_handler` for authenticated collector events.

The ingest handler validates the exact request signature, schema, idempotency key, and mandatory privacy flags. It stores a content-free deduplication record and places locally filtered/redacted conversation text on a short-retention encrypted FIFO queue.

The handler must never log its request body. Downstream extraction consumes the queue, calls Bedrock with invocation logging disabled, and persists only structured metadata and short paraphrases.

`contact_channels.py` implements independent email-link and six-digit SMS challenges, HMAC-only proof storage, expiry, attempt/send limits, and channel ownership claims. Verification remains separate from best-effort acknowledgement and channel consent. `providers/` contains AWS SES/Notify adapters and mock/production CALL-E adapters; the voice delivery service blocks calls unless the phone is verified and voice opt-in is current.

Run the dependency-free unit suite with:

```powershell
python -m unittest discover -s tests -v
```
