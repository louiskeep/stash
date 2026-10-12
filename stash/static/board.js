/* stash board: canvas of draggable paper notes, backed by the server.
 *
 * Boots from GET /board/notes, builds the board with Muuri, and saves position,
 * pile, add, and delete changes back through the CSRF-protected POST endpoints.
 * Note text is always placed with textContent, never innerHTML, so markup in a
 * note can never execute. Layout persists server-side, so the board is the same
 * on any device.
 *
 * Muuri (the drag/grid library) is expected as a global `Muuri`. The template
 * loads it before this script.
 */
(function () {
  "use strict";

  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  var MIN = 60000, HOUR = 3600000, DAY = 86400000;
  var TINTS = ["a", "b", "c", "d", "e"];

  var grid = document.getElementById("grid");
  var hint = document.getElementById("hint");
  var wrap = document.getElementById("wrap");

  // Muuri is vendored at /static/muuri.min.js (never a runtime CDN). If it is
  // missing, say so clearly instead of failing silently.
  if (typeof Muuri === "undefined") {
    hint.textContent = "The board needs its drag library. Download Muuri 0.9.5 "
      + "to stash/static/muuri.min.js (see stash/static/README.md), then reload.";
    hint.hidden = false;
    return;
  }
  var muuri = null;

  // searchIds: null = search inactive (show all); a Set = only these ids match.
  var state = { notes: [], piles: [], order: "newest", tag: null, cat: null,
                searchIds: null, from: null, to: null };

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
    wrap.appendChild(t); requestAnimationFrame(function () { t.classList.add("show"); });
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
  function tintFor(n) {
    var key = n.category || (n.tags[0] || "x"); var h = 0;
    for (var i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
    return TINTS[Math.abs(h) % TINTS.length];
  }
  function rot(id) { var r = ((id * 2654435761) % 1000) / 1000; return (r * 2.4 - 1.2).toFixed(2); }

  // --- note element (textContent only) -----------------------------------
  function noteEl(n) {
    var item = document.createElement("div");
    item.className = "item"; item.dataset.id = n.id;

    var note = document.createElement("div");
    note.className = "note " + tintFor(n);
    note.style.setProperty("--rot", rot(n.id) + "deg");

    var del = document.createElement("button");
    del.className = "del"; del.type = "button"; del.title = "Delete"; del.textContent = "×";
    del.addEventListener("click", function (e) { e.stopPropagation(); removeNote(n.id); });

    var raw = document.createElement("p");
    raw.className = "raw"; raw.textContent = n.raw;          // <- never innerHTML

    var foot = document.createElement("div"); foot.className = "foot";
    var time = document.createElement("time"); time.textContent = ago(n.created_at); foot.appendChild(time);
    if (n.category) { var c = document.createElement("span"); c.className = "c"; c.textContent = "@" + n.category; foot.appendChild(c); }
    (n.tags || []).forEach(function (t) { var s = document.createElement("span"); s.className = "t"; s.textContent = "#" + t; foot.appendChild(s); });
    if (n.timer) { var tm = document.createElement("span"); tm.className = "timer"; tm.textContent = until(n.timer); foot.appendChild(tm); }

    note.appendChild(del); note.appendChild(raw); note.appendChild(foot);
    item.appendChild(note);
    return item;
  }

  // --- filtering ---------------------------------------------------------
  // All active filters stack (AND): a note shows only if it passes every one.
  // An empty control does not narrow.
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
  function applyFilter() {
    if (!muuri) return;
    var show = [], hide = [];
    muuri.getItems().forEach(function (it) {
      var id = +it.getElement().dataset.id;
      var n = state.notes.find(function (x) { return x.id === id; });
      (n && passes(n) ? show : hide).push(it);
    });
    muuri.show(show); muuri.hide(hide);
    hint.hidden = show.length !== 0;
    buildRail();
  }
  function sortNotes(a, b) {
    var ta = new Date(a.created_at).getTime(), tb = new Date(b.created_at).getTime();
    return state.order === "newest" ? tb - ta : ta - tb;
  }

  // --- board build -------------------------------------------------------
  // Free-canvas layout: a note with saved x/y keeps exactly that spot across
  // reloads and devices; a note without saved coordinates (brand new) flows into
  // the first open grid slot so it is never dropped off-screen. This is what
  // makes the arrangement persist -- Muuri's default grid would re-flow every
  // note by order and discard saved positions.
  function posFor(id) {
    var n = state.notes.find(function (x) { return x.id === id; });
    return n && n.x != null && n.y != null ? { x: n.x, y: n.y } : null;
  }

  function customLayout(gridInst, layoutId, items, width, height, callback) {
    var COL = 224, ROW = 150, pad = 8; // flow step for unplaced notes
    var cols = Math.max(1, Math.floor((width - pad) / COL));
    var slots = [];
    var maxY = 0, flow = 0;
    for (var i = 0; i < items.length; i++) {
      var id = +items[i].getElement().dataset.id;
      var saved = posFor(id);
      var itemW = items[i].getWidth ? items[i].getWidth() : 208;
      // Clamp x to the visible width so no card sits off the right edge: the
      // board scrolls vertically only, never horizontally.
      var maxX = Math.max(pad, width - itemW - pad);
      var x, y;
      if (saved) { x = Math.max(pad, Math.min(saved.x, maxX)); y = Math.max(pad, saved.y); }
      else {
        x = pad + (flow % cols) * COL;
        y = pad + Math.floor(flow / cols) * ROW;
        flow++;
      }
      slots.push(x, y);
      if (y + ROW > maxY) maxY = y + ROW;
    }
    callback({
      id: layoutId, items: items, slots: slots,
      styles: { width: "100%", height: Math.max(height, maxY + 40) + "px" }
    });
  }

  function buildBoard() {
    if (muuri) { muuri.destroy(); grid.innerHTML = ""; }
    // Order by saved z when present (so stacking order survives), else by time.
    var ordered = state.notes.slice().sort(function (a, b) {
      var za = a.z, zb = b.z;
      if (a.x != null && b.x != null && za != null && zb != null) return za - zb;
      return sortNotes(a, b);
    });
    ordered.forEach(function (n) { grid.appendChild(noteEl(n)); });

    muuri = new Muuri(grid, {
      dragEnabled: true,
      dragRelease: { duration: 420, easing: "cubic-bezier(.17,.84,.44,1)" },
      dragPlaceholder: { enabled: true, createElement: function () { var d = document.createElement("div"); d.className = "item"; d.style.opacity = "0"; return d; } },
      layout: customLayout,
      layoutOnResize: 150,
      dragStartPredicate: function (item, e) {
        if (e.target.closest(".del")) return false;
        return Muuri.ItemDrag.defaultStartPredicate(item, e, { distance: 4, delay: 0 });
      }
    });
    // The piles module, when loaded, owns the drag-release handler (it resolves
    // stacking first, then calls savePositions), so we only self-subscribe when
    // it is absent. This keeps exactly one save per release, with a deterministic
    // order, instead of two racing fetches.
    if (!window.__boardPilesActive) muuri.on("dragReleaseEnd", savePositions);
    applyFilter();
    // Let the piles module (re)bind its handler and redraw against the new
    // Muuri instance after every rebuild (load, filter, order, add, stack).
    if (window.__onBoardBuilt) window.__onBoardBuilt();
  }

  function savePositions() {
    if (!muuri) return;
    var positions = muuri.getItems().map(function (it, i) {
      var el = it.getElement(); var r = it.getPosition();
      var id = +el.dataset.id;
      // Keep the in-memory note's x/y/z in sync so a later rebuild (filter,
      // add, stack) re-applies the position the user just set, not the stale one.
      var n = state.notes.find(function (x) { return x.id === id; });
      if (n) { n.x = r.left; n.y = r.top; n.z = i; }
      return { note_id: id, x: r.left, y: r.top, z: i };
    });
    api("/board/positions", { positions: positions }).catch(function () {
      toast("Could not save layout");
    });
  }

  // --- category + tag dropdowns ------------------------------------------
  // Two independent selects. Category value is the bare category, Tag value the
  // bare tag; empty is All. They AND in passes(n).
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
    // Keep selection if it still exists; else reset to All and clear the state,
    // so control and board never disagree after a delete removes the last option.
    if (Array.prototype.some.call(sel.options, function (o) { return o.value === prev; })) {
      sel.value = prev;
    } else {
      sel.value = "";
      state[stateKey] = null;
    }
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
    if (!muuri) return;
    // Reveal the note if a filter is hiding it, then scroll + highlight.
    var it = muuri.getItems().find(function (i) { return +i.getElement().dataset.id === id; });
    if (!it) return;
    if (!it.isVisible()) muuri.show([it]);
    var el = it.getElement();
    el.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
    el.classList.add("flash");
    setTimeout(function () { el.classList.remove("flash"); }, 1400);
  }

  // --- add / delete ------------------------------------------------------
  function removeNote(id) {
    api("/board/delete", { note_id: id }).then(function (r) {
      if (!r.ok) throw 0;
      state.notes = state.notes.filter(function (n) { return n.id !== id; });
      var it = muuri.getItems().find(function (i) { return +i.getElement().dataset.id === id; });
      if (it) muuri.remove([it], { removeElements: true });
      buildTopics(); applyFilter();
    }).catch(function () { toast("Could not delete note"); });
  }
  function addNote(text) {
    text = (text || "").trim(); if (!text) return;
    api("/board/add", { text: text }).then(function (r) {
      if (!r.ok) throw 0; return reload();
    }).catch(function () { toast("Could not add note"); });
  }

  // --- composer ----------------------------------------------------------
  var composer = null;
  function openComposer(x, y) {
    closeComposer();
    composer = document.createElement("div"); composer.className = "composer";
    var ta = document.createElement("textarea");
    ta.placeholder = "Stash a thought…  #tag @category";
    var tip = document.createElement("div"); tip.className = "tip"; tip.textContent = "#tags and @category are picked up automatically.";
    var row = document.createElement("div"); row.className = "row";
    var cancel = document.createElement("button"); cancel.className = "cancel"; cancel.type = "button"; cancel.textContent = "Cancel";
    var saveB = document.createElement("button"); saveB.className = "save"; saveB.type = "button"; saveB.textContent = "Stash";
    row.appendChild(cancel); row.appendChild(saveB);
    composer.appendChild(ta); composer.appendChild(tip); composer.appendChild(row);
    var bw = wrap.clientWidth, bh = wrap.clientHeight;
    composer.style.left = Math.min(Math.max(x, 8), bw - 238) + "px";
    composer.style.top = Math.min(Math.max(y, 8), bh - 150) + "px";
    wrap.appendChild(composer); ta.focus();
    saveB.addEventListener("click", function () { addNote(ta.value); closeComposer(); });
    cancel.addEventListener("click", closeComposer);
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { addNote(ta.value); closeComposer(); }
      if (e.key === "Escape") closeComposer();
    });
  }
  function closeComposer() { if (composer) { composer.remove(); composer = null; } }

  // --- search ------------------------------------------------------------
  var searchInput = document.getElementById("search");
  var searchTimer = null;
  function runSearch(q) {
    q = (q || "").trim();
    if (!q) { state.searchIds = null; applyFilter(); return; }
    fetch("/board/search?q=" + encodeURIComponent(q), { headers: { "X-CSRF-Token": CSRF } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.searchIds = new Set(data.ids || []);
        applyFilter();
      }).catch(function () { toast("Search failed"); });
  }
  searchInput.addEventListener("input", function () {
    var q = this.value;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { runSearch(q); }, 220);
  });

  // --- category / tag / order / date controls ----------------------------
  catSel.addEventListener("change", function () { state.cat = this.value || null; applyFilter(); });
  tagSel.addEventListener("change", function () { state.tag = this.value || null; applyFilter(); });
  document.getElementById("order").addEventListener("change", function () {
    state.order = this.value; buildBoard();
  });
  var fromInput = document.getElementById("from"), toInput = document.getElementById("to");
  // Date-only inputs. From is the start of its day; To includes the whole day
  // (end-of-day), so a note created any time on the To date still matches.
  function parseFrom(v) { if (!v) return null; var t = new Date(v + "T00:00:00").getTime(); return isNaN(t) ? null : t; }
  function parseTo(v) { if (!v) return null; var t = new Date(v + "T23:59:59.999").getTime(); return isNaN(t) ? null : t; }
  fromInput.addEventListener("change", function () { state.from = parseFrom(this.value); applyFilter(); });
  toInput.addEventListener("change", function () { state.to = parseTo(this.value); applyFilter(); });
  document.getElementById("clearBtn").addEventListener("click", function () {
    clearFilters(); applyFilter();
  });
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

  document.getElementById("addBtn").addEventListener("click", function () {
    openComposer(wrap.clientWidth - 250, wrap.clientHeight - 170);
  });
  wrap.addEventListener("dblclick", function (e) {
    if (e.target.closest(".note") || e.target.closest(".composer")) return;
    var r = this.getBoundingClientRect();
    openComposer(e.clientX - r.left + this.scrollLeft, e.clientY - r.top + this.scrollTop);
  });

  // --- saved boards ------------------------------------------------------
  // A saved board is a named filter set. "Save current" stores the active
  // filters; clicking a saved board re-applies them. One shared canvas layout.
  var boardsList = document.getElementById("boardsList");
  var boardsEmpty = document.getElementById("boardsEmpty");
  var savedBoards = [];
  var activeBoardId = null;

  function currentConfig() {
    return {
      category: state.cat, tag: state.tag,
      from: fromInput.value || null, to: toInput.value || null,
      search: searchInput.value || "", order: state.order
    };
  }
  function applyConfig(cfg) {
    cfg = cfg || {};
    // Topic selects: set state + control together.
    state.cat = cfg.category || null; catSel.value = state.cat || "";
    state.tag = cfg.tag || null; tagSel.value = state.tag || "";
    state.order = cfg.order || "newest"; document.getElementById("order").value = state.order;
    fromInput.value = cfg.from || ""; state.from = parseFrom(fromInput.value);
    toInput.value = cfg.to || ""; state.to = parseTo(toInput.value);
    searchInput.value = cfg.search || "";
    // Search is async; set it then re-filter when it returns (or clear).
    if (searchInput.value.trim()) runSearch(searchInput.value);
    else { state.searchIds = null; buildBoard(); applyFilter(); }
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

  // "Save current": an inline name input (window.prompt is inert in some hosts).
  var saveBtn = document.getElementById("saveBoardBtn");
  saveBtn.addEventListener("click", function () {
    if (document.getElementById("boardNameInput")) return;
    var input = document.createElement("input");
    input.id = "boardNameInput"; input.className = "board-name-input";
    input.placeholder = "Board name, then Enter";
    saveBtn.insertAdjacentElement("afterend", input); input.focus();
    function done(commit) {
      var name = input.value.trim(); input.remove();
      if (commit && name) {
        api("/board/boards", { name: name, config: currentConfig() }).then(function (r) {
          if (!r.ok) throw 0; return r.json();
        }).then(function (res) {
          activeBoardId = res.id; return loadBoards();
        }).catch(function () { toast("Could not save board"); });
      }
    }
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") done(true);
      if (e.key === "Escape") done(false);
    });
    input.addEventListener("blur", function () { done(true); });
  });

  // Changing any filter detaches the active-board highlight (the view no longer
  // matches a saved board exactly).
  [searchInput, catSel, tagSel, document.getElementById("order"), fromInput, toInput]
    .forEach(function (el) { el.addEventListener("input", function () { if (activeBoardId) { activeBoardId = null; renderBoards(); } }); });

  // --- load + boot -------------------------------------------------------
  function reload() {
    return fetch("/board/notes", { headers: { "X-CSRF-Token": CSRF } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.notes = data.notes || [];
        state.piles = data.piles || [];
        buildTopics(); buildBoard();
      });
  }

  reload().catch(function () { hint.textContent = "Could not load your notes."; hint.hidden = false; });
  loadBoards();

  setInterval(function () {
    if (!muuri) return;
    muuri.getItems().forEach(function (it) {
      var id = +it.getElement().dataset.id;
      var n = state.notes.find(function (x) { return x.id === id; });
      if (!n) return;
      var el = it.getElement();
      var tEl = el.querySelector(".foot time"); if (tEl) tEl.textContent = ago(n.created_at);
      var tm = el.querySelector(".timer"); if (tm && n.timer) tm.textContent = until(n.timer);
    });
    buildRail();  // keep the rail countdowns fresh too
  }, 30000);

  // expose a couple of internals for the piles module (Task 4) to extend.
  window.__board = { get muuri() { return muuri; }, state: state, api: api, toast: toast,
    savePositions: savePositions, reload: reload };
})();
