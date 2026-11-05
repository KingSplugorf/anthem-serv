use axum::{
    Json,
    body::Bytes,
    extract::{Path, RawQuery, State},
};
use serde_json::{Value, json};

use crate::Context;

pub const STARTING_EXO_TYPE: i32 = 0;

fn unlockable(name: &Value, guid: &Value) -> Value {
    json!({ "assetName": name, "assetGuid": guid, "assetType": "", "subType": "", "viewed": true })
}

fn cosmetics_unlocked(context: &Context) -> bool {
    context
        .state
        .lock()
        .expect("fichier d'état")
        .cosmetics_unlocked()
}

pub async fn unlocks(State(context): State<Context>) -> Json<Value> {
    let mut unlockables: Vec<Value> = crate::mtx::exo_unlocks(&context)
        .into_iter()
        .map(|unlock| unlockable(&json!(unlock.name), &json!(unlock.guid)))
        .collect();
    if cosmetics_unlocked(&context) {
        unlockables.extend(
            context
                .cosmetics
                .iter()
                .map(|cosmetic| unlockable(&cosmetic["assetName"], &cosmetic["assetGuid"])),
        );
    }
    for (name, guid) in crate::gear::unlockables(&owned_exo_types(&context)) {
        if !unlockables.iter().any(|unlock| unlock["assetGuid"] == guid) {
            unlockables.push(unlockable(&json!(name), &json!(guid)));
        }
    }
    Json(json!({ "totalCount": unlockables.len(), "unlockables": unlockables }))
}

pub async fn editor_cosmetics(State(context): State<Context>) -> Json<Value> {
    Json(json!({ "unlocked": cosmetics_unlocked(&context), "count": context.cosmetics.len() }))
}

pub async fn set_editor_cosmetics(State(context): State<Context>, body: Bytes) -> Json<Value> {
    let request: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    if let Some(unlocked) = request["unlocked"].as_bool() {
        context
            .state
            .lock()
            .expect("fichier d'état")
            .set_cosmetics_unlocked(unlocked);
    }
    editor_cosmetics(State(context)).await
}

pub async fn viewed_unlocks(body: Bytes) -> Json<Value> {
    let request: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    let ids = request
        .as_object()
        .and_then(|fields| {
            fields
                .iter()
                .find(|(key, _)| key.eq_ignore_ascii_case("unlockIds"))
        })
        .and_then(|(_, ids)| ids.as_array());
    let unlocks: Vec<Value> = ids
        .into_iter()
        .flatten()
        .map(|id| json!({ "unlockId": id }))
        .collect();
    Json(json!({ "unlocks": unlocks }))
}

pub(crate) fn base_inventory(context: &Context) -> Vec<Value> {
    let (salvaged, junk, starting_only) = {
        let state = context.state.lock().expect("fichier d'état");
        (
            state.salvaged_items(),
            state.junk_items(),
            state.starting_inventory_only(),
        )
    };
    crate::gear::inventory_items(&owned_exo_types(context), &salvaged, &junk)
        .into_iter()
        .filter(|item| {
            !starting_only || crate::gear::is_starting_gear(item["uid"].as_u64().unwrap_or(0))
        })
        .collect()
}

fn inventory_for(context: &Context, id: Option<&str>) -> Result<Vec<Value>, String> {
    let mut items = base_inventory(context);
    if let Some(id) = id {
        items.extend(crate::progression::items(context, id)?);
    }
    let junk = context.state.lock().expect("state").junk_items();
    for item in &mut items {
        item["isJunk"] = json!(junk.contains(&item["uid"].as_u64().unwrap_or(0)));
    }
    Ok(items)
}

fn inventory_items(context: &Context) -> Vec<Value> {
    inventory_for(context, crate::progression::active_id(context).as_deref()).unwrap_or_else(
        |error| {
            eprintln!("Inventory: {error}");
            base_inventory(context)
        },
    )
}

pub async fn inventory(
    State(context): State<Context>,
    Path(params): Path<std::collections::HashMap<String, String>>,
) -> crate::progression::Reply {
    let id = params
        .get("character")
        .cloned()
        .or_else(|| crate::progression::active_id(&context));
    let items = inventory_for(&context, id.as_deref()).map_err(crate::progression::error)?;
    Ok(Json(json!({
        "totalItemCount": items.len(),
        "items": items,
        "currencyItems": [],
        "accountAttributes": [],
    })))
}

fn field<'a>(request: &'a Value, name: &str) -> &'a Value {
    request
        .as_object()
        .and_then(|fields| {
            fields
                .iter()
                .find(|(key, _)| key.eq_ignore_ascii_case(name))
        })
        .map_or(&Value::Null, |(_, value)| value)
}

fn uids(value: &Value) -> Vec<u64> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_u64)
        .collect()
}

