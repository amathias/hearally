import { readFile } from "node:fs/promises";

import { hashFile, loadState, saveState, scanFolder } from "./folder-source.js";

/**
 * One pass over the watch folder: every settled recording not already sent is
 * uploaded and announced, in the order it was recorded.
 *
 * Each recording is recorded as done only after the ingest call succeeds, and
 * state is saved after every recording rather than at the end. A crash halfway
 * through a batch then re-sends only the one in flight; the backend treats the
 * content hash as an idempotency key, so a re-send is harmless.
 */
export async function collectOnce({ config, client, log = () => {}, deps = {} }) {
  const scan = deps.scanFolder ?? scanFolder;
  const hash = deps.hashFile ?? hashFile;
  const readBytes = deps.readBytes ?? ((path) => readFile(path));
  const load = deps.loadState ?? loadState;
  const save = deps.saveState ?? saveState;

  // One state file across every recorder. Identity is the content hash, so the
  // same recording is sent once even if it turns up in two recorders' folders.
  const uploaded = await load(config.stateFile);
  const result = { sent: 0, alreadySent: 0, failed: 0, skipped: 0, unavailable: 0, unpaired: false };

  recorders: for (const recorder of config.recorders) {
    const say = (line) => log(`[${recorder.id}] ${line}`);

    let found;
    try {
      found = await scan(recorder.folder);
    } catch (error) {
      // A recorder not set up yet, or a drive not mounted, must not stop the
      // others being collected.
      result.unavailable += 1;
      say(`folder unavailable: ${error.code ?? error.message}`);
      continue;
    }

    result.skipped += found.skipped.length;
    for (const item of found.skipped) say(`skip ${item.name}: ${item.reason}`);

    for (const recording of found.recordings) {
      const eventId = await hash(recording.path);
      if (uploaded.has(eventId)) {
        result.alreadySent += 1;
        continue;
      }

      try {
        const upload = await client.requestUpload(recording, eventId);
        // Read into memory: Plaud writes compressed audio, and a long recording
        // is tens of megabytes. Multipart upload is the answer if that changes.
        await client.putAudio(upload, await readBytes(recording.path));
        await client.ingest({
          // The account and the object key are not sent: the server derives
          // both from this computer's pairing.
          schema_version: 4,
          event_id: eventId,
          content_type: recording.contentType,
          byte_length: recording.byteLength,
          recorded_at: recording.recordedAt,
          capture_mode: recorder.captureMode,
          source: "folder",
          recorder_id: recorder.id,
          recorder_model: recorder.model,
        });

        uploaded.add(eventId);
        await save(config.stateFile, uploaded);
        result.sent += 1;
        // The file name only, never contents: recordings are named by the
        // device with a timestamp, not by what was said.
        say(`sent ${recording.name}`);
      } catch (error) {
        result.failed += 1;
        if (error.status === 401) result.unpaired = true;
        say(`failed ${recording.name}: ${error.message}`);
        // A non-retryable failure (a rejected signature, a malformed request)
        // will fail the same way for every file and every recorder, so stop.
        if (error.retryable === false) break recorders;
      }
    }
  }

  return result;
}
