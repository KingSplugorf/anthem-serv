if (process.platform === "win32") {
  require("./main_windows");
} else {
const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { IPV4, redirectorAddress, withRedirector } = require("./hosts");
const { withGameExe } = require("./game_conf");
const { availableModes, isLocal, localServerEnv, patchesEnv } = require("./modes");

const ROOT = path.resolve(__dirname, "..");
const HOSTS = "/etc/hosts";
const GAME_CONF = path.join(ROOT, "game.conf");
const GAME_EXE = "Anthem.exe";
const PATCHES_READY = "services lancés";
const SERVER_READY = "serveur central sur";
const SECOND_PLAYER = "10.200.0.2=1001:Joueur 2";
const ASKPASS = spawnSync("sh", ["-c", "command -v ksshaskpass"], { encoding: "utf8" }).stdout.trim();
const GAMES = ["jeu1", "jeu2"];
const MODES = availableModes(fs.existsSync(path.join(__dirname, "modes.json")) ? fs.readFileSync(path.join(__dirname, "modes.json"), "utf8") : null);
const SECOND_GAME = MODES.includes("dev");

let window;
let localServer = null;
let localServerReady = false;
let patches = null;
let game = null;
let second = null;
let session = null;

const settingsPath = () => path.join(app.getPath("userData"), "choix.json");

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(settingsPath(), "utf8"));
  } catch {
    return { mode: "local", ip: "192.168.1.53" };
  }
}

function lanAddress() {
  const route = spawnSync("ip", ["-4", "route", "get", "1.1.1.1"], { encoding: "utf8" }).stdout;
  return route.match(/ src (\d+\.\d+\.\d+\.\d+)/)?.[1] ?? null;
}

function gameFolder() {
  const exe = spawnSync("bash", ["-c", 'source tools/paths.sh && printf %s "$JEU_EXE"'], { cwd: ROOT, encoding: "utf8" }).stdout;
  return exe ? path.dirname(exe) : null;
}

async function chooseGameFolder() {
  const current = gameFolder();
  const choice = await dialog.showOpenDialog(window, { title: "Game folder", defaultPath: current ?? undefined, properties: ["openDirectory"] });
  if (choice.canceled || !choice.filePaths.length) return current;
  const exe = path.join(choice.filePaths[0], GAME_EXE);
  if (!fs.existsSync(exe)) {
    log(`no ${GAME_EXE} in ${choice.filePaths[0]}`);
    return current;
  }
  const conf = fs.existsSync(GAME_CONF) ? fs.readFileSync(GAME_CONF, "utf8") : "";
  if (conf) fs.copyFileSync(GAME_CONF, `${GAME_CONF}.avant`);
  fs.writeFileSync(GAME_CONF, withGameExe(conf, exe, app.getPath("home")));
  log(`game folder: ${choice.filePaths[0]}`);
  return gameFolder();
}

function log(text) {
  window?.webContents.send("journal", text);
}

function status() {
  window?.webContents.send("etat", {
    localServer: Boolean(localServer),
    localServerReady,
    patches: Boolean(patches),
    game: Boolean(game),
    second: Boolean(second),
    ready: Boolean(session?.ready),
  });
}

function run(command, args, env = {}, onLine) {
  const child = spawn(command, args, { cwd: ROOT, env: { ...process.env, ...env }, detached: true });
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => chunk.split("\n").filter(Boolean).forEach((line) => { log(line); onLine?.(line); }));
  }
  return child;
}

function finished(child) {
  return new Promise((resolve) => child.on("exit", (code) => resolve(code)));
}

function stopGroup(child) {
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch {}
}

function pointRedirector(address) {
  const current = fs.readFileSync(HOSTS, "utf8");
  if (redirectorAddress(current) === address) return Promise.resolve();
  log(`/etc/hosts: redirector to ${address} (administrator password)`);
  return new Promise((resolve, reject) => {
    const child = spawn("pkexec", ["tee", HOSTS], { stdio: ["pipe", "ignore", "pipe"] });
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", log);
    child.on("error", reject);
    child.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`pkexec failed (code ${code})`))));
    child.stdin.end(withRedirector(current, address));
  });
}

