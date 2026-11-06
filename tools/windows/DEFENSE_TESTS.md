# Independent defense experiments

These changes are separate: the shield flag experiment writes a small hook only
when explicitly installed; HUD and pickup recordings are read-only and install
no gameplay fixes. No new HUD, healing, flight, scaling or Flashfire fix is bundled.

## Shield/resistance experiment

The supplied build's generic-damage function sets flags 0x01 and 0x10. Its shield
filter rejects 0x01; its damage computation takes an early direct-health branch
for 0x10. The emulator's impact bridge calls this generic function. The opt-in
hook clears ONLY those two bits for the exact return address of that bridge,
retains the team-check bit 0x20 and all other bits, and resumes native computation.
Other callers retain the original flag operation. No shield/health values,
resistance tables or armor settings are overwritten.

This is an experimental correction to routing, not proof of full armor support.
The bridge still constructs generic damage without the original projectile's
complete hit-part/damage-type metadata. An Anrisaur head plate may therefore
remain wrong even with native filtering restored. A zero displayed damage number
is not proof that the bridge received a valid zero-damage part hit.

With the game in a mission, run `shield_flags.py PID poser` to opt in,
`shield_flags.py PID etat` for status, or `shield_flags.py PID retirer` to restore.
The installer checks the exact native prologue/site and installed impact blob
before writing. Restart also removes this in-memory experiment. Updating source
alone does not enable it. It is intentionally not included in automatic patches.
Do not reload/update other memory patch blobs during the test session.

Record `record-defense.ps1 -Kind Shield` before and after installing. Use ordinary
bullets against a blue-bar enemy, then separately compare vulnerable and armored
Anrisaur body parts with the same weapon. Report shield, health, displayed damage,
weapon, enemy and hit part for each case. If health falls while shields stay full,
or an immune part takes damage, undo the experiment and keep both reports.

## HUD diagnosis — separate recording

Use `record-defense.ps1 -Kind Hud`. Avoid pickups. Take one environmental hit.
Compare server health, client raw state and visible bars. Earlier captures showed
matching client/server health values despite absent bars; that supports a missing
HUD activation/OnDamage event investigation rather than force-writing health.
The raw client fields are evidence, not a fully validated client layout.
Possible fixes require identifying which damage notification/pilot HUD state is
missing; this update does not force HUD bits or fabricate notification events.

## Pickup diagnosis — separate recording

Use `record-defense.ps1 -Kind Pickup`. Start injured, wait five seconds, collect
one pickup, wait, and avoid further damage. Report when collection occurred.
Sound/disappearance alone does not establish healing. Server health rising with
no HUD change identifies a display path; no server increase points toward pickup
authority, heal dispatch or a missing server setter. A rise followed by a drop
requires checking subsequent damage/synchronization before changing healing.
Do not run `soigner` during this recording: it heals directly and would mask the
pickup path. No heal amount, pickup cooldown or health setter is patched here.

The latest inspector has separate budgets for code export and the 30-second
recording. `recording_budget_limited` and the last elapsed sample must still be
checked before drawing conclusions about events outside the recorded interval.
