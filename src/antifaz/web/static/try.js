(function () {
  "use strict";

  var root = document.documentElement;
  var form = document.getElementById("try-form");
  var field = document.getElementById("try-text");
  var submit = document.getElementById("try-submit");
  var example = document.getElementById("try-example");
  var exampleNote = document.getElementById("try-example-note");
  var bytesLine = document.getElementById("try-bytes");
  var wordsLine = document.getElementById("try-words");
  var overLine = document.getElementById("try-over");
  var errorBox = document.getElementById("try-error");
  var errorText = document.getElementById("try-error-text");
  var tooLong = document.getElementById("try-too-long");
  var withoutNer = document.getElementById("try-without-ner");
  var empty = document.getElementById("try-empty");
  var names = document.getElementById("try-names");
  var namesText = document.getElementById("try-names-text");
  var result = document.getElementById("try-result");
  var panes = document.getElementById("try-panes");
  var switcher = result.querySelector(".pswitch");
  var inputPane = document.getElementById("try-input");
  var sentPane = document.getElementById("try-sent");
  var replyPane = document.getElementById("try-reply");
  var blocked = document.getElementById("try-blocked");
  var blockedText = document.getElementById("try-blocked-text");
  var blockedCode = document.getElementById("try-blocked-code");
  var sentFoot = document.getElementById("try-sent-foot");
  var replyFoot = document.getElementById("try-reply-foot");
  var replyBlocked = document.getElementById("try-reply-blocked");
  var found = document.getElementById("try-found");
  var meta = document.getElementById("try-meta");
  var live = document.getElementById("try-live");
  var messages = document.getElementById("try-messages");
  var types = document.getElementById("try-types");
  var encoder = new TextEncoder();
  var MAX_TOKEN = 40;
  var REASONS = ["detector_failed", "egress_guard"];

  function whole(value) {
    var number = Number(value);
    return Number.isInteger(number) && number >= 0 ? number : 0;
  }

  var maxBytes = whole(form.getAttribute("data-max-bytes"));
  var maxWords = whole(form.getAttribute("data-max-words"));
  var busy = false;
  var nerUnused = false;
  var sequence = 0;
  var ended = false;
  var controller = null;
  var DROPPED = [
    0xad, 0xad, 0x300, 0x36f, 0x591, 0x5bd, 0x5bf, 0x5bf, 0x5c1, 0x5c2, 0x5c4, 0x5c5,
    0x5c7, 0x5c7, 0x600, 0x605, 0x610, 0x61a, 0x61c, 0x61c, 0x64b, 0x65f, 0x670, 0x670,
    0x6d6, 0x6dd, 0x6df, 0x6e4, 0x6e7, 0x6e8, 0x6ea, 0x6ed, 0x900, 0x902, 0x93a, 0x93a,
    0x93c, 0x93c, 0x941, 0x948, 0x94d, 0x94d, 0x951, 0x957, 0x962, 0x963, 0xe31, 0xe31,
    0xe34, 0xe3a, 0xe47, 0xe4e, 0x200b, 0x200f, 0x202a, 0x202e, 0x2060, 0x2064,
    0x2066, 0x206f, 0xfe00, 0xfe0f, 0xfeff, 0xfeff
  ];
  var SIGNS = [
    0x5be, 0x5be, 0x5c0, 0x5c0, 0x5c3, 0x5c3, 0x5c6, 0x5c6, 0x5f3, 0x5f4, 0x606, 0x60f,
    0x61b, 0x61b, 0x61d, 0x61f, 0x66a, 0x66d, 0x6d4, 0x6d4, 0x6de, 0x6de, 0x6e9, 0x6e9,
    0x6fd, 0x6fe, 0x903, 0x903, 0x93b, 0x93b, 0x93e, 0x940, 0x949, 0x94c, 0x94e, 0x94f,
    0x964, 0x965, 0x970, 0x970, 0xe3f, 0xe3f, 0xe4f, 0xe4f, 0xe5a, 0xe5b, 0x2000, 0x2bff,
    0x2e00, 0x2e7f, 0x3000, 0x3004, 0x3008, 0x303f, 0xe000, 0xf8ff, 0xfe10, 0xfe6f,
    0xff00, 0xff0f, 0xff1a, 0xff20, 0xff3b, 0xff40, 0xff5b, 0xff65, 0xfff0, 0xffff,
    0x1f000, 0x1ffff, 0xf0000, 0x10ffff
  ];

  function text(key) {
    var item = messages.content.querySelector('[data-msg="' + key + '"]');
    return item ? item.textContent : "";
  }

  function label(type) {
    if (typeof type !== "string") {
      return "";
    }
    var item = null;
    types.content.querySelectorAll("[data-type]").forEach(function (candidate) {
      if (candidate.getAttribute("data-type") === type.toLowerCase()) {
        item = candidate;
      }
    });
    return item ? item.textContent : "";
  }

  function fill(template, values) {
    Object.keys(values).forEach(function (name) {
      template = template.split("{" + name + "}").join(values[name]);
    });
    return template;
  }

  function number(value) {
    return value.toLocaleString(root.lang);
  }

  function counted(value) {
    return typeof value === "number" && Number.isInteger(value) && value >= 0;
  }

  function activity(status) {
    if (status >= 200 && status < 500 && status !== 401 && status !== 403) {
      document.dispatchEvent(new CustomEvent("antifaz:activity"));
    }
  }

  function inRanges(code, ranges) {
    for (var index = 0; index < ranges.length; index += 2) {
      if (code >= ranges[index] && code <= ranges[index + 1]) {
        return true;
      }
    }
    return false;
  }

  function isWord(character) {
    if (character === "_" || (character >= "0" && character <= "9")) {
      return true;
    }
    var code = character.codePointAt(0);
    if (code < 0x80) {
      return (code >= 0x41 && code <= 0x5a) || (code >= 0x61 && code <= 0x7a);
    }
    if (inRanges(code, SIGNS)) {
      return false;
    }
    if (character.toLowerCase() !== character.toUpperCase()) {
      return true;
    }
    return code >= 0x2b0;
  }

  function estimate(value) {
    var count = 0;
    var run = 0;
    for (var character of value) {
      if (inRanges(character.codePointAt(0), DROPPED)) {
        continue;
      }
      if (isWord(character)) {
        if (run === 0 || run === MAX_TOKEN) {
          count += 1;
          run = 0;
        }
        run += 1;
      } else {
        run = 0;
        if (character.trim() !== "") {
          count += 1;
        }
      }
    }
    return count;
  }

  function counter() {
    var value = field.value;
    var used = encoder.encode(value).length;
    bytesLine.textContent = fill(text("count.bytes"), {used: number(used), max: number(maxBytes)});
    bytesLine.classList.toggle("over", used > maxBytes);
    if (!wordsLine) {
      return;
    }
    if (nerUnused) {
      wordsLine.textContent = text("count.words_not_used");
      wordsLine.classList.remove("over");
      overLine.hidden = true;
      return;
    }
    var words = estimate(value);
    wordsLine.textContent = fill(text("count.words"), {used: number(words), max: number(maxWords)});
    wordsLine.classList.toggle("over", words > maxWords);
    overLine.hidden = words <= maxWords;
  }

  function hideError() {
    errorBox.hidden = true;
    errorText.textContent = "";
  }

  function showError(message) {
    errorText.textContent = message;
    errorBox.hidden = false;
  }

  function emptyOut(element) {
    element.replaceChildren();
  }

  function clearResult() {
    names.hidden = true;
    namesText.textContent = "";
    result.hidden = true;
    empty.hidden = false;
    [inputPane, sentPane, replyPane, meta].forEach(emptyOut);
    found.querySelectorAll("dd").forEach(function (item) {
      item.remove();
    });
    blockedText.textContent = "";
    blockedCode.textContent = "";
    live.textContent = "";
  }

  function wipe() {
    sequence += 1;
    if (controller) {
      controller.abort();
      controller = null;
    }
    field.value = "";
    nerUnused = false;
    hideError();
    if (tooLong) {
      tooLong.hidden = true;
    }
    exampleNote.hidden = true;
    clearResult();
    counter();
  }

  function leave() {
    ended = true;
    wipe();
  }

  function marked(value, entities) {
    var parts = [];
    var at = 0;
    entities.filter(function (entity) {
      return entity && typeof entity === "object" && counted(entity.start) &&
        counted(entity.end) && entity.start < entity.end && entity.end <= value.length;
    }).sort(function (one, other) {
      return one.start - other.start;
    }).forEach(function (entity) {
      if (entity.start < at) {
        return;
      }
      if (entity.start > at) {
        parts.push(document.createTextNode(value.slice(at, entity.start)));
      }
      var mark = document.createElement("mark");
      mark.classList.add("pii");
      mark.textContent = value.slice(entity.start, entity.end);
      var name = label(entity.type);
      if (name) {
        mark.setAttribute("data-text-type", name);
      }
      parts.push(mark);
      at = entity.end;
    });
    if (at < value.length) {
      parts.push(document.createTextNode(value.slice(at)));
    }
    return parts;
  }

  function segments(list, restored) {
    var parts = [];
    (Array.isArray(list) ? list : []).forEach(function (segment) {
      if (!segment || typeof segment !== "object") {
        return;
      }
      if (typeof segment.marker === "string") {
        var item = document.createElement("span");
        if (restored && typeof segment.value === "string") {
          item.classList.add("restored");
          item.textContent = segment.value;
        } else {
          item.classList.add("marker", "ident");
          item.textContent = segment.marker;
        }
        parts.push(item);
      } else if (typeof segment.text === "string") {
        parts.push(document.createTextNode(segment.text));
      }
    });
    return parts;
  }

  function counts(list) {
    found.querySelectorAll("dd").forEach(function (item) {
      item.remove();
    });
    var hidden = 0;
    (Array.isArray(list) ? list : []).forEach(function (count) {
      if (!count || typeof count !== "object" || !counted(count.found) || !counted(count.hidden)) {
        return;
      }
      hidden += count.hidden;
      var name = label(count.type);
      if (!name) {
        return;
      }
      var item = document.createElement("dd");
      var amount = document.createElement("b");
      item.textContent = name;
      amount.textContent = number(count.found);
      item.appendChild(amount);
      found.appendChild(item);
    });
    found.hidden = !found.querySelector("dd");
    return hidden;
  }

  function metaLine(hidden, version, checked) {
    var parts = [];
    var line = document.createElement("span");
    line.textContent = hidden === 1 ? text("meta.hidden_one")
      : fill(text("meta.hidden"), {n: number(hidden)});
    parts.push(line);
    if (typeof version === "string" && version !== "") {
      var policy = document.createElement("span");
      var pieces = text("meta.policy").split("{version}");
      var ident = document.createElement("span");
      ident.classList.add("ident");
      ident.textContent = version;
      policy.append(pieces[0], ident, pieces.slice(1).join(""));
      parts.push(policy);
    }
    var local = document.createElement("span");
    local.textContent = text("meta.local");
    parts.push(local);
    if (!checked) {
      var unchecked = document.createElement("span");
      unchecked.classList.add("warn");
      unchecked.textContent = text("names.title");
      parts.push(unchecked);
    }
    meta.replaceChildren.apply(meta, parts);
  }

  function shown(data, snapshot, askedNer) {
    if (!data || typeof data !== "object") {
      showError(text("error.internal"));
      return;
    }
    var checked = data.names_checked === true;
    var isBlocked = data.outcome === "blocked";
    var matches = data.length_utf16 === snapshot.length;
    nerUnused = !askedNer && field.value === snapshot;
    counter();
    if (!matches) {
      showError(text("mismatch"));
    }
    var entities = matches && Array.isArray(data.entities) ? data.entities : [];
    inputPane.replaceChildren.apply(inputPane, marked(snapshot, entities));
    sentPane.replaceChildren.apply(sentPane, isBlocked ? [] : segments(data.sent, false));
    replyPane.replaceChildren.apply(replyPane, isBlocked ? [] : segments(data.reply, true));
    sentPane.hidden = isBlocked;
    sentFoot.hidden = isBlocked;
    replyPane.hidden = isBlocked;
    replyFoot.hidden = isBlocked;
    blocked.hidden = !isBlocked;
    replyBlocked.hidden = !isBlocked;
    var reason = REASONS.indexOf(data.blocked_reason) === -1 ? "" : data.blocked_reason;
    blockedText.textContent = reason ? text("blocked." + reason) : "";
    blockedCode.textContent = reason;
    var hidden = counts(data.counts);
    metaLine(hidden, data.policy_version, checked);
    names.hidden = checked || isBlocked;
    namesText.textContent = data.ner === "off" ? text("names.off") : text("names.body");
    empty.hidden = true;
    result.hidden = false;
    var said = isBlocked ? text("blocked.title") : hidden === 1 ? text("meta.hidden_one")
      : fill(text("meta.hidden"), {n: number(hidden)});
    live.textContent = checked || isBlocked ? said
      : fill(text("live.unchecked"), {counts: said, names: text("names.title")});
  }

  function errorCode(data) {
    var error = data && typeof data === "object" ? data.error : null;
    return error && typeof error === "object" && typeof error.code === "string" ? error.code : "";
  }

  function failed(message) {
    clearResult();
    showError(message);
  }

  function refused(response, code) {
    var status = response.status;
    clearResult();
    if (status === 400) {
      showError(text("error.bad_body"));
    } else if (status === 408) {
      showError(text("error.timeout"));
    } else if (status === 413 && code === "text_too_long_for_ner" && tooLong) {
      tooLong.hidden = false;
      withoutNer.focus();
    } else if (status === 413) {
      showError(fill(text("error.too_large"), {max: number(maxBytes)}));
    } else if (status === 429) {
      var wait = Number(response.headers.get("Retry-After"));
      showError(Number.isInteger(wait) && wait > 0 && wait <= 3600
        ? fill(text("error.too_many"), {seconds: number(wait)}) : text("error.too_many_later"));
    } else if (status === 503) {
      showError(text(code === "ner_busy" ? "error.ner_busy" : "error.try_busy"));
    } else if (status === 500) {
      showError(text("error.internal"));
    } else {
      showError(text("error.refused"));
    }
  }

  function done() {
    busy = false;
    submit.removeAttribute("aria-disabled");
    form.removeAttribute("aria-busy");
  }

  function send(useNer) {
    if (busy || ended) {
      return;
    }
    var value = field.value;
    hideError();
    if (tooLong) {
      if (tooLong.contains(document.activeElement)) {
        field.focus();
      }
      tooLong.hidden = true;
    }
    if (typeof value.isWellFormed === "function" && !value.isWellFormed()) {
      failed(text("error.bad_body"));
      return;
    }
    if (encoder.encode(value).length > maxBytes) {
      failed(fill(text("error.too_large"), {max: number(maxBytes)}));
      return;
    }
    busy = true;
    sequence += 1;
    var mine = sequence;
    controller = new AbortController();
    submit.setAttribute("aria-disabled", "true");
    form.setAttribute("aria-busy", "true");
    var body = {text: value, lang: root.lang};
    if (!useNer) {
      body.ner = false;
    }
    var csrf = document.querySelector("meta[name=antifaz-csrf]");

    function current() {
      if (mine === sequence && !ended) {
        return true;
      }
      done();
      return false;
    }

    fetch("/panel/api/try", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers: {"Content-Type": "application/json", "X-Antifaz-CSRF": csrf ? csrf.content : ""},
      body: JSON.stringify(body),
      signal: controller.signal
    }).then(function (response) {
      activity(response.status);
      if (!current()) {
        return;
      }
      if (response.status === 401) {
        done();
        document.dispatchEvent(new CustomEvent("antifaz:unauthorized"));
        return;
      }
      return response.json().then(function (data) {
        if (!current()) {
          return;
        }
        done();
        if (response.status === 200) {
          shown(data, value, useNer);
        } else {
          refused(response, errorCode(data));
        }
      }, function () {
        if (!current()) {
          return;
        }
        done();
        refused(response, "");
      });
    }, function () {
      if (!current()) {
        return;
      }
      done();
      failed(text("error.network"));
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    send(true);
  });

  field.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.altKey) {
      event.preventDefault();
      send(true);
    }
  });

  field.addEventListener("input", function () {
    sequence += 1;
    nerUnused = false;
    exampleNote.hidden = field.value !== text("example");
    if (tooLong) {
      tooLong.hidden = true;
    }
    counter();
  });

  if (withoutNer) {
    withoutNer.addEventListener("click", function () {
      field.focus();
      send(false);
    });
  }

  example.addEventListener("click", function () {
    field.value = text("example");
    nerUnused = false;
    exampleNote.hidden = false;
    counter();
    field.focus();
  });

  switcher.querySelectorAll("button").forEach(function (button) {
    button.addEventListener("click", function () {
      var column = button.getAttribute("data-p");
      panes.setAttribute("data-p", column);
      switcher.setAttribute("data-p", column);
      switcher.querySelectorAll("button").forEach(function (other) {
        other.setAttribute("aria-pressed", other === button ? "true" : "false");
      });
    });
  });

  var platform = (navigator.platform || "").toLowerCase();
  if (platform.indexOf("mac") !== -1 || platform.indexOf("iphone") !== -1 ||
      platform.indexOf("ipad") !== -1) {
    document.getElementById("try-mod").textContent = text("cmd");
  }

  document.addEventListener("antifaz:wipe", leave);
  window.addEventListener("pagehide", leave);

  submit.disabled = false;
  example.disabled = false;
  counter();
}());
