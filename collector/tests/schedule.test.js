import assert from "node:assert/strict";
import test from "node:test";

import { appendLog, readLog, stampFor } from "../src/activity-log.js";
import { places } from "../src/places.js";
import { CRON_MARK, cronLine, cronTable, macAgentPlist, windowsTaskXml } from "../src/schedule.js";

const WINDOWS = { nodePath: "C:\\Program Files\\nodejs\\node.exe", programPath: "C:\\Users\\A & B\\Hearally\\hearally-collector.mjs" };

test("the Windows task runs hidden, every 15 minutes, on battery too", () => {
  const task = windowsTaskXml(WINDOWS);
  assert.match(task, /<Interval>PT15M<\/Interval>/u);
  assert.match(task, /<DisallowStartIfOnBatteries>false</u);
  assert.match(task, /<Command>conhost.exe<\/Command>/u);
  assert.ok(task.includes('--headless "C:\\Program Files\\nodejs\\node.exe" "C:\\Users\\A &amp; B'));
  assert.match(task, /A &amp; B/u, "paths are escaped for XML");
  assert.match(task, /<RunLevel>LeastPrivilege<\/RunLevel>/u);
});

test("the macOS agent runs the installed program every 15 minutes", () => {
  const plist = macAgentPlist({ nodePath: "/usr/local/bin/node", programPath: "/Users/a/Library/Application Support/Hearally/hearally-collector.mjs" });
  assert.match(plist, /<integer>900<\/integer>/u);
  assert.match(plist, /<string>send<\/string>/u);
  assert.match(plist, /Application Support\/Hearally\/hearally-collector.mjs/u);
});

test("cron: our line replaces an earlier one and leaves the person's own alone", () => {
  const line = cronLine({ nodePath: "/usr/bin/node", programPath: "/home/o'neil/hearally-collector.mjs" });
  assert.match(line, /^\*\/15 \* \* \* \* /u);
  assert.match(line, /'\/home\/o'\\''neil\/hearally-collector.mjs'/u, "quoted for the shell");

  const before = `0 3 * * * backup\n*/15 * * * * old ${CRON_MARK}\n`;
  const after = cronTable(before, line);
  assert.equal(after, `0 3 * * * backup\n${line}\n`);
  assert.equal(cronTable(after), "0 3 * * * backup\n", "uninstall removes only ours");
  assert.equal(cronTable(`*/15 x ${CRON_MARK}\n`), "", "an empty table stays empty");
});

test("each system keeps the collector in its usual private place", () => {
  const win = places({ platform: "win32", env: { LOCALAPPDATA: "C:\\Users\\a\\AppData\\Local" }, home: "C:\\Users\\a" });
  assert.equal(win.settings, "C:\\Users\\a\\AppData\\Local\\Hearally\\settings.json");
  assert.equal(win.recordings, "C:\\Users\\a\\Hearally recordings");

  const mac = places({ platform: "darwin", env: {}, home: "/Users/a" });
  assert.equal(mac.program, "/Users/a/Library/Application Support/Hearally/hearally-collector.mjs");

  const linux = places({ platform: "linux", env: {}, home: "/home/a" });
  assert.equal(linux.state, "/home/a/.local/share/hearally/sent.json");
  assert.equal(places({ platform: "linux", env: { XDG_DATA_HOME: "/data" }, home: "/home/a" }).dir, "/data/hearally");
});

test("the activity log is stamped in local time and trimmed", async (t) => {
  const { mkdtempSync, rmSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const dir = mkdtempSync(join(tmpdir(), "hearally-log-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = join(dir, "activity.log");

  assert.deepEqual(readLog(file), [], "no log yet is not an error");
  const now = () => new Date(2026, 8, 24, 7, 5);
  appendLog(file, ["one", "two", "three"], { now, keep: 2 });
  assert.deepEqual(readLog(file), ["2026-09-24 07:05  two", "2026-09-24 07:05  three"]);
  appendLog(file, [], { now });
  assert.equal(readLog(file).length, 2, "an empty run adds nothing");
  assert.equal(stampFor(new Date(2026, 0, 2, 3, 4)), "2026-01-02 03:04");
});
