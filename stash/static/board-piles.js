/* stash board: stacking piles.
 *
 * Drop a note onto another to stack them into a pile; a pile shows an offset
 * stack with a count badge and an editable label, can be collapsed to just the
 * top card, and a note dragged out of a pile leaves it (the pile dissolves below
 * two members). Pile membership, labels, and collapsed state persist via
 * POST /board/piles and round-trip on reload.
 *
 * Piles are a grouping layer over Muuri: Muuri still lays out one item per note,
 * and this module tags items with their pile, draws the badge/label on the top
 * card, hides collapsed members, and reconciles piles on each drag release by
 * asking which item the dragged one was dropped over.
 */
(function () {
  "use strict";

  // Tell board.js that this module owns the drag-release handler, set
  // synchronously at load so it is true before board.js's async first build.
  window.__boardPilesActive = true;

  function board() { return window.__board; }

  // pile model lives here; seeded from the server payload on each reload.
  // piles: [{ cid, label, collapsed, members:[noteId,...] }]
  var piles = [];
  var nextCid = 1;

  function noteId(item) { return +item.getElement().dataset.id; }
  function pileOf(id) { return piles.find(function (p) { return p.members.indexOf(id) !== -1; }); }

  function seedFromState() {
    var b = board(); if (!b) return;
    piles = [];
    nextCid = 1;
    // Server gives each note a pile_id (db id) and a piles list with labels.
    var byDbId = {};
    (b.state.piles || []).forEach(function (p) {
      var cid = nextCid++;
      byDbId[p.id] = { cid: cid, label: p.label || "", collapsed: !!p.collapsed, members: [] };
      piles.push(byDbId[p.id]);
    });
    b.state.notes.forEach(function (n) {
      if (n.pile_id != null && byDbId[n.pile_id]) byDbId[n.pile_id].members.push(n.id);
    });
    // Drop any pile that ended up with fewer than two members.
    piles = piles.filter(function (p) { return p.members.length >= 2; });
  }

  function labelFor(members) {
    var b = board();
    var notes = members.map(function (id) { return b.state.notes.find(function (n) { return n.id === id; }); }).filter(Boolean);
    // Prefer a tag shared by the whole pile, then the first note's first words.
    var counts = {};
    notes.forEach(function (n) { (n.tags || []).forEach(function (t) { counts[t] = (counts[t] || 0) + 1; }); });
    var shared = Object.keys(counts).filter(function (t) { return counts[t] === notes.length; });
    if (shared.length) return "#" + shared[0];
    var first = (notes[0] && notes[0].raw) || "pile";
    return first.split(/\s+/).slice(0, 3).join(" ");
  }

  function save() {
    var b = board(); if (!b) return;
    var payload = { piles: [], assignments: {} };
    piles.forEach(function (p) {
      payload.piles.push({ client_id: String(p.cid), label: p.label, collapsed: p.collapsed });
      p.members.forEach(function (id) { payload.assignments[id] = String(p.cid); });
    });
    b.api("/board/piles", payload).catch(function () { b.toast("Could not save piles"); });
  }

  // --- rendering ---------------------------------------------------------
  function clearDecor() {
    document.querySelectorAll(".pile-badge, .pile-label").forEach(function (el) { el.remove(); });
    document.querySelectorAll(".item.pile").forEach(function (el) { el.classList.remove("pile"); });
  }

  function render() {
    var b = board(); if (!b || !b.muuri) return;
    clearDecor();
    var items = b.muuri.getItems();
    var itemById = {};
    items.forEach(function (it) { itemById[noteId(it)] = it; });

    piles.forEach(function (p) {
      var members = p.members.filter(function (id) { return itemById[id]; });
      if (members.length < 2) return;
      var topId = members[members.length - 1];
      members.forEach(function (id) {
        var el = itemById[id].getElement();
        if (id === topId) {
          el.classList.add("pile");
          var note = el.querySelector(".note");
          var badge = document.createElement("span");
          badge.className = "pile-badge"; badge.textContent = members.length;
          note.appendChild(badge);
          var label = document.createElement("input");
          label.className = "pile-label"; label.value = p.label;
          label.title = "Pile label";
          label.addEventListener("click", function (e) { e.stopPropagation(); });
          label.addEventListener("change", function () { p.label = label.value; save(); });
          note.appendChild(label);
        }
      });
    });
    applyCollapse();
  }

  function applyCollapse() {
    var b = board(); if (!b || !b.muuri) return;
    var itemById = {};
    b.muuri.getItems().forEach(function (it) { itemById[noteId(it)] = it; });
    var hide = [], show = [];
    piles.forEach(function (p) {
      if (!p.collapsed) return;
      var members = p.members.filter(function (id) { return itemById[id]; });
      var topId = members[members.length - 1];
      members.forEach(function (id) { if (id !== topId) hide.push(itemById[id]); });
    });
    if (hide.length) b.muuri.hide(hide);
  }

  // --- drop-to-stack: hook Muuri drag events -----------------------------
  function overlappingItem(draggedItem) {
    var b = board();
    var dEl = draggedItem.getElement();
    var r = dEl.getBoundingClientRect();
    var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    var found = null;
    b.muuri.getItems().forEach(function (it) {
      if (it === draggedItem) return;
      if (!it.isVisible()) return;
      var rr = it.getElement().getBoundingClientRect();
      if (cx >= rr.left && cx <= rr.right && cy >= rr.top && cy <= rr.bottom) found = it;
    });
    return found;
  }

  function stack(aId, bId) {
    // Put aId into the pile of bId, or create a new pile from both.
    var pa = pileOf(aId), pb = pileOf(bId);
    if (pa) pa.members = pa.members.filter(function (x) { return x !== aId; });
    if (pb) {
      if (pb.members.indexOf(aId) === -1) pb.members.push(aId);
    } else {
      piles.push({ cid: nextCid++, label: labelFor([bId, aId]), collapsed: false, members: [bId, aId] });
    }
    cleanup(); save(); render();
  }

  function unstackIfDroppedOutside(item) {
    var id = noteId(item);
    var p = pileOf(id);
    if (!p) return false;
    // Dropped not over any pile member -> leave the pile.
    var over = overlappingItem(item);
    var overInSamePile = over && p.members.indexOf(noteId(over)) !== -1;
    if (!overInSamePile) {
      p.members = p.members.filter(function (x) { return x !== id; });
      cleanup(); save(); render();
      return true;
    }
    return false;
  }

  function cleanup() {
    // Dissolve piles that fell below two members.
    piles = piles.filter(function (p) { return p.members.length >= 2; });
  }

  function onReleaseEnd(item) {
    // One handler per release: resolve stacking first, then persist positions.
    // This replaces board.js's own save subscription (see __boardPilesActive),
    // so positions and piles are never two racing writes.
    if (!unstackIfDroppedOutside(item)) {
      var over = overlappingItem(item);
      if (over) stack(noteId(item), noteId(over));
      else render();
    }
    var b = board();
    if (b && b.savePositions) b.savePositions();
  }

  // Collapsed piles toggle open on a click of the top card (not a drag).
  // Exclude the action controls on the card: the delete button, the pile label,
  // and the clickable tag/category chips (which filter). This runs in the
  // capture phase on #grid, so the chip's own bubble-phase stopPropagation can't
  // cancel it; the guard here is what keeps a chip click from also toggling the
  // pile.
  function onTopClick(e) {
    var itemEl = e.target.closest(".item.pile");
    if (!itemEl || e.target.closest(".del") || e.target.closest(".pile-label")
        || e.target.closest(".chipbtn")) return;
    var id = +itemEl.dataset.id;
    var p = pileOf(id); if (!p) return;
    p.collapsed = !p.collapsed; save();
    var b = board();
    if (!p.collapsed) {
      // reveal members honoring the active filter
      b.muuri.show(p.members.map(function (mid) {
        return b.muuri.getItems().find(function (it) { return noteId(it) === mid; });
      }).filter(Boolean));
    }
    render();
  }

  // --- wire into the board -----------------------------------------------
  // board.js calls window.__onBoardBuilt after every (re)build, handing us the
  // fresh Muuri instance. We bind our single release handler to it and redraw
  // the piles. seedFromState re-reads pile membership from the latest server
  // payload (set by reload) so piles survive adds, filters and reorders.
  var clickWired = false;
  var boundInstance = null;
  window.__onBoardBuilt = function () {
    var b = board();
    if (!b || !b.muuri) return;
    seedFromState();
    if (boundInstance !== b.muuri) {   // bind once per Muuri instance
      b.muuri.on("dragReleaseEnd", onReleaseEnd);
      boundInstance = b.muuri;
    }
    if (!clickWired) {
      document.getElementById("grid").addEventListener("click", onTopClick, true);
      clickWired = true;
    }
    render();
  };

  // If the board was already built before this module loaded, catch up once.
  function catchUp() {
    var b = board();
    if (b && b.muuri) { window.__onBoardBuilt(); return; }
    setTimeout(catchUp, 60);
  }
  catchUp();
})();
