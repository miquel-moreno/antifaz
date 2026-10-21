(function () {
  "use strict";

  var root = document.documentElement;

  var tablist = document.querySelector("[role=tablist]");
  var tabs = Array.prototype.slice.call(tablist.querySelectorAll("[role=tab]"));
  var crumb = document.getElementById("crumb-name");

  function horizontal() {
    return tabs.length > 1 && tabs[1].offsetTop === tabs[0].offsetTop;
  }

  function syncOrientation() {
    tablist.setAttribute("aria-orientation", horizontal() ? "horizontal" : "vertical");
  }

  function reveal(tab) {
    if (tablist.scrollWidth <= tablist.clientWidth) {
      return;
    }
    tab.scrollIntoView({block: "nearest", inline: "nearest"});
  }

  function enter(view) {
    view.classList.remove("enter");
    void view.offsetWidth;
    view.classList.add("enter");
  }

  function select(tab, focus) {
    tabs.forEach(function (other) {
      var on = other === tab;
      other.setAttribute("aria-selected", on ? "true" : "false");
      other.tabIndex = on ? 0 : -1;
      document.getElementById(other.getAttribute("aria-controls")).hidden = !on;
    });
    var view = document.getElementById(tab.getAttribute("aria-controls"));
    crumb.textContent = tab.querySelector(".app-tab-label").textContent;
    enter(view);
    if (focus) {
      tab.focus();
    }
    reveal(tab);
    if (view.id === "panel-status") {
      statusFirstShown();
    }
  }

  tabs.forEach(function (tab, index) {
    tab.addEventListener("click", function () {
      select(tab, false);
    });
    tab.addEventListener("keydown", function (event) {
      var next = horizontal() ? "ArrowRight" : "ArrowDown";
      var previous = horizontal() ? "ArrowLeft" : "ArrowUp";
      var to = null;
      if (event.key === next) {
        to = tabs[(index + 1) % tabs.length];
      } else if (event.key === previous) {
        to = tabs[(index - 1 + tabs.length) % tabs.length];
      } else if (event.key === "Home") {
        to = tabs[0];
      } else if (event.key === "End") {
        to = tabs[tabs.length - 1];
      }
      if (to) {
        event.preventDefault();
        select(to, true);
      }
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey || event.repeat) {
      return;
    }
    if (event.target.closest("input,textarea,select,[contenteditable]")) {
      return;
    }
    if (!event.target.closest("[role=tablist],#main")) {
      return;
    }
    var number = ["1", "2", "3", "4", "5"].indexOf(event.key);
    if (number !== -1 && tabs[number]) {
      event.preventDefault();
      select(tabs[number], true);
    }
  });

  function edges() {
    var max = tablist.scrollWidth - tablist.clientWidth;
    tablist.classList.toggle("is-more-start", max > 1 && tablist.scrollLeft > 1);
    tablist.classList.toggle("is-more-end", max > 1 && tablist.scrollLeft < max - 1);
  }

  function layout() {
    syncOrientation();
    edges();
  }

  layout();
  tablist.addEventListener("scroll", edges, {passive: true});
  var sizes = new ResizeObserver(layout);
  sizes.observe(tablist);
  tabs.forEach(function (tab) {
    sizes.observe(tab);
  });

  var alertBox = document.getElementById("logout-alert");
  var alertText = document.getElementById("logout-alert-text");
  var messages = document.getElementById("panel-messages");
  var logoutButtons = Array.prototype.slice.call(document.querySelectorAll("[data-logout]"));

  function languageQuery() {
    if (!new URLSearchParams(location.search).has("lang")) {
      return "";
    }
    return "?lang=" + encodeURIComponent(root.lang);
  }

  var leaving = false;
  document.addEventListener("antifaz:wipe", function () {
    leaving = true;
  });

  function leave() {
    if (leaving) {
      return;
    }
    document.dispatchEvent(new CustomEvent("antifaz:wipe"));
    location.replace("/panel/login" + languageQuery());
  }

  function message(key) {
    var item = messages.content.querySelector('[data-msg="' + key + '"]');
    return item ? item.textContent : "";
  }

  function activity(status) {
    if (status >= 200 && status < 500 && status !== 401 && status !== 403) {
      document.dispatchEvent(new CustomEvent("antifaz:activity"));
    }
  }

  function failed(key, button) {
    alertText.textContent = message(key);
    alertBox.hidden = false;
    logoutButtons.forEach(function (other) {
      other.disabled = false;
    });
    button.focus();
  }

  function logout(button) {
    var meta = document.querySelector("meta[name=antifaz-csrf]");
    logoutButtons.forEach(function (other) {
      other.disabled = true;
    });
    document.dispatchEvent(new CustomEvent("antifaz:logout"));
    fetch("/panel/logout", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers: {"Content-Type": "application/json", "X-Antifaz-CSRF": meta ? meta.content : ""},
      body: "{}"
    }).then(function (response) {
      activity(response.status);
      if (response.status === 200 || response.status === 401) {
        leave();
      } else {
        failed("refused", button);
      }
    }, function () {
      failed("network", button);
    });
  }

  logoutButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      logout(button);
    });
  });

  var settingsButton = document.getElementById("settings-btn");
  var settingsMenu = document.getElementById("settings-menu");

  function menuOpen() {
    return settingsButton.getAttribute("aria-expanded") === "true";
  }

  function setMenu(open, giveFocusBack) {
    settingsButton.setAttribute("aria-expanded", open ? "true" : "false");
    settingsMenu.hidden = !open;
    if (open) {
      settingsMenu.querySelector("a[href],button").focus();
    } else if (giveFocusBack) {
      settingsButton.focus();
    }
  }

  settingsButton.addEventListener("click", function () {
    setMenu(!menuOpen(), false);
  });
  function buttonShown() {
    return settingsButton.offsetParent !== null;
  }

  function closeIfHidden() {
    if (menuOpen() && !buttonShown()) {
      setMenu(false, false);
    }
  }

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || !menuOpen()) {
      return;
    }
    if (!buttonShown()) {
      setMenu(false, false);
      return;
    }
    event.preventDefault();
    setMenu(false, true);
  });
  document.addEventListener("click", function (event) {
    if (menuOpen() && !settingsMenu.contains(event.target) && !settingsButton.contains(event.target)) {
      setMenu(false, false);
    }
  });

  function focusLeaves(event) {
    var next = event.relatedTarget;
    if (menuOpen() && next && !settingsMenu.contains(next) && next !== settingsButton) {
      setMenu(false, false);
    }
  }

  settingsMenu.addEventListener("focusout", focusLeaves);
  settingsButton.addEventListener("focusout", focusLeaves);
  new ResizeObserver(closeIfHidden).observe(document.documentElement);

  var dark = matchMedia("(prefers-color-scheme: dark)");
  function current() {
    return root.getAttribute("data-theme") || (dark.matches ? "dark" : "light");
  }
  function paint() {
    var next = current() === "dark" ? "light" : "dark";
    document.querySelectorAll(".theme-btn").forEach(function (themeButton) {
      themeButton.setAttribute("aria-label", themeButton.getAttribute("data-text-" + next));
      themeButton.querySelector(".theme-icon").setAttribute("href", next === "dark" ? "#i-moon" : "#i-sun");
    });
    document.querySelectorAll(".theme-switch").forEach(function (themeSwitch) {
      themeSwitch.setAttribute("aria-checked", next === "light" ? "true" : "false");
    });
  }
  document.querySelectorAll(".theme-btn,.theme-switch").forEach(function (themeButton) {
    themeButton.addEventListener("click", function () {
      root.setAttribute("data-theme", current() === "dark" ? "light" : "dark");
      paint();
    });
  });
  dark.addEventListener("change", paint);
  paint();

  var statusTab = document.getElementById("tab-status");
  var statusGrid = document.getElementById("status-grid");
  var statusTime = document.getElementById("status-time");
  var statusButton = document.getElementById("status-refresh");
  var statusAlert = document.getElementById("status-alert");
  var statusAlertText = document.getElementById("status-alert-text");
  var statusMessages = document.getElementById("status-messages");
  var statusSequence = 0;
  var statusStarted = false;
  var statusCheckedAt = null;
  var statusInFlight = null;
  var TONES = ["ok", "warn", "danger", "neutral"];
  var HOST_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789.-:[]";
  var VERSION_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789.+-";

  function statusText(key) {
    var item = statusMessages.content.querySelector('[data-msg="' + key + '"]');
    return item ? item.textContent : "";
  }

  function known(value, allowed) {
    return typeof value === "string" && allowed.indexOf(value) !== -1;
  }

  function label(group, value, allowed) {
    return known(value, allowed) ? statusText(group + "." + value) : statusText("unknown");
  }

  function onlyChars(value, chars, longest) {
    if (typeof value !== "string" || value === "" || value.length > longest) {
      return false;
    }
    for (var index = 0; index < value.length; index += 1) {
      if (chars.indexOf(value.charAt(index)) === -1) {
        return false;
      }
    }
    return true;
  }

  function setPill(pill, tone, text) {
    TONES.forEach(function (name) {
      pill.classList.toggle(name, name === tone);
    });
    pill.querySelector("[data-label]").textContent = text;
  }

  function count(value) {
    if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
      return statusText("unknown");
    }
    return value.toLocaleString(root.lang);
  }

  function uptime(seconds) {
    if (typeof seconds !== "number" || !Number.isInteger(seconds) || seconds < 0) {
      return statusText("unknown");
    }
    var minutes = Math.floor(seconds / 60);
    var hours = Math.floor(minutes / 60);
    var days = Math.floor(hours / 24);
    if (days > 0) {
      return statusText("uptime.days").replace("{days}", String(days))
        .replace("{hours}", String(hours % 24));
    }
    if (hours > 0) {
      return statusText("uptime.hours").replace("{hours}", String(hours))
        .replace("{minutes}", String(minutes % 60));
    }
    if (minutes > 0) {
      return statusText("uptime.minutes").replace("{minutes}", String(minutes));
    }
    return statusText("uptime.less");
  }

  function clockTime(date) {
    return new Intl.DateTimeFormat(root.lang, {hour: "2-digit", minute: "2-digit"}).format(date);
  }

  function address(value) {
    if (value === "default" || value === "custom") {
      return statusText("address." + value);
    }
    return onlyChars(value, HOST_CHARS, 300) ? value : statusText("unknown");
  }

  function field(id, text) {
    document.getElementById(id).textContent = text;
  }

  function renderProviders(providers) {
    var list = Array.isArray(providers) ? providers : [];
    document.querySelectorAll("[data-provider]").forEach(function (row) {
      var found = null;
      list.forEach(function (item) {
        if (item && typeof item === "object" && item.id === row.getAttribute("data-provider")) {
          found = item;
        }
      });
      var info = found || {};
      row.querySelector("[data-address]").textContent = address(info.address);
      var hasKey = info.has_key;
      setPill(row.querySelector("[data-key]"), hasKey === true ? "ok" : "neutral",
        hasKey === true ? statusText("key.yes") : hasKey === false ? statusText("key.no")
          : statusText("unknown"));
      row.querySelector("[data-credentials]").hidden = info.has_credentials !== true;
    });
  }

  function renderStatus(data) {
    var detectors = data.detectors && typeof data.detectors === "object" ? data.detectors : {};
    var limits = data.limits && typeof data.limits === "object" ? data.limits : {};
    var overall = known(data.overall, ["ok", "warn", "danger"]) ? data.overall : "neutral";
    setPill(document.getElementById("status-overall"), overall,
      label("overall", data.overall, ["ok", "warn", "danger"]));
    var version = typeof data.version === "string" ? data.version.toLowerCase() : "";
    field("status-version", onlyChars(version, VERSION_CHARS, 64) ? data.version
      : statusText("unknown"));
    field("status-uptime", uptime(data.uptime_seconds));
    field("status-scheme", label("scheme", data.panel_scheme, ["https", "http_localhost"]));
    field("status-hosts", count(limits.allowed_hosts));
    field("status-origins", count(limits.allowed_origins));
    field("status-proxies", count(limits.trusted_proxies));
    var states = ["off", "ok", "starting", "circuit_open", "closed"];
    var ner = known(detectors.ner, states) ? detectors.ner : "";
    var nerTone = {off: "neutral", ok: "ok", starting: "warn", circuit_open: "warn",
      closed: "danger"};
    setPill(document.getElementById("status-ner"), ner ? nerTone[ner] : "neutral",
      label("ner", detectors.ner, states));
    document.querySelectorAll("[data-ner]").forEach(function (item) {
      var now = item.getAttribute("data-ner") === ner;
      item.classList.toggle("now", now);
      if (now) {
        item.setAttribute("aria-current", "true");
      } else {
        item.removeAttribute("aria-current");
      }
      item.querySelector(".tag").hidden = !now;
    });
    renderProviders(data.providers);
  }

  function statusIdle(fromButton) {
    statusInFlight = null;
    statusGrid.setAttribute("aria-busy", "false");
    statusButton.removeAttribute("aria-disabled");
    if (fromButton) {
      statusButton.focus();
    }
  }

  function statusShown(data, fromButton) {
    renderStatus(data);
    statusCheckedAt = new Date();
    statusGrid.classList.remove("is-stale");
    statusGrid.hidden = false;
    statusTime.textContent = statusText("checked").replace("{time}", clockTime(statusCheckedAt));
    statusIdle(fromButton);
  }

  function statusFailed(key, fromButton) {
    statusAlertText.textContent = statusText("error." + key);
    statusAlert.hidden = false;
    setPill(document.getElementById("status-overall"), "neutral", statusText("overall.stale"));
    statusGrid.classList.add("is-stale");
    statusTime.textContent = statusCheckedAt
      ? statusText("checked.old").replace("{time}", clockTime(statusCheckedAt)) : "";
    statusIdle(fromButton);
  }

  function loadStatus(fromButton) {
    if (statusInFlight) {
      if (!fromButton || statusInFlight.fromButton) {
        return;
      }
      statusInFlight.controller.abort();
    }
    statusSequence += 1;
    var sequence = statusSequence;
    var controller = new AbortController();
    statusInFlight = {controller: controller, fromButton: fromButton};
    statusAlert.hidden = true;
    statusAlertText.textContent = "";
    statusGrid.setAttribute("aria-busy", "true");
    statusButton.setAttribute("aria-disabled", "true");
    statusTime.textContent = statusText("checking");
    fetch("/panel/api/status", {
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      signal: controller.signal
    }).then(function (response) {
      activity(response.status);
      if (sequence !== statusSequence) {
        return;
      }
      if (response.status === 401) {
        leave();
        return;
      }
      if (response.status !== 200) {
        statusFailed("refused", fromButton);
        return;
      }
      return response.json().then(function (data) {
        if (sequence !== statusSequence) {
          return;
        }
        if (data && typeof data === "object") {
          statusShown(data, fromButton);
        } else {
          statusFailed("refused", fromButton);
        }
      }, function () {
        if (sequence === statusSequence) {
          statusFailed("refused", fromButton);
        }
      });
    }, function () {
      if (sequence === statusSequence) {
        statusFailed("network", fromButton);
      }
    });
  }

  function statusFirstShown() {
    if (!statusStarted) {
      statusStarted = true;
      loadStatus(false);
    }
  }

  statusButton.addEventListener("click", function () {
    loadStatus(true);
  });

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && statusStarted &&
        statusTab.getAttribute("aria-selected") === "true") {
      loadStatus(false);
    }
  });

  window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
      location.reload();
    }
  });
}());
