// Generates the signed-in dashboard's pages from one layout.
//
// Each section is a real page at its own URL (/dashboard/people/ and so on),
// with the current page marked in the markup rather than guessed by script
// after load. The data itself is fetched by app.js once the person is signed in.
//
// Run by `npm run build`, before Tailwind, so Tailwind sees every class used.

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "dashboard");

export const NAV = [
  { group: null, items: [{ id: "overview", label: "Today", path: "" }] },
  {
    group: "What’s happening",
    items: [
      { id: "activity", label: "Activity", path: "activity/" },
      { id: "summaries", label: "Summaries", path: "summaries/" },
    ],
  },
  {
    group: "Your setup",
    items: [
      { id: "people", label: "People", path: "people/" },
      { id: "integrations", label: "Integrations", path: "integrations/" },
      { id: "preferences", label: "Preferences", path: "preferences/" },
    ],
  },
  { group: "Account", items: [{ id: "account", label: "Account & billing", path: "account/" }] },
];

const PAGES = {
  overview: {
    title: "Today",
    intro: "What has come in, and what Hearally can do for you so far.",
  },
  activity: {
    title: "Activity",
    intro: "Heads-ups Hearally raised, and every message sent to your allies.",
  },
  summaries: {
    title: "Summaries",
    intro: "Short summaries of things that were said, the same ones your allies may see.",
  },
  people: {
    title: "People",
    intro: "Your allies: the people you chose to hear from Hearally, and what each one hears.",
  },
  integrations: {
    title: "Integrations",
    intro: "How your recordings reach Hearally, and every recording received so far.",
  },
  preferences: {
    title: "Preferences",
    intro: "What never leaves, and how the dignity filter treats private moments.",
  },
  account: {
    title: "Account & billing",
    intro: "Your sign-in, what you have agreed to, and how Hearally is paid for.",
  },
};

const escape = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function head(title) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${escape(title)} — Hearally</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&amp;family=Inter:wght@400;500;600;700&amp;display=swap" rel="stylesheet">
<link rel="stylesheet" href="/styles.css">
</head>`;
}

const MARK = `<svg width="40" height="28" viewBox="-60 -50 140 100" aria-hidden="true" class="shrink-0">
      <circle cx="-20" cy="-13" r="11" fill="#F6F1E8"/>
      <path d="M -38 17 A 18 18 0 0 1 -2 17" fill="none" stroke="#F6F1E8" stroke-width="8.5" stroke-linecap="round"/>
      <circle cx="8" cy="-8" r="9" fill="#E8934A"/>
      <path d="M -6 19 A 14 14 0 0 1 22 19" fill="none" stroke="#E8934A" stroke-width="7.5" stroke-linecap="round"/>
      <path d="M 38 -20 A 26 26 0 0 1 38 20" fill="none" stroke="#F6F1E8" stroke-width="6" stroke-linecap="round"/>
      <path d="M 54 -32 A 42 42 0 0 1 54 32" fill="none" stroke="#E8934A" stroke-width="6" stroke-linecap="round"/>
    </svg>`;

function nav(current) {
  const groups = NAV.map(({ group, items }) => {
    const links = items
      .map(({ id, label, path }) => {
        const active = id === current;
        const cls = active
          ? "block rounded-md border-l-[3px] border-teal bg-paper px-3 py-2 font-semibold text-ink"
          : "block rounded-md border-l-[3px] border-transparent px-3 py-2 text-muted hover:bg-paper hover:text-ink";
        return `<li><a href="/dashboard/${path}" class="${cls}"${active ? ' aria-current="page"' : ""}>${escape(label)}</a></li>`;
      })
      .join("\n          ");
    const heading = group
      ? `<p class="px-3 pt-6 pb-2 font-sans text-[11px] font-bold uppercase tracking-[0.12em] text-muted">${escape(group)}</p>`
      : "";
    return `${heading}
        <ul class="space-y-0.5">
          ${links}
        </ul>`;
  }).join("\n");

  return `<nav id="dashboard-nav" aria-label="Dashboard" class="hidden border-b border-line bg-sand px-3 pb-4 md:block md:w-60 md:shrink-0 md:border-b-0 md:border-r md:pb-8 md:pt-6">
        <p id="nav-signed-in-as" class="hidden break-all px-3 pt-4 font-sans text-sm text-muted md:hidden"></p>
        ${groups}
        <p class="mt-8 px-3 font-sans text-xs text-muted"><a href="/terms/" class="underline underline-offset-4">Terms</a> · <a href="/privacy/" class="underline underline-offset-4">Privacy</a> · <a href="mailto:info@email.famplified.com" class="underline underline-offset-4">Contact</a></p>
      </nav>`;
}

function page(id) {
  const { title, intro } = PAGES[id];
  return `${head(title)}
