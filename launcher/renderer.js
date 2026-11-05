const ip = document.getElementById("ip");
const lan = document.getElementById("lan");
const play = document.getElementById("jouer");
const stop = document.getElementById("arreter");
const secondGame = document.getElementById("second");
const stopSecond = document.getElementById("arreter-second");
const state = document.getElementById("etat");
const journal = document.getElementById("journal");
const gameFolder = document.getElementById("dossier-jeu");
const changeFolder = document.getElementById("changer-dossier");
const profileSection = document.getElementById("choix-profil");
const profile = document.getElementById("profil");
const newProfile = document.getElementById("nouveau-profil");
const profileStart = document.getElementById("debut-profil");
const modes = [...document.querySelectorAll("input[name=mode]")];
let busy = false, creatingProfile = false, choicesReady = false;

const selectedMode = () => modes.find((input) => input.checked)?.value ?? "local";

function refreshMode() {
  ip.disabled = selectedMode() !== "serveur";
}

function append(text) {
  journal.textContent += `${text}\n`;
  journal.scrollTop = journal.scrollHeight;
}

const showFolder = (folder) => { gameFolder.textContent = folder ?? "not found"; };

function refreshControls() {
  play.disabled = busy || creatingProfile || !choicesReady;
  profile.disabled = newProfile.disabled = busy || creatingProfile;
  profileStart.disabled = busy || creatingProfile;
}

function showProfiles(profiles, selectedProfile) {
  profileSection.hidden = !profiles;
  if (!profiles) return;
  profile.replaceChildren(...profiles.map(({ key, label }) => {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = label;
    return option;
  }));
  profile.value = selectedProfile;
}

refreshControls();

modes.forEach((input) => input.addEventListener("change", refreshMode));
changeFolder.addEventListener("click", async () => showFolder(await window.lanceur.dossierJeu()));
play.addEventListener("click", () => window.lanceur.jouer(selectedMode(), ip.value, profileSection.hidden ? undefined : profile.value));
newProfile.addEventListener("click", async () => {
  creatingProfile = true;
  refreshControls();
  try {
    const result = await window.lanceur.nouveauProfil(profileStart.value);
    showProfiles(result.profiles, result.selectedProfile);
    append(profileStart.value === "tutorial" ? "New introduction save created. Click Play, wait for story hooks to be ready, then continue in the game." : "New Fort Tarsis character created. This option skips the tutorials.");
  } catch (error) { append(error.message); }
  finally { creatingProfile = false; refreshControls(); }
});
secondGame.addEventListener("click", () => window.lanceur.second());
stop.addEventListener("click", () => window.lanceur.arreter("jeu1"));
stopSecond.addEventListener("click", () => window.lanceur.arreter("jeu2"));
window.lanceur.surJournal(append);
window.lanceur.surEtat(({ localServer, localServerReady, patches, game, second, ready, expedition }) => {
  busy = patches || game;
  refreshControls();
  changeFolder.disabled = game;
  secondGame.disabled = !ready || second;
  stop.disabled = !patches && !game;
  stopSecond.disabled = !second;
  const mission = expedition ? expedition.error ? "Expedition needs attention — see the log" :
    expedition.phase === "active" ? `Expedition ${expedition.generation} running` :
    expedition.phase === "starting" ? "Starting expedition…" :
    expedition.phase === "queued" ? "Mission queued…" :
    expedition.ready ? "Ready for another expedition" :
    expedition.phase === "failed" ? "Expedition launch failed — see the log" :
    expedition.phase === "returning" ? "Expedition ended — preparing next launch…" : "" : "";
  state.textContent = [game ? "Game running" : patches ? "Starting…" : localServer && !localServerReady ? "Starting the local server…" : "Ready", localServerReady ? "local server running" : "", mission, second ? "second game running" : ""].filter(Boolean).join(", ");
});

window.lanceur.choix().then(({ mode, modes: available, gameFolder: folder, ip: address, lan: lanAddress, redirector, profiles, selectedProfile }) => {
  modes.forEach((input) => { input.closest("label").hidden = !available.includes(input.value); });
  showFolder(folder);
  showProfiles(profiles, selectedProfile);
  document.getElementById("choix-mode").hidden = available.length === 1;
  document.getElementById("ligne-second").hidden = !available.includes("dev");
  const chosen = available.includes(mode) ? mode : "local";
  modes.forEach((input) => { input.checked = input.value === chosen; });
  ip.value = address;
  lan.textContent = lanAddress ? `(${lanAddress})` : "";
  refreshMode();
  state.textContent = `Ready (current redirector: ${redirector ?? "none"})`;
  choicesReady = true;
  refreshControls();
}).catch((error) => append(error.message));
