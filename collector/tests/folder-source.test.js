import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { hashFile, loadState, saveState, scanFolder } from "../src/folder-source.js";

async function withFolder(fn) {
  const dir = await mkdtemp(join(tmpdir(), "hearally-collector-"));
  try {
    await fn(dir);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

// Backdate a file so the settle window treats it as finished arriving.
async function settle(path, secondsAgo = 120) {
  const when = new Date(Date.now() - secondsAgo * 1_000);
  await utimes(path, when, when);
}

test("finds settled recordings and ignores everything else", async () => {
  await withFolder(async (dir) => {
    await writeFile(join(dir, "call.m4a"), "audio-bytes");
    await writeFile(join(dir, "notes.txt"), "not audio");
    await writeFile(join(dir, "._call.m4a"), "mac metadata");
    await settle(join(dir, "call.m4a"));
    await settle(join(dir, "notes.txt"));
    await settle(join(dir, "._call.m4a"));

    const { recordings } = await scanFolder(dir);
    assert.deepEqual(
      recordings.map((r) => r.name),
      ["call.m4a"],
    );
    assert.equal(recordings[0].contentType, "audio/mp4");
    assert.equal(recordings[0].byteLength, "audio-bytes".length);
  });
});

test("holds back a file that may still be arriving", async () => {
  await withFolder(async (dir) => {
    await writeFile(join(dir, "fresh.m4a"), "partial");
    const { recordings, skipped } = await scanFolder(dir);
    assert.equal(recordings.length, 0);
    assert.deepEqual(skipped, [{ name: "fresh.m4a", reason: "still_arriving" }]);
  });
});

test("skips an empty file rather than uploading nothing", async () => {
  await withFolder(async (dir) => {
    await writeFile(join(dir, "empty.wav"), "");
    await settle(join(dir, "empty.wav"));
    const { recordings, skipped } = await scanFolder(dir);
    assert.equal(recordings.length, 0);
    assert.equal(skipped[0].reason, "empty");
  });
});

test("returns recordings oldest first", async () => {
  await withFolder(async (dir) => {
    await writeFile(join(dir, "b.m4a"), "second");
    await writeFile(join(dir, "a.m4a"), "first");
    await settle(join(dir, "b.m4a"), 60);
    await settle(join(dir, "a.m4a"), 600);

    const { recordings } = await scanFolder(dir);
    assert.deepEqual(
      recordings.map((r) => r.name),
      ["a.m4a", "b.m4a"],
    );
  });
});

test("the same bytes hash the same under a different name", async () => {
  // Content, not file name, identifies a recording. Renaming or re-exporting
  // must not cause a second upload.
  await withFolder(async (dir) => {
    await writeFile(join(dir, "one.m4a"), "identical audio");
    await writeFile(join(dir, "two.m4a"), "identical audio");
    await writeFile(join(dir, "three.m4a"), "different audio");
    const [one, two, three] = await Promise.all(
      ["one.m4a", "two.m4a", "three.m4a"].map((name) => hashFile(join(dir, name))),
    );
    assert.equal(one, two);
    assert.notEqual(one, three);
    assert.match(one, /^[0-9a-f]{64}$/);
  });
});

test("state starts empty and survives a round trip", async () => {
  await withFolder(async (dir) => {
    const stateFile = join(dir, "state.json");
    assert.equal((await loadState(stateFile)).size, 0);

    await saveState(stateFile, new Set(["b", "a"]));
    assert.deepEqual([...(await loadState(stateFile))].sort(), ["a", "b"]);

    // Written sorted, so the file diffs cleanly if anyone inspects it.
    assert.deepEqual(JSON.parse(await readFile(stateFile, "utf8")).uploaded, ["a", "b"]);
  });
});

test("state with an unexpected shape is treated as empty, not a crash", async () => {
  await withFolder(async (dir) => {
    const stateFile = join(dir, "state.json");
    await writeFile(stateFile, JSON.stringify({ uploaded: "not-a-list" }));
    assert.equal((await loadState(stateFile)).size, 0);
  });
});
