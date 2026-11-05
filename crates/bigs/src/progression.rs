//! Local progression rules. Native crafting DTOs and repeatable/kill XP are not
//! inferred: the workbench is explicit, and only known first mission completions
//! from persistence can award automatic rewards.
use std::collections::{BTreeMap, BTreeSet};
use std::sync::OnceLock;

use axum::{
    Json,
    body::Bytes,
    extract::{Path, State},
    http::StatusCode,
    response::{Html, IntoResponse, Response},
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use crate::{Context, characters, forge_items::FORGE_ITEMS, gear, state};

pub(crate) const FIRST_EARNED_UID: u64 = 1_000_000_000;
const MAX_UID: u64 = (1 << 53) - 1;
const MAX_ITEMS: usize = 1000;
pub(crate) const MAX_XP: u64 = 270850;
const QUEST_XP: u64 = 1000;

#[derive(Clone, Serialize, Deserialize)]
struct Record {
    schema: u32,
    xp: u64,
    #[serde(default)]
    items: Vec<Value>,
    #[serde(default)]
    backpack: Vec<Value>,
    #[serde(default)]
    mailbox: Vec<Value>,
    #[serde(default)]
    materials: BTreeMap<String, u64>,
    #[serde(default)]
    completed: BTreeSet<u64>,
    #[serde(default)]
    receipts: BTreeMap<String, Value>,
}

pub(crate) struct Error(StatusCode, String);
pub(crate) type Reply = Result<Json<Value>, Error>;
impl IntoResponse for Error {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error":self.1}))).into_response()
    }
}
pub(crate) fn error(message: String) -> Error {
    let status = if message.starts_with("Save failed") || message.starts_with("Invalid saved") {
        StatusCode::INTERNAL_SERVER_ERROR
    } else if message.contains("transactionId")
        || message.contains("Insufficient")
        || message.contains("full")
    {
        StatusCode::CONFLICT
    } else {
        StatusCode::BAD_REQUEST
    };
    Error(status, message)
}
fn body(bytes: &Bytes) -> Result<Value, Error> {
    serde_json::from_slice(bytes)
        .map_err(|_| Error(StatusCode::BAD_REQUEST, "Expected JSON object".into()))
}
pub(crate) fn id_text(id: &Value) -> String {
    id.as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| id.to_string())
}
pub(crate) fn active_id(context: &Context) -> Option<String> {
    context
        .characters
        .lock()
        .expect("characters")
        .first()
        .map(|pilot| id_text(&pilot.id))
}
fn check_character(context: &Context, id: &str) -> Result<(), Error> {
    if context
        .characters
        .lock()
        .expect("characters")
        .iter()
        .any(|pilot| id_text(&pilot.id) == id)
    {
        Ok(())
    } else {
        Err(Error(StatusCode::NOT_FOUND, "Unknown pilot".into()))
    }
}
pub(crate) fn level(xp: u64) -> u64 {
    (1..=30)
        .rev()
        .find(|&level| characters::xp_for_level(level) <= xp)
        .unwrap_or(1)
}
fn tier(level: u64) -> i32 {
    match level {
        30.. => 6,
        25.. => 5,
        20.. => 4,
        10.. => 3,
        2.. => 2,
        _ => 1,
    }
}
fn quest_catalog() -> &'static Vec<Value> {
    static QUESTS: OnceLock<Vec<Value>> = OnceLock::new();
    QUESTS.get_or_init(|| {
        serde_json::from_str(include_str!("progression_quests.json")).expect("quest catalog")
    })
}
fn complete(element: &Value) -> bool {
    element["RegisteredDefPropArray"]
        .as_array()
        .is_some_and(|properties| {
            properties.iter().any(|property| {
                property["PropertyId"] == 1
                    && property["Value"]
                        .as_u64()
                        .is_some_and(|flags| flags & 10 == 10)
            })
        })
}
fn saved_completed(data: &Map<String, Value>) -> BTreeSet<u64> {
    data.get("persistence")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|element| complete(element))
        .filter_map(|element| element["DefinitionId"].as_u64())
        .collect()
}
fn load(data: &Map<String, Value>, id: &str) -> Result<Record, String> {
    if let Some(saved) = data.get("progression").and_then(|records| records.get(id)) {
        let mut record: Record = serde_json::from_value(saved.clone())
            .map_err(|e| format!("Invalid saved progression: {e}"))?;
        if record.schema != 1 || record.xp > MAX_XP {
            return Err("Invalid saved progression schema or XP".into());
        }
        record.completed.extend(saved_completed(data));
        return Ok(record);
    }
    let legacy_level = data
        .get("niveau_pilote")
        .and_then(Value::as_u64)
        .unwrap_or(1)
        .clamp(1, 30);
    let completed = saved_completed(data);
    Ok(Record {
        schema: 1,
        xp: characters::xp_for_level(legacy_level),
        items: vec![],
        backpack: vec![],
        mailbox: vec![],
        materials: BTreeMap::new(),
        completed,
        receipts: BTreeMap::new(),
    })
}
fn store(data: &mut Map<String, Value>, id: &str, record: &Record) -> Result<(), String> {
    let records = data.entry("progression").or_insert_with(|| json!({}));
    if !records.is_object() {
        return Err("Invalid saved progression map".into());
    }
    records[id] = serde_json::to_value(record).map_err(|e| e.to_string())?;
    // Keep the legacy level consumed by the launcher and older editor clients
    // aligned with the emulator's active (first) pilot, without raising a newly
    // created second pilot to the first pilot's level.
    let active = data
        .get("pilotes")
        .and_then(Value::as_array)
        .and_then(|pilots| pilots.first())
        .and_then(|pilot| pilot.get("id"))
        .map(id_text)
        .unwrap_or_else(|| "1".into());
    if active == id {
        data.insert("niveau_pilote".into(), json!(level(record.xp)));
    }
    Ok(())
}
pub(crate) fn xp(context: &Context, id: &str) -> Result<u64, String> {
    load(context.state.lock().expect("state").data(), id).map(|record| record.xp)
}
pub(crate) fn javelin_budget(data: &Map<String, Value>) -> i32 {
    let legacy = data
        .get("niveau_pilote")
        .and_then(Value::as_u64)
        .unwrap_or(1);
    let highest = data
        .get("progression")
        .and_then(Value::as_object)
        .into_iter()
        .flat_map(|records| records.values())
        .filter_map(|record| record["xp"].as_u64())
        .map(level)
        .max()
        .unwrap_or(1)
        .max(legacy);
    1 + [8, 16, 26]
        .into_iter()
        .filter(|required| highest >= *required)
        .count() as i32
}
pub(crate) fn initialize(data: &mut Map<String, Value>, id: &str) -> Result<(), String> {
    let mut record = load(data, id)?;
    record.xp = 0;
    store(data, id, &record)
}
pub(crate) fn set_level(context: &Context, id: Option<&str>, value: u64) -> Result<(), String> {
    if !(1..=30).contains(&value) {
        return Err("Level must be between 1 and 30".into());
    }
    context.state.lock().expect("state").transaction(|data| {
        if let Some(id) = id {
            let mut record = load(data, id)?;
            record.xp = characters::xp_for_level(value);
            store(data, id, &record)?;
        }
        data.insert("niveau_pilote".into(), json!(value));
        Ok(())
    })
}
pub(crate) fn items(context: &Context, id: &str) -> Result<Vec<Value>, String> {
    load(context.state.lock().expect("state").data(), id).map(|record| record.items)
}
fn next_uid(data: &mut Map<String, Value>) -> Result<u64, String> {
    let uid = match data.get("next_earned_uid") {
        Some(value) => value.as_u64().ok_or("Invalid saved item UID counter")?,
        None => {
            let issued = data
                .get("progression")
                .and_then(Value::as_object)
                .into_iter()
                .flat_map(|records| records.values())
                .any(|record| {
                    ["items", "backpack", "mailbox"].iter().any(|key| {
                        record[*key]
                            .as_array()
                            .is_some_and(|items| !items.is_empty())
                    }) || record["receipts"]
                        .as_object()
                        .is_some_and(|receipts| !receipts.is_empty())
                });
            if issued {
                return Err(
                    "Invalid saved item UID counter: missing for existing equipment".into(),
                );
            }
            FIRST_EARNED_UID
        }
    };
    if !(FIRST_EARNED_UID..MAX_UID).contains(&uid) {
        return Err("Invalid saved item UID counter".into());
    }
    data.insert("next_earned_uid".into(), json!(uid + 1));
    Ok(uid)
}
fn add_material(record: &mut Record, key: &str, amount: u64) -> Result<(), String> {
    let balance = record.materials.entry(key.into()).or_default();
    *balance = balance
        .checked_add(amount)
        .ok_or("Material balance overflow")?;
    Ok(())
}
fn exos(context: &Context) -> Vec<i32> {
    crate::startup::owned_exo_types(context)
}
fn loot(
    data: &mut Map<String, Value>,
    record: &Record,
    owned: &[i32],
    seed: &str,
    rarity: Option<i32>,
) -> Result<Value, String> {
    let max = tier(level(record.xp));
    let rarity = rarity.unwrap_or(1);
    if !(1..=max).contains(&rarity) {
        return Err("Loot rarity is not unlocked at this level".into());
    }
    let eligible: Vec<_> = FORGE_ITEMS
        .iter()
        .filter(|item| {
            item.min_rarity <= rarity && item.exo_types.iter().any(|exo| owned.contains(exo))
        })
        .collect();
    let hash = seed.bytes().fold(14695981039346656037_u64, |hash, byte| {
        (hash ^ u64::from(byte)).wrapping_mul(1099511628211)
    });
    let item = eligible
        .get((hash as usize).wrapping_rem(eligible.len().max(1)))
        .ok_or("No compatible loot in catalog")?;
    Ok(gear::earned_item(
        next_uid(data)?,
        item,
        rarity,
        level(record.xp),
    ))
}
fn replay(record: &Record, key: &str, request: &Value) -> Result<Option<Value>, String> {
    match record.receipts.get(key) {
        Some(receipt) if receipt["request"] == *request => Ok(Some(receipt["response"].clone())),
        Some(_) => Err("transactionId already used for a different operation".into()),
        None => Ok(None),
    }
}
fn remember(record: &mut Record, key: String, request: Value, response: Value) -> Value {
    record
        .receipts
        .insert(key, json!({"request":request,"response":response}));
    response
}
fn transaction_id(request: &Value) -> Result<String, Error> {
    request["transactionId"]
        .as_str()
        .filter(|id| !id.is_empty() && id.len() <= 128)
        .map(|id| format!("tx/{id}"))
        .ok_or_else(|| {
            Error(
                StatusCode::BAD_REQUEST,
                "transactionId must be a nonempty string of at most 128 bytes".into(),
            )
        })
}

