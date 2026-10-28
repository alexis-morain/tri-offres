// Progressive enhancement only: every page works without this file.
(function () {
  "use strict";

  // A form that came back with errors: move focus to its summary so screen readers hear it first.
  function focusErrorSummary() {
    var summary = document.querySelector("[data-error-summary]");
    if (summary) summary.focus();
  }

  // Chips with a cap: once the cap is reached, the unchecked ones are disabled until one is released.
  function capChips(group) {
    var max = parseInt(group.getAttribute("data-max-choices"), 10);
    if (!max) return;
    var boxes = Array.prototype.slice.call(group.querySelectorAll('input[type="checkbox"]'));
    var count = document.createElement("p");
    count.className = "chips__count";
    count.setAttribute("aria-live", "polite");
    group.insertAdjacentElement("afterend", count);
    function update() {
      var checked = boxes.filter(function (b) { return b.checked; }).length;
      boxes.forEach(function (b) { if (!b.checked) b.disabled = checked >= max; });
      var note = "";
      if (checked > max) note = ". Retirez-en " + (checked - max) + ".";  // an older answer above today's cap
      else if (checked >= max) note = ". Retirez-en un pour en choisir un autre.";
      count.textContent = checked + " sur " + max + note;
    }
    boxes.forEach(function (b) { b.addEventListener("change", update); });
    update();
  }

  // The drawer is rendered open, so it works without this script. On phones it folds into a band
  // under the masthead until the person opens it.
  function foldDrawerOnPhones() {
    var drawer = document.querySelector("[data-drawer]");
    if (!drawer || !window.matchMedia) return;
    if (!window.matchMedia("(min-width: 60rem)").matches) drawer.open = false;
  }

  // Sign-in code: spaces from a paste are dropped, and the form goes as soon as the sixth digit is in.
  // The server still checks everything; without this script the person presses the button.
  function autoSubmitCode(input) {
    var form = input.form;
    if (!form) return;
    var sent = false;
    function check() {
      var digits = input.value.replace(/\s+/g, "");
      if (sent || !/^[0-9]{6}$/.test(digits)) return;
      sent = true;
      input.value = digits;
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    }
    input.addEventListener("paste", function (event) {
      var text = (event.clipboardData || window.clipboardData).getData("text");
      if (!text) return;
      event.preventDefault();
      input.value = text.replace(/\s+/g, "");
      check();
    });
    input.addEventListener("input", check);
  }

  // "Renvoyer le code" waits 60 seconds, counted down in its label. The server refuses earlier resends anyway.
  function countDown(button) {
    var left = parseInt(button.getAttribute("data-countdown"), 10);
    if (!left) return;
    var label = button.textContent.trim();
    function tick() {
      if (left <= 0) {
        button.disabled = false;
        button.textContent = label;
        return;
      }
      button.disabled = true;
      // The seconds are typed data: the mono, small.
      var seconds = document.createElement("span");
      seconds.className = "typed small";
      seconds.textContent = "(" + left + " s)";
      button.textContent = label + " ";
      button.appendChild(seconds);
      left -= 1;
      window.setTimeout(tick, 1000);
    }
    tick();
  }

  // A question that only applies after a given answer (data-show-when="name=value") stays hidden until then.
  // Without this script it stays visible and the server ignores it when it does not apply.
  function showWhen(field) {
    var rule = field.getAttribute("data-show-when").split("=");
    var form = field.closest("form");
    if (!form) return;
    var inputs = form.querySelectorAll('input[name="' + rule[0] + '"]');
    function update() {
      field.hidden = !Array.prototype.some.call(inputs, function (input) {
        return input.checked && input.value === rule[1];
      });
    }
    Array.prototype.forEach.call(inputs, function (input) { input.addEventListener("change", update); });
    update();
  }

  // Live reading of the filters: each change asks the server, without saving, how many of yesterday's offers
  // pass, and what the pay floor costs. The page shows the saved values until the first answer arrives.
  // POST, with the CSRF token as a header: typed cities and amounts never go into a URL.
  function liveCounter(form) {
    var url = form.getAttribute("data-counter-url");
    var counters = document.querySelectorAll("[data-counter], [data-counter-summary]");
    var consequence = form.querySelector("[data-consequence]");
    var token = form.querySelector('input[name="csrfmiddlewaretoken"]');
    var timer;
    var latest = 0;
    function refresh() {
      var params = new URLSearchParams();
      new FormData(form).forEach(function (value, key) {
        if (key !== "csrfmiddlewaretoken") params.append(key, value);  // never put the token in a URL
      });
      var ticket = ++latest;
      fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json", "X-CSRFToken": token ? token.value : "" },
        body: params
      })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) {
          if (!data || ticket !== latest) return;
          if (data.text) {
            Array.prototype.forEach.call(counters, function (counter) { counter.textContent = data.text; });
          }
          // null: the amount does not pass its check yet, so no sentence rather than a stale one.
          if (consequence && "consequence" in data) consequence.textContent = data.consequence || "";
        })
        .catch(function () {});
    }
    function schedule() {
      window.clearTimeout(timer);
      timer = window.setTimeout(refresh, 300);
    }
    form.addEventListener("change", schedule);
    form.addEventListener("input", schedule);
  }

  // A field with suggestions under it, as an ARIA combobox. `options.find(query)` resolves to the items,
  // `options.text(item)` is what an option reads, `options.choose(item)` fills the field, `options.typed()` runs
  // at each keystroke. Without this script the field is plain text and the server reads what was typed.
  function suggest(input, options) {
    var list = document.createElement("ul");
    list.id = input.id + "-suggestions";
    list.className = "suggestions";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", options.label);
    list.hidden = true;
    input.insertAdjacentElement("afterend", list);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", list.id);
    input.setAttribute("aria-expanded", "false");

    var items = [];
    var active = -1;
    var timer;
    var latest = 0;

    function render() {
      list.textContent = "";
      items.forEach(function (item, index) {
        var option = document.createElement("li");
        option.id = list.id + "-" + index;
        option.className = "suggestions__item";
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", index === active ? "true" : "false");
        option.textContent = options.text(item);
        option.addEventListener("mousedown", function (event) {
          event.preventDefault();  // keep focus in the field
          choose(index);
        });
        list.appendChild(option);
      });
      var open = items.length > 0;
      list.hidden = !open;
      input.setAttribute("aria-expanded", open ? "true" : "false");
      if (open && active >= 0) input.setAttribute("aria-activedescendant", list.id + "-" + active);
      else input.removeAttribute("aria-activedescendant");
    }
    function close() {
      items = [];
      active = -1;
      render();
    }
    function choose(index) {
      options.choose(items[index]);
      close();
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    function search() {
      var query = input.value.trim();
      if (query.length < 2) return close();
      var ticket = ++latest;
      options.find(query)
        .then(function (found) {
          if (ticket !== latest || document.activeElement !== input) return;
          items = found;
          active = -1;
          render();
        })
        .catch(close);
    }
    input.addEventListener("input", function () {
      options.typed();
      window.clearTimeout(timer);
      timer = window.setTimeout(search, 250);
    });
    input.addEventListener("keydown", function (event) {
      if (!items.length) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        var step = event.key === "ArrowDown" ? 1 : -1;
        active = (active + step + items.length) % items.length;
        render();
      } else if (event.key === "Enter" && active >= 0) {
        event.preventDefault();
        choose(active);
      } else if (event.key === "Escape") {
        close();
      }
    });
    input.addEventListener("blur", close);
  }

  // City fields: suggestions from the public list of French communes. Picking one fills the hidden code fields;
  // typing again clears them. Without this script only the name is sent.
  var COMMUNES_URL = "https://geo.api.gouv.fr/communes?fields=nom,code,codeDepartement,codeRegion" +
    "&boost=population&limit=5&nom=";

  function cityAutocomplete(input) {
    var form = input.form;
    if (!form || !window.fetch) return;
    var codes = {};
    [["insee", "code"], ["department", "codeDepartement"], ["region", "codeRegion"]].forEach(function (pair) {
      codes[pair[1]] = form.querySelector('input[name="' + input.name + "_" + pair[0] + '"]');
    });
    function fillCodes(commune) {
      Object.keys(codes).forEach(function (key) {
        if (codes[key]) codes[key].value = commune ? commune[key] || "" : "";
      });
    }
    suggest(input, {
      label: "Communes proposées",
      find: function (query) {
        return fetch(COMMUNES_URL + encodeURIComponent(query))
          .then(function (response) { return response.ok ? response.json() : []; })
          .then(function (communes) { return communes.slice(0, 5); });
      },
      text: function (commune) { return commune.nom + " (" + commune.codeDepartement + ")"; },
      choose: function (commune) {
        input.value = commune.nom;
        fillCodes(commune);
      },
      typed: function () { fillCodes(null); }
    });
  }

  // Poste fields: the ROME job titles from this site, signed-in people only. The server looks the title up again
  // when the screen is validated: nothing but the text is sent.
  function jobAutocomplete(input) {
    if (!window.fetch) return;
    var url = input.getAttribute("data-suggestions-url");
    suggest(input, {
      label: "Postes proposés",
      find: function (query) {
        return fetch(url + "?q=" + encodeURIComponent(query), {
          credentials: "same-origin",
          headers: { Accept: "application/json" }
        })
          .then(function (response) { return response.ok ? response.json() : {}; })
          .then(function (data) { return data.suggestions || []; });
      },
      text: function (label) { return label; },
      choose: function (label) { input.value = label; },
      typed: function () {}
    });
  }

  // CV upload. On computers the file field becomes a drop zone; while the file is sent and read, the button is
  // busy and says so. Without this script the browser's own file control and the plain submit do the same job,
  // and the server checks everything anyway (one file, 5 MB, PDF or DOCX by its first bytes).
  function cvUpload(form) {
    var input = form.querySelector('input[type="file"]');
    var zone = form.querySelector("[data-cv-drop]");
    var hint = form.querySelector("[data-cv-drop-hint]");
    var send = form.querySelector("[data-cv-send]");
    var busy = form.querySelector("[data-cv-busy]");
    if (!input || !send) return;
    // The French status line under « Choisir un fichier »: shown with this script only, it names the file chosen.
    var status = form.querySelector("[data-file-status]");
    if (status) {
      var empty = status.textContent;
      var showChoice = function () {
        status.textContent = input.files && input.files.length ? input.files[0].name : empty;
      };
      input.closest(".file-field").classList.add("file-field--enhanced");
      status.hidden = false;
      input.setAttribute("aria-describedby",
        ((input.getAttribute("aria-describedby") || "") + " " + status.id).trim());
      input.addEventListener("change", showChoice);
      // Back from the next page, the browser may restore the chosen file without a change event.
      window.addEventListener("pageshow", showChoice);
      showChoice();
    }
    var canDrop = zone && window.DataTransfer && window.matchMedia && window.matchMedia("(pointer: fine)").matches;
    if (canDrop) {
      zone.classList.add("cv-drop--active");
      if (hint) hint.hidden = false;
      ["dragenter", "dragover"].forEach(function (name) {
        zone.addEventListener(name, function (event) {
          event.preventDefault();
          zone.classList.add("cv-drop--over");
        });
      });
      ["dragleave", "drop"].forEach(function (name) {
        zone.addEventListener(name, function () { zone.classList.remove("cv-drop--over"); });
      });
      zone.addEventListener("drop", function (event) {
        event.preventDefault();
        var files = event.dataTransfer && event.dataTransfer.files;
        if (!files || !files.length) return;
        var transfer = new DataTransfer();
        transfer.items.add(files[0]);  // one file, like the server
        input.files = transfer.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      });
      // A file dropped beside the zone must not replace the page with the file itself.
      ["dragover", "drop"].forEach(function (name) {
        window.addEventListener(name, function (event) {
          if (!zone.contains(event.target)) event.preventDefault();
        });
      });
    }
    function idle() {
      send.removeAttribute("aria-busy");
      if (busy) busy.textContent = "";
    }
    form.addEventListener("submit", function (event) {
      if (send.getAttribute("aria-busy") === "true") {
        event.preventDefault();  // already sent: one analysis at a time
        return;
      }
      if (!input.files || !input.files.length) return;  // the server says what is missing
      send.setAttribute("aria-busy", "true");
      if (busy) busy.textContent = busy.getAttribute("data-text");
    });
    // Back from the next page, the browser may restore this one as it was: busy no more.
    window.addEventListener("pageshow", idle);
  }

  // A form that does its job as soon as the page is there (the summary being written). Without this script its
  // button does the same. The button stays busy while the server works: real work, no invented progress.
  function autoSubmit(form) {
    var button = form.querySelector("[data-auto-submit-button]");
    form.addEventListener("submit", function (event) {
      if (button.getAttribute("aria-busy") === "true") {
        event.preventDefault();  // already sent
        return;
      }
      button.setAttribute("aria-busy", "true");
    });
    // Back from the next page, the browser may restore this one as it was: never send it twice.
    window.addEventListener("pageshow", function (event) {
      if (event.persisted) button.removeAttribute("aria-busy");
    });
    if (form.requestSubmit) {
      form.requestSubmit(button);
    } else {
      button.setAttribute("aria-busy", "true");
      form.submit();
    }
  }

  // The sort page. The server renders the first sheet and its three buttons (the page works with them alone); here
  // the deck comes from the json_script, a sheet is cloned and filled with textContent only, and a verdict is
  // swiped, pressed or typed. Every verdict goes into a queue in localStorage first and is sent one at a time, in
  // order, so a lost connection or a lost session loses nothing.
  var VERDICT_LABELS = { no: "Pas pour moi", maybe: "Peut-être", yes: "Ça m’intéresse" };
  var QUEUE_VERDICTS = ["yes", "maybe", "no", "undo"];
  var QUEUE_MAX = 100;
  var QUEUE_MAX_AGE = 30 * 24 * 3600 * 1000;
  var SWIPE_SHARE = 0.3;
  var SETTLE_MS = 240;
  var NOTICES = {
    session: "Vos derniers avis n’ont pas pu être envoyés : votre connexion a expiré ou la page est périmée. " +
      "Ils sont gardés. Rechargez la page, ou reconnectez-vous.",
    limit: "Vous avez atteint la limite d’avis pour aujourd’hui. Vos derniers avis sont gardés et partiront " +
      "plus tard.",
    offline: "Vos derniers avis ne sont pas encore enregistrés. Ils partiront dès que la connexion revient."
  };

  function readQueue(key) {
    if (!key) return [];
    try {
      var items = JSON.parse(window.localStorage.getItem(key) || "[]");
      if (!Array.isArray(items)) throw new Error("not a list");
      var oldest = Date.now() - QUEUE_MAX_AGE;
      return items.filter(function (entry) {
        return entry && Number.isInteger(entry.offer) && entry.offer > 0 &&
          QUEUE_VERDICTS.indexOf(entry.verdict) !== -1 && Number.isFinite(entry.date) && entry.date > oldest;
      }).slice(-QUEUE_MAX);
    } catch (error) {
      try { window.localStorage.removeItem(key); } catch (ignored) {}  // unreadable: emptied
      return [];
    }
  }

  function writeQueue(key, queue) {
    if (!key) return;
    try { window.localStorage.setItem(key, JSON.stringify(queue)); } catch (ignored) {}
  }

  function sortPage(root) {
    var key = root.getAttribute("data-queue-key");
    if (!/^tri\.queue\.[0-9]+$/.test(key)) key = "";  // a key that is not this account's own is never read
    var tokenField = root.querySelector('input[name="csrfmiddlewaretoken"]');
    var notice = root.querySelector("[data-sort-notice]");
    var queue = readQueue(key);
    var flushing = false;

    function showNotice(kind) {
      notice.hidden = !kind;
      notice.querySelector("[data-sort-notice-text]").textContent = kind ? NOTICES[kind] : "";
      notice.querySelector("[data-sort-reload]").hidden = kind !== "session";
    }
    function flush() {
      if (flushing || !queue.length) return;
      flushing = true;
      var entry = queue[0];
      var undoing = entry.verdict === "undo";
      var body = new URLSearchParams({ offer: String(entry.offer) });
      if (!undoing) body.set("verdict", entry.verdict);
      fetch(root.getAttribute(undoing ? "data-undo-url" : "data-verdict-url"), {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json", "X-CSRFToken": tokenField ? tokenField.value : "" },
        body: body
      }).then(function (response) { return response.status; }, function () { return 0; }).then(function (status) {
        flushing = false;
        if (status === 200 || status === 400 || status === 404) {  // sent, or no longer the person's offer
          queue = queue.filter(function (item) { return item !== entry; });
          writeQueue(key, queue);
          showNotice(null);
          flush();
        } else if (status === 401 || status === 403) {
          showNotice("session");
        } else if (status === 429) {
          showNotice("limit");
        } else {
          showNotice("offline");  // network or server down: tried again at the next verdict or when back online
        }
      });
    }
    function enqueue(entry) {
      queue.push(entry);
      queue = queue.slice(-QUEUE_MAX);
      writeQueue(key, queue);
      flush();
    }
    Array.prototype.forEach.call(root.querySelectorAll("[data-sort-reload]"), function (button) {
      button.addEventListener("click", function () { window.location.reload(); });
    });
    window.addEventListener("online", flush);
    flush();

    var stage = root.querySelector("[data-sort-stage]");
    var proto = stage.querySelector(".sort-card");
    if (!proto) return;  // an empty deck: nothing to sort, the queue above is all there is to do
    proto = proto.cloneNode(true);
    var cards;
    try { cards = JSON.parse(document.getElementById("cards").textContent); } catch (error) { return; }
    var queued = queue.map(function (entry) { return entry.offer; });
    var before = cards.length;
    cards = cards.filter(function (card) { return queued.indexOf(card.id) === -1; });
    var remaining = (parseInt(root.getAttribute("data-count"), 10) || before) - (before - cards.length);
    var history = [];  // this visit's verdicts, to undo them: { card, verdict }
    var busy = false;
    var drag = null;
    var actions = root.querySelector("[data-sort-actions]");
    var undoForm = root.querySelector("[data-sort-undo]");
    var undoButton = undoForm.querySelector("button");
    var countEl = root.querySelector("[data-sort-count]");
    var live = root.querySelector("[data-sort-live]");
    var end = root.querySelector("[data-sort-end]");
    var buttons = {};
    Array.prototype.forEach.call(actions.querySelectorAll("button[name=verdict]"), function (button) {
      buttons[button.value] = button;
    });
    var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function top() { return stage.querySelector(".sort-card--top"); }
    function count(n) { return n + (n > 1 ? " offres" : " offre"); }
    function left() {
      return remaining > 0 ? (remaining > 1 ? remaining + " offres restantes." : "1 offre restante.")
        : "Plus aucune offre à trier.";
    }
    function build(card, role) {
      var el = proto.cloneNode(true);
      Array.prototype.forEach.call(el.querySelectorAll("[data-field]"), function (node) {
        var name = node.getAttribute("data-field");
        var value = card[name] || "";
        if (name === "date" && value) value = "publiée le " + value.slice(8, 10) + "/" + value.slice(5, 7);
        node.textContent = value;
        node.hidden = !value;
      });
      Array.prototype.forEach.call(el.querySelectorAll("[data-if]"), function (node) {
        node.hidden = !card[node.getAttribute("data-if")];
      });
      var notches = el.querySelectorAll(".gauge__notch");
      Array.prototype.forEach.call(notches, function (notch, index) {
        notch.classList.toggle("gauge__notch--on", index < card.score);
      });
      el.querySelector(".gauge__value").textContent = card.score + " sur " + notches.length;
      var link = el.querySelector("[data-link]");
      if (/^https?:\/\//.test(card.url)) {  // the server checked it too; a link is never built from anything else
        link.setAttribute("href", card.url);
        link.hidden = false;
      } else {
        link.removeAttribute("href");
        link.hidden = true;
      }
      var title = el.querySelector(".sort-card__title");
      title.id = "sort-title-" + card.id;
      el.setAttribute("aria-labelledby", title.id);
      el.classList.add("sort-card--" + role);
      if (role === "next") {  // the sheet behind: seen as an edge only, out of reach
        title.removeAttribute("id");
        el.removeAttribute("aria-labelledby");
        el.setAttribute("aria-hidden", "true");
        el.inert = true;
      }
      return el;
    }
    function render() {
      stage.textContent = "";
      if (cards[0]) stage.appendChild(build(cards[0], "top"));
      if (cards[1]) stage.appendChild(build(cards[1], "next"));
      if (countEl) {
        countEl.textContent = count(Math.max(remaining, 0));
        countEl.parentNode.hidden = !cards[0];
      }
      actions.hidden = !cards[0];
      if (cards[0]) actions.querySelector("input[name=offer]").value = cards[0].id;
      undoForm.hidden = false;
      undoButton.disabled = history.length === 0;
      if (cards[0]) return;
      var tally = { yes: 0, maybe: 0, no: 0 };
      history.forEach(function (item) { tally[item.verdict] += 1; });
      var recap = end.querySelector("[data-sort-recap]");
      recap.textContent = tally.yes + " qui vous intéressent, " + tally.maybe + " peut-être, " + tally.no +
        " pas pour vous";
      recap.hidden = history.length === 0;
      end.querySelector("[data-sort-done]").hidden = remaining > 0;
      end.querySelector("[data-sort-more]").hidden = remaining <= 0;
      end.hidden = false;
    }
    function settle(el, transform, opacity, done) {
      if (still) {
        el.style.transform = transform;
        el.style.opacity = opacity;
        done();
        return;
      }
      el.classList.add("sort-card--settling");
      el.style.transform = transform;
      el.style.opacity = opacity;
      window.setTimeout(done, SETTLE_MS);
    }
    // The live region says what happened; focus goes to the title of the sheet now on top, or to the end block.
    function announce(text) {
      live.textContent = text;
      (cards[0] ? top().querySelector(".sort-card__title") : end).focus();
    }
    function decide(verdict) {
      var el = top();
      if (busy || !el) return;
      var card = cards[0];
      busy = true;
      enqueue({ offer: card.id, verdict: verdict, date: Date.now() });
      history.push({ card: card, verdict: verdict });
      var exit = verdict === "yes" ? "translateX(120%) rotate(12deg)" : verdict === "no"
        ? "translateX(-120%) rotate(-12deg)" : "translateY(2rem)";
      settle(el, exit, "0", function () {
        cards.shift();
        remaining -= 1;
        busy = false;
        render();
        announce(VERDICT_LABELS[verdict] + " : " + card.title + ". " + left());
      });
    }
    function undo() {
      if (busy || !history.length) return;
      var last = history.pop();
      enqueue({ offer: last.card.id, verdict: "undo", date: Date.now() });
      cards.unshift(last.card);
      remaining += 1;
      end.hidden = true;
      render();
      announce("Avis annulé : " + last.card.title + ". " + left());
    }
    function arm(dx, width) {
      var verdict = Math.abs(dx) > SWIPE_SHARE * width ? (dx > 0 ? "yes" : "no") : "";
      Object.keys(buttons).forEach(function (name) { buttons[name].classList.toggle("is-armed", name === verdict); });
    }

    actions.addEventListener("submit", function (event) {
      event.preventDefault();
      if (event.submitter) decide(event.submitter.value);
    });
    undoForm.addEventListener("submit", function (event) {
      event.preventDefault();
      undo();
    });
    document.addEventListener("keydown", function (event) {
      if (event.defaultPrevented || event.repeat || event.altKey || event.shiftKey) return;
      if (event.target.closest && event.target.closest("input, textarea, select, [contenteditable]")) return;
      var undoing = event.key === "Backspace" || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z");
      var verdict = event.ctrlKey || event.metaKey ? "" :
        { ArrowRight: "yes", ArrowLeft: "no", ArrowDown: "maybe" }[event.key];
      if (!undoing && !verdict) return;
      event.preventDefault();
      if (undoing) undo(); else decide(verdict);
    });

    // A horizontal drag on the top sheet: it follows the finger, and past 30 % of its width it decides.
    // touch-action: pan-y leaves the vertical scroll to the browser.
    stage.addEventListener("pointerdown", function (event) {
      var el = top();
      if (busy || !el || event.button !== 0 || !el.contains(event.target) || event.target.closest("a, button")) return;
      drag = { el: el, id: event.pointerId, x: event.clientX, dx: 0, moved: false };
      // A mouse would select the text, then drag that selection natively, which swallows pointerup.
      if (event.pointerType === "mouse") event.preventDefault();
    });
    stage.addEventListener("dragstart", function (event) { event.preventDefault(); });
    stage.addEventListener("pointermove", function (event) {
      if (!drag || event.pointerId !== drag.id) return;
      if (event.pointerType === "mouse" && !event.buttons) return endDrag(event, false);  // its pointerup was lost
      var dx = event.clientX - drag.x;
      if (!drag.moved) {
        if (Math.abs(dx) < 6) return;
        drag.moved = true;
        drag.el.setPointerCapture(drag.id);
        drag.el.classList.remove("sort-card--settling");
        drag.el.classList.add("sort-card--dragging");
      }
      var width = drag.el.offsetWidth;
      drag.dx = dx;
      drag.el.style.transform = "translateX(" + dx + "px) rotate(" + (dx / width) * 8 + "deg)";
      arm(dx, width);
    });
    function endDrag(event, released) {
      if (!drag || event.pointerId !== drag.id) return;
      var held = drag;
      drag = null;
      if (!held.moved) return;
      held.el.classList.remove("sort-card--dragging");
      var width = held.el.offsetWidth;
      arm(0, width);
      if (released && Math.abs(held.dx) > SWIPE_SHARE * width) {
        decide(held.dx > 0 ? "yes" : "no");
      } else {
        busy = true;
        settle(held.el, "", "", function () {
          held.el.classList.remove("sort-card--settling");
          busy = false;
        });
      }
    }
    stage.addEventListener("pointerup", function (event) { endDrag(event, true); });
    stage.addEventListener("pointercancel", function (event) { endDrag(event, false); });
    stage.addEventListener("lostpointercapture", function (event) { endDrag(event, false); });

    render();
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(document.querySelectorAll("[data-sort]"), sortPage);
    focusErrorSummary();
    Array.prototype.forEach.call(document.querySelectorAll("[data-max-choices]"), capChips);
    foldDrawerOnPhones();
    Array.prototype.forEach.call(document.querySelectorAll("[data-code-input]"), autoSubmitCode);
    Array.prototype.forEach.call(document.querySelectorAll("[data-countdown]"), countDown);
    Array.prototype.forEach.call(document.querySelectorAll("[data-show-when]"), showWhen);
    Array.prototype.forEach.call(document.querySelectorAll("form[data-counter-url]"), liveCounter);
    Array.prototype.forEach.call(document.querySelectorAll("input[data-city]"), cityAutocomplete);
    Array.prototype.forEach.call(document.querySelectorAll("input[data-suggestions-url]"), jobAutocomplete);
    Array.prototype.forEach.call(document.querySelectorAll("form[data-cv-upload]"), cvUpload);
    Array.prototype.forEach.call(document.querySelectorAll("form[data-auto-submit]"), autoSubmit);
  });
})();
