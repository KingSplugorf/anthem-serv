const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

async function launcherHarness({ deferReadiness = false } = {}) {
  const ipc = new Map();
  const app = new EventEmitter();
  app.whenReady = () => Promise.resolve();
  app.getPath = () => "/user";
  app.quit = () => {};
  const spawned = [], killed = [], logs = [];
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
    const child = children.get(Number(args[1]));
    killed.push(child.pid);
    queueMicrotask(() => { child.exitCode = 0; child.emit("exit", 0); callback(null); });
  }
  class BrowserWindow {
    constructor() { this.webContents = { send: (event, message) => { if (event === "journal") logs.push(message); } }; }
    loadFile() {}
  }
  const fileSystem = {
    existsSync: () => true,
    mkdirSync() {}, writeFileSync() {}, copyFileSync() {}, closeSync() {}, openSync: () => 8,
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
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "main_windows.js"), "utf8"), {
    require: (name) => { assert.ok(name in modules, name); return modules[name]; },
    __dirname, process: { env: {} }, console,
  }, { filename: "main_windows.js" });
  await new Promise(setImmediate);
  return { ipc, spawned, killed, logs, ready };
}

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
  }
  await harness.ipc.get("arreter")();
  assert.equal(harness.killed[0], game.child.pid);
  assert.equal(new Set(harness.killed).size, 5);
  assert.ok(harness.logs.some((line) => line.includes("Game started")));
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
