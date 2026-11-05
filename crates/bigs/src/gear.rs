use serde_json::{Value, json};

use crate::forge_items::FORGE_ITEMS;

struct GearEntry {
    slot: i32,
    item_data_id: u64,
    item_guid: &'static str,
    blueprint: &'static str,
}

/// Objet accepté par les listes de la Forge, servi en un exemplaire dès qu'un javelin de `exo_types` est possédé.
pub struct ForgeItem {
    pub item_data_id: u64,
    pub item_guid: &'static str,
    pub exo_types: &'static [i32],
    pub min_rarity: i32,
}

const fn gear(
    slot: i32,
    item_data_id: u64,
    item_guid: &'static str,
    blueprint: &'static str,
) -> GearEntry {
    GearEntry {
        slot,
        item_data_id,
        item_guid,
        blueprint,
    }
}

// {ItemDataId, Slot} des ExoDefinition (configurations/exodefinitions.ebx), GUID des LiteItemData Unlockable des item lists.
const RANGER_DEFAULT_GEAR: [GearEntry; 16] = [
    gear(
        1,
        4577961591914878671,
        "8dfb94ed-0ec8-9c18-0707-554a117fe2e5",
        "equipment/weapons/assaultrifles/freelancer/weapon_assaultrifle_ranger_rapidfire_defaultitem",
    ),
    gear(
        218,
        8587198510646410836,
        "ccb99fab-9c76-1d3b-1e75-8dcdf5e54928",
        "equipment/weapons/melee/exm_lancer/mace/shockmace/default/weapon_melee_base_shockmace_default",
    ),
    gear(
        2,
        2885597631031394381,
        "4e5d26b7-9205-4b19-b18a-0b3bee941837",
        "equipment/gear/lancer/shoulder/grenade/frag/lancer_grenade_frag_defaultitem",
    ),
    gear(
        33,
        8608249098448018606,
        "909558b8-0142-4374-acce-c235a5a9a07c",
        "equipment/gear/lancer/wrist/projectilelauncher/homing/lancer_projectilelauncher_homing_defaultitem",
    ),
    gear(
        6,
        8631418977332698986,
        "d6d8f30c-2129-4737-90e7-37367bf9ade8",
        "equipment/gear/shared/gear_flashlight",
    ),
    gear(
        31,
        3643522258517184980,
        "ee6fdfc4-7035-45b0-8a3d-cfc5411a94ba",
        "equipment/gear/lancer/ultimate/missiles/base/lancer_ultimate_missiles",
    ),
    gear(
        145,
        6965721662300542054,
        "c6b79713-7efb-46eb-96ee-2061271ff98c",
        "exo/exm_lancer/parts/lancerparts_frame",
    ),
    gear(
        9,
        3957146945700805624,
        "25cecbee-ea01-4b91-a717-1f3237d9296d",
        "globals/personalization/emotes/emote_flare",
    ),
    gear(
        10,
        1480435604090893685,
        "202e6c4f-3c53-49df-b044-e0f860fd8086",
        "globals/personalization/emotes/emote_clap",
    ),
    gear(
        11,
        6765625667593726137,
        "496b391c-2fff-4de0-a42c-9cc3ba8493fa",
        "globals/personalization/emotes/emote_wave_righthand",
    ),
    gear(
        189,
        5108361573696785484,
        "52a8a412-8d91-4705-b006-9e4471181d10",
        "globals/personalization/arrivalanims/arrive_default",
    ),
    gear(
        190,
        3905097293101855347,
        "09f19db6-5bf9-4887-af3d-e42030b0fb9e",
        "globals/personalization/victoryposes/victorypose_default",
    ),
    gear(
        49,
        8423336196857462224,
        "2921d3ae-ba1d-48a6-87d7-5a86ab3f48f9",
        "exo/exm_lancer/appearances/exm_lancer_appearance_default_head",
    ),
    gear(
        50,
        5574400546200897521,
        "9b7a4ad0-2d29-4f7b-953d-6bd38254704b",
        "exo/exm_lancer/appearances/exm_lancer_appearance_default_arms",
    ),
    gear(
        51,
        1960165682436077502,
        "e44ed875-368c-4e34-80da-536f17ff5c17",
        "exo/exm_lancer/appearances/exm_lancer_appearance_default_body",
    ),
    gear(
        52,
        1210453648328198812,
        "3944e18f-9866-4fd1-90cb-060db50f1a5a",
        "exo/exm_lancer/appearances/exm_lancer_appearance_default_legs",
    ),
];

