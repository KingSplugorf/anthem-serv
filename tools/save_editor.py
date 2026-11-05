"""Usage : python3 -I tools/save_editor.py [URL_BIGS] [PORT] : sert sur http://127.0.0.1:PORT (8090 par défaut) une page qui liste les quêtes de data/game/levels/root/rootquestinfocollection.json par famille, avec leur état dans la sauvegarde (GET persistence de BIGS), et permet de les passer en verrouillée, disponible ou terminée, ainsi que de choisir les javelins possédés (Ranger toujours possédé, route /editeur/javelins de BIGS) le niveau du pilote (route /editeur/niveau) et le déblocage de tous les cosmétiques (route /editeur/cosmetiques) ; l'enregistrement envoie les changements à BIGS (PUT persistence), pris en compte par le jeu à la connexion suivante."""

import http.server
import json
import pathlib
import sys
import urllib.request

QUESTS = pathlib.Path(__file__).resolve().parent.parent / "data/game/levels/root/rootquestinfocollection.json"
DEFAULT_BIGS = "http://127.0.0.1:8080"
DEFAULT_PORT = 8090
PERSONA = 1000
QUEST_STATE = 1
STATES = {"verrouillee": 0, "disponible": 5}
COMPLETED_WITH_MISSION, COMPLETED = 11, 3
FAMILIES = {
    "critpath": "Histoire principale", "mts": "Missions secondaires", "brn": "Missions de Brin", "yar": "Missions de Yarrow",
    "subplot": "Épisodes", "cp": "Introduction", "weekly": "Hebdomadaires", "temple": "Forteresses", "heart": "Forteresses",
    "the": "Forteresses", "freeplay": "Jeu libre", "season": "Saison", "nexus": "Nexus",
}


def quest_catalog() -> list[dict]:
    catalog = []
    for item in json.loads(QUESTS.read_text())["objets"]:
        fields = item["champs"]
        name = str(fields.get("QuestName", ""))
        definition = (fields.get("RegisteredPersistence") or {}).get("DefinitionId")
        if item["classe"] != "DylanEcoQuestInfo" or not definition:
            continue
        has_mission = any(phase.get("IsMissionPhase") for phase in fields.get("QuestPhaseInfos") or [])
        family = FAMILIES.get(name.split("_")[0])
        if not has_mission or not family:
            continue
        catalog.append({"name": name, "definition": definition, "family": family, "quest": fields.get("QuestId"),
                        "completed": COMPLETED_WITH_MISSION})
    return sorted(catalog, key=lambda quest: (quest["family"], quest["name"]))


def saved_states(bigs: str) -> dict[int, int]:
    with urllib.request.urlopen(f"{bigs}/api/character/{PERSONA}/persistence?&persistenceType=0") as response:
        elements = json.loads(json.loads(response.read())["persistenceJsonData"])
    return {
        element["DefinitionId"]: prop["Value"]
        for element in elements
        for prop in element.get("RegisteredDefPropArray", [])
        if prop.get("PropertyId") == QUEST_STATE and isinstance(prop.get("Value"), int)
    }


def state_name(value: int | None) -> str:
    if value is None or value == 0:
        return "verrouillee"
    return "terminee" if value & 2 else "disponible"


