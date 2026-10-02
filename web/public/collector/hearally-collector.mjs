#!/usr/bin/env node
// Hearally collector 0.3.1. Public preview (beta). MIT licence.
// Source: collector/ in the Hearally repository. Built by web/scripts/build-collector.mjs.
// Run: node hearally-collector.mjs setup

// ../collector/src/cli.js
import { spawnSync as spawnSync2 } from "node:child_process";
import { existsSync, readFileSync as readFileSync3, rmSync as rmSync2 } from "node:fs";
import { hostname, userInfo } from "node:os";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";

// ../collector/src/activity-log.js
import { readFileSync, writeFileSync } from "node:fs";
var KEEP_LINES = 500;
function readLog(file) {
  try {
    return readFileSync(file, "utf8").split("\n").filter(Boolean);
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}
function stampFor(date) {
  const two = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}`;
}
function appendLog(file, lines, { now = () => /* @__PURE__ */ new Date(), keep = KEEP_LINES } = {}) {
  if (!lines.length) return;
  const stamp = stampFor(now());
  const all = [...readLog(file), ...lines.map((line) => `${stamp}  ${line}`)].slice(-keep);
  writeFileSync(file, `${all.join("\n")}
`, { mode: 384 });
}

// ../collector/src/api-client.js
import { createHash } from "node:crypto";
var DEVICE_TOKEN = /^hd1\.[A-Za-z0-9-]{1,64}\.[0-9a-f]{32}\.[A-Za-z0-9_-]{43}$/u;
var PAIRING_CODE = /^[A-Za-z0-9_-]{32}$/u;
var TIMEOUT_MS = 3e4;
var HearallyClient = class {
  constructor({ apiUrl, deviceToken, fetchImpl = globalThis.fetch }) {
    this.apiUrl = apiUrl.replace(/\/$/u, "");
    this.deviceToken = deviceToken;
    this.fetchImpl = fetchImpl;
  }
  async #post(path4, payload, idempotencyKey) {
    const response = await this.fetchImpl(`${this.apiUrl}${path4}`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${this.deviceToken}`,
        "content-type": "application/json",
        "idempotency-key": idempotencyKey
      },
      body: JSON.stringify(payload),
      redirect: "error",
      signal: AbortSignal.timeout(TIMEOUT_MS)
    });
    if (!response.ok) {
      throw new ApiError(path4, response.status);
    }
    return response.json();
  }
  requestUpload(recording, eventId) {
    return this.#post(
      "/v1/uploads",
      {
        event_id: eventId,
        content_type: recording.contentType,
        byte_length: recording.byteLength
      },
      eventId
    );
  }
  /**
   * Whether this computer is still paired, for `status`. Asking for an upload
   * only signs a policy on the server: nothing is stored or reserved, and the
   * upload offered is never used.
   */
  checkConnection() {
    const eventId = createHash("sha256").update("hearally connection check").digest("hex");
    return this.requestUpload({ contentType: "audio/mp4", byteLength: 1 }, eventId);
  }
  /**
   * Sends the audio to S3 as a multipart POST against the signed policy.
   *
   * The policy fields go back exactly as issued; they carry the signature, the
   * exact size and the SHA-256 that S3 checks against the bytes. S3 requires
   * the file to be the last field, and FormData keeps insertion order.
   */
  async putAudio(upload, bytes) {
    const form = new FormData();
    for (const [name, value] of Object.entries(upload.upload_fields ?? {})) {
      form.append(name, value);
    }
    const contentType = upload.upload_fields?.["Content-Type"] ?? "application/octet-stream";
    form.append("file", new Blob([bytes], { type: contentType }));
    const response = await this.fetchImpl(upload.upload_url, { method: "POST", body: form });
    if (!response.ok) throw new ApiError("upload", response.status);
  }
  ingest(event) {
    return this.#post("/v1/ingest", event, event.event_id);
  }
};
async function redeemPairing({ apiUrl, code, computerName, fetchImpl = globalThis.fetch }) {
  if (!PAIRING_CODE.test(code ?? "")) throw new TypeError("Not a pairing code");
  const response = await fetchImpl(`${apiUrl.replace(/\/$/u, "")}/v1/pairings/redeem`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ code, computer_name: computerName }),
    redirect: "error",
    signal: AbortSignal.timeout(TIMEOUT_MS)
  });
  if (!response.ok) throw new ApiError("pairing", response.status);
  const result = await response.json();
  if (!DEVICE_TOKEN.test(result?.token ?? "")) throw new Error("Hearally sent back something unexpected");
  return result.token;
}
var ApiError = class extends Error {
  constructor(step, status2) {
    super(`${step} failed with HTTP ${status2}`);
    this.step = step;
    this.status = status2;
    this.retryable = status2 >= 500 || status2 === 429;
  }
};