const COLOSSUS_DEFAULT_GEAR: [GearEntry; 16] = [
    gear(
        1,
        5599861523909539856,
        "99b6fd26-5847-5311-0686-d632dbb6e14f",
        "equipment/weapons/heavy/autocannon/weapon_heavy_autocannon_ramping_defaultitem",
    ),
    gear(
        33,
        5405319278112813329,
        "7af10029-6d61-4be6-a1a2-872bdfa74da7",
        "equipment/gear/colossus/wrist/cannon/rail_gun/colossus_cannon_rail_gun_defaultitem",
    ),
    gear(
        2,
        3662463085647325500,
        "86976f45-be94-445e-b60d-9369052bde56",
        "equipment/gear/colossus/shoulder/mortar/he/colossus_mortar_he_defaultitem",
    ),
    gear(
        218,
        1798591564437462937,
        "9eb24581-7cb8-77b7-a560-e2465d042a20",
        "equipment/weapons/melee/exh_colossus/glove/seismicglove/default/weapon_melee_base_seismicglove_default",
    ),
    gear(
        47,
        235363549187900738,
        "59145450-45ef-4044-9e41-e40775d2724f",
        "equipment/gear/colossus/evasivemaneuver/colossus_evasivemaneuver",
    ),
    gear(
        6,
        8631418977332698986,
        "d6d8f30c-2129-4737-90e7-37367bf9ade8",
        "equipment/gear/shared/gear_flashlight",
    ),
    gear(
        31,
        2575284739100881308,
        "9b362b50-c8ab-4772-a4fb-d019895227fd",
        "equipment/gear/colossus/ultimate/colossus_ultimate_gun",
    ),
    gear(
        150,
        7494572263826436557,
        "e980d11a-d48d-44e3-a75d-f2f2d97d6851",
        "exo/exh_colossus/parts/colossusparts_frame",
    ),
    gear(
        9,
        3957146945700805624,
        "25cecbee-ea01-4b91-a717-1f3237d9296d",
        "globals/personalization/emotes/emote_flare",
    ),
    gear(
        10,
        1480435604090893685,
        "202e6c4f-3c53-49df-b044-e0f860fd8086",
        "globals/personalization/emotes/emote_clap",
    ),
    gear(
        11,
        6765625667593726137,
        "496b391c-2fff-4de0-a42c-9cc3ba8493fa",
        "globals/personalization/emotes/emote_wave_righthand",
    ),
    gear(
        50,
        6516089737916483982,
        "c8bc5945-9a79-418f-9222-7dd2266d9a2c",
        "exo/exh_colossus/appearances/exh_colossus_appearance_default_arms_item",
    ),
    gear(
        51,
        4057564978529343349,
        "6243e426-b982-4009-b818-06efeaf738d6",
        "exo/exh_colossus/appearances/exh_colossus_appearance_default_torso_item",
    ),
    gear(
        52,
        3274793529643170533,
        "7cebfb9b-2930-44ed-8edc-4ff9cee97179",
        "exo/exh_colossus/appearances/exh_colossus_appearance_default_legs_item",
    ),
    gear(
        189,
        5108361573696785484,
        "52a8a412-8d91-4705-b006-9e4471181d10",
        "globals/personalization/arrivalanims/arrive_default",
    ),
    gear(
        190,
        3905097293101855347,
        "09f19db6-5bf9-4887-af3d-e42030b0fb9e",
        "globals/personalization/victoryposes/victorypose_default",
    ),
];

