/* SubaDubaLingo website: theme picker, language picker and the latest-release check. No dependencies. */
(function () {
  "use strict";
  var REPO = "https://github.com/swaipa46/subadubalingo";
  var API = "https://api.github.com/repos/swaipa46/subadubalingo/releases/latest";
  var FALLBACK_VERSION = "2.0.0";
  var LANGS = ["da", "sv", "no", "en"];
  var root = document.documentElement;
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  };
  var state = { lang: "da", release: null }; // release: {version} | "none" | null (unknown)

  /* ---- Theme: light, dark or auto (follows the visitor's system) ---- */
  function setTheme(t, save) {
    if (t === "light" || t === "dark") root.dataset.theme = t; else delete root.dataset.theme;
    document.querySelectorAll("[data-theme-set]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.themeSet === (t || "auto")));
    });
    if (save) store.set("sdl-theme", t);
  }

  /* ---- Language ---- */
  function pickLang() {
    var saved = store.get("sdl-lang");
    if (LANGS.indexOf(saved) >= 0) return saved;
    var list = navigator.languages || [navigator.language || ""];
    for (var i = 0; i < list.length; i++) {
      var l = String(list[i]).toLowerCase().slice(0, 2);
      if (l === "nb" || l === "nn") l = "no";
      if (LANGS.indexOf(l) >= 0) return l;
    }
    return "en";
  }
  function tr(key) {
    var d = window.I18N || {};
    return (d[state.lang] && d[state.lang][key]) || (d.da && d.da[key]) || "";
  }
  function setLang(lang, save) {
    state.lang = lang;
    root.lang = lang === "no" ? "nb" : lang;
    document.querySelectorAll("[data-i18n]").forEach(function (el) { el.textContent = tr(el.dataset.i18n); });
    document.querySelectorAll("[data-i18n-alt]").forEach(function (el) { el.alt = tr(el.dataset.i18nAlt); });
    document.querySelectorAll("[data-i18n-title]").forEach(function (el) { el.title = tr(el.dataset.i18nTitle); });
    document.querySelectorAll("[data-i18n-aria]").forEach(function (el) { el.setAttribute("aria-label", tr(el.dataset.i18nAria)); });
    document.querySelectorAll("[data-i18n-content]").forEach(function (el) { el.content = tr(el.dataset.i18nContent); });
    document.title = tr("meta.title");
    document.querySelectorAll("[data-lang-set]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.langSet === lang));
    });
    renderRelease();
    if (save) store.set("sdl-lang", lang);
  }

  /* ---- Latest release: show the version, or "coming soon" before the first release ---- */
  function renderRelease() {
    var el = document.getElementById("release");
    var foot = document.getElementById("footer-version");
    var buttons = document.querySelectorAll("#download, [data-download]");
    if (state.release === "none") {
      el.textContent = tr("hero.soon");
      el.className = "release soon";
      buttons.forEach(function (b) { b.href = REPO; });
      foot.textContent = tr("footer.version").replace("{v}", FALLBACK_VERSION);
    } else if (state.release) {
      el.textContent = tr("hero.version").replace("{v}", state.release.version);
      el.className = "release";
      foot.textContent = tr("footer.version").replace("{v}", state.release.version);
    } else {
      el.textContent = "";
      foot.textContent = tr("footer.version").replace("{v}", FALLBACK_VERSION);
    }
  }
  function checkRelease() {
    if (!window.fetch) return;
    fetch(API, { headers: { Accept: "application/vnd.github+json" } })
      .then(function (r) {
        if (r.status === 404) { state.release = "none"; return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        if (data && data.tag_name) state.release = { version: String(data.tag_name).replace(/^v/i, "") };
        renderRelease();
      })
      .catch(function () { /* offline or rate-limited: keep the plain download link */ });
  }

  document.querySelectorAll("[data-theme-set]").forEach(function (b) {
    b.addEventListener("click", function () { setTheme(b.dataset.themeSet, true); });
  });
  document.querySelectorAll("[data-lang-set]").forEach(function (b) {
    b.addEventListener("click", function () { setLang(b.dataset.langSet, true); });
  });
  setTheme(store.get("sdl-theme") || "auto", false);
  setLang(pickLang(), false);
  checkRelease();
})();
