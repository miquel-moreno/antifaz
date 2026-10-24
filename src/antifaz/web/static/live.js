(function () {
  "use strict";

  var root = document.documentElement;
  var view = document.getElementById("panel-live");
  var pill = document.getElementById("live-pill");
  var pillText = document.getElementById("live-pill-text");
  var reconnect = document.getElementById("live-reconnect");
  var lost = document.getElementById("live-lost");
  var lostFigures = document.getElementById("live-lost-figures");
  var lostNone = document.getElementById("live-lost-none");
  var busy = document.getElementById("live-busy");
  var aged = document.getElementById("live-aged");
  var processesBox = document.getElementById("live-processes");
  var processesText = document.getElementById("live-processes-text");
  var data = document.getElementById("live-data");
  var hiddenTotal = document.getElementById("live-hidden");
  var hiddenNone = document.getElementById("live-hidden-none");
  var bars = document.getElementById("live-bars");
  var blocksTotal = document.getElementById("live-blocks");
  var blocksSub = document.getElementById("live-blocks-sub");
  var requestsTotal = document.getElementById("live-requests");
  var requestsSub = document.getElementById("live-requests-sub");
  var apart = document.getElementById("live-apart");
  var uptimeLine = document.getElementById("live-uptime");
  var empty = document.getElementById("live-empty");
  var log = document.getElementById("live-log");
  var feed = document.getElementById("live-feed");
  var messages = document.getElementById("live-messages");
  var RECENT = 20;
  var STEPS = 20;
  var LATEST = 8640000000000;
  var ORIGINS = ["openai_chat", "anthropic_messages", "count_tokens", "scan", "try"];
  var PROXY = ["openai_chat", "anthropic_messages", "count_tokens"];
  var OUTCOMES = ["masked", "no_data", "blocked", "upstream_error"];
  var REASONS = [
    "attachment", "detector_failed", "egress_guard", "unmaskable_field", "too_deep", "none"
  ];
  var CLIENTS = [
    "claude_code", "codex", "open_webui", "openai_python", "anthropic_python", "curl", "other"
  ];
  var PROVIDERS = ["openai", "anthropic"];
  var TAGS = {
    masked: "is-mask", blocked: "is-danger", upstream_error: "is-warn", try: "", scan: "",
    no_data: "", unknown: ""
  };
  var typeLabels = Object.create(null);
  var source = null;
  var gone = false;
  var checking = false;
  var figures = null;

  document.getElementById("try-types").content.querySelectorAll("[data-type]")
    .forEach(function (item) {
      typeLabels[item.getAttribute("data-type")] = item.textContent;
    });

  function text(key) {
    var item = messages.content.querySelector('[data-msg="' + key + '"]');
    return item ? item.textContent : "";
  }

  function fill(template, values) {
    Object.keys(values).forEach(function (name) {
      template = template.split("{" + name + "}").join(values[name]);
    });
    return template;
  }

  function known(value, list) {
    return typeof value === "string" && list.indexOf(value) !== -1;
  }

  function whole(value, least) {
    return typeof value === "number" && Number.isInteger(value) && value >= least ? value : null;
  }

  function number(value) {
    return value.toLocaleString(root.lang);
  }

  function plural(key, count) {
    var one = count === 1 ? text(key + "_one") : "";
    return one || fill(text(key), {n: number(count)});
  }

  function zeros(list) {
    var counts = Object.create(null);
    list.forEach(function (name) {
      counts[name] = 0;
    });
    return counts;
  }

  function typeKey(type) {
    if (typeof type !== "string" || type !== type.toUpperCase()) {
      return "";
    }
    var key = type.toLowerCase();
    return key in typeLabels ? key : "";
  }

  function typeLabel(key) {
    return key ? typeLabels[key] : text("type.unknown");
  }

  function addTypes(into, list) {
    (Array.isArray(list) ? list : []).forEach(function (item) {
      var count = item && typeof item === "object" ? whole(item.count, 1) : null;
      if (count !== null) {
        var key = typeKey(item.type);
        into[key] = (into[key] || 0) + count;
      }
    });
    return into;
  }

  function parseEvent(raw) {
    if (!raw || typeof raw !== "object") {
      return null;
    }
    var provider = "";
    if (raw.provider === "none") {
      provider = "none";
    } else if (known(raw.provider, PROVIDERS)) {
      provider = raw.provider;
    }
    return {
      origin: known(raw.origin, ORIGINS) ? raw.origin : "",
      client: known(raw.client, CLIENTS) ? raw.client : "other",
      outcome: known(raw.outcome, OUTCOMES) ? raw.outcome : "",
      reason: known(raw.reason, REASONS) ? raw.reason : "none",
      provider: provider,
      at: whole(raw.at, 0) !== null && raw.at <= LATEST ? raw.at : null,
      types: addTypes(Object.create(null), raw.types)
    };
  }

  function record(into, event) {
    if (PROXY.indexOf(event.origin) !== -1 && event.outcome) {
      into.outcomes[event.outcome] += 1;
      if (event.outcome === "blocked") {
        into.blocks[event.reason] += 1;
      }
      Object.keys(event.types).forEach(function (key) {
        into.hidden[key] = (into.hidden[key] || 0) + event.types[key];
      });
    } else if (event.origin === "try") {
      into.tries += 1;
    } else if (event.origin === "scan") {
      into.scans += 1;
    }
  }

  function fromSnapshot(raw) {
    var made = {
      hidden: addTypes(Object.create(null), raw.hidden),
      outcomes: zeros(OUTCOMES),
      blocks: zeros(REASONS),
      tries: whole(raw.tries, 0) || 0,
      scans: whole(raw.scans, 0) || 0,
      recent: [],
      uptime: whole(raw.uptime_seconds, 0) || 0,
      at: Date.now(),
      processes: whole(raw.processes, 1) || 1
    };
    (Array.isArray(raw.outcomes) ? raw.outcomes : []).forEach(function (item) {
      if (item && typeof item === "object" && known(item.outcome, OUTCOMES)) {
        made.outcomes[item.outcome] += whole(item.count, 0) || 0;
      }
    });
    (Array.isArray(raw.blocks) ? raw.blocks : []).forEach(function (item) {
      if (item && typeof item === "object") {
        var reason = known(item.reason, REASONS) ? item.reason : "none";
        made.blocks[reason] += whole(item.count, 1) || 0;
      }
    });
    (Array.isArray(raw.recent) ? raw.recent : []).slice(0, RECENT).forEach(function (item) {
      var event = parseEvent(item);
      if (event) {
        made.recent.push(event);
      }
    });
    return made;
  }

  function el(name, className) {
    var node = document.createElement(name);
    if (className) {
      node.className = className;
    }
    return node;
  }

  function icon(kind) {
    var item = messages.content.querySelector('[data-icon="' + kind + '"]');
    return item ? item.firstElementChild.cloneNode(true) : null;
  }

  function clock(seconds) {
    return new Intl.DateTimeFormat(root.lang, {hour: "2-digit", minute: "2-digit"})
      .format(new Date(seconds * 1000));
  }

  function uptime(seconds) {
    var minutes = Math.floor(seconds / 60);
    var hours = Math.floor(minutes / 60);
    var days = Math.floor(hours / 24);
    if (days > 0) {
      return fill(text("uptime.days"), {days: number(days), hours: number(hours % 24)});
    }
    if (hours > 0) {
      return fill(text("uptime.hours"), {hours: number(hours), minutes: number(minutes % 60)});
    }
    if (minutes > 0) {
      return fill(text("uptime.minutes"), {minutes: number(minutes)});
    }
    return text("uptime.less");
  }

  function sum(counts) {
    return Object.keys(counts).reduce(function (total, key) {
      return total + counts[key];
    }, 0);
  }

  function kindOf(event) {
    if (event.origin === "try" || event.origin === "scan") {
      return event.origin;
    }
    return event.outcome || "unknown";
  }

  function row(event) {
    var kind = kindOf(event);
    var sep = text("sep");
    var item = el("li", "ev");
    item.setAttribute("data-kind", kind);
    var time = el("time");
    if (event.at !== null) {
      time.setAttribute("datetime", new Date(event.at * 1000).toISOString());
      time.textContent = clock(event.at);
    }
    var main = el("span", "ev-main");
    var who = el("span", "ev-who");
    who.textContent = event.origin === "try" ? text("who.try") : text("client." + event.client);
    if (event.provider !== "none") {
      var app = el("span", "ev-app");
      var name = el("span");
      name.textContent = event.provider ? text("provider." + event.provider)
        : text("provider.unknown");
      app.append(sep, name);
      who.append(app);
    }
    var what = el("span", "ev-what");
    var keys = Object.keys(event.types);
    var parts = [];
    if (event.outcome === "blocked") {
      parts.push(text("reason." + event.reason));
    } else if (keys.length) {
      parts.push(keys.map(function (key) {
        return fill(text("what.type"), {type: typeLabel(key), n: number(event.types[key])});
      }).join(sep));
    } else {
      parts.push(text("what.none"));
    }
    if (kind === "try" || kind === "scan") {
      parts.push(text("what.not_added"));
    }
    what.textContent = parts.join(sep);
    main.append(who, what);
    var tag = el("span", "tag");
    if (TAGS[kind]) {
      tag.classList.add(TAGS[kind]);
    }
    var symbol = icon(kind);
    if (symbol) {
      tag.append(symbol);
    }
    var words = text("tag.unknown");
    if (kind === "masked") {
      words = fill(text("tag.masked"), {n: number(sum(event.types))});
    } else if (kind !== "unknown") {
      words = text("tag." + kind);
    }
    tag.append(words);
    item.append(time, main, tag);
    return item;
  }

  function bar(key, count, largest) {
    var item = el("li");
    var label = el("span");
    label.textContent = typeLabel(key);
    var meter = el("span", "meter");
    meter.setAttribute("aria-hidden", "true");
    var fillBar = el("i");
    fillBar.setAttribute("data-v", String(Math.max(1, Math.round(count / largest * STEPS))));
    meter.append(fillBar);
    var amount = el("span", "n");
    amount.textContent = number(count);
    item.append(label, meter, amount);
    return item;
  }

  function renderFigures() {
    var sep = text("sep");
    var hidden = Object.keys(figures.hidden).map(function (key) {
      return {key: key, count: figures.hidden[key]};
    }).sort(function (a, b) {
      return b.count - a.count;
    });
    var hiddenSum = sum(figures.hidden);
    hiddenTotal.textContent = number(hiddenSum);
    hiddenNone.hidden = hiddenSum > 0;
    bars.replaceChildren.apply(bars, hidden.map(function (entry) {
      return bar(entry.key, entry.count, hidden[0].count);
    }));
    var blocks = REASONS.filter(function (reason) {
      return figures.blocks[reason] > 0;
    });
    blocksTotal.textContent = number(sum(figures.blocks));
    blocksSub.textContent = blocks.map(function (reason) {
      return plural("blocks." + reason, figures.blocks[reason]);
    }).join(sep) || text("blocks.zero");
    var outcomes = OUTCOMES.filter(function (outcome) {
      return figures.outcomes[outcome] > 0;
    });
    requestsTotal.textContent = number(sum(figures.outcomes));
    requestsSub.textContent = outcomes.map(function (outcome) {
      return plural("requests." + outcome, figures.outcomes[outcome]);
    }).join(sep) || text("requests.zero");
    apart.textContent = fill(text("apart"), {
      tries: plural("tries", figures.tries),
      scans: plural("scans", figures.scans)
    });
    var running = figures.uptime + Math.max(0, Math.floor((Date.now() - figures.at) / 1000));
    uptimeLine.textContent = fill(text("uptime"), {uptime: uptime(running)});
    processesBox.hidden = figures.processes < 2;
    processesText.textContent = fill(text("processes"), {n: number(figures.processes)});
    empty.hidden = figures.recent.length > 0;
  }

  function renderFeed() {
    log.setAttribute("aria-busy", "true");
    feed.replaceChildren.apply(feed, figures.recent.map(row));
    log.setAttribute("aria-busy", "false");
  }

  function setPill(name) {
    pill.setAttribute("data-state", name);
    pill.classList.toggle("is-connecting", name === "connecting");
    pill.classList.toggle("is-off", name === "off");
    pillText.textContent = text(name === "off" ? "pill.disconnected" : "pill." + name);
  }

  function callout(which) {
    lost.hidden = which !== "lost";
    busy.hidden = which !== "busy";
    aged.hidden = which !== "aged";
  }

  function parse(raw) {
    try {
      var value = JSON.parse(raw);
      return value && typeof value === "object" ? value : null;
    } catch (error) {
      return null;
    }
  }

  function disconnect() {
    if (source) {
      source.close();
      source = null;
    }
  }

  function stopped(which) {
    setPill("off");
    callout(which);
    lostFigures.hidden = !figures;
    lostNone.hidden = !!figures;
    reconnect.hidden = false;
    data.classList.add("is-stale");
    if (which === "busy") {
      data.hidden = true;
    }
  }

  function wipe() {
    gone = true;
    checking = false;
    disconnect();
    figures = null;
    data.hidden = true;
    feed.replaceChildren();
    bars.replaceChildren();
    [hiddenTotal, blocksTotal, blocksSub, requestsTotal, requestsSub, apart, uptimeLine,
      processesText].forEach(function (node) {
      node.textContent = "";
    });
  }

  function snapshot(raw) {
    var value = parse(raw);
    if (!value) {
      return;
    }
    figures = fromSnapshot(value);
    renderFigures();
    renderFeed();
    setPill("connected");
    callout("");
    reconnect.hidden = true;
    data.classList.remove("is-stale");
    data.hidden = false;
  }

  function request(raw) {
    var event = parseEvent(parse(raw));
    if (!event || !figures) {
      return;
    }
    record(figures, event);
    figures.recent.unshift(event);
    figures.recent.length = Math.min(figures.recent.length, RECENT);
    renderFigures();
    feed.insertBefore(row(event), feed.firstChild);
    while (feed.children.length > RECENT) {
      feed.lastElementChild.remove();
    }
  }

  function end(raw) {
    var value = parse(raw);
    var reason = value ? value.reason : "";
    disconnect();
    if (reason === "logout" || reason === "expired" || reason === "replaced") {
      wipe();
      document.dispatchEvent(new CustomEvent("antifaz:unauthorized"));
    } else if (reason === "busy") {
      stopped("busy");
    } else if (reason === "max_age") {
      stopped("aged");
    } else {
      stopped("lost");
    }
  }

  function wanted() {
    return !gone && !view.hidden && document.visibilityState === "visible";
  }

  function connect() {
    if (source || !wanted()) {
      return;
    }
    callout("");
    reconnect.hidden = true;
    setPill("connecting");
    if (figures) {
      data.classList.add("is-stale");
    }
    var stream = new EventSource("/panel/api/live");
    source = stream;
    stream.addEventListener("snapshot", function (event) {
      if (source === stream) {
        snapshot(event.data);
      }
    });
    stream.addEventListener("request", function (event) {
      if (source === stream) {
        request(event.data);
      }
    });
    stream.addEventListener("end", function (event) {
      if (source === stream) {
        end(event.data);
      }
    });
    stream.addEventListener("error", function () {
      if (source !== stream) {
        return;
      }
      disconnect();
      if (!gone) {
        stopped("lost");
      }
    });
  }

  function sync() {
    if (wanted()) {
      connect();
    } else {
      disconnect();
    }
  }

  reconnect.addEventListener("click", function () {
    if (checking || source) {
      return;
    }
    checking = true;
    reconnect.setAttribute("aria-disabled", "true");
    document.dispatchEvent(new CustomEvent("antifaz:live-check"));
  });

  document.addEventListener("antifaz:live-ok", function () {
    if (!checking) {
      return;
    }
    checking = false;
    reconnect.removeAttribute("aria-disabled");
    connect();
    view.focus({preventScroll: true});
  });

  document.addEventListener("antifaz:live-failed", function () {
    checking = false;
    reconnect.removeAttribute("aria-disabled");
  });

  new MutationObserver(sync).observe(view, {attributes: true, attributeFilter: ["hidden"]});
  document.addEventListener("visibilitychange", sync);
  document.addEventListener("antifaz:wipe", wipe);
  document.addEventListener("antifaz:logout", function () {
    disconnect();
    checking = false;
    reconnect.removeAttribute("aria-disabled");
    if (!gone && !view.hidden) {
      stopped("");
    }
  });
  window.addEventListener("pagehide", disconnect);

  sync();
}());
