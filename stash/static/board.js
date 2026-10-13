/* stash board: an ordered grid of paper notes, backed by the server.
 *
 * Boots from GET /board/notes, lays the notes out as a CSS grid ordered by time,
 * and saves merges, adds, and deletes back through the CSRF-protected POST
 * endpoints. A card is one note, or several notes merged into one: drag a note
 * onto another's top or bottom half to combine them in that order, and click the
 * dashed divider in a merged card to snip it apart again. Merging never rewrites
 * a note -- each raw note stays its own row, so a merge is a reversible grouping.
 *
 * Note text is always placed with textContent, never innerHTML, so markup in a
 * note can never execute.
 */
(function () {
  "use strict";

  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  var MIN = 60000, HOUR = 3600000, DAY = 86400000;

  var grid = document.getElementById("grid");
  var hint = document.getElementById("hint");
  var wrap = document.getElementById("wrap");

  // searchIds: null = search inactive (show all); a Set = only these ids match.
  // notes: flat list from the server. cards: the merge layout, each { ids:[..] }
  // in top-to-bottom order; a one-id card is a standalone note.
  var state = { notes: [], byId: {}, cards: [], order: "newest",
                tag: null, cat: null, searchIds: null, from: null, to: null };

  // Auto palette: a stable color per category/tag when the user has not chosen
  // one in Settings. Hues chosen to sit well on warm paper in both themes.
  var PALETTE = ["#5b5bd6", "#2f8fa8", "#c77d12", "#3a9b6e", "#c7568f",
                 "#5f7194", "#7b61d6", "#b5882a"];

  // --- helpers -----------------------------------------------------------
  function api(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify(body)
    });
  }
  function toast(msg) {
    var t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t); requestAnimationFrame(function () { t.classList.add("show"); });
    setTimeout(function () { t.classList.remove("show"); setTimeout(function () { t.remove(); }, 250); }, 1600);
  }
  function ago(t) {
    var d = Date.now() - new Date(t).getTime();
    if (d < HOUR) return Math.max(1, Math.round(d / MIN)) + "m ago";
    if (d < DAY) return Math.round(d / HOUR) + "h ago";
    return Math.round(d / DAY) + "d ago";
  }
  function until(iso) {
    var d = new Date(iso).getTime() - Date.now();
    if (d <= 0) return "due";
    if (d < HOUR) return "in " + Math.max(1, Math.round(d / MIN)) + "m";
    if (d < DAY) return "in " + Math.round(d / HOUR) + "h";
    return "in " + Math.round(d / DAY) + "d";
  }
  function noteById(id) { return state.byId[id]; }
  function hash(s) { var h = 0; s = String(s); for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return Math.abs(h); }

  // chipColors (from Settings) win; otherwise a stable palette color.
  var chipColors = {};  // "tag:x" / "cat:y" -> "#rrggbb"
  function colorFor(prefix, val) {
    if (!val) return null;
    var hex = chipColors[prefix + ":" + val];
    return hex || PALETTE[hash(prefix + val) % PALETTE.length];
  }
  function catColor(cat) { return colorFor("cat", cat); }
  function tagColor(tag) { return colorFor("tag", tag); }

  // --- cards model -------------------------------------------------------
  function buildCards() {
    // Reconstruct merged cards from each note's pile_id + seq, standalone notes
    // for the rest. The order within a group is seq (0 = top segment).
    var byPile = {};
    state.notes.forEach(function (n) {
      if (n.pile_id != null) (byPile[n.pile_id] = byPile[n.pile_id] || []).push(n);
    });
    var grouped = {}, cards = [];
    Object.keys(byPile).forEach(function (pid) {
      var members = byPile[pid].slice().sort(function (a, b) { return (a.seq || 0) - (b.seq || 0); });
      if (members.length >= 2) {
        cards.push({ ids: members.map(function (n) { return n.id; }) });
        members.forEach(function (n) { grouped[n.id] = 1; });
      }
    });
    state.notes.forEach(function (n) { if (!grouped[n.id]) cards.push({ ids: [n.id] }); });
    state.cards = cards;
  }
  function cardByKey(key) {
    return state.cards.find(function (c) { return String(c.ids[0]) === String(key); });
  }
  function cardTime(card) {
    var times = card.ids.map(function (id) { var n = noteById(id); return n ? new Date(n.created_at).getTime() : 0; });
    return state.order === "newest" ? Math.max.apply(null, times) : Math.min.apply(null, times);
  }

  function persist() {
    var groups = state.cards.filter(function (c) { return c.ids.length >= 2; })
      .map(function (c) { return c.ids; });
    api("/board/groups", { groups: groups }).then(function (r) { if (!r.ok) throw 0; })
      .catch(function () { toast("Could not save layout"); });
  }

  // --- filtering ---------------------------------------------------------
  // All active filters stack (AND) at the note level. A card shows if ANY of its
  // notes passes, so a merged card is treated as one atomic unit.
  function passes(n) {
    if (state.searchIds !== null && !state.searchIds.has(n.id)) return false;
    if (state.tag && (n.tags || []).indexOf(state.tag) === -1) return false;
    if (state.cat && n.category !== state.cat) return false;
    if (state.from || state.to) {
      var t = new Date(n.created_at).getTime();
      if (state.from && t < state.from) return false;
      if (state.to && t > state.to) return false;
    }
    return true;
  }
  function cardPasses(card) {
    return card.ids.some(function (id) { var n = noteById(id); return n && passes(n); });
  }

  // --- rendering ---------------------------------------------------------
  function cardEl(card) {
    var ids = card.ids;
    var topNote = noteById(ids[0]);
    var el = document.createElement("div");
    el.className = "card pop"; el.draggable = true; el.dataset.card = ids[0];
    var col = topNote && topNote.category ? catColor(topNote.category) : null;
    if (col) el.style.setProperty("--cat", col);

    el.appendChild(delButton(card));

    // reminder tab: the soonest pending timer among members
    var timed = ids.map(noteById).filter(function (n) { return n && n.timer; })
      .sort(function (a, b) { return new Date(a.timer) - new Date(b.timer); });
    if (timed.length) {
      var rt = document.createElement("span"); rt.className = "remind-tab";
      rt.appendChild(icon("clock"));
      rt.appendChild(document.createTextNode(until(timed[0].timer)));
      el.appendChild(rt);
    }

    ids.forEach(function (id, i) {
      if (i > 0) el.appendChild(snipEl(card, i));
      var n = noteById(id);
      var seg = document.createElement("div"); seg.className = "seg";
      seg.textContent = n ? n.raw : "";          // <- never innerHTML
      el.appendChild(seg);
    });

    el.appendChild(metaEl(card));
    wireDrag(el);
    return el;
  }

  function delButton(card) {
    var del = document.createElement("button");
    del.className = "del"; del.type = "button"; del.title = "Delete"; del.textContent = "×";
    var armed = false, resetTimer = null;
    del.addEventListener("click", function (e) {
      e.stopPropagation();
      if (card.ids.length === 1) { deleteNotes(card.ids); return; }
      // Merged card: two-step confirm so several raw notes are never lost by a
      // stray click. First click arms, second within 2.5s deletes them all.
      if (!armed) {
        armed = true; del.classList.add("armed"); del.textContent = "Delete " + card.ids.length + "?";
        resetTimer = setTimeout(function () {
          armed = false; del.classList.remove("armed"); del.textContent = "×";
        }, 2500);
        return;
      }
      if (resetTimer) clearTimeout(resetTimer);
      deleteNotes(card.ids.slice());
    });
    return del;
  }

  function snipEl(card, i) {
    var snip = document.createElement("div");
    snip.className = "snip"; snip.title = "Snip apart here"; snip.tabIndex = 0;
    snip.setAttribute("role", "button"); snip.setAttribute("aria-label", "Separate notes here");
    var lbl = document.createElement("span"); lbl.className = "lbl";
    lbl.appendChild(icon("scissors")); lbl.appendChild(document.createTextNode("separate"));
    snip.appendChild(lbl);
    snip.addEventListener("click", function (e) { e.stopPropagation(); snipAt(card, i); });
    snip.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); snipAt(card, i); } });
    snip.addEventListener("dragstart", function (e) { e.preventDefault(); e.stopPropagation(); });
    return snip;
  }

  function metaEl(card) {
    var meta = document.createElement("div"); meta.className = "meta";
    if (card.ids.length > 1) {
      var m = document.createElement("span"); m.className = "merged";
      m.appendChild(icon("merge")); m.appendChild(document.createTextNode(String(card.ids.length)));
      meta.appendChild(m);
    }
    // union of categories then tags across members, de-duped, clickable.
    var seen = {};
    card.ids.map(noteById).forEach(function (n) {
      if (n && n.category && !seen["c" + n.category]) { seen["c" + n.category] = 1; meta.appendChild(chipEl("cat", n.category)); }
    });
    card.ids.map(noteById).forEach(function (n) {
      if (!n) return;
      (n.tags || []).forEach(function (t) { if (!seen["t" + t]) { seen["t" + t] = 1; meta.appendChild(chipEl("tag", t)); } });
    });
    var time = document.createElement("span"); time.className = "time";
    var top = noteById(card.ids[0]); if (top) time.textContent = ago(top.created_at);
    meta.appendChild(time);
    return meta;
  }

  function chipEl(kind, val) {
    var color = kind === "cat" ? catColor(val) : tagColor(val);
    var b = document.createElement("button"); b.type = "button"; b.className = "chip " + kind;
    b.textContent = val;
    if (color) {
      b.style.background = "color-mix(in srgb, " + color + " 16%, var(--surface))";
      b.style.borderColor = "color-mix(in srgb, " + color + " 38%, var(--line))";
    }
    b.addEventListener("click", function (e) {
      e.stopPropagation();
      if (kind === "cat") filterByCategory(val); else filterByTag(val);
    });
    return b;
  }

  // small inline icons (no external assets)
  function icon(name) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor"); svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round"); svg.setAttribute("stroke-linejoin", "round");
    var paths = {
      clock: ["M12 12V7", "M12 12l3 2"], // plus a circle added below
      scissors: ["M20 4 8.5 15.5", "M8 8 20 20", "M9.5 15.5 20 4"],
      merge: ["M7 4v6a4 4 0 0 0 4 4h6", "m14 11 3 3-3 3"]
    };
    if (name === "clock") {
      var c = document.createElementNS(ns, "circle");
      c.setAttribute("cx", "12"); c.setAttribute("cy", "12"); c.setAttribute("r", "9"); svg.appendChild(c);
    }
    if (name === "scissors") {
      [[6, 6], [6, 18]].forEach(function (p) {
        var c = document.createElementNS(ns, "circle");
        c.setAttribute("cx", p[0]); c.setAttribute("cy", p[1]); c.setAttribute("r", "3"); svg.appendChild(c);
      });
    }
    (paths[name] || []).forEach(function (d) {
      var p = document.createElementNS(ns, "path"); p.setAttribute("d", d); svg.appendChild(p);
    });
    return svg;
  }

  function cardsForDisplay() {
    return state.cards.filter(cardPasses).sort(function (a, b) {
      var ta = cardTime(a), tb = cardTime(b);
      return state.order === "newest" ? tb - ta : ta - tb;
    });
  }
  function render() {
    var list = cardsForDisplay();
    grid.innerHTML = "";
    list.forEach(function (card) { grid.appendChild(cardEl(card)); });
    hint.hidden = list.length !== 0;
    buildRail();
  }

  // --- drag to merge -----------------------------------------------------
  var dragKey = null;
  function clearOver() {
    grid.querySelectorAll(".card.over-top, .card.over-bottom").forEach(function (el) {
      el.classList.remove("over-top", "over-bottom");
    });
  }
  function wireDrag(el) {
    el.addEventListener("dragstart", function (e) {
      dragKey = el.dataset.card; el.classList.add("dragging");
      try { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", el.dataset.card); } catch (x) {}
    });
    el.addEventListener("dragend", function () { el.classList.remove("dragging"); clearOver(); dragKey = null; });
    el.addEventListener("dragover", function (e) {
      if (dragKey == null || el.dataset.card === dragKey) return;
      e.preventDefault();
      var r = el.getBoundingClientRect();
      var topHalf = (e.clientY - r.top) < r.height / 2;
      el.classList.toggle("over-top", topHalf);
      el.classList.toggle("over-bottom", !topHalf);
    });
    el.addEventListener("dragleave", function (e) {
      if (el.contains(e.relatedTarget)) return;        // ignore moves into children
      el.classList.remove("over-top", "over-bottom");
    });
    el.addEventListener("drop", function (e) {
      e.preventDefault();
      if (dragKey == null || el.dataset.card === dragKey) return;
      var above = el.classList.contains("over-top");
      mergeCards(dragKey, el.dataset.card, above);
      clearOver();
    });
  }

  function mergeCards(srcKey, targetKey, above) {
    var src = cardByKey(srcKey), target = cardByKey(targetKey);
    if (!src || !target || src === target) return;
    var combined = { ids: above ? src.ids.concat(target.ids) : target.ids.concat(src.ids) };
    // Replace target in place with the combined card; drop the source card.
    state.cards = state.cards.reduce(function (acc, c) {
      if (c === src) return acc;
      acc.push(c === target ? combined : c);
      return acc;
    }, []);
    persist(); render();
  }

  // snip a merged card at boundary i: segments above stay one card, the rest
  // become another (each a standalone note if left with one).
  function snipAt(card, i) {
    var idx = state.cards.indexOf(card);
    if (idx === -1) return;
    var a = { ids: card.ids.slice(0, i) }, b = { ids: card.ids.slice(i) };
    state.cards.splice(idx, 1, a, b);
    persist(); render();
  }

  // --- category + tag dropdowns ------------------------------------------
  var catSel = document.getElementById("category");
  var tagSel = document.getElementById("tag");
  function fillSelect(sel, counts, prefix, stateKey, sortByCount) {
    var prev = sel.value;
    while (sel.options.length > 1) sel.remove(1);
    var keys = Object.keys(counts);
    keys.sort(sortByCount ? function (a, b) { return counts[b] - counts[a]; }
                          : function (a, b) { return a.localeCompare(b); });
    keys.forEach(function (k) {
      var o = document.createElement("option"); o.value = k;
      o.textContent = prefix + k + " (" + counts[k] + ")"; sel.appendChild(o);
    });
    if (Array.prototype.some.call(sel.options, function (o) { return o.value === prev; })) {
      sel.value = prev;
    } else { sel.value = ""; state[stateKey] = null; }
  }
  function buildTopics() {
    var tagCount = {}, catCount = {};
    state.notes.forEach(function (n) {
      (n.tags || []).forEach(function (t) { tagCount[t] = (tagCount[t] || 0) + 1; });
      if (n.category) catCount[n.category] = (catCount[n.category] || 0) + 1;
    });
    fillSelect(catSel, catCount, "@", "cat", false);
    fillSelect(tagSel, tagCount, "#", "tag", true);
  }
  function filterByCategory(cat) {
    state.cat = cat || null; catSel.value = state.cat || "";
    detachActive(); render();
  }
  function filterByTag(tag) {
    state.tag = tag || null; tagSel.value = state.tag || "";
    detachActive(); render();
  }

  // --- reminders dropdown ------------------------------------------------
  var remList = document.getElementById("remList");
  var remEmpty = document.getElementById("remEmpty");
  var remCount = document.getElementById("remindersCount");
  function buildRail() {
    var due = state.notes.filter(function (n) { return n.timer; })
      .sort(function (a, b) { return new Date(a.timer) - new Date(b.timer); });
    remList.innerHTML = "";
    remEmpty.hidden = due.length !== 0;
    if (due.length) { remCount.textContent = due.length; remCount.hidden = false; }
    else { remCount.hidden = true; }
    due.forEach(function (n) {
      var li = document.createElement("li"); li.className = "rem-item"; li.tabIndex = 0;
      li.setAttribute("role", "menuitem");
      var when = document.createElement("span"); when.className = "rem-when"; when.textContent = until(n.timer);
      var what = document.createElement("span"); what.className = "rem-what";
      what.textContent = n.raw.split(/\s+/).slice(0, 6).join(" ");
      li.appendChild(when); li.appendChild(what);
      li.addEventListener("click", function () { focusNote(n.id); closeReminders(); });
      li.addEventListener("keydown", function (e) { if (e.key === "Enter") { focusNote(n.id); closeReminders(); } });
      remList.appendChild(li);
    });
  }
  function focusNote(id) {
    var card = state.cards.find(function (c) { return c.ids.indexOf(id) !== -1; });
    if (!card) return;
    var el = grid.querySelector('.card[data-card="' + card.ids[0] + '"]');
    if (!el) {            // hidden by a filter: clear filters and re-render to reveal it
      clearFilters(); render();
      el = grid.querySelector('.card[data-card="' + card.ids[0] + '"]');
    }
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("flash");
    setTimeout(function () { el.classList.remove("flash"); }, 1400);
  }

  // --- add / delete ------------------------------------------------------
  function deleteNotes(ids) {
    var chain = Promise.resolve();
    ids.forEach(function (id) {
      chain = chain.then(function () { return api("/board/delete", { note_id: id }).then(function (r) { if (!r.ok) throw 0; }); });
    });
    chain.then(function () { return reload(); }).catch(function () { toast("Could not delete note"); });
  }
  function addNote(text) {
    text = (text || "").trim(); if (!text) return;
    api("/board/add", { text: text }).then(function (r) { if (!r.ok) throw 0; return reload(); })
      .catch(function () { toast("Could not add note"); });
  }

  // --- composer (centered) -----------------------------------------------
  var composer = null, backdrop = null;
  function openComposer() {
    closeComposer();
    backdrop = document.createElement("div"); backdrop.className = "composer-backdrop";
    backdrop.addEventListener("click", closeComposer);
    composer = document.createElement("div"); composer.className = "composer";
    var ta = document.createElement("textarea");
    ta.placeholder = "Stash a thought…  #tag @category";
    var tip = document.createElement("div"); tip.className = "tip";
    tip.textContent = "#tags and @category are picked up automatically.";
    var row = document.createElement("div"); row.className = "row";
    var cancel = document.createElement("button"); cancel.className = "cancel"; cancel.type = "button"; cancel.textContent = "Cancel";
    var saveB = document.createElement("button"); saveB.className = "save"; saveB.type = "button"; saveB.textContent = "Stash";
    row.appendChild(cancel); row.appendChild(saveB);
    composer.appendChild(ta); composer.appendChild(tip); composer.appendChild(row);
    document.body.appendChild(backdrop); document.body.appendChild(composer); ta.focus();
    saveB.addEventListener("click", function () { addNote(ta.value); closeComposer(); });
    cancel.addEventListener("click", closeComposer);
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { addNote(ta.value); closeComposer(); }
      if (e.key === "Escape") closeComposer();
    });
  }
  function closeComposer() {
    if (composer) { composer.remove(); composer = null; }
    if (backdrop) { backdrop.remove(); backdrop = null; }
  }

  // --- search ------------------------------------------------------------
  var searchInput = document.getElementById("search");
  var searchTimer = null;
  function runSearch(q) {
    q = (q || "").trim();
    if (!q) { state.searchIds = null; render(); return; }
    fetch("/board/search?q=" + encodeURIComponent(q), { headers: { "X-CSRF-Token": CSRF } })
      .then(function (r) { return r.json(); })
      .then(function (data) { state.searchIds = new Set(data.ids || []); render(); })
      .catch(function () { toast("Search failed"); });
  }
  searchInput.addEventListener("input", function () {
    var q = this.value;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { runSearch(q); }, 220);
  });

  // --- category / tag / order / date controls ----------------------------
  catSel.addEventListener("change", function () { state.cat = this.value || null; render(); });
  tagSel.addEventListener("change", function () { state.tag = this.value || null; render(); });
  var orderSel = document.getElementById("order");
  orderSel.addEventListener("change", function () { state.order = this.value; render(); });
  var fromInput = document.getElementById("from"), toInput = document.getElementById("to");
  function parseFrom(v) { if (!v) return null; var t = new Date(v + "T00:00:00").getTime(); return isNaN(t) ? null : t; }
  function parseTo(v) { if (!v) return null; var t = new Date(v + "T23:59:59.999").getTime(); return isNaN(t) ? null : t; }
  fromInput.addEventListener("change", function () { state.from = parseFrom(this.value); render(); });
  toInput.addEventListener("change", function () { state.to = parseTo(this.value); render(); });
  document.getElementById("clearBtn").addEventListener("click", function () { clearFilters(); render(); });
  function clearFilters() {
    state.tag = state.cat = null; state.searchIds = null; state.from = state.to = null;
    searchInput.value = ""; catSel.value = ""; tagSel.value = "";
    fromInput.value = ""; toInput.value = "";
  }

  // --- reminders dropdown toggle -----------------------------------------
  var remindersBtn = document.getElementById("remindersBtn");
  var remindersPanel = document.getElementById("remindersPanel");
  function openReminders() {
    remindersPanel.hidden = false; remindersBtn.setAttribute("aria-expanded", "true");
    document.addEventListener("click", outsideReminders, true);
    document.addEventListener("keydown", escReminders, true);
  }
  function closeReminders() {
    remindersPanel.hidden = true; remindersBtn.setAttribute("aria-expanded", "false");
    document.removeEventListener("click", outsideReminders, true);
    document.removeEventListener("keydown", escReminders, true);
  }
  function outsideReminders(e) {
    if (!remindersPanel.contains(e.target) && e.target !== remindersBtn && !remindersBtn.contains(e.target)) closeReminders();
  }
  function escReminders(e) { if (e.key === "Escape") { closeReminders(); remindersBtn.focus(); } }
  remindersBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    if (remindersPanel.hidden) openReminders(); else closeReminders();
  });

  document.getElementById("addBtn").addEventListener("click", openComposer);
  wrap.addEventListener("dblclick", function (e) {
    if (e.target.closest(".card") || e.target.closest(".composer")) return;
    openComposer();
  });

  // --- settings (theme, default sort, tag/category colors) ---------------
  var settings = { theme: "system", defaultSort: "newest", colors: {} };
  var settingsBtn = document.getElementById("settingsBtn");
  var settingsPanel = document.getElementById("settingsPanel");
  var colorsList = document.getElementById("colorsList");
  var colorsEmpty = document.getElementById("colorsEmpty");
  var defaultSortSel = document.getElementById("defaultSort");
  var HEX = /^#[0-9a-fA-F]{6}$/;

  function applyTheme(theme) {
    var root = document.documentElement;
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
    document.querySelectorAll("#themeRow .theme-opt").forEach(function (b) {
      b.classList.toggle("on", b.dataset.theme === theme);
    });
  }
  function applySettings(s) {
    settings = s || settings;
    chipColors = {};
    Object.keys(settings.colors || {}).forEach(function (k) {
      if (HEX.test(settings.colors[k])) chipColors[k] = settings.colors[k];
    });
    applyTheme(settings.theme);
    defaultSortSel.value = settings.defaultSort || "newest";
    renderColorRows();
  }
  function saveSettings() {
    api("/board/settings", settings).then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (clean) { applySettings(clean); render(); })
      .catch(function () { toast("Could not save settings"); });
  }
  function loadSettings() {
    return fetch("/board/settings", { headers: { "X-CSRF-Token": CSRF } })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        applySettings(s);
        if (!orderTouched) { state.order = settings.defaultSort || "newest"; orderSel.value = state.order; }
      }).catch(function () {});
  }
  function renderColorRows() {
    var cats = {}, tags = {};
    state.notes.forEach(function (n) {
      if (n.category) cats[n.category] = true;
      (n.tags || []).forEach(function (t) { tags[t] = true; });
    });
    var keys = Object.keys(cats).sort().map(function (c) { return { key: "cat:" + c, label: "@" + c, dflt: catColor(c) }; })
      .concat(Object.keys(tags).sort().map(function (t) { return { key: "tag:" + t, label: "#" + t, dflt: tagColor(t) }; }));
    colorsList.innerHTML = "";
    colorsEmpty.hidden = keys.length !== 0;
    keys.forEach(function (item) {
      var row = document.createElement("div"); row.className = "color-row";
      var lab = document.createElement("span"); lab.className = "color-label"; lab.textContent = item.label;
      var input = document.createElement("input"); input.type = "color";
      input.value = settings.colors[item.key] || item.dflt || "#888888";
      input.addEventListener("change", function () { settings.colors[item.key] = input.value; saveSettings(); });
      var clear = document.createElement("button"); clear.type = "button"; clear.className = "color-clear";
      clear.textContent = "×"; clear.title = "Reset to default color";
      clear.addEventListener("click", function () { delete settings.colors[item.key]; saveSettings(); });
      row.appendChild(lab); row.appendChild(input); row.appendChild(clear);
      colorsList.appendChild(row);
    });
  }
  function openSettings() {
    settingsPanel.hidden = false; settingsBtn.setAttribute("aria-expanded", "true");
    renderColorRows();
    document.addEventListener("click", outsideSettings, true);
    document.addEventListener("keydown", escSettings, true);
  }
  function closeSettings() {
    settingsPanel.hidden = true; settingsBtn.setAttribute("aria-expanded", "false");
    document.removeEventListener("click", outsideSettings, true);
    document.removeEventListener("keydown", escSettings, true);
  }
  function outsideSettings(e) {
    if (!settingsPanel.contains(e.target) && e.target !== settingsBtn && !settingsBtn.contains(e.target)) closeSettings();
  }
  function escSettings(e) { if (e.key === "Escape") { closeSettings(); settingsBtn.focus(); } }
  settingsBtn.addEventListener("click", function (e) {
    e.stopPropagation(); if (settingsPanel.hidden) openSettings(); else closeSettings();
  });
  document.getElementById("themeRow").addEventListener("click", function (e) {
    var b = e.target.closest(".theme-opt"); if (!b) return;
    settings.theme = b.dataset.theme; applyTheme(settings.theme); saveSettings();
  });
  defaultSortSel.addEventListener("change", function () { settings.defaultSort = this.value; saveSettings(); });
  var orderTouched = false;
  orderSel.addEventListener("change", function () { orderTouched = true; });

  // --- saved boards ------------------------------------------------------
  var boardsList = document.getElementById("boardsList");
  var boardsEmpty = document.getElementById("boardsEmpty");
  var savedBoards = [];
  var activeBoardId = null;

  function currentConfig() {
    return { category: state.cat, tag: state.tag,
             from: fromInput.value || null, to: toInput.value || null,
             search: searchInput.value || "", order: state.order };
  }
  function applyConfig(cfg) {
    cfg = cfg || {};
    state.cat = cfg.category || null; catSel.value = state.cat || "";
    state.tag = cfg.tag || null; tagSel.value = state.tag || "";
    state.order = cfg.order || "newest"; orderSel.value = state.order;
    fromInput.value = cfg.from || ""; state.from = parseFrom(fromInput.value);
    toInput.value = cfg.to || ""; state.to = parseTo(toInput.value);
    searchInput.value = cfg.search || "";
    if (searchInput.value.trim()) runSearch(searchInput.value);
    else { state.searchIds = null; render(); }
  }
  function renderBoards() {
    boardsList.innerHTML = "";
    boardsEmpty.hidden = savedBoards.length !== 0;
    savedBoards.forEach(function (b) {
      var li = document.createElement("li"); li.className = "board-row";
      var open = document.createElement("button");
      open.className = "open" + (b.id === activeBoardId ? " on" : "");
      open.type = "button"; open.textContent = b.name;
      open.addEventListener("click", function () { activeBoardId = b.id; applyConfig(b.config); renderBoards(); });
      var rm = document.createElement("button");
      rm.className = "rm"; rm.type = "button"; rm.title = "Delete board"; rm.textContent = "×";
      rm.addEventListener("click", function (e) { e.stopPropagation(); deleteBoard(b.id); });
      li.appendChild(open); li.appendChild(rm);
      boardsList.appendChild(li);
    });
  }
  function loadBoards() {
    return fetch("/board/boards", { headers: { "X-CSRF-Token": CSRF } })
      .then(function (r) { return r.json(); })
      .then(function (data) { savedBoards = data.boards || []; renderBoards(); })
      .catch(function () {});
  }
  function deleteBoard(id) {
    api("/board/boards/delete", { id: id }).then(function (r) {
      if (!r.ok) throw 0;
      savedBoards = savedBoards.filter(function (b) { return b.id !== id; });
      if (activeBoardId === id) activeBoardId = null;
      renderBoards();
    }).catch(function () { toast("Could not delete board"); });
  }
  var saveBtn = document.getElementById("saveBoardBtn");
  saveBtn.addEventListener("click", function () {
    if (document.getElementById("boardNameInput")) return;
    var input = document.createElement("input");
    input.id = "boardNameInput"; input.className = "board-name-input";
    input.placeholder = "Board name, then Enter";
    saveBtn.insertAdjacentElement("afterend", input); input.focus();
    var finished = false;
    function done(commit) {
      if (finished) return;
      finished = true;
      var name = input.value.trim(); input.remove();
      if (commit && name) {
        api("/board/boards", { name: name, config: currentConfig() }).then(function (r) {
          if (!r.ok) throw 0; return r.json();
        }).then(function (res) { activeBoardId = res.id; return loadBoards(); })
          .catch(function () { toast("Could not save board"); });
      }
    }
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") done(true);
      if (e.key === "Escape") done(false);
    });
    input.addEventListener("blur", function () { done(true); });
  });

  function detachActive() { if (activeBoardId) { activeBoardId = null; renderBoards(); } }
  [searchInput, catSel, tagSel, orderSel, fromInput, toInput].forEach(function (el) {
    el.addEventListener("input", detachActive); el.addEventListener("change", detachActive);
  });

  // --- load + boot -------------------------------------------------------
  function reload() {
    return fetch("/board/notes", { headers: { "X-CSRF-Token": CSRF } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.notes = data.notes || [];
        state.byId = {};
        state.notes.forEach(function (n) { state.byId[n.id] = n; });
        buildCards(); buildTopics(); render();
        if (!settingsPanel.hidden) renderColorRows();
      });
  }

  loadSettings()
    .catch(function () {})
    .then(reload)
    .catch(function () { hint.textContent = "Could not load your notes."; hint.hidden = false; });
  loadBoards();

  // keep "ago"/countdown text fresh without disturbing a drag in progress
  setInterval(function () {
    if (dragKey != null) return;
    grid.querySelectorAll(".card").forEach(function (el) {
      var card = cardByKey(el.dataset.card); if (!card) return;
      var top = noteById(card.ids[0]);
      var tEl = el.querySelector(".meta .time"); if (tEl && top) tEl.textContent = ago(top.created_at);
      var timed = card.ids.map(noteById).filter(function (n) { return n && n.timer; })
        .sort(function (a, b) { return new Date(a.timer) - new Date(b.timer); });
      var rt = el.querySelector(".remind-tab");
      if (rt && timed.length) rt.lastChild.textContent = until(timed[0].timer);
    });
    buildRail();
  }, 30000);
})();
