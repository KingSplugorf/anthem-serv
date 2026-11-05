const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn, execFile } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { IPV4, REDIRECTOR, redirectorAddress } = require("./hosts");
const windows = require("./windows_runtime");
const { localProfile, initialProfile, listProfiles, newCharacterProfile } = require("./windows_profiles");
const { missionStatusStream } = require("./mission_status");

if (!app.requestSingleInstanceLock()) {
  console.error("Anthem Launcher is already running. Close it before selecting a different profile.");
  app.quit();
} else {
// Keep Chromium's disposable cache away from settings and server saves. A new
// directory avoids stale Windows cache locks and ACLs from earlier launches.
const sessionCache = fs.mkdtempSync(path.join(app.getPath("temp"), "anthem-launcher-cache-"));
app.setPath("sessionData", sessionCache);
app.commandLine.appendSwitch("disk-cache-dir", path.join(sessionCache, "Cache"));
app.commandLine.appendSwitch("disable-gpu-shader-disk-cache");

const ROOT = path.resolve(__dirname, "..");
let window, game, patches;
let services = [];
let starting = false;
let closing = false;
let stopping;
let generation = 0;
let expedition = null;
const owned = new Set();
const settingsPath = () => path.join(app.getPath("userData"), "windows.json");
function send(channel, value) {
  if (!window || window.isDestroyed()) return;
  const contents = window.webContents;
  if (!contents.isDestroyed()) contents.send(channel, value);
}
const log = (message) => send("journal", message);
const status = () => send("etat", {
  localServer: services.length > 0, localServerReady: services.length === 3 && !starting,
  patches: Boolean(patches) || starting, game: Boolean(game), second: false, ready: Boolean(game),
  expedition,
});

app.on("second-instance", () => {
  if (!window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
  log("Launcher already running. Close this session before selecting a different profile.");
});

function settings() {
  try { return JSON.parse(fs.readFileSync(settingsPath(), "utf8")); }
  catch { return { mode: "local", ip: "127.0.0.1" }; }
}

function saveSettings(value) {
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify(value, null, 2));
}

function start(command, args, options = {}) {
  const child = spawn(command, args, { cwd: ROOT, windowsHide: true, ...options, shell: false });
  for (const output of [child.stdout, child.stderr]) {
    output?.setEncoding("utf8");
    output?.on("data", (chunk) => log(chunk.trimEnd()));
  }
  owned.add(child);
  child.once("exit", () => owned.delete(child));
  child.on("error", (error) => { owned.delete(child); log(error.message); });
  return child;
}

function launched(child) {
  return new Promise((resolve, reject) => {
    child.once("spawn", resolve);
    child.once("error", reject);
  });
}

function completed(command, args, options) {
  return new Promise((resolve, reject) => {
    const child = start(command, args, options);
    child.once("error", reject);
    child.once("exit", (code) => code === 0 ? resolve() : reject(new Error(`${command} exited with code ${code}`)));
  });
}

function stop(child) {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    // Stop only processes started by this launcher, including their patch followers.
    execFile("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true }, (error) => {
      if (error && child.exitCode === null) log(`Could not stop process ${child.pid}: ${error.message}`);
      resolve();
    });
  });
}

function stopSession() {
  generation++;
  if (stopping) return stopping;
  stopping = (async () => {
    // End the game before tearing down a debugger attached to it.
    await stop(game);
    await Promise.all([...owned].map(stop));
    game = patches = null;
    expedition = null;
    services = [];
    status();
  })().finally(() => { stopping = null; });
  return stopping;
}

function pythonCommand() {
  const local = path.join(ROOT, ".venv-windows", "Scripts", "python.exe");
  return fs.existsSync(local) ? { command: local, prefix: [] } : { command: "py.exe", prefix: ["-3"] };
}

