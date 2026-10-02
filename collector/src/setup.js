import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { PAIRING_CODE } from "./api-client.js";
import { recorderList } from "./config.js";

/**
 * `setup`: a few plain questions, then everything else is done for the person.
 *
 * The first is a pairing code from the dashboard (Integrations, then Connect
 * a computer). It is only redeemed at the end, once everything else is
 * answered, so an abandoned setup leaves nothing paired. If the code has
 * expired by then, setup asks for a fresh one rather than starting again.
 */

export const API_URL = "https://api.hearally.com";
export const DASHBOARD = "https://hearally.com/dashboard/integrations/";

export const PLAUDS = Object.freeze([
  { model: "note", name: "Plaud Note", folder: "Note", calls: true },
  { model: "note_pro", name: "Plaud Note Pro", folder: "Note Pro", calls: true },
  { model: "notepin", name: "Plaud NotePin", folder: "NotePin", calls: false },
  { model: "notepin_s", name: "Plaud NotePin S", folder: "NotePin S", calls: false },
]);

const USES = Object.freeze([
  { mode: "phone_call", name: "Phone calls" },
  { mode: "in_person", name: "Conversations in the room" },
  { mode: "unknown", name: "Both" },
]);

/**
 * How this computer is listed in the dashboard, so someone with two can tell
 * which one to disconnect: its own name, tidied.
 */
export function computerNameFor(hostname) {
  const tidy = String(hostname ?? "")
    .replace(/\.local$/iu, "")
    .replace(/[^\p{L}\p{N} ._'-]+/gu, " ")
    .replace(/\s+/gu, " ")
    .trim()
    .slice(0, 60);
  return tidy || "My computer";
}

async function askForCode(ask, say) {
  for (;;) {
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
  for (;;) {
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

/**
 * The questions. Returns the answers, and touches nothing: not the disk, not
 * the network.
 */
export async function interview({ ask, say, recordingsDir, pathApi = path }) {
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
    // A NotePin cannot record calls, so there is nothing to ask.
    const use = plaud.calls ? await choose(ask, say, `What will you use the ${plaud.name} for?`, USES) : USES[1];
    picked.push({ plaud, captureMode: use.mode });
    say("");
  } while (await yesNo(ask, "Do you have a second Plaud as well? Type y or n, then Enter (n): ", false));

  // One Plaud: one folder. Several: a folder each inside it, because a
  // recording file does not say which device made it.
  const folderNames = new Set();
  const ids = new Set();
  const recorders = [];
  for (const { plaud, captureMode } of picked) {
    const suggested =
      picked.length === 1 ? recordingsDir : pathApi.join(recordingsDir, unique(plaud.folder, folderNames));
    say("");
    say(`Recordings from the ${plaud.name} will be sent from this folder:`);
    say(`  ${suggested}`);
    const typed = (await ask("Press Enter to use it, or type a different folder: ")).trim().replace(/^"(.*)"$/u, "$1");
    recorders.push({
      id: unique(plaud.model.replaceAll("_", "-"), ids).replaceAll(" ", "-"),
      model: plaud.model,
      folder: typed || suggested,
      capture_mode: captureMode,
    });
  }
  // The same rules the collector applies when it runs, so a problem is found now.
  recorderList(recorders);

  say("");
  const schedule = await yesNo(
    ask,
    "Send new recordings automatically every 15 minutes while this computer is on? Type y or n, then Enter (y): ",
    true,
  );

  return { code, recorders, schedule };
}

/**
 * Pairs, saves, installs and schedules. Each outside effect comes in through
 * `deps`, so the whole sequence can be tested without touching the computer.
 */
export async function runSetup({ ask, say, places, hostname, program, nodePath, deps }) {
  const answers = await interview({ ask, say, recordingsDir: places.recordings });
  const { recorders, schedule } = answers;
  let { code } = answers;
  const computerName = computerNameFor(hostname);

  say("");
  let deviceToken;
  for (;;) {
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

  const mkdir = deps.mkdir ?? ((dir, options) => mkdirSync(dir, options));
  const write = deps.writeFile ?? ((file, data, options) => writeFileSync(file, data, options));
  const copy = deps.copyFile ?? ((from, to) => copyFileSync(from, to));

  // Owner-only: the settings hold this computer's device token.
  try {
    mkdir(places.dir, { recursive: true, mode: 0o700 });
    write(places.settings, `${JSON.stringify(settings, null, 2)}\n`, { mode: 0o600 });
  } catch (error) {
    // Paired on the server, but with nothing saved here the token is lost.
    throw new Error(
      `This computer was connected, but its settings could not be saved (${error.message}). ` +
        `Remove "${computerName}" in your dashboard, then run setup again.`,
    );
  }
  for (const recorder of recorders) mkdir(recorder.folder, { recursive: true });

  // Run from the downloaded file, install a copy, so the download can go.
  // Run from a checkout of the source, schedule the checkout as it is.
  let installed = program;
  if (path.basename(program) === path.basename(places.program)) {
    if (path.resolve(program) !== path.resolve(places.program)) copy(program, places.program);
    installed = places.program;
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
  for (const recorder of recorders) say(`  ${recorder.folder}`);
  say("");
  say("To check it is working, at any time:");
  say(`  node "${installed}" status`);
  return { settings, installed, scheduled: schedule };
}
