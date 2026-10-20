(function () {
  "use strict";

  var root = document.documentElement;
  var box = document.getElementById("session-warning");
  var banner = document.getElementById("session-banner");
  var messages = document.getElementById("session-messages");
  var main = document.getElementById("main");
  var WARN_MS = 120000;
  var SAFETY_MS = 5000;
  var LONGEST_MS = 2147483647;

  function seconds(meta) {
    var value = meta ? Number(meta.content) : NaN;
    return Number.isInteger(value) && value >= 0 ? value : 0;
  }

  function valid(value) {
    return typeof value === "number" && Number.isInteger(value) && value >= 0;
  }

  function ahead(secondsLeft) {
    return Math.max(0, secondsLeft * 1000 - SAFETY_MS);
  }

  var started = Date.now();
  var idleLength = ahead(seconds(document.querySelector("meta[name=antifaz-session-idle]")));
  var maxDeadline = started + ahead(seconds(document.querySelector("meta[name=antifaz-session-max]")));
  var idleDeadline = Math.min(started + idleLength, maxDeadline);
  var timer = null;
  var shown = "";
  var expired = false;
  var asking = false;

  function text(key) {
    var item = messages.content.querySelector('[data-msg="' + key + '"]');
    return item ? item.textContent : "";
  }

  function languageQuery() {
    if (!new URLSearchParams(location.search).has("lang")) {
      return "";
    }
    return "?lang=" + encodeURIComponent(root.lang);
  }

  function clockTime(when) {
    return new Intl.DateTimeFormat(root.lang, {hour: "2-digit", minute: "2-digit"})
      .format(new Date(when));
  }

  function expire() {
    if (expired) {
      return;
    }
    expired = true;
    clearTimeout(timer);
    timer = null;
    document.dispatchEvent(new CustomEvent("antifaz:wipe"));
    location.replace("/panel/login" + languageQuery());
  }

  function hide() {
    if (!shown) {
      return;
    }
    var hadFocus = box.contains(document.activeElement);
    shown = "";
    box.replaceChildren();
    if (hadFocus) {
      main.focus({preventScroll: true});
    }
  }

  function failed() {
    asking = false;
    var node = box.firstElementChild;
    if (!node) {
      return;
    }
    node.querySelector("[data-text]").textContent = text("failed");
    var button = node.querySelector("[data-continue]");
    if (button) {
      button.removeAttribute("aria-disabled");
    }
  }

  function extended(data) {
    asking = false;
    if (!data || typeof data !== "object" || !valid(data.idle_seconds) ||
        !valid(data.max_seconds)) {
      failed();
      return;
    }
    var now = Date.now();
    maxDeadline = now + ahead(data.max_seconds);
    idleDeadline = Math.min(now + ahead(data.idle_seconds), maxDeadline);
    schedule();
  }

  function extend(event) {
    if (asking || expired) {
      return;
    }
    asking = true;
    event.currentTarget.setAttribute("aria-disabled", "true");
    var csrf = document.querySelector("meta[name=antifaz-csrf]");
    fetch("/panel/api/session", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers: {"Content-Type": "application/json", "X-Antifaz-CSRF": csrf ? csrf.content : ""},
      body: "{}"
    }).then(function (response) {
      if (response.status === 401) {
        expire();
        return;
      }
      if (response.status !== 200) {
        failed();
        return;
      }
      return response.json().then(extended, failed);
    }, failed);
  }

  function show(nearMax, end) {
    var kind = nearMax ? "max" : "idle";
    var key = kind + ":" + end;
    if (shown === key) {
      return;
    }
    var hadFocus = box.contains(document.activeElement);
    shown = key;
    var node = banner.content.firstElementChild.cloneNode(true);
    node.querySelector("[data-text]").textContent = text(kind).replace("{time}", clockTime(end));
    var actions = node.querySelector("[data-actions]");
    if (nearMax) {
      actions.remove();
    } else {
      actions.querySelector("[data-continue]").addEventListener("click", extend);
    }
    box.replaceChildren(node);
    if (hadFocus) {
      main.focus({preventScroll: true});
    }
  }

  function schedule() {
    clearTimeout(timer);
    timer = null;
    if (expired) {
      return;
    }
    var now = Date.now();
    var end = Math.min(idleDeadline, maxDeadline);
    if (now >= end) {
      expire();
      return;
    }
    var warnAt = end - WARN_MS;
    if (now >= warnAt) {
      show(maxDeadline <= idleDeadline, end);
    } else {
      hide();
    }
    var next = now < warnAt ? warnAt : end;
    timer = setTimeout(schedule, Math.min(next - now, LONGEST_MS));
  }

  document.addEventListener("antifaz:activity", function () {
    if (expired) {
      return;
    }
    idleDeadline = Math.min(Date.now() + idleLength, maxDeadline);
    schedule();
  });

  document.addEventListener("antifaz:unauthorized", expire);

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") {
      schedule();
    }
  });

  schedule();
}());
