# Native Windows development port (experimental)

This is the first native Windows port of the **local, single-game** workflow.
It does not use WSL, Wine, Proton, Bash, or GDB on Windows. It is developer
setup, not a packaged end-user installer. Windows execution and gameplay have
not yet been validated; the automated tests run on Linux with simulated Win32
APIs and real Linux service requests.

## Prerequisites

Use Windows 10/11 x64 with:

- Rust through rustup, using the x64 MSVC host, and Visual Studio C++ Build Tools
  with the Windows SDK. Setup installs Rust 1.90.0.
- Node.js (22 or newer) with npm; the locked launcher uses Electron 37.2.0.
- 64-bit Python 3.11 or newer with the `py` launcher. Setup creates an isolated
  `.venv-windows` and installs NumPy 2.3.5.
- OpenSSL available as `openssl.exe` for generating the local development TLS
  certificate.
- Your own game installation, of the exact build supported by this repository.
  The game's existing certificate requirement in the root README still applies;
  this port does not modify certificate verification or provide game files.

From a writable checkout, run in PowerShell:

```powershell
.\tools\windows\setup.ps1
cd launcher
npm.cmd start
```

Choose the folder containing `Anthem.exe`, then **Play**. Windows asks for UAC
approval only when the redirector hosts entry needs changing. The elevated
operation preserves other aliases/comments and makes a first-use backup at
`%SystemRoot%\System32\drivers\etc\hosts.anthem.bak`. The entry remains after
closing the launcher; remove the `spring18.gosredirector.ea.com` entry manually
when you no longer want local redirection. Inspect current contents before
restoring an old hosts backup so subsequent unrelated edits are preserved.

The launcher finds all three service executables together in `bin`,
`target/release`, or `target/debug`, in that order. Missing binaries are built
with Cargo. It verifies BIGS's configuration response, the redirector's TLS/XML
response, and Blaze's TLS/Fire2 ping before starting the game directly with
`-alwaysoffline`. Patch tools run through the local Python virtual environment.
Local services explicitly listen on IPv4 loopback (`LISTEN_HOST=127.0.0.1`),
matching the launcher's probes and advertised game endpoints. Services started
outside the launcher default to IPv4 on Windows and retain the existing IPv6
default on Linux; `LISTEN_HOST` overrides the interface. Rebuild all three service
executables after updating their Rust source; restarting Electron alone does not
replace previously built binaries.
**Stop** and closing the launcher stop only its own process trees; stopping during
startup cancels the pending game launch.

Settings and development saves are under Electron's per-user application data
folder (`windows.json` and `server/`); the tracked save template is copied once
and is not overwritten by gameplay. Service logs remain in `data/logs/`, where
the mission hooks read the local Blaze matchmaking log. The ignored certificate
pair is in `data/certs/`, expires after 30 days, and is never installed as a
system trust anchor. Preserve custom certificates when regenerating an expired
pair.

## Executable compatibility report

Large game executables may exceed chat's file-transfer limit. To generate a
small report locally without running or changing the executable:

```powershell
py -3 tools/windows/inspect_game.py 'C:\Games\Anthem\Anthem.exe' --output Anthem-layout.json
```

Share `Anthem-layout.json`. The report includes the SHA-256, PE sections,
preferred image base, ASLR flag, and a few bytes at mission patch/hook sites.
It contains no full executable or game assets. Packed executable code may have
no meaningful bytes until runtime, so matching this report alone does not
establish runtime compatibility.

## Patching and debugger changes

- `tools/process_memory.py` selects Linux `/proc` access or native Win32 access.
  Windows uses `ReadProcessMemory`, `WriteProcessMemory`, `VirtualQueryEx`, and
  module enumeration. Code writes restore the original protections for each
  touched region and flush the instruction cache. Guard/no-access regions and
  incomplete reads/writes are rejected.
- Memory scanning, mission transition patching, FINRACHAOS patching, health,
  and player follow tools use this shared layer. Existing byte checks remain in
  place; unexpected bytes are not patched. Legacy French combat selection names
  are mapped to the current scripts, and unknown names produce an error.
- `tools/mission_hooks.py` holds the mission behavior used by both the Linux
  GDB adapter and `tools/windows_debugger.py`. Windows uses the native debugging
  API with three hardware execution breakpoints and one four-byte write
  watchpoint, applied to existing and newly created threads. The supervisor
  requests a graceful detach through stdin after mission startup. The adapter
  restores original debug registers, continues threads, and passes unrelated
  exceptions back to the game. If the parent exits, EOF requests detachment.
- All patch addresses and injected assembly remain specific to the original
  game build. Relocated main images are rejected (expected base `0x140000000`);
  automatic ASLR rebasing is **not implemented**. Runtime build compatibility
  still needs confirmation against your executable. Do not disable ASLR globally
  to work around a rejected image.

## Current limits and next validation

Only Local mode is enabled on Windows. Dedicated-server mode needs a way to
supply current matchmaking attributes without reading stale local Blaze logs.
Shared-hub/second-game networking, the optional `sonde_sortie` GDB exit diagnostic,
a packaged Windows installer, and first-launch key automation are not ported.
Windows starts the configured executable directly; installations that spawn a
different game process will need process discovery before patches can attach.

The critical next check is on an actual Windows machine with the supported game:
verify the local services, confirm module addresses/patch-byte checks, attach and
detach the native debugger, start a mission, and test mission/combat behavior and
cleanup. An attach failure due to Windows access permissions should be diagnosed
with the game's integrity level and launcher logs; setup does not enable debug
privileges or run the entire launcher as administrator automatically.

For a connection error before login, inspect `data/logs/redirector.log`. The
redirector records the ClientHello's server name, cipher suites and signature
schemes, then the negotiated TLS version/cipher on success. Failures explicitly
identify whether they occurred before or after ClientHello. These are TLS
metadata, not login credentials or request bodies. Successful launcher probes
do not establish that the game supports the same TLS settings or accepts the
development certificate. An offline-launch configuration alone does not confirm
certificate compatibility. TLS verification and the server's supported protocol
versions are unchanged by these diagnostics.

Automated development checks from the repository root:

```powershell
.\.venv-windows\Scripts\python.exe -m unittest discover -s tools/tests -v
npm.cmd --prefix launcher test
cargo.exe +1.90.0 build --locked --workspace --bins
```

The Python suite exercises the memory API, page-protection cleanup, debug-event
handling/detach (including failures), mission semantics, PE reporting, and combat
selection. It does not substitute for testing real Windows API calls and the game.