/// InventoryMarkItemsJunkRequest {PersonaId, CharacterId, ItemIds, Items [{Id, IsJunk}]} ; réponse InventoryMarkItemsJunkResponse vide.
pub async fn mark_junk(State(context): State<Context>, body: Bytes) -> Json<Value> {
    let request: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    let states: Vec<(u64, bool)> = field(&request, "items")
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| {
            Some((
                field(item, "id").as_u64()?,
                field(item, "isJunk").as_bool()?,
            ))
        })
        .collect();
    let states = if states.is_empty() {
        uids(field(&request, "itemIds"))
            .into_iter()
            .map(|uid| (uid, true))
            .collect()
    } else {
        states
    };
    let mut state = context.state.lock().expect("fichier d'état");
    for (uid, is_junk) in states {
        state.set_junk(uid, is_junk);
    }
    Json(json!({}))
}

/// InventorySalvageItemsRequest {PersonaId, CharacterId, ItemUIDs, Unlocks, …} ; réponse InventorySalvageItemsResponse {CurrencyItems, Unlocks}.
pub async fn salvage_items(
    State(context): State<Context>,
    Path((_persona, id)): Path<(u64, String)>,
    body: Bytes,
) -> crate::progression::Reply {
    let request: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    let mut ids = uids(field(&request, "itemUIDs"));
    ids.sort_unstable();
    ids.dedup();
    if !ids.is_empty() {
        crate::progression::salvage(&context, &id, format!("native-salvage/{ids:?}"), ids)
            .map_err(crate::progression::error)?;
    }
    Ok(Json(json!({ "currencyItems": [], "unlocks": [] })))
}

pub async fn backpack(
    State(context): State<Context>,
    Path(params): Path<std::collections::HashMap<String, String>>,
) -> crate::progression::Reply {
    let id = params
        .get("character")
        .cloned()
        .or_else(|| crate::progression::active_id(&context));
    Ok(Json(match id {
        Some(id) => {
            crate::progression::backpack(&context, &id).map_err(crate::progression::error)?
        }
        None => json!({ "items": [], "currencyItems": [], "tickets": [] }),
    }))
}

/// InventoryResolveExpeditionBackpackResponse ; le butin de mission n'est pas gardé, rien n'est résolu.
pub async fn resolve_expedition_backpack(
    State(context): State<Context>,
    Path((_persona, id)): Path<(u64, String)>,
) -> crate::progression::Reply {
    Ok(Json(
        crate::progression::resolve(&context, &id).map_err(crate::progression::error)?,
    ))
}

/// ValidateMetaMilestonesResponse.
pub async fn validate_meta_milestones() -> Json<Value> {
    Json(json!({
        "challengeProgress": [],
        "lastUpdatedTimestamp": 0,
        "achievementProgress": [],
        "completedMilestones": [],
        "newActiveMilestones": [],
    }))
}

/// SocialRewardsProgressResponse.
pub async fn social_rewards_progress() -> Json<Value> {
    Json(json!({
        "weeklyXp": 0,
        "currentTier": 0,
        "socialXp": 0,
        "contributors": [],
        "totalCurrencyContribution": 0,
        "friendBonus": 0.0,
        "guildBonus": 0.0,
        "resetTimeUtcEpochSeconds": 0,
        "maxTierXp": 0,
        "currentTierPercentage": 0.0,
        "minTierXp": 0,
    }))
}

/// GuildsGetPersonaGuildResponse sans guilde.
pub async fn persona_guild() -> Json<Value> {
    Json(json!({ "guild": { "id": "" } }))
}

pub async fn challenges() -> Json<Value> {
    Json(json!({
        "challenges": [],
        "medals": [],
        "trackedChallenges": [],
        "trackedChallengeLimit": 0,
    }))
}

pub async fn milestone_progress(RawQuery(query): RawQuery) -> Json<Value> {
    let timestamp: i64 = query
        .as_deref()
        .and_then(|query| crate::form_value(query.as_bytes(), "updatedAfterTimestamp"))
        .and_then(|value| value.parse().ok())
        .unwrap_or(0);
    Json(json!({
        "challengeProgress": [],
        "medalProgress": [],
        "lastUpdatedTimestamp": timestamp,
        "achievementProgress": [],
        "completedMilestones": [],
        "newActiveMilestones": [],
    }))
}

pub async fn leaderboard_definitions() -> Json<Value> {
    Json(json!({ "leaderboards": [] }))
}

pub async fn salvaged_promo_items() -> Json<Value> {
    Json(json!({ "salvagedItems": [] }))
}

fn loadout(context: &Context, exo_type: i32) -> Value {
    let id = loadout_id(exo_type);
    let attachments = context
        .state
        .lock()
        .expect("fichier d'état")
        .loadout_attachments(&id)
        .map(|saved| saved.iter().map(current_attachment).collect())
        .unwrap_or_else(|| crate::gear::attachments(exo_type));
    json!({
        "exoSkinId": "",
        "exoType": exo_type,
        "displayName": "",
        "loadoutId": id,
        "gear": { "attachments": attachments, "gearScore": 0.0 },
    })
}

