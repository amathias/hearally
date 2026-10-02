import assert from "node:assert/strict";
import test from "node:test";

import { contentTypeFor, isRecording } from "../src/audio.js";
import { configFromSettings, loadConfig } from "../src/config.js";

const TOKEN = `hd1.3f1c2a90-1111-4222-8333-944455556666.${"a".repeat(32)}.${"s".repeat(43)}`;

const VALID = {
  HEARALLY_API_URL: "https://api.hearally.com/",
  HEARALLY_DEVICE_TOKEN: TOKEN,
  HEARALLY_WATCH_DIR: "/recordings",
};

test("loads a valid configuration and strips the trailing slash", () => {
  const config = loadConfig(VALID);
  assert.equal(config.apiUrl, "https://api.hearally.com");
  assert.equal(config.recorders.length, 1);
  assert.equal(config.recorders[0].captureMode, "unknown");
  assert.ok(Object.isFrozen(config));
});

test("refuses plain HTTP to anything but localhost", () => {
  assert.throws(
    () => loadConfig({ ...VALID, HEARALLY_API_URL: "http://api.hearally.com" }),
    /HTTPS/,
  );
  assert.doesNotThrow(() => loadConfig({ ...VALID, HEARALLY_API_URL: "http://localhost:3000" }));
});

test("refuses a damaged device token without repeating it", () => {
  const damaged = TOKEN.slice(0, -5);
  assert.throws(
    () => loadConfig({ ...VALID, HEARALLY_DEVICE_TOKEN: damaged }),
    (error) => /Run setup again/.test(error.message) && !error.message.includes(damaged),
  );
});

test("names the missing setting", () => {
  const { HEARALLY_WATCH_DIR: _omitted, ...rest } = VALID;
  assert.throws(() => loadConfig(rest), /HEARALLY_WATCH_DIR/);
});

test("accepts only known capture modes", () => {
  assert.equal(
    loadConfig({ ...VALID, HEARALLY_CAPTURE_MODE: "phone_call" }).recorders[0].captureMode,
    "phone_call",
  );
  assert.throws(() => loadConfig({ ...VALID, HEARALLY_CAPTURE_MODE: "spying" }), /CAPTURE_MODE/);
});

test("recognises audio by extension, case-insensitively", () => {
  assert.equal(contentTypeFor("call.MP3"), "audio/mpeg");
  assert.equal(contentTypeFor("call.m4a"), "audio/mp4");
  assert.equal(contentTypeFor("notes.txt"), null);
  assert.equal(contentTypeFor("no-extension"), null);
});

test("ignores hidden and temporary files even with an audio extension", () => {
  assert.equal(isRecording("._call.m4a"), false, "macOS metadata sidecar");
  assert.equal(isRecording("~call.wav"), false, "editor temporary");
  assert.equal(isRecording("2026-09-23 call.m4a"), true);
});

// ---- several recorders ----

function withRecorders(list) {
  const { HEARALLY_WATCH_DIR: _single, ...rest } = VALID;
  return loadConfig(
    { ...rest, HEARALLY_RECORDERS_FILE: "recorders.json" },
    { readFile: () => (typeof list === "string" ? list : JSON.stringify(list)) },
  );
}

test("loads one recorder per Plaud from a recorders file", () => {
  const config = withRecorders([
    { id: "note-pro", model: "note_pro", folder: "C:/Plaud/NotePro" },
    { id: "pin", model: "notepin", folder: "C:/Plaud/NotePin" },
  ]);
  assert.deepEqual(
    config.recorders.map((r) => [r.id, r.model]),
    [["note-pro", "note_pro"], ["pin", "notepin"]],
  );
});

test("a NotePin's recordings are in-person by definition", () => {
  const [pin] = withRecorders([{ id: "pin", model: "notepin_s", folder: "/pin" }]).recorders;
  assert.equal(pin.captureMode, "in_person");
});

test("a Note's recordings default to unknown: it records either kind", () => {
  const [note] = withRecorders([{ id: "note", model: "note", folder: "/note" }]).recorders;
  assert.equal(note.captureMode, "unknown");
});

test("refuses a NotePin configured to record phone calls", () => {
  assert.throws(
    () => withRecorders([{ id: "pin", model: "notepin", folder: "/pin", capture_mode: "phone_call" }]),
    /cannot record phone calls/,
  );
});

test("each recorder needs its own folder, or recordings cannot be told apart", () => {
  assert.throws(
    () =>
      withRecorders([
        { id: "a-1", model: "note", folder: "C:/Plaud/" },
        { id: "b-1", model: "notepin", folder: "c:/plaud" },
      ]),
    /each needs its own/,
  );
});

test("refuses duplicate ids, unknown models and an empty list", () => {
  assert.throws(
    () =>
      withRecorders([
        { id: "same", model: "note", folder: "/a" },
        { id: "same", model: "note", folder: "/b" },
      ]),
    /listed twice/,
  );
  assert.throws(() => withRecorders([{ id: "x-1", model: "iphone", folder: "/a" }]), /model/);
  assert.throws(() => withRecorders([]), /at least one/);
});

test("a recorders file that is not JSON says so", () => {
  assert.throws(() => withRecorders("not json"), /could not be read as JSON/);
});

test("a recorder of unknown model may be set to phone calls", () => {
  // The default model is "other"; one Plaud Note with HEARALLY_CAPTURE_MODE
  // set to phone_call is the simplest real setup and must be accepted.
  const [only] = loadConfig({ ...VALID, HEARALLY_CAPTURE_MODE: "phone_call" }).recorders;
  assert.equal(only.model, "other");
  assert.equal(only.captureMode, "phone_call");
});

const SAVED = {
  api_url: "https://api.hearally.com",
  device_token: TOKEN,
  recorders: [{ id: "notepin", model: "notepin", folder: "/r" }],
};

test("settings saved by setup become the same configuration a .env would", () => {
  const config = configFromSettings(SAVED, { stateFile: "/app/sent.json" });
  assert.equal(config.stateFile, "/app/sent.json");
  assert.equal(config.recorders[0].captureMode, "in_person");
  assert.ok(Object.isFrozen(config));
});

test("saved settings edited by hand are held to the same rules", () => {
  assert.throws(() => configFromSettings(null, { stateFile: "s" }), /damaged/);
  assert.throws(() => configFromSettings({ ...SAVED, recorders: [] }, { stateFile: "s" }), /At least one recorder/);
  assert.throws(() => configFromSettings({ ...SAVED, device_token: "x" }, { stateFile: "s" }), /Run setup again/);
  assert.throws(
    () =>
      configFromSettings(
        { ...SAVED, recorders: [{ id: "pin", model: "notepin", folder: "/r", capture_mode: "phone_call" }] },
        { stateFile: "s" },
      ),
    /cannot record phone calls/,
  );
});
