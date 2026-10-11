// Test van de rekenlogica in het paneel (draaien met: node tests/test_panel.js)
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const file = path.join(__dirname, "..", "custom_components", "aldi_boodschappen", "frontend", "aldi-boodschappen-panel.js");
const ctx = { HTMLElement: class {}, customElements: { define() {} }, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(file, "utf8"), ctx);
const { computeTotals, sortResults, itemUrl, groupList } = ctx;

// ---- totalen
const NOW = 1_000_000;
const items = [
  { id: "1", price: 2.99, quantity: 3, checked: false, store_name: "Aldi" },
  { id: "2", price: 2.49, quantity: 1, checked: false, store_name: "Hoogvliet" },
  { id: "3", price: 1.0, quantity: 2, checked: true, store_name: "Aldi" }, // al gehaald
  { id: "4", price: null, quantity: 1, checked: false }, // losse tekst, geen prijs
  { id: "5", price: 4.0, quantity: 1, checked: false, price_valid_until: NOW - 1 }, // oude Aldi-regel zonder winkelnaam, verlopen
];
const t = computeTotals(items, NOW);
assert.strictEqual(Math.round(t.todoSum * 100) / 100, 2.99 * 3 + 2.49 + 4.0);
assert.strictEqual(t.doneSum, 2.0);
assert.strictEqual(t.noPrice, 1);
assert.strictEqual(t.stale, 1);
assert.strictEqual(Object.keys(t.byStore).sort().join(), "Aldi,Hoogvliet", "item zonder winkelnaam telt als Aldi");
assert.strictEqual(Math.round(t.byStore.Aldi * 100) / 100, Math.round((2.99 * 3 + 4.0) * 100) / 100);
assert.strictEqual(t.byStore.Hoogvliet, 2.49);
// (objecten uit de vm hebben een ander prototype, dus vergelijken we als JSON)
assert.strictEqual(JSON.stringify(computeTotals([], NOW)), JSON.stringify({ todoSum: 0, doneSum: 0, noPrice: 0, stale: 0, byStore: {} }));

// ---- sorteren
const r = [
  { n: "a", price: 3, unit_price: 0.5, unit_label: "per stuk" },
  { n: "b", price: 1, unit_price: 4, unit_label: "per kg" },
  { n: "c", price: null, unit_price: null, unit_label: null },
  { n: "d", price: 2, unit_price: 2, unit_label: "per kg" },
  { n: "e", price: 5, unit_price: 1, unit_label: "per liter" },
];
const names = (l) => l.map((x) => x.n).join("");
assert.strictEqual(names(sortResults(r, "relevance")), "abcde");
assert.strictEqual(names(sortResults(r, "price")), "bdaec", "laagste prijs eerst, zonder prijs onderaan");
assert.strictEqual(names(sortResults(r, "unit")), "dbeac", "per kg, dan per liter, dan per stuk; zonder eenheid onderaan");
assert.strictEqual(names(r), "abcde", "de bronlijst blijft ongewijzigd");

console.log("ok: totalen en sorteren kloppen");

assert.strictEqual(itemUrl({ url: "https://x/y", store: "aldi", name: "a" }), "https://x/y");
assert.strictEqual(itemUrl({ store: "hoogvliet", name: "pink lady" }), "https://hoogvliet.nl/search/pink%20lady");
assert.strictEqual(itemUrl({ store: "aldi", name: "melk" }), "https://www.aldi.nl/zoeken.html?query=melk");
assert.strictEqual(itemUrl({ name: "" }), null);
console.log("itemUrl ok");

// lijst per winkel: groepen op naam, "Overig" laatst, afgevinkt onderaan elke groep
const li = [
  { id: "1", name: "a", store_name: "Hoogvliet", code: "h:1", checked: true },
  { id: "2", name: "b", store_name: "Aldi", code: "a:1", checked: false },
  { id: "3", name: "c", store_name: "Hoogvliet", code: "h:2", checked: false },
  { id: "4", name: "d", checked: false },
  { id: "5", name: "e", code: "oud", checked: false },
];
const flat = groupList(li, "added");
assert.strictEqual(flat.length, 1);
assert.strictEqual(flat[0].title, null);
assert.strictEqual(flat[0].items.map((i) => i.id).join(""), "23451".replace("1", "") + "1", "afgevinkt onderaan");
const grouped = groupList(li, "store");
assert.strictEqual(grouped.map((g) => g.title).join("|"), "Aldi|Hoogvliet|Overig");
assert.strictEqual(grouped[0].items.map((i) => i.id).join(""), "25", "oud item zonder winkelnaam is Aldi");
assert.strictEqual(grouped[1].items.map((i) => i.id).join(""), "31", "afgevinkt onderaan");
assert.strictEqual(grouped[2].items.map((i) => i.id).join(""), "4");
assert.strictEqual(groupList([], "store").length, 0);
console.log("groupList ok");
