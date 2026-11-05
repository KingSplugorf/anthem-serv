use axum::{
    Json,
    body::Bytes,
    extract::{Path, RawQuery, State},
};
use serde_json::{Value, json};

use crate::{Context, ebs};

#[derive(Clone)]
pub struct Character {
    pub id: Value,
    pub name: String,
    pub gender: String,
    pub appearance: String,
}

impl Character {
    pub fn new(index: usize, name: &str) -> Self {
        Self {
            id: json!(index),
            name: name.to_owned(),
            gender: String::new(),
            appearance: String::new(),
        }
    }
}

impl Character {
    fn saved(&self) -> Value {
        json!({ "id": self.id, "name": self.name, "gender": self.gender, "appearance": self.appearance })
    }

    pub fn from_saved(saved: &Value) -> Option<Self> {
        Some(Self {
            id: saved.get("id")?.clone(),
            name: saved["name"].as_str()?.to_owned(),
            gender: saved["gender"].as_str().unwrap_or_default().to_owned(),
            appearance: saved["appearance"].as_str().unwrap_or_default().to_owned(),
        })
    }
}

fn save(context: &Context, characters: &[Character]) {
    context
        .state
        .lock()
        .expect("fichier d'état")
        .set_pilots(characters.iter().map(Character::saved).collect());
}

fn id_text(id: &Value) -> String {
    match id {
        Value::String(id) => id.clone(),
        id => id.to_string(),
    }
}

// LevelThresholds de DylanPilotProgressionData (globals/pilot/dylanpilotprogressioninfo) : XP des niveaux 2 à 30.
const LEVEL_THRESHOLDS: [u64; 29] = [
    1000, 3000, 6050, 10100, 15350, 20900, 26750, 32900, 39350, 46150, 53350, 60850, 68800, 77150,
    85950, 95250, 105000, 115250, 126050, 137400, 149400, 162000, 175250, 189200, 203900, 219400,
    235650, 252800, 270850,
];

/// XP minimale du niveau, comme 0x147ddbac0 : LevelThresholds[niveau - 2], dernier seuil au-delà.
fn xp_for_level(level: u64) -> u64 {
    match level.checked_sub(2) {
        None => 0,
        Some(index) => LEVEL_THRESHOLDS[(index as usize).min(LEVEL_THRESHOLDS.len() - 1)],
    }
}

fn dto(character: &Character, level: u64) -> Value {
    json!({
        "skills": [],
        "name": character.name,
        "gender": character.gender,
        "xp": xp_for_level(level),
        "level": level,
        "playTime": 0,
        "appearanceData": character.appearance,
        "lancerName": character.name,
    })
}

fn entry(character: &Character, level: u64) -> Value {
    json!({ "character": dto(character, level), "characterId": id_text(&character.id) })
}

fn pilot_level(context: &Context) -> u64 {
    context.state.lock().expect("fichier d'état").pilot_level()
}

pub async fn editor_level(State(context): State<Context>) -> Json<Value> {
    Json(json!({ "level": pilot_level(&context) }))
}

pub async fn set_editor_level(State(context): State<Context>, body: Bytes) -> Json<Value> {
    let request: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    if let Some(level) = request["level"].as_u64() {
        context
            .state
            .lock()
            .expect("fichier d'état")
            .set_pilot_level(level);
    }
    editor_level(State(context)).await
}

pub async fn list(State(context): State<Context>) -> Json<Value> {
    let level = pilot_level(&context);
    let characters = context.characters.lock().expect("registre des personnages");
    Json(
        json!({ "characters": characters.iter().map(|character| entry(character, level)).collect::<Vec<_>>() }),
    )
}

pub async fn create(State(context): State<Context>) -> Json<Value> {
    let mut characters = context.characters.lock().expect("registre des personnages");
    let character = Character::new(characters.len() + 1, &context.player_name);
    println!("personnage créé : {} ({})", character.id, character.name);
    let response = entry(&character, pilot_level(&context));
    let persona = context.persona_id;
    let created = ebs::event(
        persona,
        "character",
        "CHARACTER_CREATED",
        json!({ "createdCharacterId": id_text(&character.id) }),
    );
    let activated = ebs::event(
        persona,
        "character",
        "ACTIVE_CHARACTER_CHANGED",
        json!({ "newActiveCharacterId": id_text(&character.id) }),
    );
    characters.push(character);
    save(&context, &characters);
    let _ = context.events.send(created);
    let _ = context.events.send(activated);
    Json(response)
}

pub async fn active(State(context): State<Context>) -> Json<Value> {
    let level = pilot_level(&context);
    let characters = context.characters.lock().expect("registre des personnages");
    match characters.first() {
        Some(character) => Json(entry(character, level)),
        None => Json(json!({})),
    }
}