/// Save quest flags and their first-completion rewards in one transaction.
/// Existing completed saves, duplicate commits, tutorials and unrelated flags
/// never award XP. Editor-authored completions are recorded without rewards.
pub(crate) fn commit_quests(
    context: &Context,
    changes: &[Value],
    reward: bool,
) -> Result<(), String> {
    let id = active_id(context);
    let owned = exos(context);
    context.state.lock().expect("state").transaction(|data| {
        let mut record = id.as_ref().map(|id| load(data, id)).transpose()?;
        let mut saved = data
            .get("persistence")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        state::merge(&mut saved, changes);
        if let Some(record) = record.as_mut() {
            for element in saved.iter().filter(|element| complete(element)) {
                let Some(definition) = element["DefinitionId"].as_u64() else {
                    continue;
                };
                if !quest_catalog()
                    .iter()
                    .any(|quest| quest["definitionId"] == definition)
                    || !record.completed.insert(definition)
                {
                    continue;
                }
                if !reward {
                    continue;
                }
                let before = record.xp;
                record.xp = record.xp.saturating_add(QUEST_XP).min(MAX_XP);
                add_material(record, "parts", 10)?;
                add_material(record, "ember_1", 3)?;
                // Completion persistence must succeed even when the inventory
                // is full. Defer that item in the bounded first-quest mailbox.
                let item = loot(
                    data,
                    record,
                    &owned,
                    &format!("quest-{definition}"),
                    Some(1),
                )?;
                if record.items.len() + record.backpack.len() < MAX_ITEMS {
                    record.items.push(item.clone());
                } else {
                    record.mailbox.push(item.clone());
                }
                remember(
                    record,
                    format!("quest/{definition}"),
                    json!({"definitionId":definition}),
                    json!({"xpGranted":record.xp-before,"item":item,"parts":10,"ember_1":3}),
                );
            }
        }
        data.insert("persistence".into(), json!(saved));
        if let (Some(id), Some(record)) = (id.as_ref(), record.as_ref()) {
            store(data, id, record)?;
        }
        Ok(())
    })
}

