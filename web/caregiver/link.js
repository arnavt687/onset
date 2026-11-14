/* Onset caregiver link: reads the private fragment the agent puts in the caregiver's iMessage.

     <page>#u=<wss LiveKit URL>&w=<watch JWT>&t=<talk JWT, optional>&n=<first name, optional>&e=<expiry unix s>

   The fragment never reaches the web host (browsers do not send it), and app.js wipes it from the
   address bar right after reading. Works in the browser (window.OnsetLink) and in Node (require). */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OnsetLink = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var JWT = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;
  var LOCAL_HOSTS = { "localhost": 1, "127.0.0.1": 1, "[::1]": 1 };

  function jwtPayload(token) {
    try {
      var part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      while (part.length % 4) part += "=";
      var text = typeof atob === "function" ? atob(part) : Buffer.from(part, "base64").toString("binary");
      var obj = JSON.parse(text);
      return obj && typeof obj === "object" ? obj : null;
    } catch (e) {
      return null;
    }
  }

  // wss:// only (ws:// is allowed for a LiveKit dev server on this machine).
  function cleanUrl(raw) {
    if (typeof raw !== "string" || !raw) return null;
    var m = /^(wss?):\/\/([^/?#\s]+)(\/[^?#\s]*)?$/i.exec(raw.trim());
    if (!m) return null;
    var scheme = m[1].toLowerCase(), host = m[2].toLowerCase();
    if (host.indexOf("@") !== -1) return null;
    var bare = host.replace(/:\d+$/, "");
    if (scheme === "ws" && !LOCAL_HOSTS[bare]) return null;
    return scheme + "://" + host + (m[3] && m[3] !== "/" ? m[3] : "");
  }

  // A first name for display only: letters, spaces, apostrophes, dots and hyphens, at most 40 characters.
  function cleanName(raw) {
    if (typeof raw !== "string") return "";
    var s = raw.replace(/[^\p{L}\p{M} .'’-]/gu, "").replace(/\s+/g, " ").trim();
    return s.slice(0, 40).trim();
  }

  /* Parse a location.hash (with or without the leading "#"). Returns
       { ok: true, url, watch, talk (or null), name ("" if absent), expires (unix s) }
     or { ok: false, error: "missing" | "bad" | "expired", expires? }. Never throws. */
  function parse(hash, nowSeconds) {
    var now = typeof nowSeconds === "number" ? nowSeconds : Date.now() / 1000;
    var raw = typeof hash === "string" ? hash.replace(/^#/, "") : "";
    if (!raw) return { ok: false, error: "missing" };
    var p;
    try {
      p = new URLSearchParams(raw);
    } catch (e) {
      return { ok: false, error: "bad" };
    }
    if (!p.get("u") && !p.get("w")) return { ok: false, error: "missing" };
    var url = cleanUrl(p.get("u"));
    var watch = (p.get("w") || "").trim();
    var talk = (p.get("t") || "").trim();
    if (!url || !JWT.test(watch)) return { ok: false, error: "bad" };
    if (talk && !JWT.test(talk)) talk = "";
    var exp = null;
    var eRaw = (p.get("e") || "").trim();
    if (/^\d{9,11}$/.test(eRaw)) exp = parseInt(eRaw, 10);
    else {
      var claims = jwtPayload(watch);
      if (claims && typeof claims.exp === "number") exp = claims.exp;
    }
    if (exp === null) return { ok: false, error: "bad" };
    if (exp <= now) return { ok: false, error: "expired", expires: exp, name: cleanName(p.get("n")) };
    return { ok: true, url: url, watch: watch, talk: talk || null, name: cleanName(p.get("n")), expires: exp };
  }

  return { parse: parse, cleanName: cleanName, cleanUrl: cleanUrl, jwtPayload: jwtPayload };
});