pub async fn get(
    State(context): State<Context>,
    Path((_, id)): Path<(u64, String)>,
) -> Json<Value> {
    let level = pilot_level(&context);
    let characters = context.characters.lock().expect("registre des personnages");
    match characters
        .iter()
        .find(|character| id_text(&character.id) == id)
    {
        Some(character) => Json(json!({ "character": dto(character, level) })),
        None => Json(json!({})),
    }
}

fn update_character(
    context: &Context,
    id: &str,
    body: &Bytes,
    field: &str,
    apply: fn(&mut Character, &str),
) {
    let request: Value = serde_json::from_slice(body).unwrap_or(Value::Null);
    let mut characters = context.characters.lock().expect("registre des personnages");
    if let (Some(value), Some(character)) = (
        request[field].as_str(),
        characters
            .iter_mut()
            .find(|character| id_text(&character.id) == id),
    ) {
        apply(character, value);
        save(context, &characters);
    }
}

pub async fn set_gender(
    State(context): State<Context>,
    Path((_, id)): Path<(u64, String)>,
    body: Bytes,
) -> Json<Value> {
    update_character(&context, &id, &body, "gender", |character, gender| {
        character.gender = gender.to_owned()
    });
    Json(json!({}))
}

pub async fn set_appearance(
    State(context): State<Context>,
    Path((_, id)): Path<(u64, String)>,
    body: Bytes,
) -> Json<Value> {
    update_character(
        &context,
        &id,
        &body,
        "appearanceData",
        |character, appearance| character.appearance = appearance.to_owned(),
    );
    Json(json!({}))
}

pub async fn set_name(
    State(context): State<Context>,
    Path((_, id)): Path<(u64, String)>,
    body: Bytes,
) -> Json<Value> {
    update_character(&context, &id, &body, "name", |character, name| {
        character.name = name.to_owned()
    });
    Json(json!({}))
}

pub async fn player_card(State(context): State<Context>) -> Json<Value> {
    Json(json!({ "data": remote_view_entry(&context) }))
}

/// Une entrée par `personaIds` demandé (la carte des autres membres reprend ce personnage, faute de leurs données ici).
pub async fn remote_view(State(context): State<Context>, RawQuery(query): RawQuery) -> Json<Value> {
    let mut personas: Vec<u64> = query
        .unwrap_or_default()
        .split('&')
        .filter_map(|pair| pair.strip_prefix("personaIds="))
        .filter_map(|persona| persona.parse().ok())
        .collect();
    if personas.is_empty() {
        personas.push(context.persona_id);
    }
    remote_views(&context, personas)
}

/// Vue distante des membres d'une expédition (PlayerCardGetRemoteViewRequest {PersonaId, ExpeditionGUID}).
pub async fn expedition_remote_view(
    State(context): State<Context>,
    Path(params): Path<std::collections::HashMap<String, String>>,
) -> Json<Value> {
    let persona = params
        .get("persona")
        .and_then(|persona| persona.parse().ok())
        .unwrap_or(context.persona_id);
    remote_views(&context, vec![persona])
}

fn remote_views(context: &Context, personas: Vec<u64>) -> Json<Value> {
    let entry = remote_view_entry(context);
    let data: Vec<Value> = personas
        .into_iter()
        .map(|persona| {
            let mut card = entry.clone();
            card["personaId"] = json!(persona);
            if card.get("playercard").is_some() {
                card["playercard"]["personaId"] = json!(persona);
            }
            card
        })
        .collect();
    Json(json!({ "data": data }))
}

fn remote_view_entry(context: &Context) -> Value {
    let level = pilot_level(context);
    let characters = context.characters.lock().expect("registre des personnages");
    let Some(character) = characters.first() else {
        return json!({ "personaId": context.persona_id });
    };
    let id = id_text(&character.id);
    let (loadout_id, exo_type) = crate::startup::default_loadout(context);
    let gear_unlocks: Vec<Value> = crate::mtx::exo_unlocks(context)
        .into_iter()
        .map(|unlock| json!({ "assetName": unlock.name, "assetGuid": unlock.guid, "assetType": "", "subType": "", "viewed": true }))
        .collect();
    json!({
        "personaId": context.persona_id,
        "characterId": id,
        "skin": { "skinId": "", "displayName": "" },
        "activeCharacterLoadout": {
            "displayName": "",
            "loadoutId": loadout_id,
            "exoType": exo_type,
            "gear": { "items": crate::startup::remote_view_items(context, exo_type), "unlocks": gear_unlocks, "gearScore": 0.0 },
        },
        "activeCharacter": {
            "activeCharacterName": character.name,
            "activeCharacterLevel": level,
            "activeCharacterXp": xp_for_level(level),
            "activeCharacterGender": character.gender,
            "activeCharacterAppearanceData": character.appearance,
        },
        "playercard": {
            "personaId": context.persona_id,
            "activeCharacterId": id,
            "activeLoadoutId": loadout_id,
            "activeCharacterLevel": level,
            "activeCharacterLancerName": character.name,
            "defaultExoType": exo_type,
        },
    })
}
