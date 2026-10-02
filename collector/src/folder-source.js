import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { readdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { contentTypeFor, isRecording, MAX_AUDIO_BYTES } from "./audio.js";

/**
 * Finds recordings in a folder that the Plaud app (or the account holder, by
 * hand) exports into. The real Plaud integration plugs in later behind the
 * same shape: something that yields recordings with bytes and metadata.
 */

// A file modified this recently may still be arriving from a sync client.
// Uploading it now would send a truncated recording that looks complete.
export const SETTLE_MILLISECONDS = 30_000;

export async function scanFolder(dir, { now = Date.now, settleMs = SETTLE_MILLISECONDS } = {}) {
  const entries = await readdir(dir, { withFileTypes: true });
  const recordings = [];
  const skipped = [];

  for (const entry of entries) {
    if (!entry.isFile() || !isRecording(entry.name)) continue;

    const path = join(dir, entry.name);
    const info = await stat(path);

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
      path,
      name: entry.name,
      contentType: contentTypeFor(entry.name),
      byteLength: info.size,
      recordedAt: new Date(info.mtimeMs).toISOString(),
    });
  }

  // Oldest first, so the day's list is built in the order things were said.
  recordings.sort((a, b) => a.recordedAt.localeCompare(b.recordedAt));
  return { recordings, skipped };
}

/**
 * SHA-256 of the file's bytes, streamed so a three-hour recording is never held
 * in memory. The hash is the event id: the same recording exported twice, or
 * renamed, is recognised as the same recording and uploaded once.
 */
export function hashFile(path) {
  return new Promise((resolve, reject) => {
    const hash = createHash("sha256");
    createReadStream(path)
      .on("error", reject)
      .on("data", (chunk) => hash.update(chunk))
      .on("end", () => resolve(hash.digest("hex")));
  });
}

export async function loadState(stateFile) {
  try {
    const parsed = JSON.parse(await readFile(stateFile, "utf8"));
    if (!Array.isArray(parsed.uploaded)) return new Set();
    return new Set(parsed.uploaded.filter((value) => typeof value === "string"));
  } catch (error) {
    if (error.code === "ENOENT") return new Set();
    throw error;
  }
}

/**
 * Written to a temporary file and renamed into place, so a crash mid-write
 * leaves the previous state intact rather than a truncated file that would
 * make every recording look new and upload again.
 */
export async function saveState(stateFile, uploaded) {
  const temporary = `${stateFile}.tmp`;
  await writeFile(temporary, JSON.stringify({ uploaded: [...uploaded].sort() }, null, 2));
  await rename(temporary, stateFile);
}
