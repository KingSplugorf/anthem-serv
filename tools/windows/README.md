# Native Windows development port (experimental)

This is the first native Windows port of the **local, single-game** workflow.
It does not use WSL, Wine, Proton, Bash, or GDB on Windows. It is developer
setup, not a packaged end-user installer. On-device reports have confirmed native
Windows CA registration and the game's TLS/HTTP request to the redirector for
the inspected build. After the Blaze hostname correction, the user reported
reaching Fort Tarsis and moving around the hub, with short menu-related pauses.
Mission launch/combat and broader Windows compatibility still need validation.
The automated tests run on Linux with simulated Win32 APIs and real Linux
service requests.

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
The redirector advertises Blaze's hostname as `spring18.gosredirector.ea.com`,
matching the development certificate, with resolved IP `127.0.0.1` and port
`10041`. The existing hosts entry resolves that name to the same machine for
both TLS services. Advertising `127.0.0.1` as the hostname instead can fail the
game's certificate hostname check even after the CA was imported. The launcher's
probes use the certificate DNS name too, so they did not catch the old advertised
hostname mismatch.
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
It also includes bounded ASCII/UTF-16 string excerpts related to TLS libraries
and certificate handling, with file offsets. These are static clues only and do
not establish which TLS stack is active or whether the development certificate
is trusted. The scan memory-maps the executable read-only and does not load it
as a program; large files may take a while to scan.
It contains no full executable or game assets. Packed executable code may have
no meaningful bytes until runtime, so matching this report alone does not
establish runtime compatibility.

When investigating the game-specific certificate loader, a second read-only tool
can use the existing report to export bounded TLS-related code references:

```powershell
py -3 tools/windows/inspect_tls_refs.py 'C:\Games\Anthem\Anthem.exe' --layout Anthem-tls-layout.json --output Anthem-tls-code.json
```

It verifies the executable's SHA-256 against the report, checks the selected
string bytes, and includes small code excerpts at candidate references and paired
boolean wrappers. It does not execute code, attach to the game, or change trust.
Candidate addresses are not verified API identities and must not be called or
patched based on this report alone. Share `Anthem-tls-code.json` for analysis.

For the previously inspected executable (SHA-256
`63cbc85f17ebd5d9ede8d72cef07eff40945bf5000604b52d482812cbbcd0338`),
the CA-loader investigation can follow PEM marker pointers through data tables,
RIP-relative code references, and four levels of candidate callers, including
direct jump stubs without unwind records:

```powershell
py -3 tools/windows/inspect_tls_refs.py 'E:\EA Games\Anthem\Anthem.exe' --ca-loader --output Anthem-ca-code.json
```

Use the actual executable path. This mode needs no earlier JSON report, Python
packages, running game, or administrator access. It checks the exact executable
hash, opens it read-only, and exports bounded function excerpts and reference
counts. It rejects an output path that names the executable or input layout.
Share `Anthem-ca-code.json`; the original executable, DLLs, and certificates are
unchanged. A byte-pattern reference can occur inside an instruction operand, so
the reported candidates require disassembly and runtime verification.

