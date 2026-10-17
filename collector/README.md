# Collector

Moves recordings from a computer to Hearally. Node 22.13+, no dependencies.

**Public preview (beta).** The collector is one of two ways recordings reach Hearally. Most people sign in to Plaud once from the dashboard instead; the collector is for people who would rather not, or who export recordings themselves. The user-facing version of these instructions is at https://hearally.com/setup/collector/.

## For participants: one file, no repository

People using the preview never see this repository. They download one file from https://hearally.com/setup/collector/ and run:

```bash
node $HOME\Downloads\hearally-collector.mjs setup    # Windows PowerShell
node ~/Downloads/hearally-collector.mjs setup         # Mac or Linux
```

That file is this folder bundled by `web/scripts/build-collector.mjs` (esbuild, unminified so it can be read) as part of `npm run build` in `web/`, so the download always matches the source and CI fails if it is stale.

`setup` asks for a **pairing code**, which the account holder makes in the dashboard (Integrations, then Connect a computer), then which Plaud, what it is used for, and the folder. Only when everything is answered does it redeem the code for this computer's **device token** (`POST /v1/pairings/redeem`; see `docs/contracts/collector-pairing.md`), so an abandoned setup pairs nothing, and an expired code is asked for again without repeating the other questions. Then it:

- saves the settings and token, owner-only, in the usual per-user place: `%LOCALAPPDATA%Hearally` on Windows, `~/Library/Application Support/Hearally` on macOS, `~/.local/share/hearally` elsewhere (`src/places.js`);
- installs its own copy there, so the download can be deleted;
- makes `~/Hearally recordings` (outside Documents and Downloads, which macOS guards from background programs), with a subfolder per Plaud when there are several;
- schedules itself every 15 minutes as the signed-in user, no administrator rights (`src/schedule.js`): a Task Scheduler task from XML, run through `conhost --headless` so no window flashes, allowed on battery; a launch agent on macOS; cron elsewhere.

Other commands: `send` (what the schedule runs), `status` (whether the computer is still connected, checked live, plus folders, schedule and recent activity from `activity.log`), `uninstall` (removes the schedule and settings; recordings are left alone).

The computer is listed in the dashboard under its own name. Disconnecting it there revokes the token at once; the collector's next run is refused with 401 and tells the person how to reconnect.

Verified on Windows 11 against production: pairing through the public redeem route, sending, a resend recognised, status, revocation scoped to the owning account, and refusal afterwards; also uninstall, and the Task Scheduler definition registering and running hidden. The macOS and Linux schedules are covered by unit tests only.

## For developers: from source

```bash
node src/cli.js setup                      # pairs, saves settings, and can schedule this checkout
node src/cli.js send
npm test
```

Run from a checkout, `setup` schedules the checkout itself rather than copying it. To point at a local API instead, copy `.env.example` to `.env` with a device token from a paired setup and run `node --env-file=.env src/cli.js send`: with `HEARALLY_API_URL` in the environment, `send` uses the environment and ignores any saved settings.

## Running it

**Each run looks once and exits.** One pass over each folder, sending anything new, then it stops. `setup` schedules it; from source, schedule `node --env-file=.env src/cli.js send` yourself. It exits 0 when nothing failed and 1 when something did.

## Checking it works

1. Export a recording into the folder, wait 30 seconds, and run the collector. Expect a `sent <file>` line, then a summary: `sent 1, already sent 0, skipped 0, failed 0, recorders unavailable 0`.
2. Run it again at once. The same file should now count as `already sent 1`. That proves the state file and the server's own duplicate check both work.
3. In the dashboard, Integrations shows the computer's last-sent time and each Plaud's count of recordings received. Summaries do not appear yet: transcription is still being built.

## Troubleshooting

| Output | Meaning |
|---|---|
| `failed … HTTP 401` | This computer was disconnected in the dashboard, or its token is damaged. The run stops at the first 401, since every file would fail the same way, and says to make a new pairing code and run setup. |
| `That code has expired or was already used` | During setup. Codes last ten minutes and work once; make another. |
| `failed … HTTP 5xx` or `429` | Temporary. The recording is retried on the next run; nothing is lost. |
| `skip … still_arriving` | Modified in the last 30 seconds, possibly still being copied. Sent next time. |
| `skip … empty` / `too_large` | Zero bytes, or over 512 MB. |
| `folder unavailable: ENOENT` | That recorder's folder does not exist. Other recorders are still collected. |
| `Missing required setting …` | From source only: a line is missing from `.env`; the message names it. |

## Why it runs on a computer

It was inherited from an earlier prototype, where running locally was forced: that wearable's data could only be reached from the owner's own machine. For Hearally's users, often older people whose Plaud syncs to a phone, a program on a computer is the wrong shape, and it is here only because the right shapes are not yet available:

- **A direct connection to the user's Plaud account**, run on Hearally's servers: sign in to Plaud once, nothing else. Needs Plaud's OAuth API, which is a private beta closed to new integrations.
- **A Hearally phone app, using Plaud Embedded:** the recorder pairs with Hearally's app. Needs Plaud partner credentials, and a Plaud can only be bound to one app at a time, so it would stop working with Plaud's own app.

Both are recorded in docs/ARCHITECTURE.md section 2; Plaud sign-in through Plaud's hosted MCP server now works without a computer.

## What it does

1. Scans a folder for audio (`.m4a`, `.mp3`, `.wav`, `.ogg`, `.opus`, `.flac`).
2. Skips anything modified in the last 30 seconds: a sync client may still be writing it.
3. Hashes each file with SHA-256. **The hash is the recording's identity**, so a renamed or re-exported file is recognised and not sent twice.
4. Asks the API for a presigned URL (`POST /v1/uploads`), puts the audio straight into S3, then announces it (`POST /v1/ingest`).

Audio never passes through the API. Every API call carries this computer's device token; the server takes the account from it, so the event names no account and no object key (`docs/contracts/ingest-v4.schema.json`). The token never goes to S3.

## More than one Plaud

Many people have two: a Plaud Note on the phone for calls, and a NotePin for the day. List them in a JSON file and point `HEARALLY_RECORDERS_FILE` at it; see `recorders.example.json`.

- **Each recorder needs its own folder.** A recording carries no mark of the device that made it, so the folder is the only way to tell them apart. The collector refuses two recorders sharing one.
- **`model`** is one of `note`, `note_pro`, `notepin`, `notepin_s`, `other`.
- **NotePin recordings are in-person by definition**: the NotePin cannot record calls, so the collector marks them that way and refuses a NotePin configured for `phone_call`. A Note records either, so its recordings are sent as `unknown` unless you set `capture_mode`.
- A missing folder is reported and skipped; the other recorders are still collected.
- The same recording turning up in two folders is still sent once: identity is the content hash.

## What it deliberately does not do

**It does not redact.** Bleeping numbers out of audio needs word timestamps, which need a transcript, which comes from Parakeet on Nebius. So the collector uploads raw audio to `incoming/`, the server transcribes, bleeps, keeps only the redacted copy and deletes the original. If the pipeline fails, an S3 lifecycle rule on `incoming/` is the backstop, which can take up to about 48 hours. See docs/ARCHITECTURE.md section 11.

**It does not log content.** Only file names, which the device assigns from the time, not from what was said.

## Where it's going

The folder source stands in for the Plaud integration, which plugs in behind the same shape once the device's export path is confirmed.
