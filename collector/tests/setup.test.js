import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { computerNameFor, interview, runSetup } from "../src/setup.js";

const CODE = "Pq7_x-2mZr9KfA3bLw8YcT1dVn6HsE4u";
const FRESH = "Zz9_y-1aBc2DeF3gHi4JkL5mNo6PqR7s";
const TOKEN = `hd1.3f1c2a90-1111-4222-8333-944455556666.${"a".repeat(32)}.${"s".repeat(43)}`;
const RECORDINGS = path.join("home", "Hearally recordings");

/** Answers questions in order, and fails the test if more are asked. */
function script(answers) {
  const queue = [...answers];
  const asked = [];
  return {
    asked,
    said: [],
    ask: async (question) => {
      asked.push(question);
      if (!queue.length) throw new Error(`Unexpected question: ${question}`);
      return queue.shift();
    },
    get remaining() {
      return queue.length;
    },
  };
}

function run(answers) {
  const s = script(answers);
  const done = interview({ ask: s.ask, say: (line) => s.said.push(line), recordingsDir: RECORDINGS });
  return { s, done };
}

test("one Note Pro used for calls: code, model, use, another, folder, schedule", async () => {
  const { s, done } = run([CODE, "2", "1", "n", "", ""]);
  const answers = await done;
  assert.equal(s.remaining, 0);
  assert.equal(answers.code, CODE);
  assert.equal(answers.schedule, true, "scheduling is the default");
  assert.deepEqual(answers.recorders, [
    { id: "note-pro", model: "note_pro", folder: RECORDINGS, capture_mode: "phone_call" },
  ]);
});

test("the questions start by saying where the pairing code comes from", async () => {
  const { s, done } = run([CODE, "3", "", "", ""]);
  await done;
  assert.ok(s.said.some((line) => line.includes("hearally.com/dashboard/integrations")));
  assert.ok(s.said.some((line) => /Connect a computer/u.test(line)));
});

test("a NotePin is not asked about phone calls: it cannot record them", async () => {
  const { s, done } = run([CODE, "3", "", "", ""]);
  const { recorders } = await done;
  assert.equal(recorders[0].capture_mode, "in_person");
  assert.ok(!s.asked.some((q) => /use the/u.test(q)));
});

test("something that is not a pairing code is explained and asked for again", async () => {
  const { s, done } = run(["hc1_old-style-code", "my password", CODE, "1", "3", "", "", "n"]);
  const answers = await done;
  assert.equal(s.said.filter((line) => /not a pairing code/u.test(line)).length, 2);
  assert.equal(answers.recorders[0].capture_mode, "unknown", "'both' is sent as unknown");
  assert.equal(answers.schedule, false);
});

test("a number out of range is asked again", async () => {
  const { s, done } = run([CODE, "9", "0", "abc", "4", "", "", ""]);
  const { recorders } = await done;
  assert.equal(recorders[0].model, "notepin_s");
  assert.equal(s.said.filter((line) => /from 1 to 4/u.test(line)).length, 3);
});

test("two Plauds get a folder each, and two of the same get distinct names", async () => {
  const { done } = run([CODE, "2", "1", "y", "2", "2", "", "", "", ""]);
  const { recorders } = await done;
  assert.deepEqual(
    recorders.map((r) => [r.id, r.folder]),
    [
      ["note-pro", path.join(RECORDINGS, "Note Pro")],
      ["note-pro-2", path.join(RECORDINGS, "Note Pro 2")],
    ],
  );
});

test("a folder typed in quotes, as a dragged-in path often is, is unquoted", async () => {
  const { done } = run([CODE, "3", "n", '"D:\\Plaud"', ""]);
  const { recorders } = await done;
  assert.equal(recorders[0].folder, "D:\\Plaud");
});

test("the same folder for two Plauds is refused before anything happens", async () => {
  const { done } = run([CODE, "3", "y", "3", "n", "/same", "/same"]);
  await assert.rejects(done, /share the folder/u);
});

test("the computer is listed under its own name, tidied", () => {
  assert.equal(computerNameFor("Aarons-MacBook-Pro.local"), "Aarons-MacBook-Pro");
  assert.equal(computerNameFor("DESKTOP-7Q2\u0000X"), "DESKTOP-7Q2 X");
  assert.equal(computerNameFor("x".repeat(80)).length, 60);
  assert.equal(computerNameFor(""), "My computer");
});

