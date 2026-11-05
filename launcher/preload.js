const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("lanceur", {
  choix: () => ipcRenderer.invoke("choix"),
  jouer: (mode, ip, profileKey) => ipcRenderer.invoke("jouer", { mode, ip, profileKey }),
  nouveauProfil: (mode) => ipcRenderer.invoke("nouveau-profil", mode),
  dossierJeu: () => ipcRenderer.invoke("dossier-jeu"),
  second: () => ipcRenderer.invoke("second"),
  arreter: (which) => ipcRenderer.invoke("arreter", which),
  surJournal: (callback) => ipcRenderer.on("journal", (_event, text) => callback(text)),
  surEtat: (callback) => ipcRenderer.on("etat", (_event, state) => callback(state)),
});
