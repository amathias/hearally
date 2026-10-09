// Video slots on the public pages: <figure data-video="name" hidden></figure>.
//
// A slot stays hidden until its video is listed in /media/videos.json, so the
// site never shows an empty player. A listed video is played from
// /media/<name>.mp4, with /media/<name>.jpg as its still and
// /media/<name>.vtt as its captions (shown by default). Nothing plays until
// it is pressed. See web/README.md, "Videos".

(function () {
  "use strict";

  var slots = document.querySelectorAll("figure[data-video]");
  if (!slots.length || !window.fetch) return;

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      node.setAttribute(k, attrs[k]);
    });
    if (text) node.textContent = text;
    return node;
  }

  fetch("/media/videos.json", { cache: "no-cache" })
    .then(function (r) {
      return r.ok ? r.json() : { videos: {} };
    })
    .then(function (manifest) {
      var videos = (manifest && manifest.videos) || {};
      Array.prototype.forEach.call(slots, function (slot) {
        var name = slot.getAttribute("data-video");
        var v = videos[name];
        if (!v || !/^[a-z0-9-]+$/.test(name)) return;
        var bust = v.version ? "?v=" + encodeURIComponent(v.version) : "";
        var base = "/media/" + name;
        var video = el("video", {
          controls: "",
          preload: "none",
          playsinline: "",
          poster: base + ".jpg" + bust,
          class: "w-full rounded-xl border border-line bg-ink",
          "aria-label": v.title || "Video",
        });
        video.appendChild(el("source", { src: base + ".mp4" + bust, type: "video/mp4" }));
        video.appendChild(el("track", { kind: "captions", src: base + ".vtt" + bust, srclang: "en", label: "English", default: "" }));
        var caption = [v.title, v.duration].filter(Boolean).join(" · ");
        var figcaption = el("figcaption", { class: "mt-2 font-sans text-sm text-muted" }, caption);
        slot.replaceChildren(video, figcaption);
        slot.hidden = false;
      });
    })
    .catch(function () {});
})();
