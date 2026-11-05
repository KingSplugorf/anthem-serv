const test = require("node:test");
const assert = require("node:assert/strict");
const runtime = require("./windows_runtime");

test("native game launch preserves a Windows path with spaces without shell interpolation", () => {
  const executable = "D:\\Games & stuff\\Anthem\\Anthem.exe";
  assert.deepEqual(runtime.gameLaunch(executable, () => true), {
    command: executable, args: ["-alwaysoffline"], cwd: "D:\\Games & stuff\\Anthem",
  });
  for (const file of ["Anthem.exe", "D:\\Games\\other.exe", ""]) {
    assert.throws(() => runtime.gameLaunch(file, () => true));
  }
  assert.throws(() => runtime.gameLaunch(executable, () => false));
});

test("Windows services must all come from one complete binary directory", () => {
  const root = "checkout";
  const path = require("node:path");
  const exists = (file) => ["blaze.exe", "bigs.exe", "redirector.exe"].some((name) => file === path.join(root, "target", "release", name));
  assert.deepEqual(runtime.serverBinaries(root, exists), ["redirector.exe", "blaze.exe", "bigs.exe"].map((name) => path.join(root, "target", "release", name)));
  assert.equal(runtime.serverBinaries(root, () => false), null);
});

test("hosts elevation validates the address and keeps paths and script as encoded arguments", () => {
  assert.equal(runtime.hostsPath("D:\\Windows"), "D:\\Windows\\System32\\drivers\\etc\\hosts");
  assert.throws(() => runtime.elevatedHostsCommand("127.0.0.1; echo bad"));
  assert.throws(() => runtime.elevatedHostsCommand("999.1.1.1"));
  const args = runtime.elevatedHostsCommand("127.0.0.1");
  assert.equal(args[2], "-EncodedCommand");
  const outer = Buffer.from(args[3], "base64").toString("utf16le");
  assert.match(outer, /-Verb RunAs -Wait -PassThru/);
  const inner = outer.match(/-EncodedCommand ([A-Za-z0-9+/=]+)/)[1];
  const script = Buffer.from(inner, "base64").toString("utf16le");
  assert.match(script, /127\.0\.0\.1 spring18\.gosredirector\.ea\.com/);
  assert.match(script, /anthem\.bak/);
  assert.match(script, /\$aliases/);
});

test("LAN discovery has a loopback fallback for offline Windows development", () => {
  assert.equal(runtime.lanAddress({}), "127.0.0.1");
  assert.equal(runtime.lanAddress({ eth: [{ internal: false, family: "IPv4", address: "192.168.1.23" }] }), "192.168.1.23");
});

test("readiness fails immediately when a child service exits", async () => {
  await assert.rejects(runtime.waitForServices([{exitCode:1,signalCode:null}],Buffer.from("")), /service exited/);
});
