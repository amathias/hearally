# Collector pairing

How a computer running the collector is connected to one Hearally account, proves it on every upload, and is disconnected. Implemented in `backend/src/hearally/pairing.py`, `handlers/pairing.py` and `collector/src/setup.js`; deployed to production.

Adapted from an earlier prototype's collector pairing, with one difference: there, pairing was built but ingest did not yet accept the credential. Here, the device token is the only credential ingest accepts, and the account every recording belongs to comes from it.

## Flow

1. **Code.** Signed in to the dashboard (Integrations, then Connect a computer), the account holder asks for a code. The first time, they must accept the acknowledgement (ARCHITECTURE.md section 9), which is recorded as `USER#<sub> / CONSENT#<version>`. The code is 192 random bits, 32 URL-safe characters, used once, valid for ten minutes.
2. **Redeem.** The collector's `setup` sends the code and the computer's name. The server consumes the code and stores the computer in one DynamoDB transaction, then returns a device token, once.
3. **Use.** Every `POST /v1/uploads` and `POST /v1/ingest` carries `Authorization: Bearer <token>`. The server reads the computer's record, checks it is active and the secret's digest matches, and uses that record's account. Nothing in the request body names an account.
4. **Revoke.** The dashboard lists computers; Disconnect marks one revoked and deletes its digest. The next request from it is refused with 401, and the collector tells the person how to reconnect.

## Routes

| Request | Authorisation | Result |
|---|---|---|
| `POST /v1/me/pairings` | Cognito ID token | `201 {code, expires_at}`. `409 {error: "acknowledgement_required", version}` until the body carries `{"acknowledgement_version": <current>}` once |
| `POST /v1/pairings/redeem` | Holding a live code | Body `{code, computer_name}`. `201 {token, device_id}`, or `404 pairing_unavailable` for wrong, expired and used alike |
| `DELETE /v1/me/computers/{device_id}` | Cognito ID token | `200 {revoked: true}`; `404` for another account's computer |
| `GET /v1/me/integrations` | Cognito ID token | Computers (id, name, status, paired and last-sent times), Plauds with recording counts, and whether the acknowledgement is on record |

All sit behind CloudFront (edge token) and the API's WAF rate limit, which is what bounds guessing at the redeem route; a 192-bit code is not guessable anyway.

## Token

```
hd1.<account sub>.<device id: 32 hex>.<secret: 43 URL-safe characters, 256 bits>
```

The sub and device id locate the record, so authenticating is one consistent read with no index. Only `SHA-256(secret)` is stored. The token is a bearer credential over TLS rather than a signing key, because a server holding only a digest cannot check a signature; uploads are separately bound to their bytes by the presigned POST policy.

## Storage

| Item | Key | Notes |
|---|---|---|
| Pairing code | `PAIR#<sha256(code)> / CODE` | `account_id`, `expires_epoch`, and `ttl_epoch` so DynamoDB deletes unused codes |
| Computer | `USER#<sub> / COMPUTER#<device id>` | `name`, `status` (`active` or `revoked`), `paired_at`, `last_seen_at`, `credential_digest` (removed on revoke; never sent to the browser) |
| Plaud | `USER#<sub> / DEVICE#<recorder id>` | `model`, `last_seen_at`, `recordings` (first acceptances only) |
| Acknowledgement | `USER#<sub> / CONSENT#<version>` | `accepted_at`, `method: dashboard`; the first acceptance is kept |

Each Lambda's IAM policy is limited to the partition prefixes it touches (`infra/modules/application/main.tf`): issuing and redeeming to `PAIR#*` and `USER#*`, authentication and revocation to `USER#*`, reservations to `INGEST#*`.

## On the computer

`setup` redeems the code only after every other question is answered, so an abandoned setup pairs nothing. If the code has expired by then, it asks for a fresh one and keeps the other answers. The token is saved in a settings file readable only by the signed-in user (`%LOCALAPPDATA%\Hearally`, `~/Library/Application Support/Hearally`, `~/.local/share/hearally`). If saving fails after the code was redeemed, setup says to disconnect that computer in the dashboard and run setup again.

The earlier prototype protected its credential with Windows DPAPI. Hearally does not, for now: the settings file is already private to the user, DPAPI does not protect against other programs running as that user, and the scheduled task would need PowerShell on every run to decrypt it. Revocation from the dashboard is the control that matters if a computer is lost.

## Never

Log a code, a token or a request body. Put a code in a URL. Accept an account from the request. Tell a caller whether a code was wrong, expired or used.
