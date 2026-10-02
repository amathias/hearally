import { homedir } from "node:os";
import path from "node:path";

/**
 * Where the collector keeps itself on this computer once `setup` has run.
 *
 * The usual place for per-user application data on each system, so it is
 * private to the person who ran setup and out of the way of their own files.
 * Everything lives here, whichever copy of the program is run, so the file
 * someone downloaded can be deleted afterwards.
 */
export function places({ platform = process.platform, env = process.env, home = homedir() } = {}) {
  const p = platform === "win32" ? path.win32 : path.posix;
  let dir;
  if (platform === "win32") dir = p.join(env.LOCALAPPDATA || p.join(home, "AppData", "Local"), "Hearally");
  else if (platform === "darwin") dir = p.join(home, "Library", "Application Support", "Hearally");
  else dir = p.join(env.XDG_DATA_HOME || p.join(home, ".local", "share"), "hearally");

  return Object.freeze({
    dir,
    program: p.join(dir, "hearally-collector.mjs"),
    settings: p.join(dir, "settings.json"),
    state: p.join(dir, "sent.json"),
    log: p.join(dir, "activity.log"),
    // Outside the Documents, Desktop and Downloads folders on purpose: macOS
    // asks permission before a background program may read those.
    recordings: p.join(home, "Hearally recordings"),
  });
}
