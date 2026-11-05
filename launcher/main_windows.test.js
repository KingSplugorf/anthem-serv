const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

async function launcherHarness({ deferReadiness = false, taskkillThrows = false, taskkillExitCode = 0, env = {}, singleInstance = true } = {}) {
  const ipc = new Map();
  const app = new EventEmitter();
  app.whenReady = () => Promise.resolve();
  app.getPath = (name) => name === "temp" ? "/temp" : "/user";
  const cachePaths = [], switches = [];
  app.setPath = (name, value) => cachePaths.push({ name, value });
  app.commandLine = { appendSwitch: (...args) => switches.push(args) };
  app.requestSingleInstanceLock = () => singleInstance;
  let quitCalls = 0;
  const quit = new Promise((resolve) => app.once("quit", resolve));
  app.quit = () => {
    quitCalls++;
    const event = { prevented: false, preventDefault() { this.prevented = true; } };
    app.emit("before-quit", event);
    if (!event.prevented) app.emit("quit");
  };
  const spawned = [], killed = [], logs = [], windows = [], errors = [];
  let ready;
  const readiness = deferReadiness ? new Promise((resolve) => { ready = resolve; }) : Promise.resolve();
  let pid = 100;
  const children = new Map();
  function spawn(command, args, options) {
    const child = new EventEmitter();
    Object.assign(child, { pid: ++pid, exitCode: null, signalCode: null, stdout: new EventEmitter(), stderr: new EventEmitter() });
    child.stdout.setEncoding = child.stderr.setEncoding = () => {};
    children.set(child.pid, child);
    spawned.push({ command, args, options, child });
    queueMicrotask(() => {
      child.emit("spawn");
      if (args.includes("-c")) { child.exitCode = 0; child.emit("exit", 0); }
    });
    return child;
  }
  function execFile(command, args, options, callback) {
    assert.equal(command, "taskkill.exe");
    if (taskkillThrows) throw new Error("simulated taskkill failure");
    const child = children.get(Number(args[1]));
    killed.push(child.pid);
    queueMicrotask(() => { child.exitCode = taskkillExitCode; child.emit("exit", taskkillExitCode); callback(null); });
  }
  class BrowserWindow extends EventEmitter {
    constructor() {
      super();
      this.destroyed = false;
      this.rendererDestroyed = false;
      this.contents = {
        isDestroyed: () => this.rendererDestroyed,
        send: (event, message) => {
          if (this.rendererDestroyed) throw new Error("Object has been destroyed");
          if (event === "journal") logs.push(message);
        },
      };
      windows.push(this);
    }
    get webContents() {
      if (this.destroyed) throw new Error("Object has been destroyed");
      return this.contents;
    }
    isDestroyed() { return this.destroyed; }
    destroy() {
      this.destroyed = this.rendererDestroyed = true;
      this.emit("closed");
    }
    loadFile() {}
    isMinimized() { return Boolean(this.minimized); }
    restore() { this.minimized = false; }
    show() { this.shown = true; }
    focus() { this.focused = true; }
  }
  const fileSystem = {
    existsSync: () => true,
    mkdirSync() {}, writeFileSync() {}, copyFileSync() {}, closeSync() {}, openSync: () => 8,
    mkdtempSync: (prefix) => `${prefix}unique`,
    readFileSync(file) {
      if (file.endsWith("windows.json")) return JSON.stringify({ gameExe: "D:\\Anthem\\Anthem.exe", mode: "local" });
      if (file === "hosts") return "127.0.0.1 spring18.gosredirector.ea.com\n";
      return Buffer.from("certificate");
    },
  };
  const runtime = { ...require("./windows_runtime"), gameLaunch: (exe) => require("./windows_runtime").gameLaunch(exe, () => true), hostsPath: () => "hosts", serverBinaries: () => ["redirector.exe", "blaze.exe", "bigs.exe"], waitForServices: () => readiness };
  const modules = {
    electron: { app, BrowserWindow, dialog: {}, ipcMain: { handle: (name, callback) => ipc.set(name, callback) } },
    "node:child_process": { spawn, execFile }, "node:fs": fileSystem, "node:path": path,
    "./hosts": require("./hosts"), "./windows_runtime": runtime,
    "./mission_status": require("./mission_status"),
    "./windows_profiles": {
      initialProfile: (config, inherited) => inherited.ANTHEM_PROFILE === "fresh" ? `fresh-${inherited.ANTHEM_PROFILE_ID || "pilot-1"}` : config.profileKey || "development",
      listProfiles: () => [{ key: "development", label: "Original save — Level 30" }],
      newCharacterProfile: (_root, _user, _env, mode = "tutorial") => `${mode}-new-slot`,
      localProfile: (_root, userData, inherited, selection) => ({
      mode: selection?.startsWith("tutorial-") ? "tutorial" : "development", key: selection, state: path.join(userData, "server", "state.json"),
      env: { ...inherited, BIGS_ETAT: path.join(userData, "server", "state.json"),
        BIGS_SAUVEGARDES: path.join(userData, "server", "saves"), BLAZE_CAPTURES: path.join(userData, "server", "captures"),
        ...(selection?.startsWith("tutorial-") ? { ANTHEM_STORY_MODE: "1", BIGS_NOUVELLE_PARTIE: "1", ANTHEM_MISSION_LIFECYCLE: "1" } : {}) },
    }) },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "main_windows.js"), "utf8"), {
    require: (name) => { assert.ok(name in modules, name); return modules[name]; },
    __dirname, process: { env }, console: { error: (...args) => errors.push(args) },
  }, { filename: "main_windows.js" });
  await new Promise(setImmediate);
  return { ipc, spawned, killed, logs, ready, app, quit, windows, errors, cachePaths, switches, quitCalls: () => quitCalls };
}

