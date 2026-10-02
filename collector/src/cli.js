#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { hostname, userInfo } from "node:os";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";

import { appendLog, readLog } from "./activity-log.js";
import { HearallyClient, redeemPairing } from "./api-client.js";
import { collectOnce } from "./collect.js";
import { configFromSettings, loadConfig } from "./config.js";
import { places } from "./places.js";
import * as schedule from "./schedule.js";
import { API_URL, DASHBOARD, runSetup } from "./setup.js";

export const VERSION = "0.3.1";

const HELP = `Hearally collector ${VERSION}. Public preview (beta).

  setup      Connect this computer to your Hearally account. Run this first.
  send       Send any new recordings now. What the schedule runs.
  status     Show what is set up, whether it is connected, and what was sent.
  uninstall  Stop sending and remove the collector from this computer.
`;

const UNPAIRED =
  "This computer is no longer connected to your Hearally account. " +
  `To connect it again, make a pairing code at ${DASHBOARD} and run setup.`;

const here = places();
const program = fileURLToPath(import.meta.url);
const uid = typeof process.getuid === "function" ? process.getuid() : undefined;

/**
 * A developer runs from a checkout with a .env (node --env-file=.env ...);
 * everyone else has the settings `setup` saved.
 */
function loadSettings() {
  if (process.env.HEARALLY_API_URL) return { config: loadConfig(), logFile: null };
  if (!existsSync(here.settings)) {
    throw new Error(`Hearally is not set up on this computer yet. Run: node "${program}" setup`);
  }
  let saved;
  try {
    saved = JSON.parse(readFileSync(here.settings, "utf8"));
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
      `sent ${result.sent}, already sent ${result.alreadySent}, ` +
        `skipped ${result.skipped}, failed ${result.failed}, ` +
        `recorders unavailable ${result.unavailable}`,
    );
    if (result.unpaired) log(UNPAIRED);
  } catch (error) {
    lines.push(`stopped: ${error.message}`);
    throw error;
  } finally {
    // The schedule runs with nowhere to print, so `status` reads this instead.
    if (logFile) appendLog(logFile, lines);
  }
  if (result.failed > 0) process.exitCode = 1;
}

/**
 * Windows only: whether this window was opened with "Run as administrator".
 * `net session` succeeds only when elevated. The first real setup ran in an
 * administrator window that opened in C:\windows\system32, where the
 * documented command could not find the download.
 */
function elevated() {
  if (process.platform !== "win32") return false;
  return spawnSync("net", ["session"], { stdio: "ignore", windowsHide: true }).status === 0;
}

async function setup() {
  if (elevated()) {
    console.log("Note: this window is running as administrator. That is not needed, and the");
    console.log("schedule is best set up as you. You can carry on, or close this window and open");
    console.log("PowerShell from the Start menu without \"Run as administrator\".");
    console.log("");
  }
  const prompt = createInterface({ input: process.stdin, output: process.stdout });
  // Without a listener, readline treats Ctrl+C as "pause", not "stop".
  prompt.on("SIGINT", () => {
    console.log("\nStopped. Nothing was saved.");
    process.exit(130);
  });
  // An iterator rather than prompt.question(): it buffers, so answers typed
  // or pasted ahead of the question are not lost.
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
        schedule: ({ nodePath, programPath }) =>
          schedule.install({ nodePath, programPath, dir: here.dir, home: userInfo().homedir, uid }),
      },
    });
  } finally {
    prompt.close();
  }
}

async function status() {
  console.log(`Hearally collector ${VERSION}. Public preview (beta).\n`);
  const { config, logFile } = loadSettings();

  try {
    await new HearallyClient(config).checkConnection();
    console.log("Connected to your Hearally account: yes.");
  } catch (error) {
    console.log(error.status === 401 ? UNPAIRED : `Could not reach Hearally just now (${error.message}).`);
  }

  console.log("\nSending recordings from:");
  for (const recorder of config.recorders) {
    const missing = existsSync(recorder.folder) ? "" : "   (this folder does not exist)";
    console.log(`  ${recorder.folder}${missing}`);
  }
  if (!logFile) return;
  console.log(
    schedule.isInstalled({ uid })
      ? "\nScheduled: yes, every 15 minutes while this computer is on."
      : `\nScheduled: no. Send by hand with: node "${program}" send`,
  );
  const recent = readLog(logFile).slice(-12);
  console.log(recent.length ? "\nRecent activity:" : "\nNothing has run yet.");
  for (const line of recent) console.log(`  ${line}`);
}

function uninstall() {
  schedule.uninstall({ home: userInfo().homedir, uid });
  rmSync(here.dir, { recursive: true, force: true });
  console.log("Removed. Nothing more will be sent from this computer.");
  console.log(`To remove it from your account's list too, disconnect it at ${DASHBOARD}`);
  console.log(`Your recordings are untouched, in ${here.recordings}`);
}

const COMMANDS = { send, setup, status, uninstall };

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
  // The message only. A stack trace from a config error can include the
  // settings it was reading, and the device token lives there.
  console.error(`[collector] ${error.message}`);
  process.exitCode = 1;
});