function setupDeps(overrides = {}) {
  const effects = [];
  return {
    effects,
    deps: {
      redeem: async (code, name) => {
        effects.push(`redeem ${code} as ${name}`);
        return TOKEN;
      },
      mkdir: (dir) => effects.push(`mkdir ${dir}`),
      writeFile: (file, data, options) => effects.push({ file, data, mode: options.mode }),
      copyFile: (from, to) => effects.push(`copy ${from} -> ${to}`),
      schedule: ({ programPath }) => {
        effects.push(`schedule ${programPath}`);
        return "a test schedule";
      },
      ...overrides,
    },
  };
}

const PLACES = {
  dir: path.join("app"),
  program: path.join("app", "hearally-collector.mjs"),
  settings: path.join("app", "settings.json"),
  recordings: RECORDINGS,
};

function setup(answers, deps, program = path.join("Downloads", "hearally-collector.mjs")) {
  const s = script(answers);
  const done = runSetup({
    ask: s.ask,
    say: (line) => s.said.push(line),
    places: PLACES,
    hostname: "Kitchen-Laptop.local",
    program,
    nodePath: "node",
    deps,
  });
  return { s, done };
}

test("setup pairs, saves the token privately, installs a copy and schedules it", async () => {
  const { effects, deps } = setupDeps();
  const { s, done } = setup([CODE, "3", "", "", ""], deps);
  await done;

  assert.equal(effects[0], `redeem ${CODE} as Kitchen-Laptop`, "paired before anything is written");
  const saved = effects.find((e) => e.file === PLACES.settings);
  assert.equal(saved.mode, 0o600);
  assert.deepEqual(Object.keys(JSON.parse(saved.data)).sort(), ["api_url", "device_token", "recorders"]);
  assert.equal(JSON.parse(saved.data).device_token, TOKEN);
  assert.ok(effects.includes(`copy ${path.join("Downloads", "hearally-collector.mjs")} -> ${PLACES.program}`));
  assert.ok(effects.includes(`schedule ${PLACES.program}`), "the installed copy is scheduled, not the download");
  assert.ok(effects.includes(`mkdir ${RECORDINGS}`), "the recordings folder is made");
  assert.ok(!s.said.join("\n").includes("s".repeat(43)), "the token is never shown");
});

test("an expired code asks for a fresh one and keeps every other answer", async () => {
  let attempts = 0;
  const { effects, deps } = setupDeps({
    redeem: async (code) => {
      attempts += 1;
      if (code === CODE) throw Object.assign(new Error("pairing failed with HTTP 404"), { status: 404 });
      return TOKEN;
    },
  });
  const { s, done } = setup([CODE, "2", "1", "n", "", "", FRESH], deps);
  await done;
  assert.equal(attempts, 2);
  assert.equal(s.remaining, 0, "only the code was asked again");
  assert.ok(s.said.some((line) => /expired or was already used/u.test(line)));
  assert.ok(effects.some((e) => e.file === PLACES.settings));
});

test("Hearally unreachable during pairing saves nothing", async () => {
  const { effects, deps } = setupDeps({ redeem: async () => Promise.reject(new Error("fetch failed")) });
  const { done } = setup([CODE, "3", "", "", ""], deps);
  await assert.rejects(done, /Could not reach Hearally.*Nothing was saved/u);
  assert.deepEqual(effects, []);
});

test("paired but unable to save: says exactly how to recover", async () => {
  const { deps } = setupDeps({
    writeFile: () => {
      throw new Error("EACCES: permission denied");
    },
  });
  const { done } = setup([CODE, "3", "", "", ""], deps);
  await assert.rejects(done, /Remove "Kitchen-Laptop" in your dashboard, then run setup again/u);
});

test("declining the schedule saves but does not schedule", async () => {
  const { effects, deps } = setupDeps();
  await setup([CODE, "3", "", "", "n"], deps).done;
  assert.ok(effects.some((e) => e.file === PLACES.settings));
  assert.ok(!effects.some((e) => typeof e === "string" && e.startsWith("schedule")));
});

test("run from a source checkout, the checkout itself is scheduled and nothing copied", async () => {
  const { effects, deps } = setupDeps();
  const source = path.join("repo", "collector", "src", "cli.js");
  await setup([CODE, "3", "", "", ""], deps, source).done;
  assert.ok(!effects.some((e) => typeof e === "string" && e.startsWith("copy")));
  assert.ok(effects.includes(`schedule ${source}`));
});

test("the version the program reports matches package.json", () => {
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  const cli = readFileSync(new URL("../src/cli.js", import.meta.url), "utf8");
  assert.ok(cli.includes(`VERSION = "${pkg.version}"`));
});