const STORM_DEFAULT_GEAR: [GearEntry; 16] = [
    gear(
        1,
        9088997255893790470,
        "cd0c3058-25d0-928f-be83-a809b72441d1",
        "equipment/weapons/assaultrifles/precision/weapon_assaultrifle_precision_singleshot_defaultitem",
    ),
    gear(
        218,
        5929234382304206281,
        "b8499029-f3eb-4108-52df-a68d002c91f7",
        "equipment/weapons/melee/exl_storm/strike/explosivestrike/default/weapon_melee_base_explosivestrike_default",
    ),
    gear(
        6,
        3610957234956457560,
        "f7f65952-8e8d-47aa-bb68-7c264f08267b",
        "equipment/gear/shared/gear_seal_flashlight",
    ),
    gear(
        155,
        7639066742757161998,
        "fc50398b-1eeb-4353-9e03-f569af3dcf9f",
        "exo/exl_storm/parts/stormparts_frame",
    ),
    gear(
        33,
        5032978414215515842,
        "80f3a2a7-ed00-45c3-8c09-5c5eceef9f01",
        "equipment/gear/storm/kinesis/projectile/iceshards/storm_shards_ice_defaultitem",
    ),
    gear(
        2,
        1759753981755115936,
        "7257ea07-d897-4e6f-8adb-3f19c76cc604",
        "equipment/gear/storm/kinesis/storm/lightning/storm_lightningstorm_defaultitem",
    ),
    gear(
        9,
        3957146945700805624,
        "25cecbee-ea01-4b91-a717-1f3237d9296d",
        "globals/personalization/emotes/emote_flare",
    ),
    gear(
        10,
        1480435604090893685,
        "202e6c4f-3c53-49df-b044-e0f860fd8086",
        "globals/personalization/emotes/emote_clap",
    ),
    gear(
        11,
        6765625667593726137,
        "496b391c-2fff-4de0-a42c-9cc3ba8493fa",
        "globals/personalization/emotes/emote_wave_righthand",
    ),
    gear(
        50,
        4832763824465935745,
        "13cf39d8-6952-4425-8225-efd25618e91e",
        "exo/exm_storm/appearances/exm_storm_appearance_default_arms_item",
    ),
    gear(
        49,
        6093856626877650055,
        "55300386-0852-4ccf-8c01-485d242787b8",
        "exo/exm_storm/appearances/exm_storm_appearance_default_helm_item",
    ),
    gear(
        51,
        6038692634780847772,
        "a98849e1-75cf-4e3e-83ed-41787394d104",
        "exo/exm_storm/appearances/exm_storm_appearance_default_torso_item",
    ),
    gear(
        31,
        5793084294715954189,
        "14085844-d6cd-478f-8d7b-5b46ad90ba7d",
        "equipment/gear/storm/kinesis/ultimate/storm_ultimate",
    ),
    gear(
        52,
        4526311268536625446,
        "f7851d5d-904d-4db8-8752-85418020fd88",
        "exo/exm_storm/appearances/exm_storm_appearance_default_legs_item",
    ),
    gear(
        190,
        3905097293101855347,
        "09f19db6-5bf9-4887-af3d-e42030b0fb9e",
        "globals/personalization/victoryposes/victorypose_default",
    ),
    gear(
        189,
        5108361573696785484,
        "52a8a412-8d91-4705-b006-9e4471181d10",
        "globals/personalization/arrivalanims/arrive_default",
    ),
];

