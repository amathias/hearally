// The setup guide, one step at a time.
//
// The page is complete without this script: every step shows, in order. With
// it, one step shows at a time, with Back and Next, a progress bar, and the
// step in the address (#step-4) so a step can be linked to, bookmarked or
// reloaded. Focus moves to each step's heading, so a screen reader announces
// where you are. No text is added with innerHTML.

(function () {
  "use strict";

  var steps = Array.prototype.slice.call(document.querySelectorAll("#wizard .step"));
  var progress = document.getElementById("wizard-progress");
  var nav = document.getElementById("wizard-nav");
  if (!steps.length || !progress || !nav) return;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  var label = el("p", "font-sans text-sm font-semibold text-muted");
  var track = el("div", "mt-2 h-2 w-full overflow-hidden rounded-full bg-line");
  var bar = el("div", "h-2 rounded-full bg-teal transition-all");
  track.appendChild(bar);
  progress.appendChild(label);
  progress.appendChild(track);

  var back = el("button", "rounded-lg border border-line bg-paper px-5 py-2.5 font-sans font-semibold text-ink hover:bg-sand", "Back");
  back.type = "button";
  var next = el("button", "rounded-lg bg-teal px-6 py-2.5 font-sans font-semibold text-paper hover:brightness-110", "Next");
  next.type = "button";
  var done = el("a", "rounded-lg bg-teal px-6 py-2.5 font-sans font-semibold text-paper no-underline hover:brightness-110", "Open your dashboard");
  done.href = "/dashboard/integrations/";
  var row = el("div", "mt-10 flex flex-wrap items-center justify-between gap-4 border-t border-line pt-6");
  var right = el("div", "flex gap-3");
  right.appendChild(next);
  right.appendChild(done);
  row.appendChild(back);
  row.appendChild(right);
  nav.appendChild(row);

  var current = 0;

  function fromHash() {
    var match = /^#step-(\d+)$/.exec(location.hash);
    var n = match ? Number(match[1]) - 1 : 0;
    return n >= 0 && n < steps.length ? n : 0;
  }

  function show(index, focus) {
    current = index;
    steps.forEach(function (step, i) {
      step.hidden = i !== index;
      step.classList.remove("mt-12");
    });
    label.textContent = "Step " + (index + 1) + " of " + steps.length;
    bar.style.width = ((index + 1) / steps.length) * 100 + "%";
    back.hidden = index === 0;
    next.hidden = index === steps.length - 1;
    done.hidden = index !== steps.length - 1;
    if (focus) {
      var heading = steps[index].querySelector("h2");
      if (heading) heading.focus();
      document.getElementById("wizard").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function go(index) {
    var target = "#step-" + (index + 1);
    if (location.hash !== target) history.pushState(null, "", target);
    show(index, true);
  }

  back.addEventListener("click", function () {
    if (current > 0) go(current - 1);
  });
  next.addEventListener("click", function () {
    if (current < steps.length - 1) go(current + 1);
  });
  // Following a link to #step-4 fires hashchange; Back and Forward fire popstate.
  function sync() {
    if (fromHash() !== current) show(fromHash(), true);
  }
  window.addEventListener("popstate", sync);
  window.addEventListener("hashchange", sync);

  progress.classList.remove("hidden");
  nav.classList.remove("hidden");
  show(fromHash(), Boolean(location.hash));
})();
