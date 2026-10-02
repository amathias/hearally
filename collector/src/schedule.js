import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

/**
 * Runs the collector every 15 minutes, using whatever the system already has
 * for the job: Task Scheduler on Windows, a launch agent on macOS, cron
 * elsewhere. Each runs as the signed-in person, needs no administrator rights,
 * and is removed again by `uninstall`.
 *
 * The plans are plain data so they can be tested on any system; `apply` is
 * the only part that touches this one.
 */

export const EVERY_MINUTES = 15;
export const TASK_NAME = "Hearally collector";
export const AGENT_LABEL = "com.hearally.collector";
export const CRON_MARK = "# hearally-collector";

function xml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", `'\\''`)}'`;
}

/**
 * Task Scheduler, from an XML definition rather than schtasks flags: the flags
 * cannot turn off "only on mains power", and a laptop on battery would
 * otherwise never send anything. conhost --headless stops a console window
 * flashing up every quarter of an hour.
 */
export function windowsTaskXml({ nodePath, programPath }) {
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

export function macAgentPlist({ nodePath, programPath }) {
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

/** The crontab with our line replaced, or removed when `line` is omitted. */
export function cronTable(existing, line) {
  const kept = String(existing ?? "")
    .split("\n")
    .filter((row) => row.trim() !== "" && !row.includes(CRON_MARK));
  if (line) kept.push(line);
  return kept.length ? `${kept.join("\n")}\n` : "";
}

export function cronLine({ nodePath, programPath }) {
  return `*/${EVERY_MINUTES} * * * * ${shellQuote(nodePath)} ${shellQuote(programPath)} send >/dev/null 2>&1 ${CRON_MARK}`;
}

function macAgentPath(home) {
  return path.posix.join(home, "Library", "LaunchAgents", `${AGENT_LABEL}.plist`);
}

function run(command, args, { input, allowFailure = false } = {}) {
  const result = spawnSync(command, args, { input, encoding: "utf8", windowsHide: true });
  if (result.error) throw new Error(`${command} could not be run: ${result.error.message}`);
  if (result.status !== 0 && !allowFailure) {
    throw new Error(`${command} failed: ${(result.stderr || result.stdout || "").trim()}`);
  }
  return result;
}

/** Registers the schedule, replacing any earlier one. Returns a description. */
export function install({ platform = process.platform, nodePath, programPath, dir, home, uid }) {
  if (platform === "win32") {
    const file = path.win32.join(dir, "schedule.xml");
    // Task Scheduler reads its XML as UTF-16, as the declaration says.
    writeFileSync(file, Buffer.from(`﻿${windowsTaskXml({ nodePath, programPath })}`, "utf16le"));
    run("schtasks", ["/Create", "/F", "/TN", TASK_NAME, "/XML", file]);
    return `Task Scheduler (a task called "${TASK_NAME}")`;
  }
  if (platform === "darwin") {
    const plist = macAgentPath(home);
    mkdirSync(path.posix.dirname(plist), { recursive: true });
    writeFileSync(plist, macAgentPlist({ nodePath, programPath }));
    run("launchctl", ["bootout", `gui/${uid}/${AGENT_LABEL}`], { allowFailure: true });
    run("launchctl", ["bootstrap", `gui/${uid}`, plist]);
    return `a launch agent (${AGENT_LABEL})`;
  }
  const current = run("crontab", ["-l"], { allowFailure: true });
  run("crontab", ["-"], { input: cronTable(current.status === 0 ? current.stdout : "", cronLine({ nodePath, programPath })) });
  return "cron";
}

/** Removes the schedule. Quietly does nothing if there is none. */
export function uninstall({ platform = process.platform, home, uid }) {
  if (platform === "win32") {
    run("schtasks", ["/Delete", "/F", "/TN", TASK_NAME], { allowFailure: true });
    return;
  }
  if (platform === "darwin") {
    run("launchctl", ["bootout", `gui/${uid}/${AGENT_LABEL}`], { allowFailure: true });
    rmSync(macAgentPath(home), { force: true });
    return;
  }
  const current = run("crontab", ["-l"], { allowFailure: true });
  if (current.status === 0 && current.stdout.includes(CRON_MARK)) {
    run("crontab", ["-"], { input: cronTable(current.stdout) });
  }
}

/** Whether the schedule is registered on this computer. */
export function isInstalled({ platform = process.platform, uid }) {
  if (platform === "win32") return run("schtasks", ["/Query", "/TN", TASK_NAME], { allowFailure: true }).status === 0;
  if (platform === "darwin") {
    return run("launchctl", ["print", `gui/${uid}/${AGENT_LABEL}`], { allowFailure: true }).status === 0;
  }
  const current = run("crontab", ["-l"], { allowFailure: true });
  return current.status === 0 && current.stdout.includes(CRON_MARK);
}