/// RemoteViewLoadoutDylanOnlineAttachment {Item, AttachedTo, Slot, AttachedToUID, UID, UnlockId, ItemId} de chaque attache du loadout.
pub fn remote_view_items(context: &Context, exo_type: i32) -> Vec<Value> {
    let inventory = inventory_items(context);
    loadout(context, exo_type)["gear"]["attachments"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|attachment| {
            let uid = attachment["itemUID"].as_u64().unwrap_or(0);
            let item = inventory
                .iter()
                .find(|item| uid != 0 && item["uid"] == uid)
                .cloned()
                .unwrap_or_else(|| json!({ "uid": 0 }));
            json!({
                "uid": item["uid"],
                "slot": attachment["slot"],
                "attachedToUID": attachment["attachedToUID"],
                "attachedTo": attachment["attachedTo"],
                "itemId": attachment["itemId"],
                "unlockId": attachment["unlockId"],
                "item": item,
            })
        })
        .collect()
}

fn current_attachment(saved: &Value) -> Value {
    let mut attachment = saved.clone();
    if let Some(uid) = saved["itemUID"].as_u64().filter(|&uid| uid != 0) {
        let current = crate::gear::current_uid(uid);
        attachment["itemUID"] = json!(current);
        attachment["itemId"] = json!(current.to_string());
    }
    attachment
}

/// LoadoutDylanOnlineAttachment complet depuis une attache envoyée par le client ({slot, itemUID?, unlockId}).
fn saved_attachment(sent: &Value) -> Value {
    let uid = sent["itemUID"].as_u64().unwrap_or(0);
    json!({
        "itemUID": uid,
        "attachedToUID": sent["attachedToUID"].as_u64().unwrap_or(0),
        "itemId": if uid == 0 { String::new() } else { uid.to_string() },
        "attachedTo": sent["attachedTo"].as_str().unwrap_or_default(),
        "slot": sent["slot"],
        "unlockId": sent["unlockId"].as_str().unwrap_or_default(),
    })
}

/// LoadoutUpdateLoadoutRequest {…, LoadoutId, Gear} ; réponse LoadoutUpdateLoadoutResponse {LoadoutId}.
pub async fn update_loadout(
    State(context): State<Context>,
    Path((_persona, _character, loadout)): Path<(u64, String, String)>,
    body: Bytes,
) -> Json<Value> {
    let request: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    if let Some(sent) = request["gear"]["attachments"].as_array() {
        context
            .state
            .lock()
            .expect("fichier d'état")
            .set_loadout_attachments(&loadout, sent.iter().map(saved_attachment).collect());
    }
    Json(json!({ "loadoutId": loadout }))
}

fn starting_loadout_id() -> String {
    loadout_id(STARTING_EXO_TYPE)
}

fn loadout_id(exo_type: i32) -> String {
    (exo_type + 1).to_string()
}

pub(crate) fn owned_exo_types(context: &Context) -> Vec<i32> {
    let mut exo_types = vec![STARTING_EXO_TYPE];
    exo_types.extend(crate::mtx::bought_exo_types(context));
    exo_types
}

/// Loadout par défaut choisi à la Forge parmi les javelins possédés (identifiant, javelin), Ranger sinon.
pub fn default_loadout(context: &Context) -> (String, i32) {
    let chosen = context
        .state
        .lock()
        .expect("fichier d'état")
        .default_loadout();
    owned_exo_types(context)
        .into_iter()
        .map(|exo_type| (loadout_id(exo_type), exo_type))
        .find(|(id, _)| Some(id) == chosen.as_ref())
        .unwrap_or_else(|| (starting_loadout_id(), STARTING_EXO_TYPE))
}

pub async fn loadouts(State(context): State<Context>) -> Json<Value> {
    let (default, _) = default_loadout(&context);
    Json(json!({
        "loadouts": owned_exo_types(&context)
            .into_iter()
            .map(|exo_type| loadout(&context, exo_type))
            .collect::<Vec<_>>(),
        "defaultLoadout": default,
    }))
}

pub async fn set_default_loadout(
    State(context): State<Context>,
    Path((_persona, _character, loadout)): Path<(u64, String, String)>,
) -> Json<Value> {
    context
        .state
        .lock()
        .expect("fichier d'état")
        .set_default_loadout(&loadout);
    Json(json!({}))
}

pub async fn active_expedition() -> Json<Value> {
    Json(json!({ "expeditionInfo": {} }))
}

pub async fn create_claim(Path((persona, character)): Path<(u64, String)>) -> Json<Value> {
    Json(json!({ "claimId": format!("{persona}-{character}") }))
}

pub async fn claim(State(context): State<Context>) -> Json<Value> {
    Json(json!({ "personaId": context.persona_id }))
}
