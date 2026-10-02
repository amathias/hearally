import { readFileSync } from "node:fs";

import { DEVICE_TOKEN } from "./api-client.js";

function required(environment, name) {
  const value = environment[name]?.trim();
  if (!value) throw new Error(`Missing required setting ${name}`);
  return value;
}

const CAPTURE_MODES = new Set(["phone_call", "in_person", "unknown"]);
const MODELS = new Set(["note", "note_pro", "notepin", "notepin_s", "other"]);
// These cannot record phone calls; see backend/src/hearally/audio.py. Named by
// incapability so an "other" recorder is not refused for something unknown.
const CANNOT_RECORD_CALLS = new Set(["notepin", "notepin_s"]);
const SAFE_ID = /^[A-Za-z0-9._:-]{3,128}$/u;

/**
 * A NotePin can only ever record the room, so its recordings are in-person by
 * definition. A Note records either, and nothing in the file says which yet.
 */
function defaultCaptureMode(model) {
  return model === "notepin" || model === "notepin_s" ? "in_person" : "unknown";
}

function recorder(raw, index) {
  const where = `recorder ${index + 1}`;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error(`${where} must be an object`);
  }
  const { id, model = "other", folder } = raw;
  if (typeof id !== "string" || !SAFE_ID.test(id)) {
    throw new Error(`${where}: id must be 3-128 letters, digits, dots, colons, dashes or underscores`);
  }
  if (!MODELS.has(model)) {
    throw new Error(`${where} (${id}): model must be one of ${[...MODELS].join(", ")}`);
  }
  if (typeof folder !== "string" || !folder.trim()) {
    throw new Error(`${where} (${id}): folder is required`);
  }
  const captureMode = raw.capture_mode ?? defaultCaptureMode(model);
  if (!CAPTURE_MODES.has(captureMode)) {
    throw new Error(`${where} (${id}): capture_mode must be one of ${[...CAPTURE_MODES].join(", ")}`);
  }
  if (captureMode === "phone_call" && CANNOT_RECORD_CALLS.has(model)) {
    throw new Error(`${where} (${id}): a ${model} cannot record phone calls`);
  }
  return Object.freeze({ id, model, folder: folder.trim(), captureMode });
}

/**
 * Which recorders to collect from.
 *
 * Several recorders: HEARALLY_RECORDERS_FILE names a JSON file, one entry per
 * Plaud, each with its own folder. One folder per recorder is required, because
 * a recording carries no mark of which device made it, so the folder is the
 * only way to tell them apart.
 *
 * One recorder: HEARALLY_WATCH_DIR, as before.
 */
function loadRecorders(environment, readFile) {
  const file = environment.HEARALLY_RECORDERS_FILE?.trim();
  if (!file) {
    const mode = (environment.HEARALLY_CAPTURE_MODE ?? "").trim();
    // Name the setting the person actually typed, not the internal recorder.
    if (mode && !CAPTURE_MODES.has(mode)) {
      throw new Error(`HEARALLY_CAPTURE_MODE must be one of ${[...CAPTURE_MODES].join(", ")}`);
    }
    return [
      recorder(
        {
          id: environment.HEARALLY_RECORDER_ID?.trim() || "default",
          model: environment.HEARALLY_RECORDER_MODEL?.trim() || "other",
          folder: required(environment, "HEARALLY_WATCH_DIR"),
          ...(mode ? { capture_mode: mode } : {}),
        },
        0,
      ),
    ];
  }

  let parsed;
  try {
    parsed = JSON.parse(readFile(file, "utf8"));
  } catch (error) {
    throw new Error(`HEARALLY_RECORDERS_FILE could not be read as JSON: ${error.message}`);
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    throw new Error("HEARALLY_RECORDERS_FILE must list at least one recorder");
  }
  return recorderList(parsed);
}

/** Validates a list of recorders: known models, unique ids, one folder each. */
export function recorderList(list) {
  if (!Array.isArray(list) || list.length === 0) throw new Error("At least one recorder is needed");
  const recorders = list.map(recorder);

  const ids = new Set();
  const folders = new Set();
  for (const r of recorders) {
    if (ids.has(r.id)) throw new Error(`Recorder id ${r.id} is listed twice`);
    const folderKey = r.folder.replace(/[\\/]+$/u, "").toLowerCase();
    if (folders.has(folderKey)) {
      throw new Error(`Recorders share the folder ${r.folder}; each needs its own`);
    }
    ids.add(r.id);
    folders.add(folderKey);
  }
  return recorders;
}

export function loadConfig(environment = process.env, { readFile = readFileSync } = {}) {
  return buildConfig({
    apiUrl: required(environment, "HEARALLY_API_URL"),
    deviceToken: required(environment, "HEARALLY_DEVICE_TOKEN"),
    recorders: loadRecorders(environment, readFile),
    stateFile: environment.HEARALLY_STATE_FILE?.trim() || ".hearally-collector-state.json",
  });
}

/**
 * The settings `setup` saved on this computer, as a configuration. Checked by
 * the same rules as a hand-written .env, so a settings file edited by hand
 * cannot get round them.
 */
export function configFromSettings(settings, { stateFile }) {
  if (!settings || typeof settings !== "object") throw new Error("The saved settings are damaged");
  return buildConfig({
    apiUrl: settings.api_url,
    deviceToken: settings.device_token,
    recorders: recorderList(settings.recorders),
    stateFile,
  });
}

function buildConfig({ apiUrl, deviceToken, recorders, stateFile }) {
  const parsedUrl = new URL(apiUrl);
  if (parsedUrl.protocol !== "https:" && parsedUrl.hostname !== "localhost") {
    throw new Error("HEARALLY_API_URL must use HTTPS unless it is localhost");
  }
  if (typeof deviceToken !== "string" || !DEVICE_TOKEN.test(deviceToken)) {
    // Never echo the value: it is a credential.
    throw new Error("The device token is missing or damaged. Run setup again to pair this computer.");
  }

  return Object.freeze({
    apiUrl: apiUrl.replace(/\/$/u, ""),
    deviceToken,
    recorders: Object.freeze(recorders),
    stateFile,
  });
}