An opt-in experimental CA-registration path is available for this exact build;
see [Experimental local CA registration](#experimental-local-ca-registration).
It preserves certificate verification and imports only the public development
certificate. The function identity is based on the inspected machine code;
on-device execution has returned a positive import count and a clean debugger
detach. Login and gameplay still need validation. Static CA-loading code alone
does not prove the connection failure is caused by certificate trust.

The first report located a PEM marker parser at `0x147d65270`, its marker table
at `0x143732520`, and an original entry stub at `0x140823370` that jumps to the
parser. The parser locates BEGIN/END delimiters and returns the certificate body;
it does not register a trusted CA. An earlier inspector stopped at the entry
stub because it had no unwind record. The current inspector follows its callers
without treating the stub as a verified public CA-loading API.

To examine the surrounding TLS implementation without repeatedly requesting
individual function excerpts, export the optional code bundle:

```powershell
py -3 tools/windows/inspect_tls_refs.py 'E:\EA Games\Anthem\Anthem.exe' --ca-code-bundle --output Anthem-ca-bundle.json
```

This mode implies `--ca-loader` and includes two fixed 512 KiB executable file
regions around the observed TLS code and original entries, relevant unwind
ranges, the PEM marker table, and bounded static indirect-call pointer values.
It adds 1 MiB of raw code, represented as hex in the JSON. It does not attach to
the game, read process heap/session data, or call functions. Share
`Anthem-ca-bundle.json` for offline analysis; keep the existing executable,
certificates and earlier report. Import pointers may be unresolved in a file,
and static code still needs runtime verification before a loader can call it.

For private GitHub repositories, download updated tools through GitHub while
signed in. An unauthenticated raw-file request can return 404 even when the file
exists; a successful download in the authenticated cloud session does not
establish that an anonymous PowerShell request will work.

## Experimental local CA registration

The supplied TLS bundle shows a two-argument CA importer at `0x147d6abc0`
(original entry `0x140826440`). It parses PEM/DER, verifies the certificate with
the self-signed-CA path, and copies public identity/key data into the CA list at
`0x1442f29c0`. A game HTTP initializer calls this importer for embedded PEM
certificates. The inspected TLS connect entry at `0x147d8fe90` has integer and
pointer arguments following the Microsoft x64 ABI. These entries were identified
statically; a subsequent runtime report confirmed the CA import before the
redirector connection.

`tools/windows_ca_loader.py` integrates with the native debugger to attempt
registration before a secure local connection. It checks the executable SHA-256,
the in-memory importer/verification/list-addition code hashes, the entry stubs,
and the TLS connect prologue. Unexpected bytes abort before allocation. Once
TLS global state is initialized, a hardware breakpoint redirects one existing
game thread through a small call stub; it does not create a worker thread to
call into the game's allocator. The trigger accepts only the redirector name,
localhost or `127.0.0.1`, on ports 42230/10041. Other connections continue normally.

The stub preserves volatile integer registers, flags, stack alignment, shadow
space, and legacy floating-point/SSE state, calls the importer with only
`data/certs/test.crt`, records its signed result, then resumes the original TLS
connect entry. Certificate data/result storage remain read/write; code becomes
execute/read after writing. Once entered, the small allocation remains until the
game exits so an interrupted detach cannot free a possible return target. The
game's certificate, hostname and signature checks remain enabled. Game files,
the server certificate/key pair and Windows trust stores are not changed.

Close existing game/launcher sessions, update the repository, then run from its
root in PowerShell:

```powershell
Remove-Item Env:ANTHEM_TLS_DIAGNOSTICS -ErrorAction SilentlyContinue
Remove-Item Env:ANTHEM_SKIP_PATCHES -ErrorAction SilentlyContinue
$env:ANTHEM_LOAD_LOCAL_CA = '1'
try { npm.cmd --prefix .\launcher start }
finally { Remove-Item Env:ANTHEM_LOAD_LOCAL_CA -ErrorAction SilentlyContinue }
```

Click Play. If the first attempt already failed, wait for `Win32 local CA loader
ready`, then retry connecting in the game. The supervisor allows 120 seconds for
this phase and starts mission patches only after the importer returned a positive count
and the debugger detached. A positive native result counts certificates added;
zero means no new certificate was added (either a duplicate or an allocation
failure) and is treated as unconfirmed; a negative result is rejection/failure.

An on-device report for this build recorded `native_result: 1`, `loaded: true`,
`completed: true`, and `detached: true` before connecting to the redirector on
42230. Server logs then showed the game's TLS 1.2 connection and real
`anthem-1-pc` redirector request. Blaze on 10041 still received the client's
`CertificateUnknown` alert while it was advertised by IP. The inspected receive
certificate code sends alert 46 from its hostname-match failure path, consistent
with the launcher/certificate name mismatch corrected above. The user then
reported successful entry into the Fort Tarsis hub after that hostname change.

The report is `data/local_ca_PID.json`; the log is
`data/logs/windows_ca_loader.log`. Reports include the public certificate's DER
SHA-256, observed host/port metadata, native result and detach status. They do
not include the private key, PEM contents or process heap dumps. If the connect
breakpoint never fires, the current TLS-path hypothesis remains unconfirmed;
do not treat a ready report as successful registration. A successful import
alone also does not establish that login or mission startup works.

The generated call stub has a native Linux x64 regression using synthetic
Microsoft-ABI functions that clobber volatile registers/SSE state. The broader
suite checks rejection, scoping, allocation protection, readiness correlation,
and debugger cleanup with simulated Win32 APIs. The runtime report confirms
successful registration and user-reported hub entry on one Windows setup;
mission gameplay and broader Windows compatibility are still unverified. Leave the opt-in flag unset to retain
the normal launch workflow.

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
  API with one connection breakpoint while idle in the hub. Once a mission is
  selected, the adapter arms the two dependent execution breakpoints and the
  four-byte PlayMode write watchpoint on live threads before resuming. Joining
  or returning to an idle state removes those dependent breakpoints. Newly
  created threads receive the current configuration. The adapter refuses to
  replace debug registers that differ from its own configuration. The supervisor
  requests a graceful detach through stdin after mission startup. The adapter
  restores original debug registers, continues threads, and passes unrelated
  exceptions back to the game. If the parent exits, EOF requests detachment.
- `tools/default_point.py` makes two streaming passes over bounded memory chunks
  when locating quest launch points. It retains addresses instead of copying
  the entire writable game heap into Python memory. This trades an extra remote
  read pass for substantially lower peak memory use during startup. Reducing
  idle debugger events and scan memory use does not by itself establish the
  cause of menu pauses; Windows performance still needs a comparison run.
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

The next checks on an actual Windows machine with the supported game are menu
responsiveness, starting a mission, mission/combat behavior, and cleanup. An
attach failure due to Windows access permissions should be diagnosed
with the game's integrity level and launcher logs; setup does not enable debug
privileges or run the entire launcher as administrator automatically.

The bundled `data/saves/state.json` is a development preset with
`niveau_pilote: 30` and one pilot named `Joueur 1`. Windows copies that template
into its per-user runtime save only on first use. An empty state instead defaults
to level 1; changing the repository template does not change an existing runtime
save. To inspect what BIGS currently serves, use a separate PowerShell window
while the game/launcher are running:

```powershell
curl.exe --connect-timeout 3 --max-time 8 -i http://127.0.0.1:8080/api/character/1000/characters
```

The character response includes `name`, `lancerName`, and `level`. If the in-game
name is blank, compare those fields before changing saved progress. The terminal
running `npm start` remains occupied until the launcher closes, so diagnostic
commands typed into that terminal may not execute.
The normal service initializes its runtime character name from `JOUEUR_NOM`
(default `Joueur`); the template's stored pilot name is used when loading stored
pilots in new-game mode. One live response confirmed `name`/`lancerName` as
`Joueur` and level 30 despite a user-reported unnamed UI label, so that label's
source still needs identifying.
The character-list route has no trailing slash. An unknown route currently
returns HTTP 200 with `{}`, which is not a character-list response.

### Fresh level-1 pilot in Fort Tarsis

Close the game and launcher before running this from the repository root:

```powershell
.\tools\windows\play-fresh.ps1 -PilotName 'KingSplugorf' -Gender Male -ProfileId kingsplugorf -LoadLocalCa
```

This rebuilds BIGS and opens the normal launcher. Click **Play**. `-LoadLocalCa`
enables the existing experimental local-certificate importer for the supported
game build; omit it if your executable already accepts the server certificate.
The script keeps the existing TLS hostname and service ports. If a complete
`bin/` or release service set takes priority, it backs up that set's BIGS executable
before installing the new build there.

The fresh slot starts with a saved male pilot named `KingSplugorf`, level 1,
zero XP, the Ranger and its starter equipment. The development Forge catalog is
not granted to this slot. It skips the intro using the existing tutorial-complete
persistence and attempts to enter Fort Tarsis. Name and gender are served by the
API; the model's appearance and the hub/quest flow still need validation in the
game. The menu header's `Loading…` placeholder is a separate unresolved issue.

Saves go under the launcher's per-user directory at
`server/profiles/fresh-kingsplugorf/state.json`; the launcher prints the full path.
The old `server/state.json` and repository preset are preserved. Run the same
command to resume this slot. To create another, choose a different `-ProfileId`.
Name and gender options only initialize a slot; they do not overwrite an existing
pilot. After closing this launcher, normal `npm start` uses the development save
unless profile environment variables were already set in the calling shell.

This is a fresh-profile foundation, **not completed retail progression**. Pilot
level is still manually set; XP is the minimum threshold for that level. Mission
loot is not retained, currency rewards are placeholders, the recipe catalog is
empty, and crafting transactions are not implemented. A full progression loop
requires mission reward/loot handling, persistent XP and currencies, then recipe
costs, unlock requirements and inventory transactions. Resetting the save alone
does not supply those systems.

For manual launches, `ANTHEM_PROFILE=fresh`, `ANTHEM_PROFILE_ID`,
`ANTHEM_PILOT_NAME` and `ANTHEM_PILOT_GENDER` select the slot. The existing
experimental intro mode is also available as `ANTHEM_PROFILE=tutorial`, with a
separate save and no precreated pilot; its intro flow is unvalidated.

For a connection error before login, inspect `data/logs/redirector.log`. The
redirector records the ClientHello's server name, cipher suites and signature
schemes, then the negotiated TLS version/cipher on success. Failures explicitly
identify whether they occurred before or after ClientHello. These are TLS
metadata, not login credentials or request bodies. Successful launcher probes
do not establish that the game supports the same TLS settings or accepts the
development certificate. An offline-launch configuration alone does not confirm
certificate compatibility. TLS verification and the server's supported protocol
versions are unchanged by these diagnostics.

Before attributing a connection failure to the game or its certificate handling,
isolate the external patcher/debugger with a connection-only control run. Close
existing sessions and start from the repository root:

```powershell
Remove-Item Env:ANTHEM_TLS_DIAGNOSTICS -ErrorAction SilentlyContinue
$env:ANTHEM_SKIP_PATCHES = '1'
try { npm.cmd --prefix .\launcher start }
finally { Remove-Item Env:ANTHEM_SKIP_PATCHES -ErrorAction SilentlyContinue }
```

Click Play and confirm the launcher reports a connection test without external
memory patches or debugger. The normal service readiness checks and the same
configured executable/DLL installation remain in use. This mode does not prepare
mission launching and is not a direct hub/mission-loading implementation. Use it
only to compare the game's login connection with and without the external
patching layer; it makes no certificate-store changes.

### Passive client-certificate diagnostic mode

For the inspected executable SHA-256
`63cbc85f17ebd5d9ede8d72cef07eff40945bf5000604b52d482812cbbcd0338`,
the native debugger can observe the game's certificate-error logging instruction.
It checks both the file hash and the in-memory instruction/format bytes before
attaching, opens game memory read-only, and installs one execution breakpoint.
It records the raw error code, hostname and certificate identity fields passed
to that logging path. It does not call a CA loader or change any certificate
validation result. Reference error labels are from a reference DirtySDK header
and are not a substitute for the recorded raw code.

Close any existing game/launcher session. From the repository root:

```powershell
$env:ANTHEM_TLS_DIAGNOSTICS = '1'
try { npm.cmd --prefix .\launcher start }
finally { Remove-Item Env:ANTHEM_TLS_DIAGNOSTICS -ErrorAction SilentlyContinue }
```

Click Play. Wait for `Win32 TLS diagnostics ready` in the launcher, then retry the
connection in the game. Close the launcher and share the newest
`data/tls_client_PID.json`. Each report identifies its diagnostic run, so a stale
PID report cannot establish readiness. The report also contains a fixed 24 KiB
code excerpt around the previously observed ProtoSSL API call region to help
identify the CA-loading entry point; heap/session buffers and TLS private keys
are not captured. The normal mission debugger hooks are inactive during this
diagnostic session. Remove the environment flag (or use a fresh PowerShell
window) before testing missions normally.

Automated development checks from the repository root:

```powershell
.\.venv-windows\Scripts\python.exe -m unittest discover -s tools/tests -v
npm.cmd --prefix launcher test
cargo.exe +1.90.0 build --locked --workspace --bins
```

The Python suite exercises the memory API, page-protection cleanup, debug-event
handling/detach (including failures), mission semantics, PE reporting, and combat
selection. It does not substitute for testing real Windows API calls and the game.
