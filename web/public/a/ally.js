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
        ? "rounded-lg bg-teal px-5 py-2.5 font-sans font-semibold text-paper hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:brightness-100"
        : "rounded-lg border border-line bg-paper px-4 py-2 font-sans font-semibold text-ink hover:bg-sand disabled:cursor-not-allowed disabled:opacity-50",
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

  // The statements are shown in full; one tick agrees to all of them and to
  // the terms (ARCHITECTURE.md section 7).
  function invited(v) {
    var list = h(
      "ul",
      { class: "mt-4 list-disc space-y-2 pl-6" },
      STATEMENTS.map(function (line) {
        return h("li", { text: line });
      }),
    );
    function link(href, text) {
      return h("a", { href: href, target: "_blank", rel: "noopener", class: "font-semibold text-teal underline underline-offset-4", text: text });
    }
    var box = h("input", { type: "checkbox", id: "agree", class: "mt-1 h-5 w-5 shrink-0 accent-teal" });
    var agreement = h("div", { class: "mt-6 flex items-start gap-3 rounded-xl border border-line bg-paper px-4 py-4" }, [
      box,
      h("label", { for: "agree" }, [
        "I have read the points above, and I agree to Hearally's ",
        link("/terms/", "Terms of use"),
        " and ",
        link("/privacy/", "Privacy policy"),
        ".",
      ]),
    ]);
    var note = h("div", { "aria-live": "polite" });
    var join;
    show([
      h("h1", { class: "text-[1.8rem] font-semibold leading-tight" }, [
        h("span", { class: "text-amber-text", text: v.ally }),
        ", " + v.holder + " would like you as one of their allies.",
      ]),
      p(v.holder + " uses Hearally, which passes along things mentioned out loud near their Plaud recorder to the people they choose. They decide what you hear, and they can change it at any time."),
      h("h2", { class: "mt-8 text-lg font-semibold", text: "Before anything reaches you, please read these." }),
      list,
      agreement,
      h("div", { class: "mt-6 flex flex-wrap gap-3" }, [
        (join = btn("I understand, count me in", function () {
          if (!box.checked) {
            note.replaceChildren(p("Please tick the box above first.", "mt-3 text-flag"));
            return;
          }
          call("confirm").then(function (r) { r.status === 200 ? load() : r.status === 404 ? invalid() : failed(); }, failed);
        }, true)),
        btn("No thanks", function () {
          call("decline").then(function () { load(); }, failed);
        }),
      ]),
      note,
    ]);
    join.disabled = true;
    box.addEventListener("change", function () {
      join.disabled = !box.checked;
    });
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
            var thanks = item.grade === "priority" || item.grade === "call_today"
              ? "Thanks. Things like this will go to the daily list from now on, unless something new comes up."
              : "Thanks, noted.";
            out.replaceChildren(p(r.status === 200 ? thanks : "That could not be saved. Please try again.", "mt-2 font-sans text-sm " + (r.status === 200 ? "text-ok" : "text-flag")));
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

  // The ally's own schedule: when nothing but a heads-up may reach them, and
  // when the daily list arrives (ARCHITECTURE.md section 7).
  function scheduleCard(v) {
    var s = v.settings || {};
    var note = h("div", { "aria-live": "polite" });
    function time(label, value) {
      var input = h("input", { type: "time", name: label.toLowerCase().replace(/ /g, "-"), value: value || "", class: "mt-1 block rounded-lg border border-line bg-paper px-3 py-2" });
      return [input, h("label", { class: "block font-sans text-sm font-semibold" }, [label, input])];
    }
    var quietStart = time("Quiet from", s.quiet_start);
    var quietEnd = time("Quiet until", s.quiet_end);
    var digest = time("Daily list at", s.digest_time);
    var zones = [];
    try { zones = Intl.supportedValuesOf("timeZone"); } catch (e) {}
    var here = "";
    try { here = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) {}
    [s.timezone, here].forEach(function (z) { if (z && zones.indexOf(z) < 0) zones.unshift(z); });
    var zone = h("select", { name: "timezone", class: "mt-1 block w-full rounded-lg border border-line bg-paper px-3 py-2" },
      zones.map(function (z) {
        var o = h("option", { value: z, text: z.replace(/_/g, " ") });
        if (z === s.timezone) o.setAttribute("selected", "");
        return o;
      }));
    return h("section", { class: "mt-10 rounded-xl border border-line bg-paper px-5 py-5" }, [
      h("h2", { class: "text-lg font-semibold", text: "Your schedule" }),
      p("During quiet hours a heads-up still reaches you by email, but you are never phoned, and anything worth a call waits until they end. The daily list comes once a day.", "mt-1 text-muted"),
      h("div", { class: "mt-4 grid gap-4 sm:grid-cols-3" }, [quietStart[1], quietEnd[1], digest[1]]),
      h("label", { class: "mt-4 block font-sans text-sm font-semibold" }, ["Time zone", zone]),
      h("div", { class: "mt-4" }, [
        btn("Save", function () {
          call("settings", { settings: { quiet_start: quietStart[0].value, quiet_end: quietEnd[0].value, digest_time: digest[0].value, timezone: zone.value } }).then(function (r) {
            note.replaceChildren(p(r.status === 200 ? "Saved." : (r.data && r.data.detail) || "That could not be saved. Please try again.", "mt-2 font-sans text-sm " + (r.status === 200 ? "text-ok" : "text-flag")));
          }, failed);
        }),
      ]),
      note,
    ]);
  }

  // Heads-up calls (ARCHITECTURE.md section 8): the ally adds their own number,
  // proves it with a code by text, then chooses calls. Never the holder.
  function phoneCard(v) {
    var ph = v.phone || {};
    var note = h("div", { "aria-live": "polite" });
    var body = h("div", {});
    function say(r, ok) {
      note.replaceChildren(p(r.status === 200 ? ok : (r.data && r.data.detail) || "That did not work. Please try again.", "mt-2 font-sans text-sm " + (r.status === 200 ? "text-ok" : "text-flag")));
    }
    function numberForm() {
      var input = h("input", { type: "tel", name: "phone", autocomplete: "tel", inputmode: "tel", placeholder: "555 201 0123", class: "mt-1 block w-full max-w-[16rem] rounded-lg border border-line bg-paper px-3 py-2" });
      body.replaceChildren(
        h("label", { class: "block font-sans text-sm font-semibold" }, ["Your US mobile number", input]),
        h("div", { class: "mt-3" }, [
          btn("Text me a code", function () {
            call("phone", { phone: input.value }).then(function (r) {
              if (r.status === 200) return codeForm(r.data.sent_to);
              say(r, "");
            }, failed);
          }),
        ]),
      );
    }
    function codeForm(sentTo) {
      var input = h("input", { type: "text", name: "code", autocomplete: "one-time-code", inputmode: "numeric", maxlength: "6", class: "mt-1 block w-full max-w-[10rem] rounded-lg border border-line bg-paper px-3 py-2 tracking-[0.3em]" });
      body.replaceChildren(
        p("We texted a code to " + (sentTo || "your number") + ". It works for 10 minutes.", "text-muted"),
        h("label", { class: "mt-3 block font-sans text-sm font-semibold" }, ["The 6-digit code", input]),
        h("div", { class: "mt-3 flex flex-wrap gap-3" }, [
          btn("Confirm", function () {
            call("phone_confirm", { code: input.value }).then(function (r) {
              if (r.status === 200) return load();
              say(r, "");
            }, failed);
          }, true),
          btn("Use a different number", numberForm),
        ]),
      );
    }
    function verified() {
      var box = h("input", { type: "checkbox", id: "calls", name: "calls", class: "mt-1 h-5 w-5 shrink-0 accent-teal" });
      if (ph.calls) box.checked = true;
      box.addEventListener("change", function () {
        call("calls", { on: box.checked }).then(function (r) {
          if (r.status !== 200) box.checked = !box.checked;
          say(r, box.checked ? "Calls are on." : "Calls are off.");
        }, failed);
      });
      body.replaceChildren(
        p("Your number: •••" + ph.last4 + ", confirmed.", "font-semibold"),
        h("div", { class: "mt-4 flex items-start gap-3" }, [
          box,
          h("label", { for: "calls" }, [
            "Call me for heads-ups about " + v.holder + ". Hearally may place an automated call to this number when there is a heads-up: at most 10 a month across " + v.holder + "'s account, and never during your quiet hours. The call reads the heads-up and ends; it asks you for nothing, and it is not an emergency service. You can turn this off at any time.",
          ]),
        ]),
        h("div", { class: "mt-4 flex flex-wrap gap-3" }, [
          btn("Change number", numberForm),
          btn("Remove my number", function () {
            call("phone_remove").then(function () { load(); }, failed);
          }),
        ]),
      );
    }
    if (ph.verified) verified();
    else if (ph.pending) codeForm("");
    else numberForm();
    return h("section", { class: "mt-10 rounded-xl border border-line bg-paper px-5 py-5" }, [
      h("h2", { class: "text-lg font-semibold", text: "Phone calls for heads-ups (optional)" }),
      p("Besides the email, you can be phoned when there is a heads-up. First, prove the number is yours with a code by text.", "mt-1 text-muted"),
      h("div", { class: "mt-4" }, [body]),
      note,
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
      v.level === "off" ? null : phoneCard(v),
      v.level === "off" ? null : scheduleCard(v),
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