<body class="dashboard min-h-screen bg-sand text-ink" data-section="${id}">
<a href="#main" class="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-paper focus:px-4 focus:py-2 focus:font-semibold">Skip to content</a>

<header class="flex items-center gap-3 bg-ink px-4 py-3 text-sand md:px-6">
  <a href="/dashboard/" class="flex items-center gap-2.5 no-underline">
    ${MARK}
    <span class="font-sans text-xl font-bold tracking-tight text-amber">hearally</span>
  </a>
  <span class="ml-2 hidden rounded bg-ink-2 px-2 py-0.5 font-sans text-xs font-semibold text-muted-dark sm:inline">Public preview (beta)</span>
  <div class="ml-auto flex min-w-0 items-center gap-3">
    <span class="hidden min-w-0 max-w-[26rem] truncate font-sans text-sm text-muted-dark sm:block"><span id="signed-in-as" class="hidden"></span></span>
    <button id="sign-out" type="button" hidden class="rounded-md border border-line-dark px-3 py-1.5 font-sans text-sm font-semibold text-sand hover:bg-ink-2">Sign out</button>
    <button id="menu-toggle" type="button" aria-expanded="false" aria-controls="dashboard-nav" class="rounded-md border border-line-dark px-3 py-1.5 font-sans text-sm font-semibold text-sand hover:bg-ink-2 md:hidden">Menu</button>
  </div>
</header>

<div class="md:flex">
  ${nav(id)}
  <main id="main" tabindex="-1" class="dashboard-main min-w-0 flex-1 px-5 py-8 md:px-10 md:py-10">
    <h1 class="text-[1.9rem] font-semibold leading-tight">${escape(title)}</h1>
    <p class="mt-2 max-w-[40rem] text-muted">${escape(intro)}</p>
    <div id="content" class="mt-8" aria-live="polite">
      <p class="text-muted">Loading…</p>
    </div>
    <noscript><p class="mt-8 max-w-[40rem]">The dashboard needs JavaScript to sign you in and load your information.</p></noscript>
  </main>
</div>

<script src="/dashboard/config.js"></script>
<script src="/dashboard/app.js"></script>
</body>
</html>
`;
}

function bare(title, body, section) {
  return `${head(title)}
<body class="dashboard min-h-screen bg-sand text-ink" data-section="${escape(section)}">
<main id="main" class="mx-auto max-w-xl px-6 py-20">
  <a href="/dashboard/" class="font-sans text-2xl font-bold tracking-tight text-teal no-underline">hearally</a>
  ${body}
</main>
<script src="/dashboard/config.js"></script>
<script src="/dashboard/app.js"></script>
</body>
</html>
`;
}

function write(relative, html) {
  const file = join(root, relative, "index.html");
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, html);
}

for (const { items } of NAV) {
  for (const { id, path } of items) write(path, page(id));
}
write(
  "callback/",
  bare(
    "Signing in",
    `<h1 class="mt-10 text-2xl font-semibold">Signing you in…</h1>
  <div id="content" class="mt-4 text-muted" aria-live="polite"><p>One moment.</p></div>`,
    "callback",
  ),
);
// Where Plaud's sign-in returns: the redirect URI registered with Plaud (see
// backend/src/hearally/plaud_link.py).
write(
  "integrations/plaud/",
  bare(
    "Connecting Plaud",
    `<h1 class="mt-10 text-2xl font-semibold">Connecting your Plaud…</h1>
  <div id="content" class="mt-4 text-muted" aria-live="polite"><p>One moment.</p></div>`,
    "plaud-callback",
  ),
);
write(
  "signed-out/",
  bare(
    "Signed out",
    `<h1 class="mt-10 text-2xl font-semibold">You are signed out</h1>
  <p class="mt-4 text-muted">Hearally keeps you signed in only while this browser tab is open.</p>
  <p class="mt-8"><a href="/dashboard/" class="font-semibold text-teal underline underline-offset-4">Sign in again</a> <span class="text-muted">or</span> <a href="/" class="font-semibold text-teal underline underline-offset-4">go to hearally.com</a></p>`,
    "signed-out",
  ),
);
console.log("dashboard pages written");