const INTERCEPTOR_DEFAULT_GEAR: [GearEntry; 16] = [
    gear(
        1,
        7438346415141347930,
        "0a8a4459-23c4-1abc-7b33-e8a5a7e2fd65",
        "equipment/weapons/pistols/freelancer/weapon_pistol_light_smg_defaultitem",
    ),
    gear(
        218,
        5333535209427681996,
        "39c4c319-ada0-141a-a9ac-1a27563f91d8",
        "equipment/weapons/melee/exf_interceptor/blades/twinblades/default/weapon_melee_base_twinblades_default",
    ),
    gear(
        6,
        8631418977332698986,
        "d6d8f30c-2129-4737-90e7-37367bf9ade8",
        "equipment/gear/shared/gear_flashlight",
    ),
    gear(
        2,
        3628307353216419987,
        "a7781797-bd20-4a65-a3c3-501b20f2991f",
        "equipment/gear/interceptor/acidbomb/interceptor_acidbomb_defaultitem",
    ),
    gear(
        33,
        5982847942097859079,
        "1ad1ed4d-7c45-43ff-a1fc-36d108e430c8",
        "equipment/gear/interceptor/plasmastar/interceptor_plasmastar_defaultitem",
    ),
    gear(
        160,
        6445106557770083110,
        "70f2e821-da77-4482-a4aa-5eb9ac976f30",
        "exo/exf_interceptor/parts/interceptorparts_frame",
    ),
    gear(
        9,
        3957146945700805624,
        "25cecbee-ea01-4b91-a717-1f3237d9296d",
        "globals/personalization/emotes/emote_flare",
    ),
    gear(
        10,
        1480435604090893685,
        "202e6c4f-3c53-49df-b044-e0f860fd8086",
        "globals/personalization/emotes/emote_clap",
    ),
    gear(
        11,
        6765625667593726137,
        "496b391c-2fff-4de0-a42c-9cc3ba8493fa",
        "globals/personalization/emotes/emote_wave_righthand",
    ),
    gear(
        190,
        3905097293101855347,
        "09f19db6-5bf9-4887-af3d-e42030b0fb9e",
        "globals/personalization/victoryposes/victorypose_default",
    ),
    gear(
        189,
        5108361573696785484,
        "52a8a412-8d91-4705-b006-9e4471181d10",
        "globals/personalization/arrivalanims/arrive_default",
    ),
    gear(
        50,
        5318250215994026878,
        "9140c90d-4c55-4bb8-9960-be1f12a23a0f",
        "exo/exf_interceptor/appearances/exf_interceptor_default_arms_item",
    ),
    gear(
        49,
        6423903695352325488,
        "0ac2e1a1-289c-4853-a0e0-b0a89cecef8c",
        "exo/exf_interceptor/appearances/exf_interceptor_default_helm_item",
    ),
    gear(
        52,
        8168868336393581822,
        "8baff95a-c34e-43f3-bb28-d62daf17cff2",
        "exo/exf_interceptor/appearances/exf_interceptor_default_legs_item",
    ),
    gear(
        51,
        5903203976759629915,
        "8c4057f4-7d6f-4858-8635-b327806aaf2e",
        "exo/exf_interceptor/appearances/exf_interceptor_default_torso_item",
    ),
    gear(
        31,
        5856381306552282722,
        "011ababb-4475-4d03-8c32-b938b29f6eb0",
        "equipment/gear/interceptor/ultimate/interceptor_ultimate",
    ),
];

// Homologues non Unlockable des objets par défaut des emplacements 1, 2, 33 et 218, refusés par les listes de la Forge.
const RANGER_BASIC_GEAR: [GearEntry; 4] = [
    gear(
        1,
        6452643257997312772,
        "33ffe482-333d-867d-99cd-0110b402d3d0",
        "equipment/weapons/assaultrifles/freelancer/weapon_assaultrifle_ranger_rapidfire",
    ),
    gear(
        218,
        5664243612819904888,
        "3fb8c4d8-8c1a-648b-77ea-bd30b568d2bf",
        "equipment/weapons/melee/exm_lancer/mace/shockmace/weapon_melee_base_shockmace",
    ),
    gear(
        2,
        1468891257186348531,
        "f3fe9aa3-1bab-4df8-ba6d-9e5732ffe429",
        "equipment/gear/lancer/shoulder/grenade/frag/lancer_grenade_frag",
    ),
    gear(
        33,
        7721281199318676144,
        "cd039129-aaa6-48ab-96f3-204fc0a08129",
        "equipment/gear/lancer/wrist/projectilelauncher/homing/lancer_projectilelauncher_homing",
    ),
];

