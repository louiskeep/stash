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
  var chipsBox = document.getElementById("chips");

  // Muuri is vendored at /static/muuri.min.js (never a runtime CDN). If it is
  // missing, say so clearly instead of failing silently.
  if (typeof Muuri === "undefined") {
    hint.textContent = "The board needs its drag library. Download Muuri 0.9.5 "
      + "to stash/static/muuri.min.js (see stash/static/README.md), then reload.";
    hint.hidden = false;
    return;
  }
  var muuri = null;

  var state = { notes: [], piles: [], order: "newest", tag: null, cat: null, timersOnly: false };

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
  function passes(n) {
    if (state.tag && (n.tags || []).indexOf(state.tag) === -1) return false;
    if (state.cat && n.category !== state.cat) return false;
    if (state.timersOnly && !n.timer) return false;
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
      var x, y;
      if (saved) { x = saved.x; y = saved.y; }
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

  // --- chips -------------------------------------------------------------
  function buildChips() {
    var tagCount = {}, catCount = {};
    state.notes.forEach(function (n) {
      (n.tags || []).forEach(function (t) { tagCount[t] = (tagCount[t] || 0) + 1; });
      if (n.category) catCount[n.category] = (catCount[n.category] || 0) + 1;
    });
    chipsBox.innerHTML = "";
    Object.keys(catCount).sort().forEach(function (c) {
      chipsBox.appendChild(chip("@" + c, catCount[c], "cat", function () {
        state.cat = state.cat === c ? null : c; state.tag = null; refreshChips(); applyFilter();
      }, function () { return state.cat === c; }));
    });
    Object.keys(tagCount).sort(function (a, b) { return tagCount[b] - tagCount[a]; }).forEach(function (t) {
      chipsBox.appendChild(chip("#" + t, tagCount[t], "tag", function () {
        state.tag = state.tag === t ? null : t; state.cat = null; refreshChips(); applyFilter();
      }, function () { return state.tag === t; }));
    });
  }
  function chip(label, n, cls, fn, isOn) {
    var b = document.createElement("button");
    b.className = "chip " + cls + (isOn() ? " on" : "");
    b.type = "button"; b._isOn = isOn;
    b.textContent = label + " ";
    var sp = document.createElement("span"); sp.className = "n"; sp.textContent = n; b.appendChild(sp);
    b.addEventListener("click", fn);
    return b;
  }
  function refreshChips() {
    chipsBox.querySelectorAll(".chip").forEach(function (c) { c.classList.toggle("on", c._isOn()); });
  }

  // --- add / delete ------------------------------------------------------
  function removeNote(id) {
    api("/board/delete", { note_id: id }).then(function (r) {
      if (!r.ok) throw 0;
      state.notes = state.notes.filter(function (n) { return n.id !== id; });
      var it = muuri.getItems().find(function (i) { return +i.getElement().dataset.id === id; });
      if (it) muuri.remove([it], { removeElements: true });
      buildChips(); applyFilter();
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

  // --- bar wiring --------------------------------------------------------
  document.getElementById("order").addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (!b) return;
    state.order = b.dataset.order;
    this.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x === b); });
    buildBoard();
  });
  var timersBtn = document.getElementById("timersBtn");
  timersBtn.addEventListener("click", function () {
    state.timersOnly = !state.timersOnly; this.classList.toggle("on", state.timersOnly); applyFilter();
  });
  document.getElementById("addBtn").addEventListener("click", function () {
    openComposer(wrap.clientWidth - 250, wrap.clientHeight - 170);
  });
  wrap.addEventListener("dblclick", function (e) {
    if (e.target.closest(".note") || e.target.closest(".composer")) return;
    var r = this.getBoundingClientRect();
    openComposer(e.clientX - r.left + this.scrollLeft, e.clientY - r.top + this.scrollTop);
  });

  // --- load + boot -------------------------------------------------------
  function reload() {
    return fetch("/board/notes", { headers: { "X-CSRF-Token": CSRF } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.notes = data.notes || [];
        state.piles = data.piles || [];
        buildChips(); buildBoard();
      });
  }

  reload().catch(function () { hint.textContent = "Could not load your notes."; hint.hidden = false; });

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
  }, 30000);

  // expose a couple of internals for the piles module (Task 4) to extend.
  window.__board = { get muuri() { return muuri; }, state: state, api: api, toast: toast,
    savePositions: savePositions, reload: reload };
})();
