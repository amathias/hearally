// The ally's page: their whole interface to Hearally (ARCHITECTURE.md section 7).
//
// The link's token arrives in the URL fragment, which browsers never send to
// a server. It is moved into this tab's session storage and removed from the
// address bar at once, then sent to the API in request bodies only.
//
// Amber is the ally's colour (brand/README.md): this page is theirs.

(function () {
  "use strict";

  var API = window.HEARALLY_CONFIG.api;
  var content = document.getElementById("content");
  var KEY = "hearally-ally-token";

  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") el.textContent = attrs[k];
      else if (k === "class") el.className = attrs[k];
      else el.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      if (c) el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return el;
  }
  function p(text, cls) {
    return h("p", { class: cls || "mt-3 text-muted", text: text });
  }
  function btn(label, onClick, primary) {
    var b = h("button", {
      type: "button",
      class: primary
        ? "rounded-lg bg-teal px-5 py-2.5 font-sans font-semibold text-paper hover:brightness-110"
        : "rounded-lg border border-line bg-paper px-4 py-2 font-sans font-semibold text-ink hover:bg-sand",
      text: label,
    });
    b.addEventListener("click", onClick);
    return b;
  }
  function show(nodes) {
    content.replaceChildren.apply(content, nodes.filter(Boolean));
  }
  function when(value) {
    var d = new Date(value);
    return isNaN(d) ? "" : d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  }

  // The token: from the fragment the first time, then from this tab's storage.
  var token = null;
  var match = /(?:^|[#&])t=([A-Za-z0-9_-]{20,100})/.exec(location.hash);
  if (match) {
    token = match[1];
    try {
      sessionStorage.setItem(KEY, token);
    } catch (e) {}
    history.replaceState(null, "", location.pathname);
  } else {
    try {
      token = sessionStorage.getItem(KEY);
    } catch (e) {}
  }

  function call(action, extra) {
    var body = Object.assign({ token: token }, extra || {});
    return fetch(API + "/v1/ally/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        return { status: r.status, data: data };
      });
    });
  }

  function invalid() {
    show([
      h("h1", { class: "text-2xl font-semibold", text: "This link is no longer valid." }),
      p("It may have expired, or the person who shared it may have changed who hears from Hearally. If you were expecting something, ask them."),
    ]);
  }

  function failed() {
    show([h("h1", { class: "text-2xl font-semibold", text: "This could not be loaded." }), p("Please try again in a moment."), h("div", { class: "mt-4" }, [btn("Try again", load)])]);
  }

  var STATEMENTS = [
    "Hearally is not an emergency service. If someone needs help now, call 911.",
    "It passes along things that were said out loud. It does not detect what happened, and it will miss things.",
    "It will sometimes pass along something that turns out to be nothing.",
    "Nobody is standing by, and you are not on duty: there is no promise that anyone sees or acts on a message, including you.",
    "You can stop receiving these at any time, from this page.",
  ];

  function invited(v) {
    var boxes = [];
    var list = h("div", { class: "mt-5" });
    STATEMENTS.forEach(function (line, i) {
      var box = h("input", { type: "checkbox", id: "s" + i, class: "mt-1 h-5 w-5 shrink-0 accent-teal" });
      boxes.push(box);
      list.appendChild(h("div", { class: "mt-3 flex items-start gap-3" }, [box, h("label", { for: "s" + i, text: line })]));
    });
    var note = h("div", { "aria-live": "polite" });
    show([
      h("h1", { class: "text-[1.8rem] font-semibold leading-tight" }, [
        h("span", { class: "text-amber-text", text: v.ally }),
        ", " + v.holder + " would like you as one of their allies.",
      ]),
      p(v.holder + " uses Hearally, which passes along things mentioned out loud near their Plaud recorder to the people they choose. They decide what you hear, and they can change it at any time."),
      h("h2", { class: "mt-8 text-lg font-semibold", text: "Before anything reaches you, please read and tick each of these." }),
      list,
      h("div", { class: "mt-6 flex flex-wrap gap-3" }, [
        btn("I understand, count me in", function () {
          if (boxes.some(function (b) { return !b.checked; })) {
            note.replaceChildren(p("Please tick each line first.", "mt-3 text-flag"));
            return;
          }
          call("confirm").then(function (r) { r.status === 200 ? load() : r.status === 404 ? invalid() : failed(); }, failed);
        }, true),
        btn("No thanks", function () {
          call("decline").then(function () { load(); }, failed);
        }),
      ]),
      note,
    ]);
  }

  var REASONS = [
    ["handled_it", "It's handled"],
    ["known_caller", "I know the caller"],
    ["was_me", "That was me"],
    ["tv_or_radio", "It was the TV or radio"],
    ["not_a_concern", "Not a concern"],
    ["other", "Something else"],
  ];

  function itemRow(item) {
    var out = h("div", { "aria-live": "polite" });
    var select = h("select", { class: "rounded-md border border-line bg-paper px-2 py-1.5 font-sans text-sm", "aria-label": "Why it was nothing" },
      REASONS.map(function (r) { return h("option", { value: r[0], text: r[1] }); }));
    var nothing = h("details", { class: "mt-3" }, [
      h("summary", { class: "cursor-pointer font-sans text-sm font-semibold text-teal", text: "This was nothing" }),
      h("div", { class: "mt-2 flex flex-wrap items-center gap-2" }, [
        select,
        btn("Send", function () {
          call("dismiss", { item_id: item.id, reason: select.value }).then(function (r) {
            out.replaceChildren(p(r.status === 200 ? "Thanks. Hearally will take that into account." : "That could not be saved. Please try again.", "mt-2 font-sans text-sm " + (r.status === 200 ? "text-ok" : "text-flag")));
          }, failed);
        }),
      ]),
      out,
    ]);
    var label = { priority: "Heads-up", call_today: "Worth a call today", daily: "Today's list" }[item.grade] || "";
    return h("li", { class: "border-b border-line py-4 last:border-b-0" }, [
      h("p", { class: "font-sans text-xs font-bold uppercase tracking-[0.12em] text-amber-text", text: label + " · " + when(item.spoken_at) }),
      h("p", { class: "mt-1 text-lg", text: item.summary }),
      nothing,
    ]);
  }

  function confirmed(v) {
    var items = v.items || [];
    show([
      h("h1", { class: "text-[1.8rem] font-semibold leading-tight" }, [
        "What ", v.holder + " shared with ", h("span", { class: "text-amber-text", text: v.ally }),
      ]),
      p(v.level === "alerts_only" ? "You hear heads-ups only." : "You hear heads-ups, things worth a call, and a daily list."),
      items.length
        ? h("ul", { class: "mt-6 rounded-xl border border-line bg-paper px-5" }, items.map(itemRow))
        : h("div", { class: "mt-6 rounded-xl border border-dashed border-line bg-paper/60 px-5 py-4" }, [p("Nothing yet. When something comes up, you will get an email with a link back here.", "text-muted")]),
      h("div", { class: "mt-10" }, [
        btn("Stop receiving these", function () {
          if (!window.confirm("Stop receiving anything from Hearally about " + v.holder + "? They will see that you stopped.")) return;
          call("stop").then(function () { load(); }, failed);
        }),
      ]),
    ]);
  }

  function load() {
    if (!token) return invalid();
    call("view").then(function (r) {
      if (r.status === 404) return invalid();
      if (r.status !== 200) return failed();
      var v = r.data;
      if (v.status === "invited") return invited(v);
      if (v.status === "confirmed") return confirmed(v);
      show([
        h("h1", { class: "text-2xl font-semibold", text: v.status === "declined" ? "You said no thanks." : "You have stopped receiving these." }),
        p("Nothing from Hearally will reach you. If that changes, " + v.holder + " can invite you again."),
      ]);
    }, failed);
  }

  load();
})();
