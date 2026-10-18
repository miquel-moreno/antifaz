(function () {
  "use strict";

  var form = document.getElementById("login-form");
  var input = document.getElementById("login-token");
  var alertBox = document.getElementById("login-alert");
  var button = form.querySelector("button[type=submit]");
  var messages = document.getElementById("login-messages");

  function languageQuery() {
    if (!new URLSearchParams(location.search).has("lang")) {
      return "";
    }
    return "?lang=" + encodeURIComponent(document.documentElement.lang);
  }

  function message(key) {
    var item = messages.content.querySelector('[data-msg="' + key + '"]');
    return item ? item.textContent : "";
  }

  function show(text) {
    alertBox.textContent = text;
    button.disabled = false;
    input.focus();
  }

  function retrySeconds(response) {
    var raw = response.headers.get("Retry-After");
    var seconds = Number(raw);
    if (raw === null || !Number.isInteger(seconds) || seconds <= 0 || String(seconds) !== raw) {
      return 0;
    }
    return seconds;
  }

  function answer(response) {
    if (response.status === 200) {
      location.replace("/panel" + languageQuery());
      return;
    }
    if (response.status === 429) {
      var seconds = retrySeconds(response);
      show(seconds ? message("too-many").replace("{seconds}", String(seconds))
        : message("too-many-later"));
    } else if (response.status === 401) {
      show(message("failed"));
    } else {
      show(message("refused"));
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var token = input.value;
    input.value = "";
    if (token === "") {
      show(message("empty"));
      return;
    }
    alertBox.textContent = "";
    button.disabled = true;
    fetch("/panel/login", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: token})
    }).then(answer, function () {
      show(message("network"));
    });
    token = "";
  });

  var root = document.documentElement;
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
  }
  document.querySelectorAll(".theme-btn").forEach(function (themeButton) {
    themeButton.addEventListener("click", function () {
      root.setAttribute("data-theme", current() === "dark" ? "light" : "dark");
      paint();
    });
  });
  dark.addEventListener("change", paint);
  paint();
}());
