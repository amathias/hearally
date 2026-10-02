// The Hearally dashboard: sign-in, loading, and rendering each section.
//
// Principles, because this is the private side of the product:
// - Real data only. Every section renders what the API returns for the signed-in
//   person, with explicit loading, empty and error states. No sample data.
// - No dead controls. If something is not built yet, the page says so.
// - Text is added with textContent, never parsed as HTML, so a summary
//   containing markup cannot run in the page.
// - Tokens live in sessionStorage: gone when the tab closes.

(function () {
  "use strict";

  var CONFIG = window.HEARALLY_CONFIG;
  var STORE = window.sessionStorage;
  var section = document.body.getAttribute("data-section");
  var content = document.getElementById("content");

  // ---------- small DOM helper ----------

  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      if (key === "text") el.textContent = attrs[key];
      else if (key === "class") el.className = attrs[key];
      else el.setAttribute(key, attrs[key]);
    });
    (children || []).forEach(function (child) {
      if (child) el.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return el;
  }

  function show(nodes) {
    content.replaceChildren.apply(content, [].concat(nodes));
  }

  function para(text, cls) {
    return h("p", { class: cls || "max-w-[40rem] text-muted", text: text });
  }

  function heading(text) {
    return h("h2", { class: "mt-12 mb-1 text-[1.4rem] font-semibold first:mt-0", text: text });
  }

  function button(label, onClick, primary) {
    var el = h("button", {
      type: "button",
      class: primary
        ? "rounded-lg bg-teal px-5 py-2.5 font-sans font-semibold text-paper hover:brightness-110"
        : "rounded-lg border border-line bg-paper px-4 py-2 font-sans font-semibold text-ink hover:bg-sand",
      text: label,
    });
    el.addEventListener("click", onClick);
    return el;
  }

  // ---------- states ----------

  function loading(what) {
    content.setAttribute("aria-busy", "true");
    show(para("Loading " + what + "…"));
  }

  function empty(title, detail) {
    return h("div", { class: "mt-4 rounded-xl border border-dashed border-line bg-paper/60 px-5 py-4" }, [
      h("p", { class: "font-semibold", text: title }),
      detail ? para(detail, "mt-1 max-w-[40rem] text-muted") : null,
    ]);
  }

  function failed(message, retry) {
    content.removeAttribute("aria-busy");
    show(
      h("div", { class: "border-l-2 border-flag pl-5", role: "alert" }, [
        h("p", { class: "font-semibold", text: "This could not be loaded." }),
        para(message, "mt-1 max-w-[40rem] text-muted"),
        h("div", { class: "mt-4" }, [button("Try again", retry)]),
      ]),
    );
  }

  function signedOut() {
    content.removeAttribute("aria-busy");
    document.getElementById("sign-out").hidden = true;
    show([
      h("div", { class: "max-w-[40rem]" }, [
        h("p", { class: "text-lg font-semibold", text: "Sign in to see your dashboard." }),
        para(
          "Hearally is in public preview. If you do not have an account yet, you can create one on the sign-in page.",
          "mt-2 text-muted",
        ),
        h("div", { class: "mt-6" }, [button("Sign in", signIn, true)]),
      ]),
    ]);
  }

  // ---------- sign-in (authorization code with PKCE) ----------

  function randomString(bytes) {
    var array = new Uint8Array(bytes);
    crypto.getRandomValues(array);
    return base64url(array);
  }

  function base64url(bytes) {
    var s = "";
    bytes.forEach(function (b) {
      s += String.fromCharCode(b);
    });
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function signIn() {
    var verifier = randomString(48);
    var state = randomString(24);
    STORE.setItem("pkce_verifier", verifier);
    STORE.setItem("pkce_state", state);
    STORE.setItem("return_to", location.pathname);
    crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)).then(function (digest) {
      var params = new URLSearchParams({
        response_type: "code",
        client_id: CONFIG.clientId,
        redirect_uri: CONFIG.redirectUri,
        scope: "openid email profile",
        code_challenge: base64url(new Uint8Array(digest)),
        code_challenge_method: "S256",
        state: state,
      });
      location.assign(CONFIG.authOrigin + "/oauth2/authorize?" + params);
    });
  }

  function saveTokens(json) {
    STORE.setItem("id_token", json.id_token);
    STORE.setItem("expires_at", String(Date.now() + (json.expires_in || 3600) * 1000));
    if (json.refresh_token) STORE.setItem("refresh_token", json.refresh_token);
  }

  function tokenRequest(body) {
    return fetch(CONFIG.authOrigin + "/oauth2/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(body),
    }).then(function (response) {
      if (!response.ok) throw new Error("token " + response.status);
      return response.json();
    });
  }

  function finishSignIn() {
    var params = new URLSearchParams(location.search);
    var code = params.get("code");
    var state = params.get("state");
    var expected = STORE.getItem("pkce_state");
    var verifier = STORE.getItem("pkce_verifier");
    STORE.removeItem("pkce_state");
    STORE.removeItem("pkce_verifier");

    // The state check stops a sign-in started somewhere else being completed here.
    if (!code || !state || state !== expected || !verifier) {
      show([
        para("That sign-in link could not be used. Please start again.", "text-ink"),
        h("p", { class: "mt-6" }, [
          h("a", { href: "/dashboard/", class: "font-semibold text-teal underline underline-offset-4", text: "Back to sign in" }),
        ]),
      ]);
      return;
    }

    tokenRequest({
      grant_type: "authorization_code",
      client_id: CONFIG.clientId,
      code: code,
      redirect_uri: CONFIG.redirectUri,
      code_verifier: verifier,
    })
      .then(function (json) {
        saveTokens(json);
        // Only ever return to a dashboard page: never an arbitrary URL.
        var back = STORE.getItem("return_to") || "/dashboard/";
        STORE.removeItem("return_to");
        location.replace(/^\/dashboard\/[a-z-/]*$/.test(back) ? back : "/dashboard/");
      })
      .catch(function () {
        show(para("Signing in did not complete. Please try again from the dashboard.", "text-ink"));
      });
  }

  function validToken() {
    var token = STORE.getItem("id_token");
    var expires = Number(STORE.getItem("expires_at") || 0);
    if (token && Date.now() < expires - 60000) return Promise.resolve(token);
    var refresh = STORE.getItem("refresh_token");
    if (!refresh) return Promise.resolve(null);
    return tokenRequest({ grant_type: "refresh_token", client_id: CONFIG.clientId, refresh_token: refresh })
      .then(function (json) {
        saveTokens(json);
        return json.id_token;
      })
      .catch(function () {
        clearSession();
        return null;
      });
  }

  function clearSession() {
    ["id_token", "expires_at", "refresh_token"].forEach(function (k) {
      STORE.removeItem(k);
    });
  }

  function signOut() {
    clearSession();
    var params = new URLSearchParams({ client_id: CONFIG.clientId, logout_uri: CONFIG.logoutUri });
    location.assign(CONFIG.authOrigin + "/logout?" + params);
  }

  function claims(token) {
    try {
      var payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(decodeURIComponent(escape(atob(payload))));
    } catch (e) {
      return {};
    }
  }

  // ---------- API ----------

  function load(name) {
    return validToken().then(function (token) {
      if (!token) return { signedOut: true };
      var who = claims(token).email;
      if (who) {
        var label = document.getElementById("signed-in-as");
        label.textContent = "Signed in as " + who;
        // On a phone the header has no room, so the menu says it instead.
        var inNav = document.getElementById("nav-signed-in-as");
        if (inNav) {
          inNav.textContent = "Signed in as " + who;
          inNav.classList.remove("hidden");
        }
        label.classList.remove("hidden");
      }
      document.getElementById("sign-out").hidden = false;
      return fetch(CONFIG.api + "/v1/me/" + name, {
        headers: { Authorization: "Bearer " + token },
      }).then(function (response) {
        if (response.status === 401) {
          clearSession();
          return { signedOut: true };
        }
        if (!response.ok) throw new Error(String(response.status));
        return response.json().then(function (data) {
          return { data: data };
        });
      });
    });
  }

  // A signed-in change: POST or DELETE. Resolves to { status, data }, or
  // { signedOut: true } when the session has ended.
  function send(method, path, body) {
    return validToken().then(function (token) {
      if (!token) return { signedOut: true };
      var headers = { Authorization: "Bearer " + token };
      if (body) headers["Content-Type"] = "application/json";
      return fetch(CONFIG.api + path, {
        method: method,
        headers: headers,
        body: body ? JSON.stringify(body) : undefined,
      }).then(function (response) {
        if (response.status === 401) {
          clearSession();
          return { signedOut: true };
        }
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            return { status: response.status, data: data };
          });
      });
    });
  }

  // ---------- rendering ----------

  // Never amber (amber means allies) and never alarm red.
  var STATUS = {
    available: { label: "Works now", cls: "text-ok", dot: "bg-ok" },
    testing: { label: "Being tested", cls: "text-teal", dot: "bg-teal" },
    in_progress: { label: "Being built", cls: "text-muted", dot: "bg-muted/50" },
    not_yet: { label: "Not yet", cls: "text-muted", dot: "bg-muted/50" },
  };


  function capabilities(list) {
    return h(
      "ul",
      { class: "divide-y divide-line border-y border-line" },
      list.map(function (c) {
        var s = STATUS[c.status] || STATUS.not_yet;
        return h("li", { class: "grid gap-1 py-4 sm:grid-cols-[13rem_8rem_1fr] sm:gap-6" }, [
          h("span", { class: "font-semibold", text: c.name }),
          h("span", { class: "inline-flex items-center gap-2 font-sans text-sm font-semibold " + s.cls }, [
            h("span", { class: "inline-block h-2 w-2 rounded-full " + s.dot, "aria-hidden": "true" }),
            s.label,
          ]),
          h("span", { class: "text-muted", text: c.detail }),
        ]);
      }),
    );
  }

  function when(value) {
    if (!value) return "";
    var date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    return isNaN(date) ? "" : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }

  function list(items, render) {
    return h("ul", { class: "divide-y divide-line border-y border-line" }, items.map(render));
  }

  function summaryRow(item) {
    return h("li", { class: "py-4" }, [
      h("p", { text: item.summary || "" }),
      h("p", { class: "mt-1 font-sans text-sm text-muted", text: [when(item.spoken_at), item.category].filter(Boolean).join(" · ") }),
    ]);
  }

  // ---------- connecting a computer ----------

  // ARCHITECTURE.md section 9. Each line is ticked separately, and the version is
  // recorded with the acceptance. Changing any line means a new version.
  var ACKNOWLEDGEMENT = [
    "Hearally is not for emergencies. If someone needs help now, I will call 911.",
    "Hearally is not a medical device. It does not diagnose, treat or monitor any health condition.",
    "Hearally reports things that were said out loud. It does not detect falls, accidents or illness.",
    "It will miss things, and it will sometimes flag things that turn out to be nothing.",
    "Nobody is standing by. Messages go to the allies I choose; there is no guarantee anyone sees or acts on one.",
    "Care decisions are mine.",
    "I will only record conversations I have the legal right to record. Some places, including several US states, require everyone in a conversation, phone calls included, to agree to being recorded. Where the law requires that, I will get agreement first. I am responsible for what I record and upload, not Hearally.",
    "I am 18 or older.",
  ];

  // The signup acknowledgement (ARCHITECTURE.md section 9), wherever it is needed:
  // Today, and before connecting Plaud. Each line ticked on its own; nothing
  // is brought in until the current version is accepted.
  function agreementPanel(version, onAgreed) {
    var boxes = [];
    var status = h("div", { "aria-live": "polite" });
    var fieldset = h("fieldset", { class: "mt-3" }, [
      h("legend", { class: "sr-only", text: "Please read and agree to each of these" }),
    ]);
    ACKNOWLEDGEMENT.forEach(function (line, i) {
      var box = h("input", { type: "checkbox", id: "agree-" + i, class: "mt-1 h-5 w-5 shrink-0 accent-teal" });
      boxes.push(box);
      fieldset.appendChild(h("div", { class: "mt-3 flex items-start gap-3" }, [box, h("label", { for: "agree-" + i, text: line })]));
    });
    var agree = button(
      "I agree",
      function () {
        if (
          boxes.some(function (b) {
            return !b.checked;
          })
        ) {
          status.replaceChildren(para("Please tick each line first.", "mt-3 text-flag"));
          return;
        }
        agree.disabled = true;
        send("POST", "/v1/me/acknowledgement", { acknowledgement_version: version }).then(
          function (r) {
            agree.disabled = false;
            if (r.signedOut) return signedOut();
            if (r.status === 200) return onAgreed();
            if (r.status === 409) return render(); // the wording changed: show it again
            status.replaceChildren(para("That could not be saved just now. Please try again.", "mt-3 text-flag"));
          },
          function () {
            agree.disabled = false;
            status.replaceChildren(para("Hearally could not be reached. Please try again.", "mt-3 text-flag"));
          },
        );
      },
      true,
    );
    return h("section", { class: "mt-4 rounded-xl border border-line bg-paper px-6 py-6" }, [
      h("h3", { class: "text-lg font-semibold", text: "Before your recordings come in" }),
      para(
        "Please read and agree to each of these. Hearally does not bring in or transcribe any recording until you have.",
        "mt-1 max-w-[40rem] text-muted",
      ),
      fieldset,
      h("div", { class: "mt-5" }, [agree]),
      status,
    ]);
  }

  function connectPanel(d) {
    var panel = h("section", { class: "mt-4 max-w-[40rem]", "aria-labelledby": "connect-heading" });
    var status = h("div", { "aria-live": "polite" });
    var fieldset = null;
    var boxes = [];

    panel.appendChild(h("h2", { id: "connect-heading", class: "text-xl font-semibold", text: "Connect a computer" }));
    panel.appendChild(
      para(
        "The Hearally collector runs on your computer and sends recordings from one folder to your account. Three steps:",
        "mt-2 text-ink",
      ),
    );

    // Step 1 comes first on purpose: the first real setup went looking for the
    // download only after the code was already ticking down.
    panel.appendChild(h("p", { class: "mt-5 font-semibold", text: "1. Download the collector" }));
    panel.appendChild(
      h("p", { class: "mt-2" }, [
        h("a", {
          href: "/collector/hearally-collector.mjs",
          download: "",
          class: "inline-block rounded-lg bg-ink px-5 py-2.5 font-sans font-semibold text-sand no-underline hover:brightness-125",
          text: "Download the collector",
        }),
      ]),
    );
    panel.appendChild(para("It saves to your Downloads folder. It needs Node.js, free from nodejs.org.", "mt-2 text-muted"));
    panel.appendChild(h("p", { class: "mt-6 font-semibold", text: "2. Create a pairing code" }));
    panel.appendChild(
      para("It connects one computer to your account. Each code works once, for ten minutes.", "mt-1 text-muted"),
    );

    if (!d.acknowledged) {
      fieldset = h("fieldset", { class: "mt-5" }, [
        h("legend", { class: "font-semibold", text: "Before your first recording, please read and agree to each of these." }),
      ]);
      ACKNOWLEDGEMENT.forEach(function (line, i) {
        var box = h("input", { type: "checkbox", id: "ack-" + i, class: "mt-1 h-5 w-5 shrink-0 accent-teal" });
        boxes.push(box);
        fieldset.appendChild(h("div", { class: "mt-3 flex items-start gap-3" }, [box, h("label", { for: "ack-" + i, text: line })]));
      });
      panel.appendChild(fieldset);
    }

    var create = button(
      "Create a pairing code",
      function () {
        var unticked = boxes.some(function (b) {
          return !b.checked;
        });
        if (unticked) {
          status.replaceChildren(para("Please tick each line above first.", "mt-4 text-flag"));
          return;
        }
        create.disabled = true;
        status.replaceChildren(para("Creating a code…", "mt-4 text-muted"));
        send("POST", "/v1/me/pairings", d.acknowledged ? null : { acknowledgement_version: d.acknowledgement_version })
          .then(function (result) {
            create.disabled = false;
            if (result.signedOut) return signedOut();
            if (result.status === 201) {
              d.acknowledged = true;
              boxes = [];
              if (fieldset) fieldset.hidden = true;
              return status.replaceChildren(codeCard(result.data));
            }
            // The acknowledgement changed since the page loaded: show it again.
            if (result.status === 409) return render();
            status.replaceChildren(para("A code could not be created just now. Please try again.", "mt-4 text-flag"));
          })
          .catch(function () {
            create.disabled = false;
            status.replaceChildren(para("Hearally could not be reached. Please try again.", "mt-4 text-flag"));
          });
      },
      true,
    );

    panel.appendChild(h("div", { class: "mt-5" }, [create]));
    panel.appendChild(status);
    panel.appendChild(
      h("p", { class: "mt-4" }, [
        h("a", { href: "/setup/", class: "font-sans font-semibold text-teal underline underline-offset-4", text: "How to set up the collector →" }),
      ]),
    );
    return panel;
  }

  // ---------- tables and status ----------

  // A real table: one header row, one row per thing. Scrolls sideways on a
  // narrow phone rather than squashing its columns.
  function table(columns, rows) {
    return h("div", { class: "mt-4 overflow-x-auto rounded-xl border border-line bg-paper" }, [
      h("table", { class: "w-full min-w-[34rem] border-collapse text-left" }, [
        h("thead", {}, [
          h(
            "tr",
            { class: "border-b border-line" },
            columns.map(function (c) {
              return h("th", {
                scope: "col",
                class: "px-4 py-3 font-sans text-[11px] font-bold uppercase tracking-[0.12em] text-muted",
                text: c,
              });
            }),
          ),
        ]),
        h(
          "tbody",
          {},
          rows.map(function (row) {
            row.className = "border-b border-line last:border-b-0";
            return row;
          }),
        ),
      ]),
    ]);
  }

  function cell(children, cls) {
    return h("td", { class: "px-4 py-3 align-top " + (cls || "") }, [].concat(children));
  }

  // A dot and a word. Green for connected, grey otherwise: nothing here is an alarm.
  function statusDot(on, label) {
    return h("span", { class: "inline-flex items-center gap-2 font-sans text-sm font-semibold " + (on ? "text-ok" : "text-muted") }, [
      h("span", { class: "inline-block h-2.5 w-2.5 rounded-full " + (on ? "bg-ok" : "bg-muted/50"), "aria-hidden": "true" }),
      label,
    ]);
  }

  function duration(seconds) {
    if (typeof seconds !== "number" || !seconds) return "";
    if (seconds < 60) return Math.round(seconds) + " s";
    var m = Math.round(seconds / 60);
    return m < 60 ? m + " min" : Math.floor(m / 60) + " h " + (m % 60) + " min";
  }

  function disconnectComputer(c) {
    var ok = window.confirm(
      "Disconnect " + (c.name || "this computer") + "? It will stop sending recordings straight away. Recordings already sent are kept.",
    );
    if (!ok) return;
    send("DELETE", "/v1/me/computers/" + encodeURIComponent(c.id)).then(function (result) {
      if (result.signedOut) return signedOut();
      render();
    }, render);
  }

  // ---------- Plaud sign-in ----------
  //
  // Hearally's servers hold the Plaud sign-in and bring in new recordings
  // every 15 minutes. This page only ever sees what they report. See
  // backend/src/hearally/plaud_link.py and plaud_import.py.

  function plaudStart(onError) {
    send("POST", "/v1/me/plaud/start").then(function (r) {
      if (r.signedOut) return signedOut();
      if (r.status === 200 && r.data.authorize_url) return location.assign(r.data.authorize_url);
      onError("Plaud’s sign-in could not be opened just now. Please try again.");
    });
  }

  function plaudDisconnect() {
    var ok = window.confirm(
      "Disconnect Plaud? Hearally will stop bringing in new recordings from your Plaud account. Recordings already received are kept.",
    );
    if (!ok) return;
    send("POST", "/v1/me/plaud/disconnect").then(function (r) {
      if (r.signedOut) return signedOut();
      render();
    }, render);
  }

  // "Test the connection": asks Plaud now, in plain words. Developer detail
  // appears only when Plaud's answer could not be read, folded away.
  function plaudTest(out) {
    out.replaceChildren(para("Asking Plaud… this can take a few seconds.", "mt-2 font-sans text-sm text-muted"));
    send("POST", "/v1/me/plaud/check").then(function (r) {
      if (r.signedOut) return signedOut();
      if (r.status === 409) {
        return out.replaceChildren(para("Plaud no longer accepts this sign-in. Disconnect, then connect again.", "mt-2 font-sans text-sm text-flag"));
      }
      if (r.status !== 200) {
        var detail = r.data && r.data.detail ? " (" + r.data.detail + ")" : "";
        return out.replaceChildren(para("Plaud did not answer as expected" + detail + ". Please try again later.", "mt-2 font-sans text-sm text-flag"));
      }
      var d = r.data;
      var seen = d.recordings ? d.recordings.listed : 0;
      var audio = d.audio && d.audio.available;
      var nodes = [
        para(
          "Plaud answered. It shows Hearally " + seen + " recording" + (seen === 1 ? "" : "s") + (audio ? ", and their audio can be fetched." : "."),
          "mt-2 font-sans text-sm " + (d.recordings ? "text-ok" : "text-flag"),
        ),
      ];
      if (d.diagnostic) {
        nodes.push(
          h("details", { class: "mt-2" }, [
            h("summary", { class: "cursor-pointer font-sans text-xs text-muted", text: "Details for Hearally’s developers" }),
            h("pre", { class: "mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-ink px-4 py-3 font-mono text-[12px] text-sand" }, [
              h("code", { text: "PARSE: " + JSON.stringify(d.diagnostic.parse_error) + "\n\n" + d.diagnostic.list_files_outline }),
            ]),
          ]),
        );
      }
      out.replaceChildren.apply(out, nodes);
    });
  }

  // The first thing a new account holder sees: connect Plaud, the easy way.
  function plaudConnectCard() {
    var note = h("div", { "aria-live": "polite" });
    return h("section", { class: "mt-4 rounded-xl border border-line bg-paper px-6 py-6" }, [
      h("h3", { class: "text-lg font-semibold", text: "Connect your Plaud" }),
      para(
        "Sign in to Plaud once. After that, new recordings come in by themselves about every 15 minutes, with no computer and nothing to export. Only recordings made after you connect are brought in.",
        "mt-2 max-w-[40rem] text-muted",
      ),
      para("You will go to Plaud’s own page to sign in and approve, then come back here. This way of connecting is new, and we are still testing it.", "mt-2 max-w-[40rem] font-sans text-sm text-muted"),
      h("div", { class: "mt-4" }, [
        button(
          "Sign in to Plaud",
          function () {
            note.replaceChildren(para("Opening Plaud’s sign-in…", "mt-3 text-muted"));
            plaudStart(function (message) {
              note.replaceChildren(para(message, "mt-3 text-flag"));
            });
          },
          true,
        ),
      ]),
      note,
    ]);
  }

  // Where each recording came from, in words.
  function fromLabel(r, computers) {
    if (r.via === "plaud") return "Plaud sign-in";
    var id = String(r.via || "").replace(/^computer:/, "");
    var match = computers.filter(function (c) {
      return c.id === id;
    })[0];
    return match ? match.name : "A computer";
  }

  function recordingsTable(d) {
    if (!d.recordings.length) {
      return empty(
        "No recordings listed yet.",
        "Each recording appears here as it arrives. Recordings received before 30 September 2026 are not listed.",
      );
    }
    return table(
      ["Recorded", "Length", "From", "Status"],
      d.recordings.map(function (r) {
        return h("tr", {}, [
          cell(when(r.recorded_at)),
          cell(duration(r.duration_s) || "—", "text-muted"),
          cell(fromLabel(r, d.computers)),
          cell(
            // Transcription is not built yet, so every recording waits.
            h("span", {
              class: "font-sans text-sm " + (r.status === "transcribed" ? "text-ok" : "text-muted"),
              text:
                {
                  received: "Received, transcribing within the hour",
                  transcribed: r.items ? "Transcribed · " + r.items + " to pass on" : "Transcribed · nothing to pass on",
                  failed: "Could not be transcribed",
                }[r.status] || r.status,
            }),
          ),
        ]);
      }),
    );
  }

  // One table of every way in: Plaud sign-in and each computer.
  function connectionsTable(d, plaud) {
    var plaudVia = d.recordings.filter(function (r) {
      return r.via === "plaud";
    });
    var testOut = h("div", { "aria-live": "polite" });
    var plaudRow = h("tr", {}, [
      cell([
        h("p", { class: "font-semibold", text: "Plaud account" }),
        h("p", { class: "mt-0.5 font-sans text-sm text-muted", text: "Plaud sign-in · checks every 15 minutes" }),
        testOut,
      ]),
      cell(statusDot(true, "Connected")),
      cell(
        plaudVia.length
          ? when(plaudVia[0].recorded_at)
          : plaud.imported
            ? plaud.imported + " brought in"
            : "Nothing yet",
        "text-muted",
      ),
      cell(
        h("div", { class: "flex flex-wrap gap-2" }, [
          button("Test", function () {
            plaudTest(testOut);
          }),
          button("Disconnect", plaudDisconnect),
        ]),
      ),
    ]);
    var rows = plaud.connected ? [plaudRow] : [];
    d.computers.forEach(function (c) {
      var active = c.status === "active";
      rows.push(
        h("tr", {}, [
          cell([
            h("p", { class: "font-semibold", text: c.name || "A computer" }),
            h("p", { class: "mt-0.5 font-sans text-sm text-muted", text: "Hearally collector · connected " + when(c.paired_at) }),
          ]),
          cell(statusDot(active, active ? "Connected" : "Disconnected")),
          cell(c.last_seen_at ? when(c.last_seen_at) : "Nothing yet", "text-muted"),
          cell(
            active
              ? button("Disconnect", function () {
                  disconnectComputer(c);
                })
              : "",
          ),
        ]),
      );
    });
    if (!rows.length) {
      return empty("Nothing is connected yet.", "Connect your Plaud below, and your recordings start arriving.");
    }
    return table(["Connection", "Status", "Last recording", ""], rows);
  }

  function integrationsPage(d) {
    var box = h("div", {}, [para("Loading your connections…", "text-muted")]);
    send("POST", "/v1/me/plaud/status")
      .then(function (r) {
        if (r.signedOut) return signedOut();
        var plaud = r.status === 200 ? r.data : { connected: false };
        var any = plaud.connected || d.computers.some(function (c) {
          return c.status === "active";
        });
        var other = h("details", { class: "mt-6 rounded-xl border border-line bg-paper px-6 py-4" }, [
          h("summary", {
            class: "cursor-pointer font-sans font-semibold text-ink",
            text: any ? "Connect a computer as well" : "Use a computer instead",
          }),
          para(
            "The Hearally collector is a small program for your computer. It sends recordings you export from Plaud and save in one folder. Use it if you would rather not sign in to Plaud from Hearally.",
            "mt-3 max-w-[40rem] text-muted",
          ),
          connectPanel(d),
        ]);
        box.replaceChildren.apply(
          box,
          [
            d.acknowledged ? null : agreementPanel(d.acknowledgement_version, render),
            heading("How recordings reach Hearally"),
            connectionsTable(d, plaud),
            plaud.connected || !d.acknowledged ? null : plaudConnectCard(),
            other,
            heading("Recordings received"),
            recordingsTable(d),
          ].filter(Boolean),
        );
      })
      .catch(function () {
        box.replaceChildren(para("Your connections could not be loaded. Please reload the page.", "text-flag"));
      });
    return box;
  }

  // ---------- allies (design sections 3, 4 and 7) ----------
  //
  // Amber is the ally's colour: their names are amber wherever they appear.

  var LEVELS = [
    ["alerts_and_items", "Heads-ups, calls and the daily list"],
    ["alerts_only", "Heads-ups only"],
    ["off", "Nothing for now"],
  ];
  var STATUS_WORDS = {
    invited: ["Invited, not yet confirmed", false],
    confirmed: ["Confirmed", true],
    declined: ["Said no thanks", false],
    stopped: ["Stopped receiving", false],
  };

  function allyAction(action, body) {
    return send("POST", "/v1/me/allies/" + action, body).then(function (r) {
      if (r.signedOut) return signedOut();
      if (r.status >= 200 && r.status < 300) return render();
      window.alert((r.data && r.data.detail) || "That could not be saved just now. Please try again.");
    });
  }

  function nameCard(d) {
    var input = h("input", {
      type: "text",
      maxlength: "40",
      value: d.display_name || "",
      placeholder: "For example, Aaron or Mum",
      class: "mt-2 w-full max-w-[20rem] rounded-lg border border-line bg-paper px-3 py-2",
      "aria-label": "How your allies know you",
    });
    return h("section", { class: "mt-4 rounded-xl border border-line bg-paper px-6 py-5" }, [
      h("h3", { class: "text-lg font-semibold", text: "How your allies know you" }),
      para("Used in every message, so they know who it is about.", "mt-1 text-muted"),
      input,
      h("div", { class: "mt-3" }, [
        button("Save", function () {
          allyAction("name", { display_name: input.value });
        }, !d.display_name),
      ]),
    ]);
  }

  function addCard(d) {
    function field(label, attrs) {
      var input = h("input", Object.assign({ class: "mt-1 w-full rounded-lg border border-line bg-paper px-3 py-2" }, attrs));
      return [input, h("label", { class: "block font-sans text-sm font-semibold" }, [label, input])];
    }
    var name = field("Name", { type: "text", maxlength: "60", autocomplete: "off" });
    var email = field("Email", { type: "email", maxlength: "254", autocomplete: "off" });
    var rel = field("Relationship (optional)", { type: "text", maxlength: "40", placeholder: "Daughter, neighbour, friend" });
    var level = h("select", { class: "mt-1 w-full rounded-lg border border-line bg-paper px-3 py-2" },
      LEVELS.map(function (l) { return h("option", { value: l[0], text: l[1] }); }));
    var note = h("div", { "aria-live": "polite" });
    return h("section", { class: "mt-4 rounded-xl border border-line bg-paper px-6 py-6" }, [
      h("h3", { class: "text-lg font-semibold", text: "Add an ally" }),
      para("They get an email with a private link. Nothing reaches them until they read what Hearally is and is not, and confirm. Up to five allies.", "mt-1 max-w-[40rem] text-muted"),
      h("div", { class: "mt-4 grid max-w-[40rem] gap-4 sm:grid-cols-2" }, [
        name[1], email[1], rel[1],
        h("label", { class: "block font-sans text-sm font-semibold" }, ["What they hear", level]),
      ]),
      h("div", { class: "mt-5" }, [
        button("Send the invitation", function () {
          if (!d.display_name) {
            note.replaceChildren(para("First, save how your allies know you, above.", "mt-3 text-flag"));
            return;
          }
          var tz = "";
          try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) {}
          allyAction("add", { name: name[0].value, email: email[0].value, relationship: rel[0].value, level: level.value, timezone: tz });
        }, true),
      ]),
      note,
    ]);
  }

  function alliesTable(d) {
    return table(
      ["Ally", "Status", "What they hear", ""],
      d.allies.map(function (a) {
        var st = STATUS_WORDS[a.status] || [a.status, false];
        var level = h("select", { class: "rounded-md border border-line bg-paper px-2 py-1.5 font-sans text-sm", "aria-label": "What " + a.name + " hears" },
          LEVELS.map(function (l) {
            var o = h("option", { value: l[0], text: l[1] });
            if (l[0] === a.level) o.setAttribute("selected", "");
            return o;
          }));
        level.addEventListener("change", function () {
          allyAction("update", { ally_id: a.id, level: level.value });
        });
        return h("tr", {}, [
          cell([
            h("p", { class: "font-semibold text-amber-text", text: a.name }),
            h("p", { class: "mt-0.5 font-sans text-sm text-muted", text: [a.relationship, a.email].filter(Boolean).join(" · ") }),
            a.first ? h("p", { class: "mt-1 font-sans text-xs font-bold uppercase tracking-[0.12em] text-amber-text", text: "First ally" }) : null,
          ].filter(Boolean)),
          cell(statusDot(st[1], st[0])),
          cell(level),
          cell(h("div", { class: "flex flex-wrap gap-2" }, [
            a.first || a.status !== "confirmed" ? null : button("Make first ally", function () { allyAction("update", { ally_id: a.id, first: true }); }),
            a.status === "invited" ? button("Resend", function () { allyAction("resend", { ally_id: a.id }); }) : null,
            button("Remove", function () {
              if (window.confirm("Remove " + a.name + "? Their link stops working at once, and nothing more reaches them.")) {
                allyAction("remove", { ally_id: a.id });
              }
            }),
          ].filter(Boolean))),
        ]);
      }),
    );
  }

  function peoplePage(d) {
    return h("div", {}, [
      nameCard(d),
      heading("Your allies"),
      d.allies.length
        ? alliesTable(d)
        : empty("You have not added any allies yet.", "Add someone below. Each person confirms before receiving anything."),
      para("A heads-up goes to every confirmed ally who hears them, at most one a day. Something worth a call today goes to your first ally, held until morning during their quiet hours (21:00 to 08:00). Everything else arrives in one daily list at 18:00.", "mt-4 max-w-[44rem] font-sans text-sm text-muted"),
      d.allies.length < 5 ? addCard(d) : null,
      heading("What an ally receives"),
      para("An example. Every message says Hearally is not an emergency service, and lets them say it was nothing.", "mt-1 max-w-[40rem] text-muted"),
      h("div", { class: "mt-4 max-w-[30rem] rounded-2xl bg-ink px-6 py-6 text-sand" }, [
        h("p", { class: "font-sans text-[11px] font-bold uppercase tracking-[0.12em] text-muted-dark", text: "Email" }),
        h("p", { class: "mt-3 leading-relaxed", text: "About " + (d.display_name || "you") + ": a call this afternoon sounded like a common scam script. The caller asked for an account number and said not to mention it to family." }),
        h("p", { class: "mt-4 border-t border-line-dark pt-3 text-[13px] text-muted-dark", text: "Open, or say it was nothing · Not an emergency service" }),
      ]),
    ].filter(Boolean));
  }

  // Plaud sends the browser back here with ?code&state (or ?error). The code
  // is handed to Hearally's servers at once and removed from the address bar.
  function finishPlaudSignIn() {
    var params = new URLSearchParams(location.search);
    history.replaceState(null, "", location.pathname);
    function done(title, detail) {
      show([
        h("p", { class: "text-lg font-semibold", text: title }),
        detail ? para(detail, "mt-2 text-muted") : null,
        h("p", { class: "mt-6" }, [
          h("a", {
            href: "/dashboard/integrations/#plaud",
            class: "font-semibold text-teal underline underline-offset-4",
            text: "Back to Integrations",
          }),
        ]),
      ]);
    }
    if (params.get("error")) {
      return done("Plaud sign-in was not completed.", "Plaud said: " + params.get("error").slice(0, 60) + ".");
    }
    if (!params.get("code") || !params.get("state")) {
      return done("Nothing to finish here.", "Start from Integrations, With Plaud sign-in.");
    }
    send("POST", "/v1/me/plaud/finish", { code: params.get("code"), state: params.get("state") })
      .then(function (r) {
        if (r.signedOut) return done("Your Hearally session ended.", "Sign in to Hearally, then try Plaud sign-in again.");
        if (r.status === 200) return location.replace("/dashboard/integrations/#plaud");
        if (r.status === 409) {
          return done("That sign-in expired.", "Plaud sign-in must be finished within ten minutes, in the same tab. Please try again.");
        }
        done("Plaud sign-in did not finish.", r.data && r.data.detail ? r.data.detail : "Please try again.");
      })
      .catch(function () {
        done("Hearally could not be reached.", "Please try again.");
      });
  }

  function commandLine(system, command) {
    return h("div", { class: "mt-3" }, [
      h("p", { class: "font-sans text-sm font-semibold text-muted", text: system }),
      h("pre", { class: "mt-1 overflow-x-auto rounded-lg bg-ink px-4 py-3 font-mono text-[14px] text-sand" }, [
        h("code", { text: command }),
      ]),
    ]);
  }

  function codeCard(issued) {
    var expires = new Date(issued.expires_at);
    var copied = h("span", { class: "ml-3 font-sans text-sm text-muted", role: "status" });
    var copy = button("Copy", function () {
      if (!navigator.clipboard) return;
      navigator.clipboard.writeText(issued.code).then(function () {
        copied.textContent = "Copied.";
      });
    });
    return h("div", { class: "mt-5 rounded-lg border border-line bg-paper p-5" }, [
      h("p", { class: "font-sans text-sm font-semibold uppercase tracking-wider text-muted", text: "Your pairing code" }),
      h("p", { class: "mt-2 select-all break-all font-mono text-2xl font-semibold tracking-wide", text: issued.code }),
      h("div", { class: "mt-3" }, [copy, copied]),
      para(
        "Works once, until " +
          (isNaN(expires) ? "ten minutes from now" : expires.toLocaleTimeString(undefined, { timeStyle: "short" })) +
          ". It works like a password: do not share it.",
        "mt-3 text-muted",
      ),
      h("p", { class: "mt-5 font-semibold", text: "3. Run the collector's setup, and paste this code when it asks." }),
      para(
        "Windows: open PowerShell from the Start menu (not as administrator). Mac: open Terminal. Type the line for your computer, then press Enter:",
        "mt-2 text-muted",
      ),
      commandLine("Windows", "node $HOME\\Downloads\\hearally-collector.mjs setup"),
      commandLine("Mac", "node ~/Downloads/hearally-collector.mjs setup"),
      para(
        "Then answer its questions: which Plaud you have, what you use it for, and press Enter to accept the rest.",
        "mt-3 text-muted",
      ),
      h("p", { class: "mt-4" }, [
        h("a", { href: "/setup/", class: "font-sans font-semibold text-teal underline underline-offset-4", text: "Step-by-step setup guide →" }),
      ]),
      h("div", { class: "mt-4" }, [button("I have finished setup", render)]),
    ]);
  }

  var RENDER = {
    overview: function (d) {
      var box = h("div", {}, [para("Loading…", "text-muted")]);
      send("POST", "/v1/me/plaud/status")
        .then(function (r) {
          if (r.signedOut) return signedOut();
          var plaud = r.status === 200 ? r.data : { connected: false };
          var c = d.counts;
          var connected = plaud.connected || c.computers > 0;
          var last = d.latest_recording;

          // One line that says whether recordings are reaching Hearally.
          var status = h("div", { class: "flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-line bg-paper px-5 py-4" }, [
            statusDot(connected, connected ? "Recordings can reach Hearally" : "Nothing is connected yet"),
            h("span", {
              class: "font-sans text-sm text-muted",
              text: [
                plaud.connected ? "Plaud connected" : "",
                c.computers ? c.computers + " computer" + (c.computers === 1 ? "" : "s") + " connected" : "",
                last ? "last recording " + when(last.recorded_at) : "",
              ]
                .filter(Boolean)
                .join(" · "),
            }),
            connected
              ? null
              : h("a", {
                  href: "/dashboard/integrations/",
                  class: "font-sans text-sm font-semibold text-teal underline underline-offset-4",
                  text: "Connect your Plaud →",
                }),
          ].filter(Boolean));

          // What to do next, in order. "Later" steps are honest about not being built.
          function step(n, state, title, detail, link) {
            var badge = {
              done: ["Done", "bg-ok text-paper"],
              next: ["Next", "bg-teal text-paper"],
              later: ["Coming", "bg-line text-muted"],
            }[state];
            return h("li", { class: "flex gap-4 py-4" }, [
              h("span", { class: "font-display text-2xl font-semibold " + (state === "later" ? "text-muted" : "text-teal"), text: String(n) }),
              h("div", { class: "min-w-0 flex-1" }, [
                h("p", { class: "flex flex-wrap items-center gap-2 font-semibold" }, [
                  title,
                  h("span", { class: "rounded px-2 py-0.5 font-sans text-[11px] font-bold uppercase tracking-[0.12em] " + badge[1], text: badge[0] }),
                ]),
                para(detail, "mt-1 max-w-[40rem] text-muted"),
                link ? h("p", { class: "mt-2" }, [h("a", { href: link[1], class: "font-sans text-sm font-semibold text-teal underline underline-offset-4", text: link[0] })]) : null,
              ].filter(Boolean)),
            ]);
          }

          box.replaceChildren.apply(box, [
            d.acknowledged ? null : agreementPanel(d.acknowledgement_version, render),
            status,
            heading("Getting set up"),
            h("ol", { class: "divide-y divide-line border-y border-line" }, [
              step(
                1,
                connected ? "done" : "next",
                "Connect your Plaud",
                connected
                  ? "Your recordings reach Hearally and are kept private."
                  : "Sign in to Plaud once, and new recordings come in by themselves.",
                connected ? ["See your connections →", "/dashboard/integrations/"] : ["Connect your Plaud →", "/dashboard/integrations/"],
              ),
              step(2, "later", "Add your allies", "Choose the people who should hear from Hearally. Each of them confirms before receiving anything."),
              step(3, "later", "Choose what each ally hears", "You will see a real example of what they would have received before you agree."),
            ]),
            heading("Latest summaries"),
            d.latest_summaries.length
              ? list(d.latest_summaries, summaryRow)
              : empty(
                  "No summaries yet.",
                  "Recordings are transcribed about every half hour. Anything worth passing on appears here, usually within an hour of recording.",
                ),
            heading("What works in the preview"),
            capabilities(d.capabilities),
          ].filter(Boolean));
        })
        .catch(function () {
          box.replaceChildren(para("Today could not be loaded. Please reload the page.", "text-flag"));
        });
      return [box];
    },

    activity: function (d) {
      return [
        heading("Heads-ups"),
        d.alerts.length
          ? list(d.alerts, function (a) {
              return h("li", { class: "py-4" }, [
                h("p", { text: a.summary || "" }),
                h("p", { class: "mt-1 font-sans text-sm text-muted", text: [when(a.created_at), a.status].filter(Boolean).join(" · ") }),
              ]);
            })
          : empty("No heads-ups have been raised.", "A heads-up is raised when a call sounds like a common scam script. There will be at most one a day."),
        heading("What your allies heard"),
        d.sent.length
          ? list(d.sent, function (m) {
              return h("li", { class: "py-4" }, [
                h("p", {}, [
                  h("span", { class: "font-semibold text-amber-text", text: m.ally_name || "An ally" }),
                  " · " + ({ heads_up: "Heads-up", call_today: "Worth a call today", digest: "Daily list" }[m.kind] || "Message") + " by " + (m.channel || "email"),
                ]),
                h("p", { class: "mt-1 font-sans text-sm text-muted", text: [when(m.sent_at), m.status].filter(Boolean).join(" · ") }),
              ]);
            })
          : empty("Nothing has been sent yet.", "Every message to an ally is listed here: what your allies heard, and when."),
      ];
    },

    summaries: function (d) {
      return [
        d.summaries.length
          ? list(d.summaries, summaryRow)
          : empty(
              "No summaries yet.",
              "A summary is a sentence or two about something worth passing on, such as a new appointment. They appear here once a recording is transcribed, usually within an hour.",
            ),
        heading("Held back"),
        d.held_back.length
          ? para(
              d.held_back.length + " moment" + (d.held_back.length === 1 ? " was" : "s were") + " held back by the dignity filter. Only you can see these.",
              "mt-4 max-w-[40rem] text-muted",
            )
          : empty(
              "Nothing has been held back.",
              "When the dignity filter keeps something private, it is listed here for you, and only you.",
            ),
      ];
    },

    people: function (d) {
      return [peoplePage(d)];
    },

    integrations: function (d) {
      return [integrationsPage(d)];
    },

    preferences: function (d) {
      return [
        heading("Topics that never leave"),
        d.blocklist_topics.length
          ? list(d.blocklist_topics, function (t) {
              return h("li", { class: "py-3", text: t });
            })
          : empty("No topics set.", "You will be able to name anything you never want shared. It is removed before any message is written."),
        heading("Dignity filter"),
        para(
          d.dignity_filter === "custom"
            ? "You have adjusted the dignity filter."
            : "The default is on: private moments such as venting, family arguments and bodily remarks are held back from every ally, and film or television audio is ignored.",
        ),
        d.editing_available
          ? null
          : empty("Changing preferences is not available yet.", "These settings are shown so you know how Hearally treats your recordings today. Editing them from the dashboard is being built."),
      ];
    },

    account: function (d) {
      return [
        heading("Sign-in"),
        h("dl", { class: "divide-y divide-line border-y border-line" }, [
          h("div", { class: "grid gap-1 py-4 sm:grid-cols-[12rem_1fr]" }, [
            h("dt", { class: "text-muted", text: "Email" }),
            h("dd", { text: d.email + (d.email_verified ? "" : " (not verified)") }),
          ]),
          h("div", { class: "grid gap-1 py-4 sm:grid-cols-[12rem_1fr]" }, [
            h("dt", { class: "text-muted", text: "Agreements" }),
            h("dd", {}, [
              d.agreement
                ? "You agreed to the preview terms on " + when(d.agreement.accepted_at) + " (version " + d.agreement.version + "). "
                : "Not yet. You will be asked to agree to each line before your first recording arrives. ",
              h("a", { href: "/#not", class: "text-teal underline underline-offset-4", text: "What Hearally is not" }),
            ]),
          ]),
        ]),
        heading("Billing"),
        h("div", { class: "mt-4 max-w-[40rem] space-y-3 rounded-xl border border-line bg-paper px-5 py-4" }, [
          h("p", { class: "font-semibold", text: "Hearally is free during the public preview." }),
          para(
            "You will not be charged, and we do not collect payment details, while the preview lasts.",
            "text-ink",
          ),
          para(
            "In the future, billing information will be required to keep using Hearally. We will tell you well in advance, before any charges begin, so you can decide whether to continue.",
            "text-ink",
          ),
        ]),
        heading("Closing your account"),
        h("div", { class: "mt-4 rounded-xl border border-line bg-paper px-5 py-4" }, [
          h("p", { class: "font-semibold", text: "To close your account, email us." }),
          h("p", { class: "mt-1 max-w-[40rem] text-muted" }, [
            "Write to ",
            h("a", { href: "mailto:info@email.famplified.com", class: "text-teal underline underline-offset-4", text: "info@email.famplified.com" }),
            " from the address you sign in with, and we delete your account and data as the ",
            h("a", { href: "/privacy/", class: "text-teal underline underline-offset-4", text: "privacy policy" }),
            " describes. Disconnecting your Plaud and computers under Integrations stops anything new arriving straight away.",
          ]),
        ]),
      ];
    },
  };

  var LABEL = {
    overview: "today’s overview",
    activity: "your activity",
    summaries: "your summaries",
    people: "your allies",
    integrations: "your integrations",
    preferences: "your preferences",
    account: "your account",
  };

  function render() {
    loading(LABEL[section] || "this page");
    load(section)
      .then(function (result) {
        content.removeAttribute("aria-busy");
        if (result.signedOut) return signedOut();
        show(RENDER[section](result.data));
      })
      .catch(function () {
        failed("Hearally could not be reached, or something went wrong on our side. Your information is safe.", render);
      });
  }

  // ---------- page wiring ----------

  var toggle = document.getElementById("menu-toggle");
  if (toggle) {
    var navEl = document.getElementById("dashboard-nav");
    toggle.addEventListener("click", function () {
      var open = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!open));
      navEl.classList.toggle("hidden", open);
    });
  }
  var out = document.getElementById("sign-out");
  if (out) out.addEventListener("click", signOut);

  if (section === "callback") finishSignIn();
  else if (section === "plaud-callback") finishPlaudSignIn();
  else if (section === "signed-out") clearSession();
  else if (RENDER[section]) render();
})();
