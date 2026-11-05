# Full introduction on native Windows (experimental)

Close the game before updating. In the launcher select **Full introduction
(experimental)** and click **New character**, then **Play**. Every click creates
an independent empty slot. Existing level-30 and Fort Tarsis saves remain in the
profile selector. To keep the previous creator-to-hub path, select **Fort Tarsis —
skip the tutorials** before creating the slot.

Wait for **Win32 mission debugger** readiness before continuing from the game's
title screen. The game controls its original introduction, character creation,
cinematics and tutorial transitions. No predefined name, face or gender is
inserted. Explicit creation and later customization requests are saved. Shared
FirstBoot, accessibility and graphics settings are not reset.

The full-introduction slot does not inject tutorial-completed flags. Only real
persistence commits from the game advance them. Both tutorial completions can
receive the local progression system's one-time rewards; these are local balance
rules, not a reconstruction of the official reward table. Restarting the same
slot retains the pilot, completion flags, XP and other saved persistence. This
cannot promise resuming at an unsaved mid-mission checkpoint.

## What changed

- The selected profile environment reaches all services, the game and patcher.
- The debugger attaches before expensive mission metadata scans.
- Direct native startup for the known Heart of Rage and Shaper Ruin quests can
  start scoped combat workers without a Blaze matchmaking event.
- Native tutorial startup retains its own level and scripted start/checkpoint.
  Level transitions revoke the previous worker's memory-write lease.
- `/local/onboarding` reports the saved creation/tutorial flags without changing
  them. The normal mission diagnostic includes this report and the last observed
  native startup.

The automated tests exercise save persistence, rewards, lease transitions and
simulated Windows breakpoint handling. **The complete cutscene → tutorials →
creator → Fort Tarsis sequence has not been verified in the native Windows game.**
Other missing scripted mission interactions may still block progress. Unknown
startup quest IDs are recorded for diagnosis and never guessed or forced into a
tutorial. Existing hub saves are not converted into new-game saves.

## If the sequence gets stuck

Keep one game running at the failure and run this from the repository root:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $gameProcessList = @(Get-Process -Name Anthem -ErrorAction SilentlyContinue)
    if ($gameProcessList.Count -ne 1) { throw 'Keep one Anthem game running.' }
    py -3 .\tools\windows\inspect_mission.py --pid $gameProcessList[0].Id
}
```

Share the generated mission-diagnostics JSON and the visible objective/screen.
If the title screen was continued before debugger readiness, restart that same
slot and wait before continuing; do not reset the save or mark quests complete.
