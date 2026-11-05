const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function harness({ windows = true } = {}) {
  function element(value = "") {
    return { value, textContent: "", hidden: false, disabled: false, listeners: new Map(),
      addEventListener(name, callback) { this.listeners.set(name, callback); },
      replaceChildren(...children) { this.children = children; },
      closest() { return this; } };
  }
  const ids = ["ip", "lan", "jouer", "arreter", "second", "arreter-second", "etat", "journal", "dossier-jeu", "changer-dossier", "choix-profil", "profil", "nouveau-profil", "choix-mode", "ligne-second"];
  const elements = Object.fromEntries(ids.map((id) => [id, element()]));
  const modes = ["local", "dev", "serveur"].map(element);
  const played = [];
  let onState;
  const original = { key: "development", label: "Original save — Level 30" };
  const creator = { key: "creator-new", label: "New character" };
  const api = {
    choix: async () => ({ mode: "local", modes: ["local"], gameFolder: "D:/Anthem", ip: "127.0.0.1",
      profiles: windows ? [original] : undefined, selectedProfile: "development" }),
    jouer: (...args) => played.push(args),
    nouveauProfil: async () => ({ profiles: [original, creator], selectedProfile: creator.key }),
    dossierJeu: async () => "D:/Anthem", second() {}, arreter() {}, surJournal() {},
    surEtat(callback) { onState = callback; },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "renderer.js"), "utf8"), {
    document: { getElementById: (id) => elements[id], querySelectorAll: () => modes, createElement: () => element() },
    window: { lanceur: api },
  });
  await new Promise(setImmediate);
  return { elements, played, onState, api };
}

test("save selector passes the chosen Original save to Play", async () => {
  const h = await harness();
  assert.equal(h.elements["choix-profil"].hidden, false);
  assert.equal(h.elements.profil.value, "development");
  assert.match(h.elements.profil.children[0].textContent, /Level 30/);
  h.elements.jouer.listeners.get("click")();
  assert.deepEqual(h.played[0], ["local", "127.0.0.1", "development"]);
});

test("New character selects its new slot without starting the game or cloning a name", async () => {
  const h = await harness();
  await h.elements["nouveau-profil"].listeners.get("click")();
  assert.equal(h.elements.profil.value, "creator-new");
  assert.equal(h.played.length, 0);
  h.elements.jouer.listeners.get("click")();
  assert.equal(h.played[0][2], "creator-new");
});

test("save selection is disabled while a game starts or runs and restored after Stop", async () => {
  const h = await harness();
  for (const state of [{ patches: true, game: false }, { patches: false, game: true }]) {
    h.onState(state);
    assert.equal(h.elements.profil.disabled, true);
    assert.equal(h.elements["nouveau-profil"].disabled, true);
  }
  h.onState({ patches: false, game: false });
  assert.equal(h.elements.profil.disabled, false);
  assert.equal(h.elements.jouer.disabled, false);
});

test("Linux launcher keeps its existing flow with the Windows profile selector hidden", async () => {
  const h = await harness({ windows: false });
  assert.equal(h.elements["choix-profil"].hidden, true);
  h.elements.jouer.listeners.get("click")();
  assert.equal(h.played[0][2], undefined);
});