const COLOSSUS_BASIC_GEAR: [GearEntry; 4] = [
    gear(
        1,
        799379385601999955,
        "0012a061-331a-0697-e291-f701f9309607",
        "equipment/weapons/heavy/autocannon/weapon_heavy_autocannon_ramping",
    ),
    gear(
        33,
        7829027378519606486,
        "75e45bba-5b31-43c7-a789-973c91523d47",
        "equipment/gear/colossus/wrist/cannon/rail_gun/colossus_cannon_rail_gun",
    ),
    gear(
        2,
        7907705977770488292,
        "b0d0196f-3cdb-4df1-a8bd-32eb04ad034a",
        "equipment/gear/colossus/shoulder/mortar/he/colossus_mortar_he",
    ),
    gear(
        218,
        3735768119415995892,
        "9a7c8bd2-f28d-6991-f53c-7f8089a44c61",
        "equipment/weapons/melee/exh_colossus/glove/seismicglove/weapon_melee_base_seismicglove",
    ),
];

const STORM_BASIC_GEAR: [GearEntry; 4] = [
    gear(
        1,
        4800479867345387783,
        "f4bc5fcc-d262-eb2b-6eb4-6597ba34da86",
        "equipment/weapons/assaultrifles/precision/weapon_assaultrifle_precision_singleshot",
    ),
    gear(
        218,
        1722666588054445799,
        "ca3335ee-7fd2-69c0-eef3-c58476177edc",
        "equipment/weapons/melee/exl_storm/strike/explosivestrike/weapon_melee_base_explosivestrike",
    ),
    gear(
        33,
        9213450015076289584,
        "3f75f6fa-6bb1-42d7-8bdb-994959957add",
        "equipment/gear/storm/kinesis/projectile/iceshards/storm_shards_ice",
    ),
    gear(
        2,
        3667652933076444108,
        "3f6afbf5-d703-478f-98d9-0b327af603b9",
        "equipment/gear/storm/kinesis/storm/lightning/storm_lightningstorm",
    ),
];

const INTERCEPTOR_BASIC_GEAR: [GearEntry; 4] = [
    gear(
        1,
        7903095166361125824,
        "65f52592-bd53-d12a-a644-055afa8db01a",
        "equipment/weapons/pistols/freelancer/weapon_pistol_light_smg",
    ),
    gear(
        218,
        7433355267856111836,
        "96d366b7-ca25-8d3f-f120-84741fa043fe",
        "equipment/weapons/melee/exf_interceptor/blades/twinblades/weapon_melee_base_twinblades",
    ),
    gear(
        2,
        5301676759598748402,
        "9b4f3e60-9ff9-48e2-ad08-f07d7c72c95c",
        "equipment/gear/interceptor/acidbomb/interceptor_acidbomb",
    ),
    gear(
        33,
        5304980382892937908,
        "b5a3d7c8-9bf8-4172-8924-5e3706d93bff",
        "equipment/gear/interceptor/plasmastar/interceptor_plasmastar",
    ),
];

const STARTING_RARITY: i32 = 1;
const STARTING_LEVEL: i32 = 1;
const UIDS_PER_EXO: u64 = 100;
const FIRST_FORGE_UID: u64 = 10_001;
const FORMER_FORGE_UIDS_PER_EXO: u64 = 10_000;

fn default_gear(exo_type: i32) -> &'static [GearEntry] {
    match exo_type {
        0 => &RANGER_DEFAULT_GEAR,
        1 => &COLOSSUS_DEFAULT_GEAR,
        2 => &STORM_DEFAULT_GEAR,
        3 => &INTERCEPTOR_DEFAULT_GEAR,
        _ => &[],
    }
}