// ../collector/src/collect.js
import { readFile as readFile2 } from "node:fs/promises";

// ../collector/src/folder-source.js
import { createHash as createHash2 } from "node:crypto";
import { createReadStream } from "node:fs";
import { readdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";

// ../collector/src/audio.js
var AUDIO_TYPES = Object.freeze({
  ".mp3": "audio/mpeg",
  ".m4a": "audio/mp4",
  ".mp4": "audio/mp4",
  ".wav": "audio/wav",
  ".ogg": "audio/ogg",
  ".opus": "audio/opus",
  ".flac": "audio/flac"
});
var MAX_AUDIO_BYTES = 512 * 1024 * 1024;
function contentTypeFor(fileName) {
  const dot = fileName.lastIndexOf(".");
  if (dot < 0) return null;
  return AUDIO_TYPES[fileName.slice(dot).toLowerCase()] ?? null;
}
function isRecording(fileName) {
  if (fileName.startsWith(".") || fileName.startsWith("~")) return false;
  return contentTypeFor(fileName) !== null;
}

// ../collector/src/folder-source.js
var SETTLE_MILLISECONDS = 3e4;
async function scanFolder(dir, { now = Date.now, settleMs = SETTLE_MILLISECONDS } = {}) {
  const entries = await readdir(dir, { withFileTypes: true });
  const recordings = [];
  const skipped = [];
  for (const entry of entries) {
    if (!entry.isFile() || !isRecording(entry.name)) continue;
    const path4 = join(dir, entry.name);
    const info = await stat(path4);
    if (info.size === 0) {
      skipped.push({ name: entry.name, reason: "empty" });
      continue;
    }
    if (info.size > MAX_AUDIO_BYTES) {
      skipped.push({ name: entry.name, reason: "too_large" });
      continue;
    }
    if (now() - info.mtimeMs < settleMs) {
      skipped.push({ name: entry.name, reason: "still_arriving" });
      continue;
    }
    recordings.push({
      path: path4,
      name: entry.name,
      contentType: contentTypeFor(entry.name),
      byteLength: info.size,
      recordedAt: new Date(info.mtimeMs).toISOString()
    });
  }
  recordings.sort((a, b) => a.recordedAt.localeCompare(b.recordedAt));
  return { recordings, skipped };
}
function hashFile(path4) {
  return new Promise((resolve, reject) => {
    const hash = createHash2("sha256");
    createReadStream(path4).on("error", reject).on("data", (chunk) => hash.update(chunk)).on("end", () => resolve(hash.digest("hex")));
  });
}
async function loadState(stateFile) {
  try {
    const parsed = JSON.parse(await readFile(stateFile, "utf8"));
    if (!Array.isArray(parsed.uploaded)) return /* @__PURE__ */ new Set();
    return new Set(parsed.uploaded.filter((value) => typeof value === "string"));
  } catch (error) {
    if (error.code === "ENOENT") return /* @__PURE__ */ new Set();
    throw error;
  }
}
async function saveState(stateFile, uploaded) {
  const temporary = `${stateFile}.tmp`;
  await writeFile(temporary, JSON.stringify({ uploaded: [...uploaded].sort() }, null, 2));
  await rename(temporary, stateFile);
}

// ../collector/src/collect.js
async function collectOnce({ config, client, log = () => {
}, deps = {} }) {
  const scan = deps.scanFolder ?? scanFolder;
  const hash = deps.hashFile ?? hashFile;
  const readBytes = deps.readBytes ?? ((path4) => readFile2(path4));
  const load = deps.loadState ?? loadState;
  const save = deps.saveState ?? saveState;
  const uploaded = await load(config.stateFile);
  const result = { sent: 0, alreadySent: 0, failed: 0, skipped: 0, unavailable: 0, unpaired: false };
  recorders: for (const recorder2 of config.recorders) {
    const say = (line) => log(`[${recorder2.id}] ${line}`);
    let found;
    try {
      found = await scan(recorder2.folder);
    } catch (error) {
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
        await client.putAudio(upload, await readBytes(recording.path));
        await client.ingest({
          // The account and the object key are not sent: the server derives
          // both from this computer's pairing.
          schema_version: 4,
          event_id: eventId,
          content_type: recording.contentType,
          byte_length: recording.byteLength,
          recorded_at: recording.recordedAt,
          capture_mode: recorder2.captureMode,
          source: "folder",
          recorder_id: recorder2.id,
          recorder_model: recorder2.model
        });
        uploaded.add(eventId);
        await save(config.stateFile, uploaded);
        result.sent += 1;
        say(`sent ${recording.name}`);
      } catch (error) {
        result.failed += 1;
        if (error.status === 401) result.unpaired = true;
        say(`failed ${recording.name}: ${error.message}`);
        if (error.retryable === false) break recorders;
      }
    }
  }
  return result;
}

