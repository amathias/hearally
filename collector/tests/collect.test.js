import assert from "node:assert/strict";
import test from "node:test";

import { ApiError } from "../src/api-client.js";
import { collectOnce } from "../src/collect.js";

const NOTE = Object.freeze({ id: "note-pro", model: "note_pro", folder: "/note", captureMode: "unknown" });
const PIN = Object.freeze({ id: "pin", model: "notepin", folder: "/pin", captureMode: "in_person" });

function config(recorders = [NOTE]) {
  return Object.freeze({ stateFile: "/state.json", recorders });
}

function recording(folder, name) {
  return {
    path: `${folder}/${name}`,
    name,
    contentType: "audio/mp4",
    byteLength: 10,
    recordedAt: "2026-09-23T10:00:00.000Z",
  };
}

/**
 * A fake world: folders of recordings, content hashes, saved state and an API.
 * `folders` maps a folder to its recordings; a folder missing from the map
 * behaves like one that does not exist on disk.
 */
function harness({ folders = {}, alreadyUploaded = [], failOn = {}, sameBytes = {} } = {}) {
  const saves = [];
  const ingested = [];
  const calls = [];

  const client = {
    requestUpload: async (rec, eventId) => {
      calls.push(`upload:${rec.name}`);
      if (failOn[rec.name]) throw failOn[rec.name];
      return { upload_url: "https://s3/", upload_fields: {}, object_key: `incoming/${eventId}` };
    },
    putAudio: async () => calls.push("put"),
    ingest: async (event) => {
      calls.push("ingest");
      ingested.push(event);
    },
  };

  const deps = {
    scanFolder: async (folder) => {
      if (!(folder in folders)) {
        const error = new Error("no such folder");
        error.code = "ENOENT";
        throw error;
      }
      return { recordings: folders[folder].map((n) => recording(folder, n)), skipped: [] };
    },
    // Files listed in sameBytes hash to the same value: identical content.
    hashFile: async (path) => sameBytes[path] ?? `hash-of-${path}`,
    readBytes: async () => Buffer.from("audio"),
    loadState: async () => new Set(alreadyUploaded),
    saveState: async (_file, set) => saves.push([...set].sort()),
  };

  return { client, deps, saves, ingested, calls };
}

test("sends a new recording: upload, then put, then ingest", async () => {
  const h = harness({ folders: { "/note": ["call.m4a"] } });
  const result = await collectOnce({ config: config(), client: h.client, deps: h.deps });

  assert.equal(result.sent, 1);
  assert.deepEqual(h.calls, ["upload:call.m4a", "put", "ingest"]);
});

test("the ingest event names the recording, never the account, the key or the local path", async () => {
  const h = harness({ folders: { "/note": ["call.m4a"] } });
  await collectOnce({ config: config(), client: h.client, deps: h.deps });

  const event = h.ingested[0];
  assert.equal(event.schema_version, 4);
  assert.equal(event.event_id, "hash-of-/note/call.m4a");
  assert.ok(!("account_id" in event) && !("object_key" in event), "the server derives both");
  assert.ok(!JSON.stringify(event).includes('"/note/'), "local paths stay local");
});

test("a 401 marks the computer as unpaired so the person can be told how to fix it", async () => {
  const h = harness({
    folders: { "/note": ["call.m4a"] },
    failOn: { "call.m4a": new ApiError("/v1/uploads", 401) },
  });
  const result = await collectOnce({ config: config(), client: h.client, deps: h.deps });
  assert.equal(result.unpaired, true);
});

test("each recording says which recorder made it", async () => {
  const h = harness({ folders: { "/note": ["call.m4a"], "/pin": ["kitchen.m4a"] } });
  await collectOnce({ config: config([NOTE, PIN]), client: h.client, deps: h.deps });

  const byRecorder = Object.fromEntries(
    h.ingested.map((e) => [e.recorder_id, [e.recorder_model, e.capture_mode]]),
  );
  assert.deepEqual(byRecorder, {
    "note-pro": ["note_pro", "unknown"],
    pin: ["notepin", "in_person"],
  });
});

test("a recorder whose folder is missing does not stop the others", async () => {
  const lines = [];
  const h = harness({ folders: { "/pin": ["kitchen.m4a"] } });
  const result = await collectOnce({
    config: config([NOTE, PIN]),
    client: h.client,
    deps: h.deps,
    log: (line) => lines.push(line),
  });

  assert.equal(result.unavailable, 1);
  assert.equal(result.sent, 1);
  assert.ok(lines.includes("[note-pro] folder unavailable: ENOENT"));
});

test("the same recording in two recorders' folders is sent once", async () => {
  const h = harness({
    folders: { "/note": ["a.m4a"], "/pin": ["copy-of-a.m4a"] },
    sameBytes: { "/note/a.m4a": "same", "/pin/copy-of-a.m4a": "same" },
  });
  const result = await collectOnce({ config: config([NOTE, PIN]), client: h.client, deps: h.deps });

  assert.equal(result.sent, 1);
  assert.equal(result.alreadySent, 1);
});

test("a recording already sent is not sent again", async () => {
  const h = harness({
    folders: { "/note": ["old.m4a", "new.m4a"] },
    alreadyUploaded: ["hash-of-/note/old.m4a"],
  });
  const result = await collectOnce({ config: config(), client: h.client, deps: h.deps });

  assert.equal(result.sent, 1);
  assert.equal(result.alreadySent, 1);
});

test("state is saved after every recording, not only at the end", async () => {
  // A crash after the first of three must not cause all three to re-upload.
  const h = harness({ folders: { "/note": ["a.m4a", "b.m4a", "c.m4a"] } });
  await collectOnce({ config: config(), client: h.client, deps: h.deps });

  assert.deepEqual(h.saves, [
    ["hash-of-/note/a.m4a"],
    ["hash-of-/note/a.m4a", "hash-of-/note/b.m4a"],
    ["hash-of-/note/a.m4a", "hash-of-/note/b.m4a", "hash-of-/note/c.m4a"],
  ]);
});

test("a failed recording is not marked as sent, and the next one is tried", async () => {
  const h = harness({
    folders: { "/note": ["bad.m4a", "good.m4a"] },
    failOn: { "bad.m4a": new ApiError("/v1/uploads", 503) },
  });
  const result = await collectOnce({ config: config(), client: h.client, deps: h.deps });

  assert.equal(result.failed, 1);
  assert.equal(result.sent, 1);
  assert.ok(!h.saves.flat().includes("hash-of-/note/bad.m4a"), "it must be retried next run");
});

test("a non-retryable failure stops every recorder, not just the current one", async () => {
  // A rejected signature fails identically everywhere; carrying on only
  // fills the log with the same 401.
  const h = harness({
    folders: { "/note": ["a.m4a"], "/pin": ["b.m4a"] },
    failOn: { "a.m4a": new ApiError("/v1/uploads", 401) },
  });
  const result = await collectOnce({ config: config([NOTE, PIN]), client: h.client, deps: h.deps });

  assert.equal(result.failed, 1);
  assert.ok(!h.calls.includes("upload:b.m4a"));
});