pub(crate) async fn page() -> Html<&'static str> {
    Html(include_str!("progression.html"))
}
pub(crate) async fn script() -> impl IntoResponse {
    (
        [(
            axum::http::header::CONTENT_TYPE,
            "text/javascript; charset=utf-8",
        )],
        include_str!("progression.js"),
    )
}
pub(crate) async fn overview(State(context): State<Context>) -> Reply {
    let pilots = context
        .characters
        .lock()
        .expect("characters")
        .iter()
        .map(|pilot| json!({"id":id_text(&pilot.id),"name":pilot.name}))
        .collect::<Vec<_>>();
    Ok(Json(
        json!({"pilots":pilots,"rules":{"questXp":QUEST_XP,"levelCap":30,
        "automaticRewards":"First completion of a known mission quest; no rewards for simply ending an expedition."},"quests":quest_catalog()}),
    ))
}
pub(crate) async fn view(State(context): State<Context>, Path(id): Path<String>) -> Reply {
    check_character(&context, &id)?;
    let record = load(context.state.lock().expect("state").data(), &id).map_err(error)?;
    let current = level(record.xp);
    let owned = exos(&context);
    let recipes: Vec<_> = gear::craft_catalog()
        .into_iter()
        .filter(|recipe| {
            recipe["exoTypes"].as_array().is_some_and(|types| {
                types
                    .iter()
                    .any(|exo| owned.contains(&(exo.as_i64().unwrap_or(-1) as i32)))
            })
        })
        .collect();
    Ok(Json(
        json!({"characterId":id,"xp":record.xp,"level":current,"levelFloorXp":characters::xp_for_level(current),
        "nextLevelXp":if current<30 {Some(characters::xp_for_level(current+1))}else{None},
        "materials":record.materials,"items":workbench_items(record.items),"backpack":record.backpack,"mailbox":record.mailbox,"receipts":record.receipts,
        "recipes":recipes}),
    ))
}
pub(crate) async fn craft(
    State(context): State<Context>,
    Path(id): Path<String>,
    bytes: Bytes,
) -> Reply {
    check_character(&context, &id)?;
    let request = body(&bytes)?;
    let key = transaction_id(&request)?;
    let recipe = gear::craft_catalog()
        .into_iter()
        .find(|recipe| recipe["recipeId"] == request["recipeId"] && request["recipeId"].is_string())
        .ok_or_else(|| error("Unknown recipe".into()))?;
    let owned = exos(&context);
    if !recipe["exoTypes"].as_array().is_some_and(|types| {
        types
            .iter()
            .any(|exo| owned.contains(&(exo.as_i64().unwrap_or(-1) as i32)))
    }) {
        return Err(error("Recipe is incompatible with owned javelins".into()));
    }
    let normalized = json!({"operation":"craft","recipeId":recipe["recipeId"]});
    let response = context
        .state
        .lock()
        .expect("state")
        .transaction(|data| {
            let mut record = load(data, &id)?;
            if let Some(result) = replay(&record, &key, &normalized)? {
                return Ok(result);
            }
            if record.items.len() + record.backpack.len() >= MAX_ITEMS {
                return Err("Pilot inventory full".into());
            }
            let rarity = recipe["rarity"].as_i64().unwrap_or(1) as i32;
            if rarity > tier(level(record.xp)) {
                return Err("Recipe rarity is not unlocked".into());
            }
            for (material, amount) in recipe["costs"].as_object().ok_or("Invalid recipe costs")? {
                let cost = amount.as_u64().ok_or("Invalid recipe cost")?;
                let balance = record.materials.entry(material.clone()).or_default();
                *balance = balance
                    .checked_sub(cost)
                    .ok_or_else(|| format!("Insufficient {material}"))?;
            }
            let item_data = recipe["itemDataId"].as_u64().ok_or("Invalid recipe item")?;
            let item = FORGE_ITEMS
                .iter()
                .find(|item| item.item_data_id == item_data)
                .ok_or("Unknown recipe item")?;
            let item = gear::earned_item(next_uid(data)?, item, rarity, level(record.xp));
            record.items.push(item.clone());
            let response = json!({"item":item,"materials":record.materials});
            let response = remember(&mut record, key, normalized, response);
            store(data, &id, &record)?;
            Ok(response)
        })
        .map_err(error)?;
    Ok(Json(response))
}