test("Chromium caches use a temporary session directory while settings and saves keep userData", async () => {
  const harness = await launcherHarness();
  assert.deepEqual(harness.cachePaths, [{ name: "sessionData", value: "/temp/anthem-launcher-cache-unique" }]);
  assert.deepEqual(harness.switches, [["disk-cache-dir", "/temp/anthem-launcher-cache-unique/Cache"], ["disable-gpu-shader-disk-cache"]]);
  await harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
  const bigs = harness.spawned.find((item) => item.command === "bigs.exe");
  assert.equal(bigs.options.env.BIGS_ETAT, "/user/server/state.json");
  await harness.ipc.get("arreter")();
});

test("tutorial profile environment reaches the services, game and mission patcher", async () => {
  const h = await launcherHarness();
  await h.ipc.get("jouer")({}, { mode: "local", ip: "127.0.0.1", profileKey: "tutorial-fresh" });
  const participants = h.spawned.filter(({ command, args }) => ["redirector.exe", "blaze.exe", "bigs.exe"].includes(command) ||
    command.endsWith("Anthem.exe") || args.some((arg) => arg.endsWith("auto_patches.py")));
  assert.equal(participants.length, 5);
  for (const participant of participants) {
    assert.equal(participant.options.env.ANTHEM_STORY_MODE, "1");
    assert.equal(participant.options.env.BIGS_NOUVELLE_PARTIE, "1");
  }
  assert.equal(participants.find(({ command }) => command === "blaze.exe").options.env.BLAZE_LOCAL_LIFECYCLE, "1");
});

test("a second launcher exits before creating caches, windows or services", async () => {
  const harness = await launcherHarness({ singleInstance: false });
  await harness.quit;
  assert.equal(harness.quitCalls(), 1);
  assert.equal(harness.ipc.size, 0);
  assert.equal(harness.windows.length, 0);
  assert.equal(harness.spawned.length, 0);
  assert.deepEqual(harness.cachePaths, []);
});