fn basic_gear(exo_type: i32) -> &'static [GearEntry] {
    match exo_type {
        0 => &RANGER_BASIC_GEAR,
        1 => &COLOSSUS_BASIC_GEAR,
        2 => &STORM_BASIC_GEAR,
        3 => &INTERCEPTOR_BASIC_GEAR,
        _ => &[],
    }
}

fn forge_rank(item_data_id: u64) -> Option<usize> {
    FORGE_ITEMS
        .iter()
        .position(|item| item.item_data_id == item_data_id)
}

fn forge_uid(rank: usize) -> u64 {
    FIRST_FORGE_UID + rank as u64
}

/// UID propres à chaque javelin : 1 à 16 pour l'équipement par défaut du Ranger, 17 à 20 pour ses homologues, 101… pour le Colossus, etc.
fn numbered(exo_type: i32) -> impl Iterator<Item = (u64, &'static GearEntry)> {
    let first = exo_type.max(0) as u64 * UIDS_PER_EXO + 1;
    (first..).zip(default_gear(exo_type).iter().chain(basic_gear(exo_type)))
}

fn numbered_defaults(exo_type: i32) -> impl Iterator<Item = (u64, &'static GearEntry)> {
    numbered(exo_type).take(default_gear(exo_type).len())
}

fn starting_items(exo_type: i32) -> impl Iterator<Item = (u64, &'static GearEntry)> {
    numbered(exo_type).filter(|(_, gear)| forge_rank(gear.item_data_id).is_none())
}

fn is_basic_counterpart(item_data_id: u64) -> bool {
    (0..4).any(|exo_type| {
        basic_gear(exo_type)
            .iter()
            .any(|basic| basic.item_data_id == item_data_id)
    })
}

fn item_instance(uid: u64, gear: &GearEntry) -> Value {
    instance(uid, gear.item_data_id, gear.item_guid, STARTING_RARITY)
}

fn instance(uid: u64, item_data_id: u64, item_guid: &str, rarity: i32) -> Value {
    json!({
        "uid": uid,
        "gameItemId": item_data_id,
        "assetId": item_guid,
        "instanceId": uid.to_string(),
        "rarity": rarity,
        "level": STARTING_LEVEL,
        "count": 1,
        "viewed": true,
        "isJunk": false,
    })
}

/// UID 10001 + rang dans FORGE_ITEMS : un exemplaire par objet compatible avec l'un des javelins possédés.
fn forge_items(exo_types: &[i32]) -> impl Iterator<Item = Value> + '_ {
    FORGE_ITEMS
        .iter()
        .enumerate()
        .filter(move |(_, item)| exo_types.iter().any(|exo| item.exo_types.contains(exo)))
        .map(|(rank, item)| {
            instance(
                forge_uid(rank),
                item.item_data_id,
                item.item_guid,
                item.min_rarity.max(STARTING_RARITY),
            )
        })
}

/// Objets d'inventaire des javelins possédés (équipement par défaut, homologues non Unlockable, objets de la Forge),
/// sans les UID démantelés, IsJunk posé pour les UID mis au rebut.
pub fn inventory_items(exo_types: &[i32], salvaged: &[u64], junk: &[u64]) -> Vec<Value> {
    exo_types
        .iter()
        .flat_map(|&exo_type| starting_items(exo_type).map(|(uid, gear)| item_instance(uid, gear)))
        .chain(forge_items(exo_types))
        .filter(|item| !salvaged.contains(&item["uid"].as_u64().unwrap_or(0)))
        .map(|mut item| {
            item["isJunk"] = json!(junk.contains(&item["uid"].as_u64().unwrap_or(0)));
            item
        })
        .collect()
}

/// Équipement de départ : UID des javelins (défaut et homologues) et objets de la Forge homologues d'un objet par défaut.
pub fn is_starting_gear(uid: u64) -> bool {
    match uid.checked_sub(FIRST_FORGE_UID) {
        None => true,
        Some(rank) => FORGE_ITEMS
            .get(rank as usize)
            .is_some_and(|item| is_basic_counterpart(item.item_data_id)),
    }
}