async function chooseFolder() {
  const config = settings();
  const choice = await dialog.showOpenDialog(window, { title: "Game folder", defaultPath: config.gameExe ? path.dirname(config.gameExe) : undefined, properties: ["openDirectory"] });
  if (!choice.canceled && choice.filePaths.length) {
    const executable = path.join(choice.filePaths[0], "Anthem.exe");
    windows.gameLaunch(executable);
    saveSettings({ ...config, gameExe: executable });
  }
  return settings().gameExe ? path.dirname(settings().gameExe) : null;
}

async function play(_event, { mode, ip, profileKey }) {
  if (starting || game || patches) return;
  if (!["local", "serveur"].includes(mode)) throw new Error("Unsupported Windows mode");
  const config = settings();
  const selectedProfile = profileKey === undefined ? initialProfile(config, process.env) : profileKey;
  const launch = windows.gameLaunch(config.gameExe);
  const address = mode === "local" ? "127.0.0.1" : String(ip).trim();
  if (!IPV4.test(address)) throw new Error("Enter a valid server IPv4 address");
  const currentGeneration = generation;
  const ensureActive = () => {
    if (closing || currentGeneration !== generation) throw new Error("Startup cancelled");
  };
  const python = pythonCommand();
  let sessionEnvironment = { ...process.env };
  starting = true;
  status();
  try {
    await completed(python.command, [...python.prefix, "-X", "utf8", "-I", "-c", "import struct, numpy; assert struct.calcsize('P') == 8, '64-bit Python is required'"]);
    ensureActive();
    if (mode === "local") {
      const certPath = path.join(ROOT, "data", "certs", "test.crt");
      for (const file of [certPath, path.join(ROOT, "data", "certs", "test.key")]) {
        if (!fs.existsSync(file)) throw new Error("Development certificate missing. Run tools/windows/setup.ps1 first.");
      }
      let binaries = windows.serverBinaries(ROOT);
      if (!binaries) {
        log("Building the Windows services…");
        await completed("cargo.exe", ["+1.90.0", "build", "--locked", "--workspace", "--bins"]);
        ensureActive();
        binaries = windows.serverBinaries(ROOT);
      }
      if (!binaries) throw new Error("Windows service executables were not found after building");
      const profile = localProfile(ROOT, app.getPath("userData"), process.env, selectedProfile);
      sessionEnvironment = profile.env;
      log(`Using ${profile.mode} profile: ${profile.state}`);
      fs.mkdirSync(path.join(ROOT, "data", "logs"), { recursive: true });
      // Use the certificate's DNS identity for TLS, retaining the resolved IP
      // for transport. Advertising the IP as the hostname fails name checks.
      // Match tools/start.sh: a distinct dedicated identity makes the game take
      // ClientJoinMultiplayer, where launch_from_hub redirects it to InProc.
      const env = { ...profile.env, LISTEN_HOST: "127.0.0.1", BLAZE_HOST: REDIRECTOR, BLAZE_IP: address,
        BLAZE_SERVEUR_MISSION_ID: process.env.BLAZE_SERVEUR_MISSION_ID ?? "2000000",
        BLAZE_LOCAL_LIFECYCLE: profile.env.ANTHEM_MISSION_LIFECYCLE === "0" ? "0" : "1",
        BLAZE_MISSION_PAIR: profile.env.ANTHEM_MISSION_LIFECYCLE === "0" ? profile.env.BLAZE_MISSION_PAIR || "0" : "0",
        BIGS_URL: `http://${address}:8080` };
      for (const executable of binaries) {
        ensureActive();
        const name = path.basename(executable, ".exe");
        const output = fs.openSync(path.join(ROOT, "data", "logs", `${name}.log`), "w");
        let child;
        try { child = start(executable, [], { env, stdio: ["ignore", output, output] }); }
        finally { fs.closeSync(output); }
        services.push(child);
        await launched(child);
        child.once("exit", (code) => {
          if (!closing && !stopping && !starting && services.includes(child)) {
            log(`${name} stopped (${code}); stopping the session`);
            stopSession().catch((error) => log(error.message));
          }
        });
      }
      await windows.waitForServices(services, fs.readFileSync(certPath));
      ensureActive();
      log("Local services are ready");
    } else {
      // Mission hooks read the latest matchmaking request from a local Blaze log.
      // Never replay stale local attributes while using a remote server.
      throw new Error("Windows dedicated-server gameplay is not supported yet; use Local mode for now");
    }
    const currentHosts = fs.readFileSync(windows.hostsPath(), "utf8");
    if (redirectorAddress(currentHosts) !== address) {
      log("Windows will ask permission to update the redirector hosts entry");
      await completed("powershell.exe", windows.elevatedHostsCommand(address));
    }
    ensureActive();
    saveSettings({ ...config, mode, ip: address, profileKey: selectedProfile });
    game = start(launch.command, launch.args, { cwd: launch.cwd, windowsHide: false, env: sessionEnvironment });
    await launched(game);
    ensureActive();
    const gamePid = game.pid;
    game.once("exit", () => {
      log("Game closed");
      stopSession().catch((error) => log(error.message));
    });
    if (sessionEnvironment.ANTHEM_SKIP_PATCHES === "1") {
      log("Connection test: game started without external memory patches or debugger. Mission launching is not prepared in this mode.");
      return;
    }
    const patchOptions = { env: { ...sessionEnvironment,
      ...(sessionEnvironment.ANTHEM_MISSION_LIFECYCLE === "0" ? {} : { MISSION_PAIR: "0" }) } };
    const patchProcess = start(python.command, [...python.prefix, "-X", "utf8", "-I", path.join(ROOT, "tools", "auto_patches.py"), String(gamePid)], patchOptions);
    patches = patchProcess;
    patchProcess.stdout?.on("data", missionStatusStream((value) => {
      if (game && currentGeneration === generation && !stopping) { expedition = value; status(); }
    }));
    await launched(patchProcess);
    patchProcess.once("exit", (code) => {
      if (patches === patchProcess) patches = null;
      if (code !== 0 && game && !stopping && !closing && currentGeneration === generation) {
        log(`Patching failed (${code}); stopping the session. See the patch output above and data/logs/`);
        stopSession().catch((error) => log(error.message));
      }
      status();
    });
    log("Game started with native Windows patch tools (experimental)");
  } catch (error) {
    await stopSession();
    throw error;
  } finally {
    starting = false;
    status();
  }
}

