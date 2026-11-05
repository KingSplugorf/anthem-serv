use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

const PILOT_CREATION: u32 = 1963412089;
const PILOT_CREATED: u32 = 3031061379;
const TUTORIAL_QUESTS: [u32; 2] = [1835450465, 1910188215];
const QUEST_STATE: u32 = 1;
const COMPLETED_AND_MISSION_COMPLETE: u8 = 10;
const PERSISTENCE_KEY: &str = "persistence";
const BOUGHT_OFFERS_KEY: &str = "offres_achetees";
const PILOT_LEVEL_KEY: &str = "niveau_pilote";
const PILOTS_KEY: &str = "pilotes";
const COSMETICS_UNLOCKED_KEY: &str = "cosmetiques_debloques";
const DEFAULT_LOADOUT_KEY: &str = "loadout_par_defaut";
const LOADOUT_ATTACHMENTS_KEY: &str = "attaches_des_loadouts";
const SALVAGED_ITEMS_KEY: &str = "objets_demanteles";
const JUNK_ITEMS_KEY: &str = "objets_au_rebut";
const STARTING_PILOT_LEVEL: u64 = 1;

fn registered_element(definition: u32, property: u32, value: Value) -> Value {
    json!({ "DefinitionId": definition, "RegisteredDefPropArray": [{ "PropertyId": property, "Value": value }] })
}

fn tutorial_completed_elements() -> Vec<Value> {
    let mut elements = vec![registered_element(
        PILOT_CREATION,
        PILOT_CREATED,
        json!(true),
    )];
    elements.extend(TUTORIAL_QUESTS.map(|quest| {
        registered_element(quest, QUEST_STATE, json!(COMPLETED_AND_MISSION_COMPLETE))
    }));
    elements
}

fn merge_properties(target: &mut Value, properties: &[Value]) {
    let Some(existing) = target["RegisteredDefPropArray"].as_array_mut() else {
        target["RegisteredDefPropArray"] = Value::Array(properties.to_vec());
        return;
    };
    for property in properties {
        match existing
            .iter_mut()
            .find(|known| known["PropertyId"] == property["PropertyId"])
        {
            Some(known) => *known = property.clone(),
            None => existing.push(property.clone()),
        }
    }
}