fn protected(data: &Map<String, Value>, owned: &[i32]) -> Vec<u64> {
    owned
        .iter()
        .flat_map(|exo| {
            let key = (exo + 1).to_string();
            let attachments = data
                .get("attaches_des_loadouts")
                .and_then(|loads| loads.get(&key))
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_else(|| gear::attachments(*exo));
            attachments
                .into_iter()
                .filter_map(|attachment| attachment["itemUID"].as_u64().map(gear::current_uid))
                .collect::<Vec<_>>()
        })
        .collect()
}
pub(crate) fn salvage(
    context: &Context,
    id: &str,
    key: String,
    mut uids: Vec<u64>,
) -> Result<Value, String> {
    uids.sort_unstable();
    uids.dedup();
    if uids.is_empty() || uids.len() > 100 {
        return Err("Select between 1 and 100 items".into());
    }
    let owned = exos(context);
    let base = crate::startup::base_inventory(context);
    let normalized = json!({"operation":"salvage","uids":uids});
    context.state.lock().expect("state").transaction(|data| {
        let mut record = load(data, id)?;
        if let Some(result) = replay(&record, &key, &normalized)? {
            return Ok(result);
        }
        let protected = protected(data, &owned);
        let mut removed = data
            .get("objets_demanteles")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        for uid in &uids {
            if gear::is_starting_gear(*uid) || protected.contains(uid) {
                return Err("Starting or equipped items cannot be salvaged".into());
            }
            if removed.contains(&json!(uid)) {
                return Err("Item was already salvaged".into());
            }
            let item = record
                .items
                .iter()
                .chain(base.iter())
                .find(|item| item["uid"] == *uid)
                .ok_or("Unknown inventory item")?;
            let rarity = item["rarity"].as_u64().unwrap_or(1).clamp(1, 6);
            add_material(&mut record, "parts", 2 + 2 * rarity)?;
            add_material(&mut record, &format!("ember_{rarity}"), 1)?;
            removed.push(json!(uid));
        }
        record
            .items
            .retain(|item| !uids.contains(&item["uid"].as_u64().unwrap_or(0)));
        data.insert("objets_demanteles".into(), json!(removed));
        if let Some(junk) = data
            .get_mut("objets_au_rebut")
            .and_then(Value::as_array_mut)
        {
            junk.retain(|uid| !uids.iter().any(|known| uid == &json!(known)));
        }
        let response = json!({"salvaged":uids,"materials":record.materials});
        let response = remember(&mut record, key, normalized, response);
        store(data, id, &record)?;
        Ok(response)
    })
}
pub(crate) async fn salvage_local(
    State(context): State<Context>,
    Path(id): Path<String>,
    bytes: Bytes,
) -> Reply {
    check_character(&context, &id)?;
    let request = body(&bytes)?;
    let key = transaction_id(&request)?;
    let uids: Vec<u64> = serde_json::from_value(request["uids"].clone())
        .map_err(|_| error("Expected integer uids array".into()))?;
    Ok(Json(salvage(&context, &id, key, uids).map_err(error)?))
}