// ../collector/src/config.js
import { readFileSync as readFileSync2 } from "node:fs";
function required(environment, name) {
  const value = environment[name]?.trim();
  if (!value) throw new Error(`Missing required setting ${name}`);
  return value;
}
var CAPTURE_MODES = /* @__PURE__ */ new Set(["phone_call", "in_person", "unknown"]);
var MODELS = /* @__PURE__ */ new Set(["note", "note_pro", "notepin", "notepin_s", "other"]);
var CANNOT_RECORD_CALLS = /* @__PURE__ */ new Set(["notepin", "notepin_s"]);
var SAFE_ID = /^[A-Za-z0-9._:-]{3,128}$/u;
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
function loadRecorders(environment, readFile3) {
  const file = environment.HEARALLY_RECORDERS_FILE?.trim();
  if (!file) {
    const mode = (environment.HEARALLY_CAPTURE_MODE ?? "").trim();
    if (mode && !CAPTURE_MODES.has(mode)) {
      throw new Error(`HEARALLY_CAPTURE_MODE must be one of ${[...CAPTURE_MODES].join(", ")}`);
    }
    return [
      recorder(
        {
          id: environment.HEARALLY_RECORDER_ID?.trim() || "default",
          model: environment.HEARALLY_RECORDER_MODEL?.trim() || "other",
          folder: required(environment, "HEARALLY_WATCH_DIR"),
          ...mode ? { capture_mode: mode } : {}
        },
        0
      )
    ];
  }
  let parsed;
  try {
    parsed = JSON.parse(readFile3(file, "utf8"));
  } catch (error) {
    throw new Error(`HEARALLY_RECORDERS_FILE could not be read as JSON: ${error.message}`);
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    throw new Error("HEARALLY_RECORDERS_FILE must list at least one recorder");
  }
  return recorderList(parsed);
}
function recorderList(list) {
  if (!Array.isArray(list) || list.length === 0) throw new Error("At least one recorder is needed");
  const recorders = list.map(recorder);
  const ids = /* @__PURE__ */ new Set();
  const folders = /* @__PURE__ */ new Set();
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
function loadConfig(environment = process.env, { readFile: readFile3 = readFileSync2 } = {}) {
  return buildConfig({
    apiUrl: required(environment, "HEARALLY_API_URL"),
    deviceToken: required(environment, "HEARALLY_DEVICE_TOKEN"),
    recorders: loadRecorders(environment, readFile3),
    stateFile: environment.HEARALLY_STATE_FILE?.trim() || ".hearally-collector-state.json"
  });
}
function configFromSettings(settings, { stateFile }) {
  if (!settings || typeof settings !== "object") throw new Error("The saved settings are damaged");
  return buildConfig({
    apiUrl: settings.api_url,
    deviceToken: settings.device_token,
    recorders: recorderList(settings.recorders),
    stateFile
  });
}
function buildConfig({ apiUrl, deviceToken, recorders, stateFile }) {
  const parsedUrl = new URL(apiUrl);
  if (parsedUrl.protocol !== "https:" && parsedUrl.hostname !== "localhost") {
    throw new Error("HEARALLY_API_URL must use HTTPS unless it is localhost");
  }
  if (typeof deviceToken !== "string" || !DEVICE_TOKEN.test(deviceToken)) {
    throw new Error("The device token is missing or damaged. Run setup again to pair this computer.");
  }
  return Object.freeze({
    apiUrl: apiUrl.replace(/\/$/u, ""),
    deviceToken,
    recorders: Object.freeze(recorders),
    stateFile
  });
}

// ../collector/src/places.js
import { homedir } from "node:os";
import path from "node:path";
function places({ platform = process.platform, env = process.env, home = homedir() } = {}) {
  const p = platform === "win32" ? path.win32 : path.posix;
  let dir;
  if (platform === "win32") dir = p.join(env.LOCALAPPDATA || p.join(home, "AppData", "Local"), "Hearally");
  else if (platform === "darwin") dir = p.join(home, "Library", "Application Support", "Hearally");
  else dir = p.join(env.XDG_DATA_HOME || p.join(home, ".local", "share"), "hearally");
  return Object.freeze({
    dir,
    program: p.join(dir, "hearally-collector.mjs"),
    settings: p.join(dir, "settings.json"),
    state: p.join(dir, "sent.json"),
    log: p.join(dir, "activity.log"),
    // Outside the Documents, Desktop and Downloads folders on purpose: macOS
    // asks permission before a background program may read those.
    recordings: p.join(home, "Hearally recordings")
  });
}

// ../collector/src/schedule.js
import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync as writeFileSync2 } from "node:fs";
import path2 from "node:path";
var EVERY_MINUTES = 15;
var TASK_NAME = "Hearally collector";
var AGENT_LABEL = "com.hearally.collector";
var CRON_MARK = "# hearally-collector";
function xml(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}
function shellQuote(value) {
  return `'${String(value).replaceAll("'", `'\\''`)}'`;
}
function windowsTaskXml({ nodePath, programPath }) {
  return `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Sends new Plaud recordings to Hearally. Remove with: node "${xml(programPath)}" uninstall</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT${EVERY_MINUTES}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>conhost.exe</Command>
      <Arguments>--headless "${xml(nodePath)}" "${xml(programPath)}" send</Arguments>
    </Exec>
  </Actions>
</Task>
`;
}
function macAgentPlist({ nodePath, programPath }) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${AGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${xml(nodePath)}</string>
    <string>${xml(programPath)}</string>
    <string>send</string>
  </array>
  <key>StartInterval</key>
  <integer>${EVERY_MINUTES * 60}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/dev/null</string>
  <key>StandardErrorPath</key>
  <string>/dev/null</string>
</dict>
</plist>
`;
}
function cronTable(existing, line) {
  const kept = String(existing ?? "").split("\n").filter((row) => row.trim() !== "" && !row.includes(CRON_MARK));
  if (line) kept.push(line);
  return kept.length ? `${kept.join("\n")}
` : "";
}
function cronLine({ nodePath, programPath }) {
  return `*/${EVERY_MINUTES} * * * * ${shellQuote(nodePath)} ${shellQuote(programPath)} send >/dev/null 2>&1 ${CRON_MARK}`;
}
function macAgentPath(home) {
  return path2.posix.join(home, "Library", "LaunchAgents", `${AGENT_LABEL}.plist`);
}
function run(command, args, { input, allowFailure = false } = {}) {
  const result = spawnSync(command, args, { input, encoding: "utf8", windowsHide: true });
  if (result.error) throw new Error(`${command} could not be run: ${result.error.message}`);
  if (result.status !== 0 && !allowFailure) {
    throw new Error(`${command} failed: ${(result.stderr || result.stdout || "").trim()}`);
  }
  return result;
}
function install({ platform = process.platform, nodePath, programPath, dir, home, uid: uid2 }) {
  if (platform === "win32") {
    const file = path2.win32.join(dir, "schedule.xml");
    writeFileSync2(file, Buffer.from(`\uFEFF${windowsTaskXml({ nodePath, programPath })}`, "utf16le"));
    run("schtasks", ["/Create", "/F", "/TN", TASK_NAME, "/XML", file]);
    return `Task Scheduler (a task called "${TASK_NAME}")`;
  }
  if (platform === "darwin") {
    const plist = macAgentPath(home);
    mkdirSync(path2.posix.dirname(plist), { recursive: true });
    writeFileSync2(plist, macAgentPlist({ nodePath, programPath }));
    run("launchctl", ["bootout", `gui/${uid2}/${AGENT_LABEL}`], { allowFailure: true });
    run("launchctl", ["bootstrap", `gui/${uid2}`, plist]);
    return `a launch agent (${AGENT_LABEL})`;
  }
  const current = run("crontab", ["-l"], { allowFailure: true });
  run("crontab", ["-"], { input: cronTable(current.status === 0 ? current.stdout : "", cronLine({ nodePath, programPath })) });
  return "cron";
}
function uninstall({ platform = process.platform, home, uid: uid2 }) {
  if (platform === "win32") {
    run("schtasks", ["/Delete", "/F", "/TN", TASK_NAME], { allowFailure: true });
    return;
  }
  if (platform === "darwin") {
    run("launchctl", ["bootout", `gui/${uid2}/${AGENT_LABEL}`], { allowFailure: true });
    rmSync(macAgentPath(home), { force: true });
    return;
  }
  const current = run("crontab", ["-l"], { allowFailure: true });
  if (current.status === 0 && current.stdout.includes(CRON_MARK)) {
    run("crontab", ["-"], { input: cronTable(current.stdout) });
  }
}
function isInstalled({ platform = process.platform, uid: uid2 }) {
  if (platform === "win32") return run("schtasks", ["/Query", "/TN", TASK_NAME], { allowFailure: true }).status === 0;
  if (platform === "darwin") {
    return run("launchctl", ["print", `gui/${uid2}/${AGENT_LABEL}`], { allowFailure: true }).status === 0;
  }
  const current = run("crontab", ["-l"], { allowFailure: true });
  return current.status === 0 && current.stdout.includes(CRON_MARK);
}

