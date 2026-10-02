import { readFileSync, writeFileSync } from "node:fs";

/**
 * What the scheduled runs did, for `status` to show. File names and counts
 * only, the same as the terminal output: never anything that was said.
 * Trimmed to the most recent lines so it cannot grow without end.
 */

export const KEEP_LINES = 500;

export function readLog(file) {
  try {
    return readFileSync(file, "utf8").split("\n").filter(Boolean);
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

/** Local time, since the person reading it is thinking in their own. */
export function stampFor(date) {
  const two = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}`;
}

export function appendLog(file, lines, { now = () => new Date(), keep = KEEP_LINES } = {}) {
  if (!lines.length) return;
  const stamp = stampFor(now());
  const all = [...readLog(file), ...lines.map((line) => `${stamp}  ${line}`)].slice(-keep);
  writeFileSync(file, `${all.join("\n")}\n`, { mode: 0o600 });
}