app.whenReady().then(() => {
  window = new BrowserWindow({ width: 560, height: 760, title: "Anthem Launcher", autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true } });
  window.on("closed", () => { window = null; });
  ipcMain.handle("choix", () => {
    const config = settings();
    let redirector = null;
    try { redirector = redirectorAddress(fs.readFileSync(windows.hostsPath(), "utf8")); } catch {}
    const selectedProfile = initialProfile(config, process.env);
    return { ...config, modes: ["local"], lan: windows.lanAddress(), gameFolder: config.gameExe ? path.dirname(config.gameExe) : null, redirector,
      profiles: listProfiles(app.getPath("userData"), selectedProfile), selectedProfile };
  });
  ipcMain.handle("nouveau-profil", (_event, mode = "tutorial") => {
    if (starting || game || patches || services.length) throw new Error("Stop the game before creating a new character.");
    const key = newCharacterProfile(ROOT, app.getPath("userData"), process.env, mode);
    saveSettings({ ...settings(), profileKey: key });
    return { profiles: listProfiles(app.getPath("userData"), key), selectedProfile: key };
  });
  ipcMain.handle("dossier-jeu", () => chooseFolder().catch((error) => { log(error.message); return null; }));
  ipcMain.handle("jouer", (event, args) => play(event, args).catch((error) => log(error.message)));
  ipcMain.handle("arreter", () => stopSession());
  ipcMain.handle("second", () => log("Second-game mode is not supported on Windows yet"));
  window.loadFile(path.join(__dirname, "index.html"));
});

app.on("before-quit", (event) => {
  if (closing) return;
  event.preventDefault();
  closing = true;
  stopSession()
    .catch((error) => console.error("Launcher cleanup failed:", error))
    .finally(() => app.quit());
});
app.on("window-all-closed", () => app.quit());
}
