/* Aldi Boodschappen - zijbalkpaneel voor Home Assistant (vanilla web component) */

const T = {
  title: "Boodschappen",
  searchPlaceholder: "Zoek een product, bijv. kwark…",
  searching: "Zoeken…",
  noResults: "Geen producten gevonden.",
  sort: "Sorteren",
  sortRelevance: "Meest relevant",
  sortPrice: "Laagste prijs",
  sortUnit: "Laagste prijs per kg / liter / stuk",
  cheapest: "Goedkoopst",
  perStore: "Per winkel",
  addPlain: "Voeg toe als losse tekst",
  add: "Toevoegen",
  everyWeek: "Elke week",
  listSort: "Lijst sorteren",
  listSortAdded: "Volgorde van toevoegen",
  listSortStore: "Per winkel",
  other: "Overig",
  list: "Mijn lijst",
  empty: "Je lijst is leeg. Zoek hierboven een product.",
  newWeek: "Nieuwe week starten",
  total: "Totaal te halen",
  alreadyBought: "al gehaald",
  noPrice: "zonder prijs",
  stale: "prijs mogelijk verouderd",
  refresh: "Prijzen bijwerken",
  refreshing: "Bezig…",
  newWeekConfirm:
    "Afgevinkte eenmalige producten worden verwijderd en terugkerende producten worden weer op 'te halen' gezet. Doorgaan?",
  remove: "Verwijderen",
  error: "Zoeken mislukt",
  toBuy: "Te halen",
  done: "Gehaald",
  recurring: "Terugkerend",
};

const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const money = (v, cur = "€") =>
  v === null || v === undefined ? "" : `${cur} ${Number(v).toFixed(2).replace(".", ",")}`;

/* Producten die vóór de winkelkeuze zijn toegevoegd hebben geen winkelnaam: dat waren altijd Aldi-producten. */
const storeOf = (item) => item.store_name || "Aldi";

/* Winkel waaronder een item in de lijst valt; eigen tekst zonder product staat onder "Overig". */
function groupKey(item) {
  return item.store_name || (item.code ? "Aldi" : T.other);
}

/* Verdeelt de lijst in groepen. Afgevinkte items staan steeds onderaan hun groep. Pure functie. */
function groupList(items, mode) {
  const open = (list) => [...list.filter((i) => !i.checked), ...list.filter((i) => i.checked)];
  if (mode !== "store") return [{ title: null, items: open(items) }];
  const groups = new Map();
  for (const i of items) {
    const k = groupKey(i);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(i);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a === T.other) - (b === T.other) || a.localeCompare(b, "nl"))
    .map(([title, list]) => ({ title, items: open(list) }));
}

/* Link van een item; zonder eigen link een zoekpagina van de winkel, zodat er altijd iets te klikken is. */
function itemUrl(it) {
  if (it.url) return it.url;
  const q = encodeURIComponent(it.name || "");
  if (!q) return null;
  if (it.store === "hoogvliet") return `https://hoogvliet.nl/search/${q}`;
  if (it.store === "aldi") return `https://www.aldi.nl/zoeken.html?query=${q}`;
  return null;
}

/* Totalen van de lijst. Pure functie, zodat hij los getest kan worden. */
function computeTotals(items, nowSec) {
  const todo = items.filter((i) => !i.checked);
  const done = items.filter((i) => i.checked);
  const line = (i) => (i.price != null ? i.price * i.quantity : 0);
  const sum = (list) => list.reduce((s, i) => s + line(i), 0);
  const byStore = {};
  for (const i of todo) {
    if (i.price == null) continue;
    byStore[storeOf(i)] = (byStore[storeOf(i)] || 0) + line(i);
  }
  return {
    todoSum: sum(todo),
    doneSum: sum(done),
    noPrice: todo.filter((i) => i.price == null).length,
    stale: todo.filter((i) => i.price != null && i.price_valid_until && i.price_valid_until < nowSec).length,
    byStore,
  };
}

