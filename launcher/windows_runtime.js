const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const http = require("node:http");
const tls = require("node:tls");
const { IPV4, REDIRECTOR } = require("./hosts");

function lanAddress(interfaces = os.networkInterfaces()) {
  return Object.values(interfaces).flat().find((item) => item && !item.internal && item.family === "IPv4")?.address ?? "127.0.0.1";
}

function hostsPath(systemRoot = process.env.SystemRoot || "C:\\Windows") {
  return path.win32.join(systemRoot, "System32", "drivers", "etc", "hosts");
}

const encodedCommand = (script) => Buffer.from(script, "utf16le").toString("base64");

function hostsScript(address) {
  if (!IPV4.test(address)) throw new Error("Invalid redirector IPv4 address");
  // Read inside the elevated process, preserving unrelated aliases and comments.
  return `$ErrorActionPreference = 'Stop'
$p = Join-Path $env:SystemRoot 'System32\\drivers\\etc\\hosts'
if (-not (Test-Path -LiteralPath ($p + '.anthem.bak'))) { Copy-Item -LiteralPath $p -Destination ($p + '.anthem.bak') }
$lines = foreach ($line in [IO.File]::ReadAllLines($p)) {
  $parts = $line -split '#', 2
  $fields = @($parts[0].Trim() -split '\\s+' | Where-Object { $_ })
  if ($fields.Count -gt 1 -and $fields[1..($fields.Count-1)] -contains '${REDIRECTOR}') {
    $aliases = @($fields[1..($fields.Count-1)] | Where-Object { $_ -ine '${REDIRECTOR}' })
    $comment = if ($parts.Count -gt 1) { ' #' + $parts[1] } else { '' }
    if ($aliases.Count) { $fields[0] + ' ' + ($aliases -join ' ') + $comment }
    elseif ($comment) { $comment.TrimStart() }
  } else { $line }
}
$text = (($lines + '${address} ${REDIRECTOR}') -join [Environment]::NewLine) + [Environment]::NewLine
[IO.File]::WriteAllText($p, $text, (New-Object Text.UTF8Encoding $false))
`;
}

function elevatedHostsCommand(address) {
  const inner = encodedCommand(hostsScript(address));
  const script = `$ErrorActionPreference = 'Stop'; $p = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe') -Verb RunAs -Wait -PassThru -ArgumentList '-NoProfile -NonInteractive -EncodedCommand ${inner}'; exit $p.ExitCode`;
  return ["-NoProfile", "-NonInteractive", "-EncodedCommand", encodedCommand(script)];
}

function serverBinaries(root, exists = fs.existsSync) {
  for (const directory of ["bin", path.join("target", "release"), path.join("target", "debug")]) {
    const result = ["redirector", "blaze", "bigs"].map((name) => path.join(root, directory, `${name}.exe`));
    if (result.every(exists)) return result;
  }
  return null;
}

function requestConfig(port = 8080) {
  return new Promise((resolve, reject) => {
    const request = http.get({ host: "127.0.0.1", port, path: "/api/gameconfig/config", timeout: 2000 }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { body += chunk; if (body.length > 65536) response.destroy(new Error("Oversized BIGS response")); });
      response.on("error", reject);
      response.on("end", () => {
        try {
          const value = JSON.parse(body);
          if (response.statusCode !== 200 || !["bools", "floats", "ints", "strings"].every((key) => Array.isArray(value[key]))) throw new Error("Unexpected BIGS config response");
          resolve();
        } catch (error) { reject(error); }
      });
    });
    request.on("timeout", () => request.destroy(new Error("BIGS readiness timeout")));
    request.on("error", reject);
  });
}

function tlsProbe(port, ca, blaze = false) {
  return new Promise((resolve, reject) => {
    let response = Buffer.alloc(0);
    const socket = tls.connect({ host: "127.0.0.1", port, servername: REDIRECTOR, ca, rejectUnauthorized: true }, () => {
      if (blaze) socket.write(Buffer.from("00000000000000000000000001800000", "hex"));
      else {
        const body = "<serverinstancerequest><name>anthem</name></serverinstancerequest>";
        socket.write(`POST /redirector/getServerInstance HTTP/1.1\r\nHost: ${REDIRECTOR}\r\nContent-Length: ${Buffer.byteLength(body)}\r\nConnection: close\r\n\r\n${body}`);
      }
    });
    socket.setTimeout(2000, () => socket.destroy(new Error("Service TLS readiness timeout")));
    socket.on("error", reject);
    socket.on("close", () => reject(new Error("Service closed before completing readiness reply")));
    socket.on("data", (chunk) => {
      response = Buffer.concat([response, chunk]);
      if (response.length > 65536) return socket.destroy(new Error("Oversized service response"));
      if (blaze && response.length >= 16) {
        if (!response.subarray(0, 13).equals(Buffer.from("00000000000000000000000001", "hex")) || response[13] !== 0xa0) {
          return socket.destroy(new Error("Unexpected Blaze ping response"));
        }
        resolve();
        socket.end();
      }
    });
    socket.on("end", () => {
      if (blaze) {
        if (response.length < 16) reject(new Error("Incomplete Blaze ping reply"));
      } else if (/^HTTP\/1\.1 200 OK\r\n/.test(response.toString()) && response.includes(Buffer.from("<port>10041</port>"))) resolve();
      else reject(new Error("Unexpected redirector response"));
    });
  });
}

async function waitForServices(children, certificate, timeout = 15000) {
  const deadline = Date.now() + timeout;
  let failure;
  do {
    if (children.some((child) => child.exitCode !== null || child.signalCode !== null)) throw new Error("A service exited during startup; inspect the launcher log");
    try {
      await Promise.all([requestConfig(), tlsProbe(42230, certificate), tlsProbe(10041, certificate, true)]);
      if (children.some((child) => child.exitCode !== null || child.signalCode !== null)) throw new Error("A service exited during readiness checks");
      return;
    } catch (error) { failure = error; }
    await new Promise((resolve) => setTimeout(resolve, 200));
  } while (Date.now() < deadline);
  throw new Error(`Services did not become ready: ${failure?.message}`);
}

function gameLaunch(executable, exists = fs.existsSync) {
  if (!path.win32.isAbsolute(executable || "") || path.win32.basename(executable).toLowerCase() !== "anthem.exe" || !exists(executable)) {
    throw new Error("Choose the Windows folder containing Anthem.exe first");
  }
  return { command: executable, args: ["-alwaysoffline"], cwd: path.win32.dirname(executable) };
}

module.exports = { lanAddress, hostsPath, encodedCommand, hostsScript, elevatedHostsCommand, serverBinaries, requestConfig, tlsProbe, waitForServices, gameLaunch };