// ../collector/src/setup.js
import { copyFileSync, mkdirSync as mkdirSync2, writeFileSync as writeFileSync3 } from "node:fs";
import path3 from "node:path";
var API_URL = "https://api.hearally.com";
var DASHBOARD = "https://hearally.com/dashboard/integrations/";
var PLAUDS = Object.freeze([
  { model: "note", name: "Plaud Note", folder: "Note", calls: true },
  { model: "note_pro", name: "Plaud Note Pro", folder: "Note Pro", calls: true },
  { model: "notepin", name: "Plaud NotePin", folder: "NotePin", calls: false },
  { model: "notepin_s", name: "Plaud NotePin S", folder: "NotePin S", calls: false }
]);
var USES = Object.freeze([
  { mode: "phone_call", name: "Phone calls" },
  { mode: "in_person", name: "Conversations in the room" },
  { mode: "unknown", name: "Both" }
]);
function computerNameFor(hostname2) {
  const tidy = String(hostname2 ?? "").replace(/\.local$/iu, "").replace(/[^\p{L}\p{N} ._'-]+/gu, " ").replace(/\s+/gu, " ").trim().slice(0, 60);
  return tidy || "My computer";
}
async function askForCode(ask, say) {
  for (; ; ) {
    const answer = (await ask("Paste your pairing code, then press Enter: ")).trim();
    if (PAIRING_CODE.test(answer)) return answer;
    say("That is not a pairing code. It is 32 letters, numbers, dashes and underscores,");
    say(`shown at ${DASHBOARD} after you choose Connect a computer.`);
  }
}
async function choose(ask, say, question, options) {
  say("");
  say(question);
  options.forEach((option, index) => say(`  ${index + 1}  ${option.name}`));
  for (; ; ) {
    const answer = (await ask("Type a number and press Enter: ")).trim();
    const picked = options[Number(answer) - 1];
    if (/^\d+$/u.test(answer) && picked) return picked;
    say(`Please type a number from 1 to ${options.length}.`);
  }
}
async function yesNo(ask, question, byDefault) {
  const answer = (await ask(question)).trim().toLowerCase();
  if (!answer) return byDefault;
  return answer.startsWith("y");
}
function unique(base, taken) {
  let candidate = base;
  for (let n = 2; taken.has(candidate.toLowerCase()); n += 1) candidate = `${base} ${n}`;
  taken.add(candidate.toLowerCase());
  return candidate;
}
async function interview({ ask, say, recordingsDir, pathApi = path3 }) {
  say("Hearally collector setup. Public preview (beta).");
  say("This takes about two minutes. Press Ctrl+C at any time to stop; nothing is saved until the end.");
  say("");
  say(`You need a pairing code: sign in at ${DASHBOARD}`);
  say("and choose Connect a computer. Each code works once, for ten minutes.");
  say("");
  const code = await askForCode(ask, say);
  say("");
  say("Now say which Plaud you have. This only describes it: nothing connects to the");
  say("recorder itself. You will save its recordings into a folder on this computer.");
  const picked = [];
  do {
    const plaud = await choose(ask, say, picked.length ? "Which Plaud is the next one?" : "Which Plaud do you have?", PLAUDS);
    const use = plaud.calls ? await choose(ask, say, `What will you use the ${plaud.name} for?`, USES) : USES[1];
    picked.push({ plaud, captureMode: use.mode });
    say("");
  } while (await yesNo(ask, "Do you have a second Plaud as well? Type y or n, then Enter (n): ", false));
  const folderNames = /* @__PURE__ */ new Set();
  const ids = /* @__PURE__ */ new Set();
  const recorders = [];
  for (const { plaud, captureMode } of picked) {
    const suggested = picked.length === 1 ? recordingsDir : pathApi.join(recordingsDir, unique(plaud.folder, folderNames));
    say("");
    say(`Recordings from the ${plaud.name} will be sent from this folder:`);
    say(`  ${suggested}`);
    const typed = (await ask("Press Enter to use it, or type a different folder: ")).trim().replace(/^"(.*)"$/u, "$1");
    recorders.push({
      id: unique(plaud.model.replaceAll("_", "-"), ids).replaceAll(" ", "-"),
      model: plaud.model,
      folder: typed || suggested,
      capture_mode: captureMode
    });
  }
  recorderList(recorders);
  say("");
  const schedule = await yesNo(
    ask,
    "Send new recordings automatically every 15 minutes while this computer is on? Type y or n, then Enter (y): ",
    true
  );
  return { code, recorders, schedule };
}
async function runSetup({ ask, say, places: places2, hostname: hostname2, program: program2, nodePath, deps }) {
  const answers = await interview({ ask, say, recordingsDir: places2.recordings });
  const { recorders, schedule } = answers;
  let { code } = answers;
  const computerName = computerNameFor(hostname2);
  say("");
  let deviceToken;
  for (; ; ) {
    say(`Connecting "${computerName}" to your Hearally account...`);
    try {
      deviceToken = await deps.redeem(code, computerName);
      say("  Connected.");
      break;
    } catch (error) {
      if (error.status !== 404) {
        throw new Error(`Could not reach Hearally (${error.message}). Nothing was saved; try again in a few minutes.`);
      }
      say("  That code has expired or was already used. Make a new one in your dashboard;");
      say("  your other answers are kept.");
      code = await askForCode(ask, say);
    }
  }
  const settings = { api_url: API_URL, device_token: deviceToken, recorders };
  const mkdir = deps.mkdir ?? ((dir, options) => mkdirSync2(dir, options));
  const write = deps.writeFile ?? ((file, data, options) => writeFileSync3(file, data, options));
  const copy = deps.copyFile ?? ((from, to) => copyFileSync(from, to));
  try {
    mkdir(places2.dir, { recursive: true, mode: 448 });
    write(places2.settings, `${JSON.stringify(settings, null, 2)}
`, { mode: 384 });
  } catch (error) {
    throw new Error(
      `This computer was connected, but its settings could not be saved (${error.message}). Remove "${computerName}" in your dashboard, then run setup again.`
    );
  }
  for (const recorder2 of recorders) mkdir(recorder2.folder, { recursive: true });
  let installed = program2;
  if (path3.basename(program2) === path3.basename(places2.program)) {
    if (path3.resolve(program2) !== path3.resolve(places2.program)) copy(program2, places2.program);
    installed = places2.program;
  }
  say("");
  if (schedule) {
    const how = deps.schedule({ nodePath, programPath: installed });
    say(`Hearally will look for new recordings every 15 minutes, using ${how}.`);
  } else {
    say("Not scheduled. Send recordings yourself whenever you like with:");
    say(`  node "${installed}" send`);
  }
  say("");
  say(`Set up. "${computerName}" is listed in your dashboard, where you can disconnect it at any time.`);
  say("Now, save recordings from the Plaud app into:");
  for (const recorder2 of recorders) say(`  ${recorder2.folder}`);
  say("");
  say("To check it is working, at any time:");
  say(`  node "${installed}" status`);
  return { settings, installed, scheduled: schedule };
}

// ../collector/src/cli.js
var VERSION = "0.3.1";
var HELP = `Hearally collector ${VERSION}. Public preview (beta).

  setup      Connect this computer to your Hearally account. Run this first.
  send       Send any new recordings now. What the schedule runs.
  status     Show what is set up, whether it is connected, and what was sent.
  uninstall  Stop sending and remove the collector from this computer.
`;
var UNPAIRED = `This computer is no longer connected to your Hearally account. To connect it again, make a pairing code at ${DASHBOARD} and run setup.`;
var here = places();
var program = fileURLToPath(import.meta.url);
var uid = typeof process.getuid === "function" ? process.getuid() : void 0;
function loadSettings() {
  if (process.env.HEARALLY_API_URL) return { config: loadConfig(), logFile: null };
  if (!existsSync(here.settings)) {
    throw new Error(`Hearally is not set up on this computer yet. Run: node "${program}" setup`);
  }
  let saved;
  try {
    saved = JSON.parse(readFileSync3(here.settings, "utf8"));
  } catch {
    throw new Error(`The saved settings are damaged. Run setup again: node "${program}" setup`);
  }
  return { config: configFromSettings(saved, { stateFile: here.state }), logFile: here.log };
}
async function send() {
  const { config, logFile } = loadSettings();
  const lines = [];
  const log = (line) => {
    console.log(`[collector] ${line}`);
    lines.push(line);
  };
  let result;
  try {
    result = await collectOnce({ config, client: new HearallyClient(config), log });
    log(
      `sent ${result.sent}, already sent ${result.alreadySent}, skipped ${result.skipped}, failed ${result.failed}, recorders unavailable ${result.unavailable}`
    );
    if (result.unpaired) log(UNPAIRED);
  } catch (error) {
    lines.push(`stopped: ${error.message}`);
    throw error;
  } finally {
    if (logFile) appendLog(logFile, lines);
  }
  if (result.failed > 0) process.exitCode = 1;
}
function elevated() {
  if (process.platform !== "win32") return false;
  return spawnSync2("net", ["session"], { stdio: "ignore", windowsHide: true }).status === 0;
}
async function setup() {
  if (elevated()) {
    console.log("Note: this window is running as administrator. That is not needed, and the");
    console.log("schedule is best set up as you. You can carry on, or close this window and open");
    console.log('PowerShell from the Start menu without "Run as administrator".');
    console.log("");
  }
  const prompt = createInterface({ input: process.stdin, output: process.stdout });
  prompt.on("SIGINT", () => {
    console.log("\nStopped. Nothing was saved.");
    process.exit(130);
  });
  const lines = prompt[Symbol.asyncIterator]();
  try {
    await runSetup({
      ask: async (question) => {
        process.stdout.write(question);
        const { value, done } = await lines.next();
        if (done) throw new Error("Setup stopped before it finished. Nothing was saved.");
        return value;
      },
      say: (line) => console.log(line),
      places: here,
      hostname: hostname(),
      program,
      nodePath: process.execPath,
      deps: {
        redeem: (code, computerName) => redeemPairing({ apiUrl: API_URL, code, computerName }),
        schedule: ({ nodePath, programPath }) => install({ nodePath, programPath, dir: here.dir, home: userInfo().homedir, uid })
      }
    });
  } finally {
    prompt.close();
  }
}
async function status() {
  console.log(`Hearally collector ${VERSION}. Public preview (beta).
`);
  const { config, logFile } = loadSettings();
  try {
    await new HearallyClient(config).checkConnection();
    console.log("Connected to your Hearally account: yes.");
  } catch (error) {
    console.log(error.status === 401 ? UNPAIRED : `Could not reach Hearally just now (${error.message}).`);
  }
  console.log("\nSending recordings from:");
  for (const recorder2 of config.recorders) {
    const missing = existsSync(recorder2.folder) ? "" : "   (this folder does not exist)";
    console.log(`  ${recorder2.folder}${missing}`);
  }
  if (!logFile) return;
  console.log(
    isInstalled({ uid }) ? "\nScheduled: yes, every 15 minutes while this computer is on." : `
Scheduled: no. Send by hand with: node "${program}" send`
  );
  const recent = readLog(logFile).slice(-12);
  console.log(recent.length ? "\nRecent activity:" : "\nNothing has run yet.");
  for (const line of recent) console.log(`  ${line}`);
}
function uninstall2() {
  uninstall({ home: userInfo().homedir, uid });
  rmSync2(here.dir, { recursive: true, force: true });
  console.log("Removed. Nothing more will be sent from this computer.");
  console.log(`To remove it from your account's list too, disconnect it at ${DASHBOARD}`);
  console.log(`Your recordings are untouched, in ${here.recordings}`);
}
var COMMANDS = { send, setup, status, uninstall: uninstall2 };
async function main() {
  const name = process.argv[2] ?? "send";
  const command = COMMANDS[name];
  if (!command) {
    console.log(HELP);
    if (!["help", "--help", "-h"].includes(name)) process.exitCode = 1;
    return;
  }
  await command();
}
main().catch((error) => {
  console.error(`[collector] ${error.message}`);
  process.exitCode = 1;
});
export {
  VERSION
};