test("a second-instance request focuses the running launcher without stopping its game", async () => {
  const harness = await launcherHarness();
  await harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
  const window = harness.windows[0];
  window.minimized = true;
  harness.app.emit("second-instance");
  assert.equal(window.minimized, false);
  assert.equal(window.shown, true);
  assert.equal(window.focused, true);
  assert.deepEqual(harness.killed, []);
  await harness.ipc.get("arreter")();
});

test("new-character action selects its separate slot and is refused while playing", async () => {
  const harness = await launcherHarness();
  const selection = await harness.ipc.get("nouveau-profil")();
  assert.equal(selection.selectedProfile, "tutorial-new-slot");
  await harness.ipc.get("jouer")(null, { mode: "local", ip: "", profileKey: "development" });
  assert.throws(() => harness.ipc.get("nouveau-profil")(), /Stop the game/);
  await harness.ipc.get("arreter")();
});

test("Windows launcher starts native services, the game and Python patches, then stops only its children", async () => {
  const harness = await launcherHarness();
  await harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
  assert.equal(harness.spawned.length, 6);
  const game = harness.spawned.find((item) => item.command === "D:\\Anthem\\Anthem.exe");
  assert.deepEqual(Array.from(game.args), ["-alwaysoffline"]);
  const patches = harness.spawned.find((item) => item.args.some((arg) => arg.endsWith("auto_patches.py")));
  assert.equal(patches.args.at(-1), String(game.child.pid));
  assert.equal(harness.spawned.some((item) => /bash|proton|gdb|pkexec/.test(item.command)), false);
  assert.ok(harness.spawned.every((item) => item.options.shell === false));
  for (const service of harness.spawned.filter((item) => ["redirector.exe", "blaze.exe", "bigs.exe"].includes(item.command))) {
    assert.equal(service.options.env.LISTEN_HOST, "127.0.0.1");
    assert.equal(service.options.env.BLAZE_HOST, "spring18.gosredirector.ea.com");
    assert.equal(service.options.env.BLAZE_IP, "127.0.0.1");
    assert.equal(service.options.env.BLAZE_SERVEUR_MISSION_ID, "2000000");
    assert.equal(service.options.env.BLAZE_LOCAL_LIFECYCLE, "1");
    assert.equal(service.options.env.BLAZE_MISSION_PAIR, "0");
  }
  await harness.ipc.get("arreter")();
  assert.equal(harness.killed[0], game.child.pid);
  assert.equal(new Set(harness.killed).size, 5);
  assert.ok(harness.logs.some((line) => line.includes("Game started")));
});

test("explicit mission-server identity overrides retain the Linux startup semantics", async () => {
  for (const value of ["3000000", ""]) {
    const harness = await launcherHarness({ env: { BLAZE_SERVEUR_MISSION_ID: value } });
    await harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
    assert.equal(harness.spawned.find((item) => item.command === "blaze.exe").options.env.BLAZE_SERVEUR_MISSION_ID, value);
    await harness.ipc.get("arreter")();
  }
});

test("intentional shutdown does not report a terminated patch process as a new patching failure", async () => {
  const harness = await launcherHarness({ taskkillExitCode: 1 });
  await harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
  await harness.ipc.get("arreter")();
  assert.equal(new Set(harness.killed).size, 5);
  assert.equal(harness.logs.some((line) => line.includes("Patching failed")), false);
});

test("an unexpected patch failure during a running game is reported and stops its session", async () => {
  const harness = await launcherHarness();
  await harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
  const patcher = harness.spawned.find((item) => item.args.some((arg) => arg.endsWith("auto_patches.py"))).child;
  patcher.exitCode = 1;
  patcher.emit("exit", 1);
  await new Promise(setImmediate);
  assert.equal(harness.logs.filter((line) => line.includes("Patching failed (1)")).length, 1);
  assert.equal(new Set(harness.killed).size, 4);
});