/* Sorteert zoekresultaten zonder de bronlijst aan te passen. */
function sortResults(results, mode) {
  const list = [...results];
  const rank = { "per kg": 0, "per liter": 1, "per stuk": 2 };
  const byNumber = (get) => (a, b) => {
    const x = get(a), y = get(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return x - y;
  };
  if (mode === "price") return list.sort(byNumber((p) => p.price));
  if (mode === "unit")
    return list.sort((a, b) => {
      const ra = rank[a.unit_label] ?? 9, rb = rank[b.unit_label] ?? 9;
      return ra !== rb ? ra - rb : byNumber((p) => p.unit_price)(a, b);
    });
  return list; // relevantie: de volgorde van de server
}

class AldiBoodschappenPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._items = [];
    this._results = [];
    this._shown = [];
    this._warnings = [];
    this._sort = "relevance";
    this._listSort = (() => { try { return localStorage.getItem("aldi_list_sort") === "store" ? "store" : "added"; } catch (e) { return "added"; } })();
    this._query = "";
    this._status = ""; // "", "loading", "error:..."
    this._unsub = null;
    this._timer = null;
    this._searchSeq = 0;
    this._rendered = false;
    this.attachShadow({ mode: "open" });
    // Een foto die niet laadt (bijv. door hotlink-beveiliging) wordt het winkelwagen-icoon, geen kapot plaatje
    this.shadowRoot.addEventListener(
      "error",
      (e) => {
        if (e.target && e.target.tagName === "IMG") {
          const icon = document.createElement("span");
          icon.textContent = "🛒";
          e.target.replaceWith(icon);
        }
      },
      true
    );
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._connect();
  }

  set narrow(v) { this._narrow = v; }
  set panel(v) { this._panel = v; }
  set route(v) { this._route = v; }

  connectedCallback() {
    this._render();
    if (this._hass && !this._unsub) this._connect();
  }

  disconnectedCallback() {
    if (this._unsub) {
      this._unsub.then?.((u) => u());
      this._unsub = null;
    }
  }

  async _connect() {
    if (this._unsub) return;
    this._unsub = this._hass.connection.subscribeMessage(
      (msg) => {
        this._items = msg.items || [];
        this._renderList();
      },
      { type: "aldi_boodschappen/subscribe" }
    );
  }

  _ws(msg) {
    return this._hass.callWS(msg);
  }

  /* ---------- rendering ---------- */

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; height:100%; overflow:auto; background:var(--primary-background-color); color:var(--primary-text-color); font-family:var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
        header { display:flex; align-items:center; gap:12px; padding:12px 16px; background:var(--app-header-background-color, var(--primary-color)); color:var(--app-header-text-color, #fff); position:sticky; top:0; z-index:2; }
        header h1 { font-size:20px; font-weight:400; margin:0; flex:1; }
        header button { background:transparent; border:1px solid currentColor; color:inherit; border-radius:18px; padding:6px 14px; cursor:pointer; font-size:13px; }
        main { max-width:1100px; margin:0 auto; padding:16px; }
        .search { display:flex; gap:8px; }
        .search input { flex:1; font-size:16px; padding:12px 14px; border-radius:10px; border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); outline:none; }
        .search input:focus { border-color:var(--primary-color); }
        .hint { margin:12px 2px; color:var(--secondary-text-color); font-size:14px; }
        .hint button { background:none; border:none; color:var(--primary-color); cursor:pointer; padding:0; font-size:14px; text-decoration:underline; }
        .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(170px,1fr)); gap:12px; margin:12px 0 24px; }
        .card { background:var(--card-background-color); border-radius:12px; box-shadow:var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.2)); display:flex; flex-direction:column; overflow:hidden; }
        .card .img { height:90px; background:var(--secondary-background-color) center/contain no-repeat; display:flex; align-items:center; justify-content:center; font-size:34px; }
        .card .img img { max-width:100%; max-height:100%; object-fit:contain; background:#fff; }
        .card .body { padding:10px; display:flex; flex-direction:column; gap:4px; flex:1; }
        .card .name { font-size:14px; font-weight:500; line-height:1.25; }
        .card .meta { font-size:12px; color:var(--secondary-text-color); }
        .card .price { font-weight:600; }
        .card .old { text-decoration:line-through; color:var(--secondary-text-color); font-size:12px; margin-left:6px; }
        .card .actions { margin-top:auto; display:flex; flex-direction:column; gap:6px; padding-top:6px; }
        label.chk { display:flex; align-items:center; gap:6px; font-size:13px; cursor:pointer; }
        .btn { background:var(--primary-color); color:var(--text-primary-color,#fff); border:none; border-radius:8px; padding:8px; cursor:pointer; font-size:14px; }
        h2 { font-size:16px; font-weight:500; margin:24px 0 8px; }
        .list { background:var(--card-background-color); border-radius:12px; overflow:hidden; box-shadow:var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.2)); }
        .row { display:flex; align-items:center; gap:10px; padding:10px 12px; border-bottom:1px solid var(--divider-color); }
        .row:last-child { border-bottom:none; }
        .row img { width:44px; height:44px; object-fit:contain; background:#fff; border-radius:6px; }
        .row .ph { width:44px; height:44px; display:flex; align-items:center; justify-content:center; border-radius:6px; background:var(--secondary-background-color); font-size:20px; }
        .row .info { flex:1; min-width:0; }
        .row .nm { font-size:15px; overflow:hidden; text-overflow:ellipsis; }
        .row.done .nm { text-decoration:line-through; color:var(--secondary-text-color); }
        .row .sub { font-size:12px; color:var(--secondary-text-color); }
        .row input[type=checkbox] { width:20px; height:20px; cursor:pointer; }
        .qty { display:flex; align-items:center; gap:4px; }
        .qty button, .icon { background:var(--secondary-background-color); color:var(--primary-text-color); border:none; border-radius:50%; width:28px; height:28px; cursor:pointer; font-size:16px; line-height:1; }
        .tag { cursor:pointer; user-select:none; font-size:12px; padding:4px 8px; border-radius:12px; border:1px solid var(--divider-color); color:var(--secondary-text-color); white-space:nowrap; }
        .tag.on { background:var(--primary-color); border-color:var(--primary-color); color:var(--text-primary-color,#fff); }
        .total { margin-top:12px; padding:14px 16px; border-radius:12px; background:var(--card-background-color); box-shadow:var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.2)); display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
        .total[hidden] { display:none; }
        .total .sum { font-size:22px; font-weight:600; }
        .total .lbl { color:var(--secondary-text-color); font-size:14px; }
        .total .note { flex-basis:100%; color:var(--secondary-text-color); font-size:12px; }
        .total .note.warn { color:var(--warning-color, #b36b00); }
        .total button { margin-left:auto; background:var(--secondary-background-color); color:var(--primary-text-color); border:none; border-radius:18px; padding:6px 14px; cursor:pointer; font-size:13px; }
        .row .old-price { color:var(--warning-color, #b36b00); font-size:11px; }
        .empty { padding:24px; text-align:center; color:var(--secondary-text-color); }
        .sortbar { display:flex; align-items:center; gap:8px; margin:8px 0 0; font-size:13px; color:var(--secondary-text-color); }
        .grouphd { display:flex; justify-content:space-between; margin:14px 0 4px; padding-bottom:4px; border-bottom:1px solid var(--divider-color); font-weight:600; font-size:14px; }
        .grouphd span:last-child { font-weight:400; color:var(--secondary-text-color); }
        .sortbar[hidden] { display:none; }
        .sortbar select { font-size:13px; padding:6px 8px; border-radius:8px; border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); }
        .badges { display:flex; flex-wrap:wrap; gap:4px; }
        .badge { font-size:11px; padding:2px 7px; border-radius:10px; background:var(--secondary-background-color); color:var(--secondary-text-color); }
        .badge.cheap { background:var(--success-color, #2e7d32); color:#fff; }
        .card.cheap { outline:2px solid var(--success-color, #2e7d32); }
        .warnings { margin-top:4px; color:var(--warning-color, #b36b00); font-size:13px; }
        a { color:inherit; }
      </style>
      <header>
        <h1>${T.title}</h1>
        <button id="newweek">${T.newWeek}</button>
      </header>
      <main>
        <div class="search"><input id="q" type="search" autocomplete="off" placeholder="${escapeHtml(T.searchPlaceholder)}" /></div>
        <div id="status" class="hint"></div>
        <div id="sortbar" class="sortbar" hidden>
          <label for="sort">${T.sort}</label>
          <select id="sort">
            <option value="relevance">${T.sortRelevance}</option>
            <option value="price">${T.sortPrice}</option>
            <option value="unit">${T.sortUnit}</option>
          </select>
        </div>
        <div id="results" class="grid"></div>
        <h2>${T.list}</h2>
        <div class="sortbar">
          <label for="lsort">${T.listSort}</label>
          <select id="lsort">
            <option value="added">${T.listSortAdded}</option>
            <option value="store">${T.listSortStore}</option>
          </select>
        </div>
        <div id="list" class="list"></div>
        <div id="total" class="total" hidden></div>
      </main>`;

    const input = this.shadowRoot.getElementById("q");
    input.value = this._query;
    input.addEventListener("input", (e) => {
      this._query = e.target.value;
      clearTimeout(this._timer);
      this._timer = setTimeout(() => this._search(), 350);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        clearTimeout(this._timer);
        this._search();
      }
    });
    const sortSelect = this.shadowRoot.getElementById("sort");
    sortSelect.value = this._sort;
    sortSelect.addEventListener("change", (e) => {
      this._sort = e.target.value;
      this._renderResults();
    });
    const listSort = this.shadowRoot.getElementById("lsort");
    listSort.value = this._listSort;
    listSort.addEventListener("change", (e) => {
      this._listSort = e.target.value;
      try { localStorage.setItem("aldi_list_sort", this._listSort); } catch (err) { /* geen opslag beschikbaar */ }
      this._renderList();
    });
    this.shadowRoot.getElementById("newweek").addEventListener("click", async () => {
      if (confirm(T.newWeekConfirm)) await this._ws({ type: "aldi_boodschappen/new_week" });
    });

    // Event delegation
    this.shadowRoot.getElementById("results").addEventListener("click", (e) => this._onResultClick(e));
    this.shadowRoot.getElementById("status").addEventListener("click", (e) => {
      if (e.target.id === "plain") this._addPlain();
    });
    this.shadowRoot.getElementById("list").addEventListener("click", (e) => this._onListClick(e));
    this.shadowRoot.getElementById("total").addEventListener("click", (e) => this._onTotalClick(e));
    this.shadowRoot.getElementById("list").addEventListener("change", (e) => this._onListChange(e));

    this._rendered = true;
    this._renderStatus();
    this._renderResults();
    this._renderList();
  }

  _renderStatus() {
    const el = this.shadowRoot.getElementById("status");
    if (!el) return;
    if (this._status === "loading") el.textContent = T.searching;
    else if (this._status.startsWith("error:"))
      el.innerHTML = `${escapeHtml(T.error)}: ${escapeHtml(this._status.slice(6))}. <button id="plain">${T.addPlain} "${escapeHtml(this._query.trim())}"</button>`;
    else if (this._query.trim().length >= 2 && !this._results.length)
      el.innerHTML = `${T.noResults} <button id="plain">${T.addPlain} "${escapeHtml(this._query.trim())}"</button>`;
    else if (this._query.trim().length >= 2)
      el.innerHTML = `<button id="plain">${T.addPlain} "${escapeHtml(this._query.trim())}"</button>`;
    else el.textContent = "";
    // Een winkel die niet reageert is geen reden om de andere resultaten te verbergen
    if (this._warnings.length && this._status !== "loading")
      el.insertAdjacentHTML(
        "beforeend",
        `<div class="warnings">⚠ ${this._warnings.map(escapeHtml).join("<br>⚠ ")}</div>`
      );
  }

  _renderResults() {
    const el = this.shadowRoot.getElementById("results");
    if (!el) return;
    this._shown = sortResults(this._results, this._sort);
    const bar = this.shadowRoot.getElementById("sortbar");
    if (bar) bar.hidden = this._results.length < 2;
    el.innerHTML = this._shown
      .map(
        (p, i) => `
      <div class="card${p.cheapest ? " cheap" : ""}">
        <div class="img">${p.image ? `<img src="${escapeHtml(p.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : "🛒"}</div>
        <div class="body">
          <div class="badges">
            ${p.store_name ? `<span class="badge">${escapeHtml(p.store_name)}</span>` : ""}
            ${p.cheapest ? `<span class="badge cheap">${T.cheapest} ${escapeHtml(p.unit_label || "")}</span>` : ""}
          </div>
          <div class="name">${escapeHtml(p.name)}</div>
          ${p.available === false ? `<div class="meta">Nu niet beschikbaar</div>` : ""}
          ${p.brand ? `<div class="meta">${escapeHtml(p.brand)}</div>` : ""}
          ${p.description ? `<div class="meta">${escapeHtml(p.description)}</div>` : ""}
          <div><span class="price">${money(p.price, p.currency)}</span>${p.old_price ? `<span class="old">${money(p.old_price, p.currency)}</span>` : ""}</div>
          ${p.unit_price != null ? `<div class="meta">${money(p.unit_price, p.currency)} ${escapeHtml(p.unit_label || "")}</div>` : ""}
          ${p.base_price ? `<div class="meta">${escapeHtml(p.base_price)}</div>` : ""}
          <div class="actions">
            <label class="chk"><input type="checkbox" data-rec="${i}"> 🔁 ${T.everyWeek}</label>
            <button class="btn" data-add="${i}">${T.add}</button>
          </div>
        </div>
      </div>`
      )
      .join("");
  }

  _renderTotal() {
    const el = this.shadowRoot?.getElementById("total");
    if (!el) return;
    const { todoSum, doneSum: boughtSum, noPrice, stale, byStore } = computeTotals(
      this._items,
      Date.now() / 1000
    );
    if (!this._items.length) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    const stores = Object.entries(byStore);
    el.innerHTML = `
      <span class="lbl">${T.total}</span>
      <span class="sum">${money(todoSum)}</span>
      ${boughtSum > 0 ? `<span class="lbl">(+ ${money(boughtSum)} ${T.alreadyBought})</span>` : ""}
      <button id="refresh">${T.refresh}</button>
      ${stores.length > 1 ? `<span class="note">${T.perStore}: ${stores.map(([n, v]) => `${escapeHtml(n)} ${money(v)}`).join(" · ")}</span>` : ""}
      ${noPrice ? `<span class="note">${noPrice} ${noPrice === 1 ? "product" : "producten"} ${T.noPrice}, niet meegeteld</span>` : ""}
      ${stale ? `<span class="note warn">${stale} ${stale === 1 ? "prijs" : "prijzen"} mogelijk verouderd, gebruik "${T.refresh}"</span>` : ""}`;
  }

  _renderList() {
    const el = this.shadowRoot?.getElementById("list");
    if (!el) return;
    this._renderTotal();
    if (!this._items.length) {
      el.innerHTML = `<div class="empty">${T.empty}</div>`;
      return;
    }
    const money_ = (list) => list.reduce((s, i) => s + (!i.checked && i.price != null ? i.price * i.quantity : 0), 0);
    const row = (it) => {
        const thumb = it.image
          ? `<img src="${escapeHtml(it.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
          : `<div class="ph">🛒</div>`;
        const sub = [it.store_name, it.description, it.price != null ? money(it.price * it.quantity, it.currency) : ""]
          .filter(Boolean)
          .join(" · ");
        const href = itemUrl(it);
        const nameHtml = href
          ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(it.name)}</a>`
          : escapeHtml(it.name);
        return `
        <div class="row ${it.checked ? "done" : ""}">
          <input type="checkbox" data-check="${it.id}" ${it.checked ? "checked" : ""} title="${it.checked ? T.done : T.toBuy}">
          ${thumb}
          <div class="info"><div class="nm">${nameHtml}</div>${sub ? `<div class="sub">${escapeHtml(sub)}</div>` : ""}${it.price != null && it.price_valid_until && it.price_valid_until < Date.now() / 1000 ? `<div class="old-price">⚠ ${T.stale}</div>` : ""}</div>
          <div class="qty">
            <button data-dec="${it.id}">−</button><span>${it.quantity}</span><button data-inc="${it.id}">+</button>
          </div>
          <span class="tag ${it.recurring ? "on" : ""}" data-rec-toggle="${it.id}" title="${T.everyWeek}" aria-label="${T.everyWeek}" role="button">🔁</span>
          <button class="icon" data-del="${it.id}" title="${T.remove}">✕</button>
        </div>`;
    };
    el.innerHTML = groupList(this._items, this._listSort)
      .map((g) => {
        const sum = money_(g.items);
        const head = g.title
          ? `<div class="grouphd"><span>${escapeHtml(g.title)}</span><span>${sum ? money(sum) : ""}</span></div>`
          : "";
        return head + g.items.map(row).join("");
      })
      .join("");
  }

  /* ---------- acties ---------- */

  async _search() {
    const q = this._query.trim();
    const seq = ++this._searchSeq;
    if (q.length < 2) {
      this._results = [];
      this._warnings = [];
      this._status = "";
      this._renderStatus();
      this._renderResults();
      return;
    }
    this._status = "loading";
    this._renderStatus();
    try {
      const res = await this._ws({ type: "aldi_boodschappen/search", query: q });
      if (seq !== this._searchSeq) return; // verouderd antwoord
      this._results = res.results || [];
      this._warnings = res.warnings || [];
      this._status = "";
    } catch (err) {
      if (seq !== this._searchSeq) return;
      this._results = [];
      this._warnings = [];
      this._status = `error:${err.message || err}`;
    }
    this._renderStatus();
    this._renderResults();
  }

  async _onResultClick(e) {
    const btn = e.target.closest("[data-add]");
    if (!btn) return;
    const idx = Number(btn.dataset.add);
    const product = this._shown[idx]; // de volgorde die op het scherm staat (kan gesorteerd zijn)
    const rec = this.shadowRoot.querySelector(`[data-rec="${idx}"]`)?.checked ?? false;
    await this._ws({
      type: "aldi_boodschappen/add",
      name: product.name,
      product,
      quantity: 1,
      recurring: rec,
    });
    btn.textContent = "✓";
    setTimeout(() => (btn.textContent = T.add), 900);
  }

  async _addPlain() {
    const name = this._query.trim();
    if (!name) return;
    await this._ws({ type: "aldi_boodschappen/add", name, quantity: 1, recurring: false });
    this._query = "";
    this._results = [];
    this._status = "";
    this.shadowRoot.getElementById("q").value = "";
    this._renderStatus();
    this._renderResults();
  }

  _item(id) {
    return this._items.find((i) => i.id === id);
  }

  async _onListClick(e) {
    const t = e.target;
    if (t.dataset.del) {
      await this._ws({ type: "aldi_boodschappen/remove", item_id: t.dataset.del });
    } else if (t.dataset.inc || t.dataset.dec) {
      const id = t.dataset.inc || t.dataset.dec;
      const it = this._item(id);
      if (!it) return;
      const quantity = Math.max(1, it.quantity + (t.dataset.inc ? 1 : -1));
      await this._ws({ type: "aldi_boodschappen/update", item_id: id, quantity });
    } else if (t.dataset.recToggle) {
      const it = this._item(t.dataset.recToggle);
      if (!it) return;
      await this._ws({ type: "aldi_boodschappen/update", item_id: it.id, recurring: !it.recurring });
    }
  }

  async _onTotalClick(e) {
    const btn = e.target.closest("#refresh");
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = T.refreshing;
    try {
      await this._ws({ type: "aldi_boodschappen/refresh_prices" });
    } catch (err) {
      /* de lijst blijft zoals hij was */
    }
    this._renderTotal();
  }

  async _onListChange(e) {
    const t = e.target;
    if (t.dataset.check) {
      await this._ws({ type: "aldi_boodschappen/update", item_id: t.dataset.check, checked: t.checked });
    }
  }
}

customElements.define("aldi-boodschappen-panel", AldiBoodschappenPanel);