def bigs_editor(bigs: str, route: str, value: dict | None = None):
    body = None if value is None else json.dumps(value).encode()
    request = urllib.request.Request(f"{bigs}/editeur/{route}", data=body, method="GET" if body is None else "PUT",
                                     headers={"content-type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def save_states(bigs: str, changes: dict[str, str], catalog: dict[int, dict]) -> int:
    elements = []
    for definition, state in changes.items():
        quest = catalog[int(definition)]
        value = quest["completed"] if state == "terminee" else STATES[state]
        elements.append({"DefinitionId": int(definition), "RegisteredDefPropArray": [{"PropertyId": QUEST_STATE, "Value": value}]})
    body = json.dumps({"personaId": PERSONA, "transactionId": 0, "source": "editor", "persistenceJsonProp": json.dumps(elements)}).encode()
    request = urllib.request.Request(f"{bigs}/api/character/{PERSONA}/persistence", data=body, method="PUT",
                                     headers={"content-type": "application/json"})
    with urllib.request.urlopen(request) as response:
        response.read()
    return len(elements)


PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Sauvegarde Anthem</title>
<style>
body{font:14px system-ui,sans-serif;margin:0;padding:16px 24px;background:#14171c;color:#e6e6e6}
h1{font-size:20px;margin:0 0 4px}p.aide{color:#9aa3ad;margin:0 0 16px}
section{margin-bottom:20px;border:1px solid #2a3038;border-radius:6px}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 12px;background:#1c2128}
header h2{font-size:15px;margin:0;flex:1}
table{width:100%;border-collapse:collapse}td{padding:4px 12px;border-top:1px solid #23292f}
td.nom{font-family:monospace}td.choix{white-space:nowrap}
label{margin-right:10px}button{background:#2d6cdf;color:#fff;border:0;border-radius:4px;padding:5px 10px;cursor:pointer}
button.petit{background:#2a3038}#barre{position:sticky;top:0;background:#14171c;padding:8px 0;display:flex;gap:12px;align-items:center}
#etat{color:#9aa3ad}tr.modifie td{background:#2a2410}
</style></head><body>
<h1>Sauvegarde Anthem</h1>
<p class="aide">État des quêtes à phase de mission. Les changements sont pris en compte à la prochaine connexion du jeu.</p>
<div id="barre"><button id="enregistrer">Enregistrer</button><span id="etat"></span></div>
<section><header><h2>Javelins possédés</h2><button class="petit" id="enregistrer-javelins">Enregistrer les javelins</button></header>
<div id="javelins" style="padding:8px 12px"></div></section>
<section><header><h2>Cosmétiques</h2><button class="petit" id="enregistrer-cosmetiques">Enregistrer les cosmétiques</button></header>
<div style="padding:8px 12px"><label><input id="cosmetiques" type="checkbox"> Tout débloquer (<span id="nombre-cosmetiques">0</span> : décalcomanies, matériaux, bannières, armures, emotes, poses, arrivées)</label></div></section>
<section><header><h2>Niveau du pilote</h2><button class="petit" id="enregistrer-niveau">Enregistrer le niveau</button></header>
<div style="padding:8px 12px"><input id="niveau" type="number" min="1" max="30" step="1"></div></section>
<div id="familles"></div>
<script>
const NOMS = {verrouillee: "Verrouillée", disponible: "Disponible", terminee: "Terminée"};
let quetes = [], initial = {};
function ligne(q) {
  const tr = document.createElement("tr");
  tr.dataset.definition = q.definition;
  const choix = Object.keys(NOMS).map(etat =>
    `<label><input type="radio" name="q${q.definition}" value="${etat}" ${q.state === etat ? "checked" : ""}> ${NOMS[etat]}</label>`).join("");
  tr.innerHTML = `<td class="nom">${q.name}</td><td>${q.quest ?? ""}</td><td class="choix">${choix}</td>`;
  tr.addEventListener("change", marquer);
  return tr;
}
function marquer() {
  let changes = 0;
  document.querySelectorAll("tr[data-definition]").forEach(tr => {
    const choisi = tr.querySelector("input:checked").value;
    const modifie = choisi !== initial[tr.dataset.definition];
    tr.classList.toggle("modifie", modifie);
    changes += modifie;
  });
  document.getElementById("etat").textContent = changes ? `${changes} modification(s) non enregistrée(s)` : "";
}
function afficher() {
  const racine = document.getElementById("familles");
  racine.innerHTML = "";
  const familles = [...new Set(quetes.map(q => q.family))];
  for (const famille of familles) {
    const section = document.createElement("section");
    const entete = document.createElement("header");
    entete.innerHTML = `<h2>${famille}</h2>` + Object.keys(NOMS).map(etat => `<button class="petit" data-etat="${etat}">Tout ${NOMS[etat].toLowerCase()}</button>`).join("");
    const table = document.createElement("table");
    quetes.filter(q => q.family === famille).forEach(q => table.appendChild(ligne(q)));
    entete.querySelectorAll("button").forEach(bouton => bouton.addEventListener("click", () => {
      table.querySelectorAll(`input[value="${bouton.dataset.etat}"]`).forEach(radio => radio.checked = true);
      marquer();
    }));
    section.append(entete, table);
    racine.appendChild(section);
  }
}
const NOMS_JAVELINS = {Lancer: "Ranger", Colossus: "Colosse", Storm: "Tempête", Nova: "Intercepteur"};
async function chargerJavelins() {
  const javelins = await (await fetch("/javelins")).json();
  document.getElementById("javelins").innerHTML = javelins.map(j =>
    `<label><input type="checkbox" value="${j.name}" ${j.owned ? "checked" : ""} ${j.name === "Lancer" ? "disabled" : ""}> ${NOMS_JAVELINS[j.name] ?? j.name}</label>`).join("");
}
document.getElementById("enregistrer-javelins").addEventListener("click", async () => {
  const owned = [...document.querySelectorAll("#javelins input:checked:not(:disabled)")].map(c => c.value);
  const reponse = await fetch("/javelins", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(owned)});
  await chargerJavelins();
  document.getElementById("etat").textContent = reponse.ok ? "Javelins enregistrés" : "Erreur à l'enregistrement des javelins";
});
chargerJavelins();
async function chargerNiveau() {
  document.getElementById("niveau").value = (await (await fetch("/niveau")).json()).level;
}
document.getElementById("enregistrer-niveau").addEventListener("click", async () => {
  const level = Number(document.getElementById("niveau").value);
  const reponse = await fetch("/niveau", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({level})});
  await chargerNiveau();
  document.getElementById("etat").textContent = reponse.ok ? "Niveau enregistré" : "Erreur à l'enregistrement du niveau";
});
chargerNiveau();
async function chargerCosmetiques() {
  const cosmetiques = await (await fetch("/cosmetiques")).json();
  document.getElementById("cosmetiques").checked = cosmetiques.unlocked;
  document.getElementById("nombre-cosmetiques").textContent = cosmetiques.count;
}
document.getElementById("enregistrer-cosmetiques").addEventListener("click", async () => {
  const unlocked = document.getElementById("cosmetiques").checked;
  const reponse = await fetch("/cosmetiques", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({unlocked})});
  await chargerCosmetiques();
  document.getElementById("etat").textContent = reponse.ok ? "Cosmétiques enregistrés" : "Erreur à l'enregistrement des cosmétiques";
});
chargerCosmetiques();
async function charger() {
  quetes = await (await fetch("/quetes")).json();
  initial = Object.fromEntries(quetes.map(q => [q.definition, q.state]));
  afficher(); marquer();
}
document.getElementById("enregistrer").addEventListener("click", async () => {
  const changes = {};
  document.querySelectorAll("tr.modifie").forEach(tr => changes[tr.dataset.definition] = tr.querySelector("input:checked").value);
  if (!Object.keys(changes).length) { document.getElementById("etat").textContent = "Rien à enregistrer"; return; }
  const reponse = await fetch("/enregistrer", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(changes)});
  const resultat = await reponse.json();
  await charger();
  document.getElementById("etat").textContent = reponse.ok ? `${resultat.enregistrees} quête(s) enregistrée(s)` : `Erreur : ${resultat.erreur}`;
});
charger();
</script></body></html>
"""


class Editor(http.server.BaseHTTPRequestHandler):
    bigs = DEFAULT_BIGS

    def reply(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, status: int, value) -> None:
        self.reply(status, "application/json; charset=utf-8", json.dumps(value).encode())

    def do_GET(self) -> None:
        if self.path == "/":
            self.reply(200, "text/html; charset=utf-8", PAGE.encode())
        elif self.path in ("/javelins", "/niveau", "/cosmetiques"):
            try:
                self.reply_json(200, bigs_editor(self.bigs, self.path[1:]))
            except OSError as error:
                self.reply_json(502, {"erreur": f"BIGS injoignable : {error}"})
        elif self.path == "/quetes":
            try:
                states = saved_states(self.bigs)
            except OSError as error:
                self.reply_json(502, {"erreur": f"BIGS injoignable : {error}"})
                return
            self.reply_json(200, [{**quest, "state": state_name(states.get(quest["definition"]))} for quest in quest_catalog()])
        else:
            self.reply(404, "text/plain", b"")

    def do_POST(self) -> None:
        if self.path == "/javelins":
            owned = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
            try:
                self.reply_json(200, bigs_editor(self.bigs, "javelins", {"owned": [name for name in owned if isinstance(name, str)]}))
            except OSError as error:
                self.reply_json(502, {"erreur": f"BIGS injoignable : {error}"})
            return
        if self.path == "/cosmetiques":
            unlocked = json.loads(self.rfile.read(int(self.headers.get("content-length", 0)))).get("unlocked")
            if not isinstance(unlocked, bool):
                self.reply_json(400, {"erreur": "booléen attendu"})
                return
            try:
                self.reply_json(200, bigs_editor(self.bigs, "cosmetiques", {"unlocked": unlocked}))
            except OSError as error:
                self.reply_json(502, {"erreur": f"BIGS injoignable : {error}"})
            return
        if self.path == "/niveau":
            level = json.loads(self.rfile.read(int(self.headers.get("content-length", 0)))).get("level")
            if type(level) is not int or not 1 <= level <= 30:
                self.reply_json(400, {"erreur": "niveau entier attendu, de 1 à 30"})
                return
            try:
                self.reply_json(200, bigs_editor(self.bigs, "niveau", {"level": level}))
            except OSError as error:
                self.reply_json(502, {"erreur": f"BIGS injoignable : {error}"})
            return
        if self.path != "/enregistrer":
            self.reply(404, "text/plain", b"")
            return
        changes = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        catalog = {quest["definition"]: quest for quest in quest_catalog()}
        unknown = [definition for definition, state in changes.items() if int(definition) not in catalog or state not in (*STATES, "terminee")]
        if unknown:
            self.reply_json(400, {"erreur": f"quêtes ou états inconnus : {unknown}"})
            return
        try:
            self.reply_json(200, {"enregistrees": save_states(self.bigs, changes, catalog)})
        except OSError as error:
            self.reply_json(502, {"erreur": f"BIGS injoignable : {error}"})

    def log_message(self, format: str, *arguments) -> None:
        pass


def main() -> None:
    Editor.bigs = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BIGS
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    print(f"éditeur de sauvegarde : http://127.0.0.1:{port} (BIGS {Editor.bigs})")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Editor).serve_forever()


if __name__ == "__main__":
    main()
