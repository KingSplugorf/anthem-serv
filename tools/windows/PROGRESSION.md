# XP, earned equipment and the local crafting workbench

With your selected save running in the launcher (**click Play** so BIGS is ready),
open **http://127.0.0.1:8080/progression**. This page shows exact XP, level progress,
saved materials, earned equipment and starter recipes. Crafting and salvage use
the inventory served to the game. Refresh after completing a quest; reconnect
the game if it has cached an older level or inventory. The currently running
launcher profile determines which save is edited.

## Initial rules

- The original 30-level XP thresholds are used, including partial XP and the
  270,850 XP cap. The active pilot's legacy level stays synchronized for the
  launcher and older save-editor clients.
- A first completion of one of 74 known non-tutorial mission quest definitions
  grants **1,000 XP, one compatible common equipment instance at the current
  pilot level, 10 parts and 3 common embers**. These are local development
  rewards, not recovered official reward values.
- Rewards require a persistence commit with the quest's final completed and
  mission-completed flags (PropertyId 1, bits 2 and 8). Ending, abandoning or
  resolving an empty expedition does not grant anything by itself. Native
  completion reporting still needs an on-device check.
- Duplicate commits, restarts and resetting/recompleting a rewarded quest do
  not generate another first-completion reward. Previously completed saves,
  tutorial skips and changes made through the supplied save editor do not
  generate retroactive rewards.
- Starter recipes use known compatible equipment blueprints. Each costs
  **10 parts + 2 common embers** and creates a separate common item at the
  pilot's current level.
- Salvage grants **2 + twice the rarity** parts and one ember of that rarity.
  Starting, equipped and unknown items cannot be salvaged. Batches are atomic.
  Native salvage and workbench salvage share balances and removed-item records.
- The existing starting javelin token is retained, with additional tokens at
  levels 8, 16 and 26. Concurrent purchases cannot overspend the balance.
- Earned equipment has persistent UIDs starting at 1,000,000,000, preserved
  through inventory, loadouts, remote cards and restarts. The earned-item and
  backpack limit is 1,000 per pilot. Full inventories defer quest loot to a
  mailbox instead of blocking quest/XP saving; salvage items, then use
  **Claim pending loot**.

## Save compatibility

Launcher profiles retain separate state files. The new `progression` map stores
XP, earned items, materials, backpack contents and receipts by character ID.
New pilots start at zero XP. Existing pilots without this map retain the XP floor
of their saved `niveau_pilote`. Exact XP and derived levels are returned by both
character and player-card APIs.

Original saves, pilot identity, development-catalog equipment, loadouts,
cosmetics and unrelated top-level JSON fields are retained. Legacy campaign
persistence and loadout selection still follow the emulator's existing active
pilot/profile behavior; switching independent campaigns inside one profile is
not implemented. Use separate launcher profiles for independent playthroughs.

Progression transactions save a complete candidate before publishing live state.
A disk-write failure rolls back rewards, costs and receipts together. Damaged
saves and invalid progression records are rejected rather than silently reset.
Workbench transaction IDs survive a lost response or page reload so retries do
not debit twice. Project updates preserve runtime saves and certificates.

## Remaining native integration

**Craft through the workbench for now.** Native Forge recipe advertisement stays
empty until its DTO and request behavior are verified. Automatic ground-drop
ticket ingestion, kill XP, repeatable mission XP, freeplay encounter rewards,
original loot probabilities, inscriptions and full blueprint progression are
not implemented. This is not yet a complete campaign/progression loop, and it
does not change client memory patches.

The advertised `bigs-butin-rarete-1` through `-6` tickets have a transactional
staging adapter, rarity/level checks, saved backpack contents and native-shaped
backpack resolution. The rarity-0 ticket is not interpreted as equipment.
This adapter does not establish where native pickups are sent; request capture
is required before wiring them. The game supplies localized equipment names in
its Forge; the workbench shows saved IDs to identify instances.

## Local API

All routes use the currently running BIGS profile. Unknown pilot IDs are rejected
by the workbench API. Operation IDs are nonempty strings up to 128 bytes;
reusing one for a different operation returns 409.

| Route | Purpose / JSON body |
|---|---|
| `GET /local/progression` | Pilots, known completion definitions and reward rules |
| `GET /local/progression/characters/{id}` | XP, levels, materials, items, recipes and receipts |
| `POST .../{id}/craft` | `{transactionId: string, recipeId: string}` |
| `POST .../{id}/salvage` | `{transactionId: string, uids: [integer]}` |
| `POST .../{id}/tickets` | Adapter: `{transactionId: string, ticket: string}` |
| `POST .../{id}/claim` | Claim staged backpack items and available mailbox loot |

Crafting and ticket staging do not grant XP. Native inventory/backpack routes
retain their existing response shapes.

## Verification

Run `cargo test -p bigs` for rewards, retries, restart/profile compatibility,
level caps, equipped-item protection, wallet concurrency, full inventories,
malformed saves and disk-write rollback. Windows compilation is checked with
`cargo check -p bigs --target x86_64-pc-windows-msvc --locked`; this does not
claim native gameplay validation.

The optional browser test uses `playwright-core` and Chromium. Build BIGS, then
run `node crates/bigs/tests/workbench.browser.cjs` with the package on `NODE_PATH`
and optionally `CHROMIUM_PATH`/`BIGS_TEST_BINARY` configured. It starts an isolated
temporary profile and checks craft retries after lost responses/page reloads,
salvage, safe pilot-name rendering and an empty character list.