/// Explicit adapter for the placeholder loot tickets already advertised by
/// /api/gameconfig/loottables. Native pickup request routing is still unverified.
pub(crate) async fn stage_ticket(
    State(context): State<Context>,
    Path(id): Path<String>,
    bytes: Bytes,
) -> Reply {
    check_character(&context, &id)?;
    let request = body(&bytes)?;
    let key = transaction_id(&request)?;
    let ticket = request["ticket"]
        .as_str()
        .ok_or_else(|| error("Expected loot ticket".into()))?;
    let rarity = ticket
        .strip_prefix("bigs-butin-rarete-")
        .and_then(|value| value.parse::<i32>().ok())
        .filter(|rarity| (1..=6).contains(rarity))
        .ok_or_else(|| error("Unsupported loot ticket".into()))?;
    let owned = exos(&context);
    let normalized = json!({"operation":"ticket","ticket":ticket});
    let response = context
        .state
        .lock()
        .expect("state")
        .transaction(|data| {
            let mut record = load(data, &id)?;
            if let Some(result) = replay(&record, &key, &normalized)? {
                return Ok(result);
            }
            if record.items.len() + record.backpack.len() >= MAX_ITEMS {
                return Err("Pilot inventory full".into());
            }
            let item = loot(data, &record, &owned, &key, Some(rarity))?;
            record.backpack.push(item.clone());
            let response = remember(&mut record, key, normalized, json!({"item":item}));
            store(data, &id, &record)?;
            Ok(response)
        })
        .map_err(error)?;
    Ok(Json(response))
}
pub(crate) fn backpack(context: &Context, id: &str) -> Result<Value, String> {
    let record = load(context.state.lock().expect("state").data(), id)?;
    Ok(json!({"items":record.backpack,"currencyItems":[],"tickets":[]}))
}
pub(crate) fn resolve(context: &Context, id: &str) -> Result<Value, String> {
    context.state.lock().expect("state").transaction(|data| {
        let mut record=load(data,id)?;
        let ids: Vec<_>=record.backpack.iter().map(|item|item["uid"].clone()).collect();
        record.items.append(&mut record.backpack);
        let room=MAX_ITEMS.saturating_sub(record.items.len()).min(record.mailbox.len());
        record.items.extend(record.mailbox.drain(..room));
        store(data,id,&record)?;
        Ok(json!({"sourceIds":ids,"targetIds":ids,"upgradedIds":[],"currencyTypes":[],"currencyCounts":[],"unlockIds":[],"currencies":[]}))
    })
}

fn workbench_items(mut items: Vec<Value>) -> Vec<Value> {
    // ItemDataId is a native u64 and must not round through JavaScript Number.
    for item in &mut items {
        if let Some(id) = item["gameItemId"].as_u64() {
            item["gameItemId"] = json!(id.to_string());
        }
    }
    items
}
pub(crate) async fn claim(State(context): State<Context>, Path(id): Path<String>) -> Reply {
    check_character(&context, &id)?;
    Ok(Json(resolve(&context, &id).map_err(error)?))
}
