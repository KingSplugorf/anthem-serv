"""Build the source-only Windows lifecycle update manifest before committing.

Run from any directory: python3 tools/windows/build_lifecycle_manifest.py
Never includes game assets, credentials, saves, certificates or node_modules.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = '''
.gitignore
Cargo.toml
Cargo.lock
crates/blaze/Cargo.toml
crates/blaze/src/lib.rs
crates/blaze/src/main.rs
crates/blaze/src/components/game_manager.rs
crates/blaze/src/components/game_manager/local_lifecycle.rs
crates/bigs/src/lib.rs
crates/bigs/src/main.rs
crates/bigs/src/state.rs
crates/bigs/src/startup.rs
crates/bigs/src/characters.rs
launcher/main.js
launcher/main_windows.js
launcher/windows_profiles.js
launcher/windows_runtime.js
launcher/hosts.js
launcher/game_conf.js
launcher/modes.js
launcher/mission_status.js
launcher/renderer.js
launcher/preload.js
launcher/index.html
tools/auto_patches.py
tools/mission_hooks.py
tools/hub_game.py
tools/patch_finrachaos.py
tools/mission_lifecycle.py
tools/mission_lease.py
tools/mission_jobs.py
tools/mission_supervisor.py
tools/mission_workers.py
tools/mission_child.py
tools/windows_debugger.py
tools/windows_ca_loader.py
tools/tls_client_diagnostics.py
tools/windows_memory.py
tools/process_memory.py
tools/default_point.py
tools/missions_inproc.py
tools/launch_from_hub.py
tools/end_expedition.py
tools/combat_patches/ai/navigation.py
tools/combat_patches/ai/player_position.py
tools/combat_patches/ai/shots.py
tools/combat_patches/server_damage.py
tools/windows/play-profile.ps1
tools/windows/play-fresh.ps1
tools/windows/inspect_mission.py
tools/windows/inspect_expedition.py
tools/tests/test_direct_python.py
tools/windows/update-lifecycle.ps1
tools/windows/build_lifecycle_manifest.py
tools/windows/README.md
'''.split()


def build():
    return {'schema': 1, 'purpose': 'native-expedition-lifecycle', 'files': [
        {'path': name, 'sha256': hashlib.sha256((ROOT / name).read_bytes()).hexdigest()}
        for name in FILES]}


if __name__ == '__main__':
    destination = ROOT / 'tools/windows/lifecycle-files.json'
    destination.write_text(json.dumps(build(), indent=2) + '\n', encoding='utf-8')
    print(f'{len(FILES)} source files: {destination}')