/// UID actuel d'un UID gardé : anciens UID de la Forge par javelin (20001… Colossus) et homologues devenus objets de la Forge.
pub fn current_uid(uid: u64) -> u64 {
    if uid >= crate::progression::FIRST_EARNED_UID {
        return uid;
    }
    if let Some(offset) = uid.checked_sub(FIRST_FORGE_UID) {
        return FIRST_FORGE_UID + offset % FORMER_FORGE_UIDS_PER_EXO;
    }
    let exo_type = (uid.saturating_sub(1) / UIDS_PER_EXO) as i32;
    numbered(exo_type)
        .find(|(numbered_uid, _)| *numbered_uid == uid)
        .and_then(|(_, gear)| forge_rank(gear.item_data_id))
        .map_or(uid, forge_uid)
}

pub(crate) fn earned_item(uid: u64, item: &ForgeItem, rarity: i32, level: u64) -> Value {
    let mut value = instance(uid, item.item_data_id, item.item_guid, rarity);
    value["level"] = json!(level);
    value["viewed"] = json!(false);
    value
}

/// Initial workbench recipes use known equipment blueprint paths, not invented native recipe DTOs.
pub(crate) fn craft_catalog() -> Vec<Value> {
    let mut recipes = Vec::new();
    for exo in 0..4 {
        for entry in basic_gear(exo) {
            if recipes
                .iter()
                .any(|recipe: &Value| recipe["itemDataId"] == entry.item_data_id)
            {
                continue;
            }
            if let Some(item) = FORGE_ITEMS
                .iter()
                .find(|item| item.item_data_id == entry.item_data_id)
            {
                if item.min_rarity > 1 {
                    continue;
                }
                let name = entry
                    .blueprint
                    .rsplit('/')
                    .next()
                    .unwrap_or("Equipment")
                    .trim_start_matches("weapon_")
                    .trim_end_matches("_defaultitem")
                    .replace('_', " ");
                recipes.push(json!({"recipeId":format!("basic-{}",entry.item_data_id),"name":name,
                    "itemDataId":entry.item_data_id,"exoTypes":item.exo_types,"rarity":item.min_rarity.max(1),
                    "requiredLevel":1,"costs":{"parts":10,"ember_1":2}}));
            }
        }
    }
    recipes
}

/// GameConfigItemSalvageDto {GameItemId, ItemBreakdowns} des objets de la Forge, sans les homologues de l'équipement de départ.
pub fn salvage_table() -> Vec<Value> {
    FORGE_ITEMS
        .iter()
        .filter(|item| !is_basic_counterpart(item.item_data_id))
        .map(|item| json!({ "gameItemId": item.item_data_id.to_string(), "itemBreakdowns": [] }))
        .collect()
}

/// Déblocages (BlueprintPath, ItemGuid) de l'équipement par défaut des javelins possédés, sans doublon.
pub fn unlockables(exo_types: &[i32]) -> Vec<(&'static str, &'static str)> {
    let mut found: Vec<(&'static str, &'static str)> = Vec::new();
    for gear in exo_types
        .iter()
        .flat_map(|&exo_type| default_gear(exo_type))
    {
        if !found.iter().any(|(_, guid)| *guid == gear.item_guid) {
            found.push((gear.blueprint, gear.item_guid));
        }
    }
    found
}

/// LoadoutDylanOnlineAttachment {ItemId, AttachedTo, Slot, ItemUID, AttachedToUID, UnlockId}.
pub fn attachments(exo_type: i32) -> Vec<Value> {
    numbered_defaults(exo_type)
        .map(|(uid, gear)| {
            json!({
                "itemUID": uid,
                "attachedToUID": 0,
                "itemId": uid.to_string(),
                "attachedTo": "",
                "slot": gear.slot,
                "unlockId": gear.item_guid,
            })
        })
        .collect()
}
