(function () {
  "use strict";

  var root = document.documentElement;

  var tablist = document.querySelector("[role=tablist]");
  var tabs = Array.prototype.slice.call(tablist.querySelectorAll("[role=tab]"));
  var crumb = document.getElementById("crumb-name");

  function horizontal() {
    return getComputedStyle(tablist).flexDirection.indexOf("row") === 0;
  }

  function syncOrientation() {
    tablist.setAttribute("aria-orientation", horizontal() ? "horizontal" : "vertical");
  }

  function reveal(tab) {
    if (tablist.scrollWidth <= tablist.clientWidth) {
      return;
    }
    var start = tab.offsetLeft;
    var end = start + tab.offsetWidth;
    if (start < tablist.scrollLeft) {
      tablist.scrollLeft = start - 8;
    } else if (end > tablist.scrollLeft + tablist.clientWidth) {
      tablist.scrollLeft = end - tablist.clientWidth + 8;
    }
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

  function message(key) {
    var item = messages.content.querySelector('[data-msg="' + key + '"]');
    return item ? item.textContent : "";
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
    fetch("/panel/logout", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers: {"Content-Type": "application/json", "X-Antifaz-CSRF": meta ? meta.content : ""},
      body: "{}"
    }).then(function (response) {
      if (response.status === 200 || response.status === 401) {
        location.replace("/panel/login" + languageQuery());
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

  window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
      location.reload();
    }
  });
}());