/// Fusionne des éléments {DefinitionId, RegisteredDefPropArray} : une propriété connue est remplacée, une nouvelle ajoutée.
pub fn merge(elements: &mut Vec<Value>, changes: &[Value]) {
    for change in changes {
        let properties = change["RegisteredDefPropArray"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        match elements
            .iter_mut()
            .find(|element| element["DefinitionId"] == change["DefinitionId"])
        {
            Some(element) => merge_properties(element, &properties),
            None => elements.push(change.clone()),
        }
    }
}

pub struct StateFile {
    path: Option<PathBuf>,
    state: Map<String, Value>,
}

impl StateFile {
    pub fn in_memory() -> Self {
        Self {
            path: None,
            state: Map::new(),
        }
    }

    pub fn open(path: &str) -> Self {
        let path = PathBuf::from(path);
        let state = std::fs::read(&path)
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_default();
        Self {
            path: Some(path),
            state,
        }
    }

    fn save(&self) {
        let Some(path) = &self.path else { return };
        if let Err(error) = write_atomically(path, &self.state) {
            eprintln!("fichier d'état {} non écrit : {error}", path.display());
        }
    }

    fn list(&self, key: &str) -> Vec<Value> {
        self.state
            .get(key)
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    pub fn commit_persistence(&mut self, changes: &[Value]) {
        let mut saved = self.list(PERSISTENCE_KEY);
        merge(&mut saved, changes);
        self.state
            .insert(PERSISTENCE_KEY.to_owned(), Value::Array(saved));
        self.save();
    }

    pub fn saved_persistence(&self) -> Vec<Value> {
        self.list(PERSISTENCE_KEY)
    }

    /// Persistance servie : « tutoriel terminé », puis la progression sauvegardée.
    pub fn served_persistence(&self) -> Vec<Value> {
        let mut elements = tutorial_completed_elements();
        merge(&mut elements, &self.list(PERSISTENCE_KEY));
        elements
    }

    pub fn bought_offers(&self) -> Vec<String> {
        self.list(BOUGHT_OFFERS_KEY)
            .iter()
            .filter_map(|offer| offer.as_str().map(str::to_owned))
            .collect()
    }

    pub fn set_bought_offers(&mut self, offers: &[String]) {
        self.state.insert(
            BOUGHT_OFFERS_KEY.to_owned(),
            Value::Array(offers.iter().map(|offer| json!(offer)).collect()),
        );
        self.save();
    }

    pub fn pilots(&self) -> Vec<Value> {
        self.list(PILOTS_KEY)
    }

    pub fn set_pilots(&mut self, pilots: Vec<Value>) {
        self.state
            .insert(PILOTS_KEY.to_owned(), Value::Array(pilots));
        self.save();
    }

    pub fn pilot_level(&self) -> u64 {
        self.state
            .get(PILOT_LEVEL_KEY)
            .and_then(Value::as_u64)
            .unwrap_or(STARTING_PILOT_LEVEL)
    }

    pub fn starting_inventory_only(&self) -> bool {
        self.state
            .get("inventaire_depart_seul")
            .and_then(Value::as_bool)
            .unwrap_or(false)
    }

    pub fn set_pilot_level(&mut self, level: u64) {
        self.state.insert(PILOT_LEVEL_KEY.to_owned(), json!(level));
        self.save();
    }

    pub fn cosmetics_unlocked(&self) -> bool {
        self.state
            .get(COSMETICS_UNLOCKED_KEY)
            .and_then(Value::as_bool)
            .unwrap_or(false)
    }

    pub fn set_cosmetics_unlocked(&mut self, unlocked: bool) {
        self.state
            .insert(COSMETICS_UNLOCKED_KEY.to_owned(), json!(unlocked));
        self.save();
    }

    pub fn default_loadout(&self) -> Option<String> {
        self.state
            .get(DEFAULT_LOADOUT_KEY)
            .and_then(Value::as_str)
            .map(str::to_owned)
    }

    pub fn set_default_loadout(&mut self, loadout_id: &str) {
        self.state
            .insert(DEFAULT_LOADOUT_KEY.to_owned(), json!(loadout_id));
        self.save();
    }

    pub fn loadout_attachments(&self, loadout_id: &str) -> Option<Vec<Value>> {
        self.state
            .get(LOADOUT_ATTACHMENTS_KEY)?
            .get(loadout_id)?
            .as_array()
            .cloned()
    }

    pub fn set_loadout_attachments(&mut self, loadout_id: &str, attachments: Vec<Value>) {
        let saved = self
            .state
            .entry(LOADOUT_ATTACHMENTS_KEY.to_owned())
            .or_insert_with(|| json!({}));
        if !saved.is_object() {
            *saved = json!({});
        }
        saved[loadout_id] = Value::Array(attachments);
        self.save();
    }

    fn uids(&self, key: &str) -> Vec<u64> {
        self.list(key).iter().filter_map(Value::as_u64).collect()
    }

    fn set_uids(&mut self, key: &str, uids: Vec<u64>) {
        self.state.insert(
            key.to_owned(),
            Value::Array(uids.into_iter().map(|uid| json!(uid)).collect()),
        );
        self.save();
    }

    pub fn salvaged_items(&self) -> Vec<u64> {
        self.uids(SALVAGED_ITEMS_KEY)
    }

    pub fn salvage_items(&mut self, uids: &[u64]) {
        let mut salvaged = self.salvaged_items();
        for &uid in uids {
            if !salvaged.contains(&uid) {
                salvaged.push(uid);
            }
        }
        let junk = self
            .junk_items()
            .into_iter()
            .filter(|uid| !uids.contains(uid))
            .collect();
        self.set_uids(SALVAGED_ITEMS_KEY, salvaged);
        self.set_uids(JUNK_ITEMS_KEY, junk);
    }

    pub fn junk_items(&self) -> Vec<u64> {
        self.uids(JUNK_ITEMS_KEY)
    }

    pub fn set_junk(&mut self, uid: u64, is_junk: bool) {
        let mut junk: Vec<u64> = self
            .junk_items()
            .into_iter()
            .filter(|&known| known != uid)
            .collect();
        if is_junk {
            junk.push(uid);
        }
        self.set_uids(JUNK_ITEMS_KEY, junk);
    }

    pub fn buy_offers(&mut self, offers: &[&str]) {
        let mut bought = self.list(BOUGHT_OFFERS_KEY);
        for offer in offers {
            if !bought.iter().any(|known| known == offer) {
                bought.push(json!(offer));
            }
        }
        self.state
            .insert(BOUGHT_OFFERS_KEY.to_owned(), Value::Array(bought));
        self.save();
    }
}

fn write_atomically(path: &Path, state: &Map<String, Value>) -> std::io::Result<()> {
    if let Some(directory) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(directory)?;
    }
    let temporary = path.with_extension("json.tmp");
    std::fs::write(&temporary, serde_json::to_vec_pretty(state)?)?;
    std::fs::rename(&temporary, path)
}
