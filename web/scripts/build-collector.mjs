// Builds the collector into one file people download from hearally.com.
//
// The collector is several small modules with no dependencies. Bundled, it is
// a single file that runs with `node hearally-collector.mjs setup`: nothing to
// unzip, no repository, no package manager. See /setup/.
//
// Runs in `npm run build`, so the download always matches the source, and CI
// fails if a committed copy is stale.

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const outfile = join(root, "web", "public", "collector", "hearally-collector.mjs");
const { version } = JSON.parse(readFileSync(join(root, "collector", "package.json"), "utf8"));

await build({
  entryPoints: [join(root, "collector", "src", "cli.js")],
  outfile,
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  // Readable on purpose: someone deciding whether to run it can read it.
  minify: false,
  legalComments: "none",
  banner: {
    js: [
      `// Hearally collector ${version}. Public preview (beta). MIT licence.`,
      "// Source: collector/ in the Hearally repository. Built by web/scripts/build-collector.mjs.",
      "// Run: node hearally-collector.mjs setup",
    ].join("\n"),
  },
  logLevel: "warning",
});

const digest = createHash("sha256").update(readFileSync(outfile)).digest("hex");
console.log(`collector ${version} -> public/collector/hearally-collector.mjs sha256 ${digest}`);
