// Google Analytics 4: property "Hearally web" (G-H6X9QJ05LG), on every page.
//
// What it may see, and nothing else: which page was opened (its path), and
// the path of the page before it. Never a query string or a fragment, where
// the ally link's token (/a/#t=...) and the one-time sign-in codes
// (?code=...) travel; never page content, item summaries, names or email
// addresses; no user ID. Google signals and ad personalisation are off.
//
// Loaded last on each page (defer, at the end of <body>), so the ally page
// has already moved its token out of the address bar before this runs.
// See docs/ARCHITECTURE.md section 10.

(function () {
  "use strict";

  var ID = "G-H6X9QJ05LG";

  function path(url) {
    if (!url) return undefined;
    try {
      var u = new URL(url, location.href);
      return u.origin + u.pathname;
    } catch (e) {
      return undefined;
    }
  }

  window.dataLayer = window.dataLayer || [];
  function gtag() {
    window.dataLayer.push(arguments);
  }

  gtag("js", new Date());
  gtag("config", ID, {
    page_location: path(location.href),
    page_referrer: path(document.referrer),
    allow_google_signals: false,
    allow_ad_personalization_signals: false,
  });

  var script = document.createElement("script");
  script.async = true;
  script.src = "https://www.googletagmanager.com/gtag/js?id=" + ID;
  document.head.appendChild(script);
})();
