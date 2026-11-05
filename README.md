This project does not encourage piracy; it exists to preserve video game content. You must own the game to use it.
I will not provide support for troubleshooting the project should any bugs arise. Many features remain unfinished or uncorrected. You are free to build upon, use, or improve this project as you see fit.
I do not explain how to patch the game either: `Anthem.exe` must already accept the server's certificate.

This project was made to learn and understand how things work. It contains no code from the original game.


# Anthem, private server: what you need to play

Native Windows development support is experimental. See [Windows setup and port status](tools/windows/README.md) for prerequisites, local startup, and remaining validation.

anthem-serveur: launch and play.

## Certificate, once

The redirector and Blaze encrypt the game's connection with a self-signed certificate, accepted by `Anthem.exe` once patched (not covered here). Generate it in `data/certs/` (again when it expires):

```
mkdir -p data/certs && openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
  -keyout data/certs/test.key -out data/certs/test.crt \
  -subj "/CN=spring18.gosredirector.ea.com" -addext "subjectAltName=DNS:spring18.gosredirector.ea.com"
```

## Launching

| Launcher | Command |
|---|---|
| Simplified, one game | `tools/play.sh` |
| With a window | `cd lanceur && npm start` |

Both compile the services (Rust, `cargo`), point `/etc/hosts` to this PC (administrator password required), start the services and patches, then the game. Everything stops when the game closes.

Game folder: choose it in the launcher (« Change » button), or set it in `game.conf` at the root, optional, with paths relative to this folder or to `~`:

```
JEU_EXE=~/Games/Anthem/Anthem.exe
PROTON=~/.config/heroic/tools/proton/GE-Proton-latest
PREFIXE_ANTHEM=~/Games/Heroic/Prefixes/default/Anthem
```

These are the default values (Heroic).

## Useful tools

| Tool | Command | Effect |
|---|---|---|
| Weapon damage | `python3 -I tools/damage_editor.py`, then http://127.0.0.1:8091 | sets the damage served to the game, applied the next time the game is launched |
| Save | `python3 -I tools/save_editor.py`, then http://127.0.0.1:8090 (services running) | quests, javelins, level, cosmetics |
| Invulnerability | `python3 -I tools/combat_patches/health/damage.py PID invulnerable oui` (or `non`) | the player no longer takes damage |
| Damage status | `python3 -I tools/combat_patches/health/server_health.py PID etat` | shots counted, last damage |

`PID`: the game's PID, `pgrep -f '^X:.*Anthem\.exe'`.

Logs are in `data/logs/`, saves in `data/saves/`.
