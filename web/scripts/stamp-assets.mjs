// Stamps each stylesheet and script reference with a hash of its contents.
//
// HTML is cached for five minutes and assets for an hour. Without a stamp, a
// deploy that adds new CSS classes serves new HTML against an old cached
// stylesheet, and the page renders broken for up to an hour. With the stamp,
// a changed file has a new URL, so the browser fetches it straight away, and an
// unchanged one keeps its URL and stays cached.
//
// Runs last in `npm run build`, after Tailwind has written the stylesheet.

import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const publicDir = join(dirname(fileURLToPath(import.meta.url)), "..", "public");

const ASSETS = ["/styles.css", "/dashboard/app.js", "/dashboard/config.js", "/setup/setup.js", "/a/ally.js", "/video.js", "/analytics.js"];

const stamps = Object.fromEntries(
  ASSETS.map((asset) => {
    const bytes = readFileSync(join(publicDir, asset));
    return [asset, createHash("sha256").update(bytes).digest("hex").slice(0, 10)];
  }),
);

function htmlFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return htmlFiles(path);
    return name.endsWith(".html") ? [path] : [];
  });
}

let changed = 0;
for (const file of htmlFiles(publicDir)) {
  const before = readFileSync(file, "utf8");
  let after = before;
  for (const [asset, stamp] of Object.entries(stamps)) {
    const escaped = asset.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
    // Matches the bare path or an older stamp, inside an href or src attribute.
    after = after.replace(new RegExp(`(["'])${escaped}(\\?v=[0-9a-f]+)?\\1`, "g"), `$1${asset}?v=${stamp}$1`);
  }
  if (after !== before) {
    writeFileSync(file, after);
    changed += 1;
  }
}
console.log(`stamped ${changed} page(s):`, stamps);
