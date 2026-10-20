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
    if (status !== 401 && status !== 403) {
      document.dispatchEvent(new CustomEvent("antifaz:activity"));
    }
  }

  function isWord(character) {
    if (character === "_" || (character >= "0" && character <= "9")) {
      return true;
    }
    if (character.toLowerCase() !== character.toUpperCase()) {
      return true;
    }
    var code = character.codePointAt(0);
    return code >= 0x3040 && !(code >= 0xfe00 && code <= 0xfe6f) &&
      !(code >= 0xff00 && code <= 0xff20) && code < 0x1f000;
  }

  function estimate(value) {
    var count = 0;
    var run = 0;
    for (var character of value) {
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

  function wipe() {
    field.value = "";
    nerUnused = false;
    hideError();
    if (tooLong) {
      tooLong.hidden = true;
    }
    names.hidden = true;
    namesText.textContent = "";
    result.hidden = true;
    empty.hidden = false;
    exampleNote.hidden = true;
    [inputPane, sentPane, replyPane, meta].forEach(emptyOut);
    found.querySelectorAll("dd").forEach(function (item) {
      item.remove();
    });
    blockedText.textContent = "";
    blockedCode.textContent = "";
    live.textContent = "";
    counter();
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
    nerUnused = !askedNer;
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

  function refused(response, code) {
    var status = response.status;
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
    if (busy) {
      return;
    }
    var value = field.value;
    hideError();
    if (tooLong) {
      tooLong.hidden = true;
    }
    if (typeof value.isWellFormed === "function" && !value.isWellFormed()) {
      showError(text("error.bad_body"));
      return;
    }
    if (encoder.encode(value).length > maxBytes) {
      showError(fill(text("error.too_large"), {max: number(maxBytes)}));
      return;
    }
    busy = true;
    submit.setAttribute("aria-disabled", "true");
    form.setAttribute("aria-busy", "true");
    var body = {text: value, lang: root.lang};
    if (!useNer) {
      body.ner = false;
    }
    var csrf = document.querySelector("meta[name=antifaz-csrf]");
    fetch("/panel/api/try", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers: {"Content-Type": "application/json", "X-Antifaz-CSRF": csrf ? csrf.content : ""},
      body: JSON.stringify(body)
    }).then(function (response) {
      activity(response.status);
      if (response.status === 401) {
        done();
        document.dispatchEvent(new CustomEvent("antifaz:unauthorized"));
        return;
      }
      return response.json().then(function (data) {
        done();
        if (response.status === 200) {
          shown(data, value, useNer);
        } else {
          refused(response, errorCode(data));
        }
      }, function () {
        done();
        refused(response, "");
      });
    }, function () {
      done();
      showError(text("error.network"));
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
    nerUnused = false;
    exampleNote.hidden = field.value !== text("example");
    if (tooLong) {
      tooLong.hidden = true;
    }
    counter();
  });

  if (withoutNer) {
    withoutNer.addEventListener("click", function () {
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

  document.addEventListener("antifaz:expired", wipe);
  window.addEventListener("pagehide", wipe);

  counter();
}());