test("legacy mission switch disables lifecycle on the services and preserves explicit peer configuration", async () => {
  const harness = await launcherHarness({ env: { ANTHEM_MISSION_LIFECYCLE: "0", BLAZE_MISSION_PAIR: "1" } });
  await harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
  const blaze = harness.spawned.find((item) => item.command === "blaze.exe");
  assert.equal(blaze.options.env.BLAZE_LOCAL_LIFECYCLE, "0");
  assert.equal(blaze.options.env.BLAZE_MISSION_PAIR, "1");
  await harness.ipc.get("arreter")();
});

test("cancelling Windows startup during readiness prevents game launch", async () => {
  const harness = await launcherHarness({ deferReadiness: true });
  const play = harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
  await new Promise(setImmediate);
  assert.equal(harness.spawned.length, 4);
  await harness.ipc.get("arreter")();
  harness.ready();
  await play;
  assert.equal(harness.spawned.length, 4);
  assert.ok(harness.logs.includes("Startup cancelled"));
});


test("shutdown after the window closes still cleans up every owned process", { timeout: 2000 }, async () => {
  const harness = await launcherHarness();
  await harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
  harness.windows[0].destroy();
  assert.doesNotThrow(() => harness.spawned.at(-1).child.stdout.emit("data", "late patch output"));
  harness.app.emit("window-all-closed");
  await harness.quit;
  assert.equal(new Set(harness.killed).size, 5);
  assert.equal(harness.quitCalls(), 2);
  assert.deepEqual(harness.errors, []);
});

test("destroyed window and renderer objects are never sent status or log messages", async () => {
  for (const destroyedWindow of [false, true]) {
    const harness = await launcherHarness();
    await harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
    const window = harness.windows[0];
    // Simulate teardown before Electron delivers the final closed event.
    window.destroyed = destroyedWindow;
    window.rendererDestroyed = true;
    assert.doesNotThrow(() => harness.spawned.at(-1).child.stdout.emit("data", "late output"));
    await harness.ipc.get("arreter")();
    assert.equal(new Set(harness.killed).size, 5);
  }
});

test("unexpected cleanup rejection is reported and quit completes without an unhandled promise", { timeout: 2000 }, async () => {
  const harness = await launcherHarness({ taskkillThrows: true });
  await harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
  harness.windows[0].destroy();
  harness.app.emit("window-all-closed");
  await harness.quit;
  await new Promise(setImmediate);
  assert.equal(harness.quitCalls(), 2);
  assert.equal(harness.errors.length, 1);
  assert.match(String(harness.errors[0][1]), /simulated taskkill failure/);
});

test("closing during service readiness cancels startup without sending to the closed window", { timeout: 2000 }, async () => {
  const harness = await launcherHarness({ deferReadiness: true });
  const play = harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
  await new Promise(setImmediate);
  harness.windows[0].destroy();
  harness.app.emit("window-all-closed");
  await harness.quit;
  harness.ready();
  await play;
  assert.equal(harness.spawned.length, 4);
  assert.equal(new Set(harness.killed).size, 3);
  assert.deepEqual(harness.errors, []);
});

test("connection-only mode runs services and the same game without spawning a patcher or debugger", async () => {
  const harness = await launcherHarness({ env: { ANTHEM_SKIP_PATCHES: "1", ANTHEM_TLS_DIAGNOSTICS: "1" } });
  await harness.ipc.get("jouer")(null, { mode: "local", ip: "" });
  assert.equal(harness.spawned.length, 5); // dependency check, three services, game
  assert.ok(harness.spawned.some((item) => item.command === "D:\\Anthem\\Anthem.exe"));
  assert.equal(harness.spawned.some((item) => item.args.some((arg) => /auto_patches|windows_debugger/.test(arg))), false);
  assert.ok(harness.logs.some((line) => line.includes("without external memory patches or debugger")));
  await harness.ipc.get("arreter")();
  assert.equal(new Set(harness.killed).size, 4);
});