async function startLocalServer(mode) {
  if (localServerReady) return true;
  if (localServer) return false;
  const address = lanAddress();
  if (!address) {
    log("network address of this PC not found");
    return false;
  }
  log("building the services");
  if ((await finished(run("cargo", ["build", "-q", "--bins"]))) !== 0) {
    log("building the services: failed");
    return false;
  }
  return new Promise((resolve) => {
    localServer = run("tools/central_server.sh", [address, SECOND_PLAYER], localServerEnv(mode), (line) => {
      if (localServerReady || !line.includes(SERVER_READY)) return;
      localServerReady = true;
      status();
      resolve(true);
    });
    localServer.on("exit", (code) => {
      localServer = null;
      localServerReady = false;
      log(`local server stopped (code ${code})`);
      status();
      resolve(false);
    });
    status();
  });
}

function stopLocalServer() {
  if (localServer) stopGroup(localServer);
}

async function play(_event, { mode, ip }) {
  if (patches || game || !MODES.includes(mode)) return;
  const address = isLocal(mode) ? lanAddress() : ip.trim();
  if (isLocal(mode) && !(await startLocalServer(mode))) {
    log("local server unavailable");
    return;
  }
  if (!address || !IPV4.test(address)) {
    log(`invalid address: ${address}`);
    return;
  }
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify({ mode, ip }));
  try {
    await pointRedirector(address);
  } catch (error) {
    log(error.message);
    return;
  }
  session = { address, ready: false };
  patches = run("tools/start.sh", [], patchesEnv(mode, address), (line) => {
    if (session.ready || !line.includes(PATCHES_READY)) return;
    session.ready = true;
    game = run("tools/launch_game.sh", []);
    game.on("exit", () => { game = null; log("game closed"); status(); });
    status();
  });
  patches.on("exit", (code) => { patches = null; if (session) session.ready = false; log(`patches stopped (code ${code})`); status(); });
  status();
}

async function playSecond() {
  if (!SECOND_GAME || !session?.ready || second) return;
  log("second game network");
  const code = await finished(run("tools/second_jeu.sh", ["reseau"], { SERVEUR_CENTRAL: session.address, SUDO_ASKPASS: ASKPASS }));
  if (code !== 0) {
    log(`second game network: failed (code ${code})`);
    return;
  }
  second = run("tools/second_jeu.sh", ["lancer"], { SUDO_ASKPASS: ASKPASS });
  second.on("exit", () => { second = null; log("second game closed"); status(); });
  status();
}

function stopGame(_event, which) {
  if (!GAMES.includes(which)) return;
  log(`stopping the ${which === "jeu1" ? "game" : "second game"}`);
  run("tools/stop_game.sh", [which]);
  if (which === "jeu1" && !second) {
    if (patches) stopGroup(patches);
    stopLocalServer();
  }
}

app.whenReady().then(() => {
  window = new BrowserWindow({
    width: 560,
    height: 640,
    title: "Anthem Launcher",
    autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true },
  });
  ipcMain.handle("choix", () => ({ ...readSettings(), modes: MODES, gameFolder: gameFolder(), lan: lanAddress(), redirector: redirectorAddress(fs.readFileSync(HOSTS, "utf8")) }));
  ipcMain.handle("jouer", play);
  ipcMain.handle("dossier-jeu", chooseGameFolder);
  ipcMain.handle("second", playSecond);
  ipcMain.handle("arreter", stopGame);
  window.loadFile(path.join(__dirname, "index.html"));
});

function stopServices() {
  if (patches) stopGroup(patches);
  stopLocalServer();
}

app.on("window-all-closed", () => {
  stopServices();
  app.quit();
});

for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    stopServices();
    app.quit();
  });
}

}
