use axum::{Router, body::Body, http::Request};
use bigs::{Context, router};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use std::{
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};
use tower::ServiceExt;

struct Save(PathBuf);
impl Save {
    fn new(mut data: Value) -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "anthem-progression-{}-{}.json",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        if data.get("pilotes").is_none() {
            data["pilotes"] =
                json!([{"id":1,"name":"KingSplugorf","gender":"Male","appearance":""}]);
        }
        std::fs::write(&path, data.to_string()).unwrap();
        Self(path)
    }
    fn app(&self) -> Router {
        router(
            Context::new(1000, "Joueur")
                .with_state_file(self.0.to_str().unwrap())
                .with_saved_characters(),
        )
    }
    fn read(&self) -> Value {
        serde_json::from_slice(&std::fs::read(&self.0).unwrap()).unwrap()
    }
}
impl Drop for Save {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
        let _ = std::fs::remove_file(self.0.with_extension("json.tmp"));
    }
}
async fn call(app: &Router, method: &str, path: &str, body: Value) -> (u16, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(path)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status().as_u16();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap())
}
async fn ok(app: &Router, method: &str, path: &str, body: Value) -> Value {
    let (status, value) = call(app, method, path, body).await;
    assert_eq!(status, 200, "{value}");
    value
}
async fn view(app: &Router) -> Value {
    ok(app, "GET", "/local/progression/characters/1", Value::Null).await
}
fn quests() -> Vec<Value> {
    serde_json::from_str(include_str!("../src/progression_quests.json")).unwrap()
}
fn flags(definition: &Value, value: u64) -> Value {
    json!({"DefinitionId":definition,"RegisteredDefPropArray":[{"PropertyId":1,"Value":value}]})
}
async fn complete(app: &Router, definition: &Value, source: &str, value: u64) {
    ok(
        app,
        "PUT",
        "/api/character/1000/persistence",
        json!({"transactionId":1,"source":source,
        "persistenceJsonProp":json!([flags(definition,value)]).to_string()}),
    )
    .await;
}
async fn earn(app: &Router) {
    complete(app, &quests()[0]["definitionId"], "game", 11).await;
}

#[tokio::test]
async fn first_mission_completion_persists_exact_xp_loot_materials_and_idempotency() {
    let save = Save::new(
        json!({"niveau_pilote":1,"inventaire_depart_seul":true,"custom":{"preserve":true}}),
    );
    let app = save.app();
    earn(&app).await;
    let state = view(&app).await;
    assert_eq!(state["xp"], 1000);
    assert_eq!(state["level"], 2);
    assert_eq!(save.read()["niveau_pilote"], 2);
    assert_eq!(state["materials"], json!({"parts":10,"ember_1":3}));
    assert_eq!(state["items"].as_array().unwrap().len(), 1);
    assert_eq!(state["items"][0]["level"], 2);
    assert!(state["items"][0]["gameItemId"].is_string());
    earn(&app).await;
    assert_eq!(state, view(&app).await);
    drop(app);
    let restarted = save.app();
    earn(&restarted).await;
    assert_eq!(state, view(&restarted).await);
    let active = ok(
        &restarted,
        "GET",
        "/api/character/1000/characters/active",
        Value::Null,
    )
    .await;
    let card = ok(
        &restarted,
        "GET",
        "/api/playercard/?personaIds=1000",
        Value::Null,
    )
    .await;
    assert_eq!(active["character"]["xp"], 1000);
    assert_eq!(
        card["data"][0]["activeCharacter"]["activeCharacterXp"],
        1000
    );
    assert_eq!(active["character"]["name"], "KingSplugorf");
    assert_eq!(active["character"]["gender"], "Male");
    assert_eq!(save.read()["custom"], json!({"preserve":true}));
}

#[tokio::test]
async fn no_reward_for_unrelated_flags_incomplete_missions_abandonment_or_replayed_reset() {
    let save = Save::new(json!({}));
    let app = save.app();
    complete(&app, &json!(123), "game", 11).await;
    complete(&app, &quests()[0]["definitionId"], "game", 3).await;
    ok(
        &app,
        "POST",
        "/api/inventory/1000/characters/1/backpack/resolve-expedition",
        json!({}),
    )
    .await;
    assert_eq!(view(&app).await["xp"], 0);
    earn(&app).await;
    complete(&app, &quests()[0]["definitionId"], "game", 5).await;
    earn(&app).await;
    assert_eq!(view(&app).await["xp"], 1000);
}

#[tokio::test]
async fn legacy_level_thirty_completed_quests_and_unknown_fields_are_preserved() {
    let definition = quests()[0]["definitionId"].clone();
    let save = Save::new(
        json!({"niveau_pilote":30,"persistence":[flags(&definition,11)],"offres_achetees":["Offer_Exo_Storm"],"custom":"keep"}),
    );
    let before = save.read();
    let app = save.app();
    assert_eq!(view(&app).await["xp"], 270850);
    assert_eq!(before, save.read()); // Reading is not a save migration.
    earn(&app).await;
    assert_eq!(view(&app).await["items"], json!([]));
    complete(&app, &quests()[1]["definitionId"], "game", 11).await;
    let state = view(&app).await;
    assert_eq!(state["xp"], 270850);
    assert_eq!(state["level"], 30);
    assert_eq!(state["items"].as_array().unwrap().len(), 1);
    assert_eq!(save.read()["offres_achetees"], before["offres_achetees"]);
    assert_eq!(save.read()["custom"], "keep");
}

#[tokio::test]
async fn editor_completions_do_not_mint_rewards_even_after_reset_and_recompletion() {
    let save = Save::new(json!({}));
    let app = save.app();
    complete(&app, &quests()[0]["definitionId"], "editor", 11).await;
    complete(&app, &quests()[0]["definitionId"], "game", 5).await;
    earn(&app).await;
    assert_eq!(view(&app).await["xp"], 0);
    assert_eq!(view(&app).await["items"], json!([]));
}

#[tokio::test]
async fn crafting_debits_once_and_native_inventory_and_loadout_preserve_earned_uid() {
    let save = Save::new(json!({"inventaire_depart_seul":true}));
    let app = save.app();
    earn(&app).await;
    let state = view(&app).await;
    let recipe = state["recipes"][0]["recipeId"].clone();
    assert!(recipe.is_string());
    let request = json!({"transactionId":"craft-1","recipeId":recipe});
    let result = ok(
        &app,
        "POST",
        "/local/progression/characters/1/craft",
        request.clone(),
    )
    .await;
    let uid = result["item"]["uid"].clone();
    assert!(uid.as_u64().unwrap() >= 1_000_000_000);
    assert_eq!(result["materials"], json!({"parts":0,"ember_1":1}));
    assert_eq!(
        result,
        ok(
            &app,
            "POST",
            "/local/progression/characters/1/craft",
            request.clone()
        )
        .await
    );
    let inventory = ok(&app, "GET", "/api/inventory/1000/characters/1", Value::Null).await;
    assert!(
        inventory["items"]
            .as_array()
            .unwrap()
            .iter()
            .any(|item| item["uid"] == uid)
    );
    ok(
        &app,
        "PUT",
        "/api/loadout/1000/characters/1/loadout/1",
        json!({"gear":{"attachments":[{"slot":1,"itemUID":uid}]}}),
    )
    .await;
    drop(app);
    let restarted = save.app();
    assert_eq!(
        result,
        ok(
            &restarted,
            "POST",
            "/local/progression/characters/1/craft",
            request
        )
        .await
    );
    let loadouts = ok(
        &restarted,
        "GET",
        "/api/loadout/1000/characters/1/loadouts",
        Value::Null,
    )
    .await;
    assert_eq!(
        loadouts["loadouts"][0]["gear"]["attachments"][0]["itemUID"],
        uid
    );
    let card = ok(
        &restarted,
        "GET",
        "/api/playercard/?personaIds=1000",
        Value::Null,
    )
    .await;
    assert_eq!(
        card["data"][0]["activeCharacterLoadout"]["gear"]["items"][0]["item"]["uid"],
        uid
    );
    let before = save.read();
    let (status, _) = call(
        &restarted,
        "POST",
        "/local/progression/characters/1/salvage",
        json!({"transactionId":"salvage-equipped","uids":[uid]}),
    )
    .await;
    assert_eq!(status, 400);
    assert_eq!(before, save.read());
}

#[tokio::test]
async fn invalid_crafts_and_receipt_conflicts_leave_wallet_and_disk_unchanged() {
    let save = Save::new(json!({}));
    let app = save.app();
    let state = view(&app).await;
    let recipe = state["recipes"][0]["recipeId"].clone();
    let before = save.read();
    let (status, _) = call(
        &app,
        "POST",
        "/local/progression/characters/1/craft",
        json!({"transactionId":"poor","recipeId":recipe}),
    )
    .await;
    assert_eq!(status, 409);
    assert_eq!(before, save.read());
    earn(&app).await;
    let result = ok(
        &app,
        "POST",
        "/local/progression/characters/1/craft",
        json!({"transactionId":"shared","recipeId":recipe}),
    )
    .await;
    let before = save.read();
    let (status, _) = call(
        &app,
        "POST",
        "/local/progression/characters/1/salvage",
        json!({"transactionId":"shared","uids":[result["item"]["uid"]]}),
    )
    .await;
    assert_eq!(status, 409);
    assert_eq!(before, save.read());
    let (status, _) = call(
        &app,
        "POST",
        "/local/progression/characters/1/craft",
        json!({"transactionId":"unknown","recipeId":"invented"}),
    )
    .await;
    assert_eq!(status, 400);
    assert_eq!(before, save.read());
}

#[tokio::test]
async fn salvage_earned_loot_rewards_materials_once_and_rejects_starters_and_unknowns() {
    let save = Save::new(json!({}));
    let app = save.app();
    earn(&app).await;
    let uid = view(&app).await["items"][0]["uid"].clone();
    let result = ok(
        &app,
        "POST",
        "/api/inventory/1000/characters/1/items/salvage",
        json!({"itemUIDs":[uid,uid]}),
    )
    .await;
    assert_eq!(result, json!({"currencyItems":[],"unlocks":[]}));
    let state = view(&app).await;
    assert_eq!(state["materials"], json!({"parts":14,"ember_1":4}));
    assert_eq!(state["items"], json!([]));
    ok(
        &app,
        "POST",
        "/api/inventory/1000/characters/1/items/salvage",
        json!({"itemUIDs":[uid]}),
    )
    .await;
    assert_eq!(state, view(&app).await);
    let before = save.read();
    for ids in [json!([1]), json!([999999999]), json!([uid, 999999999])] {
        let (status, _) = call(
            &app,
            "POST",
            "/local/progression/characters/1/salvage",
            json!({"transactionId":"bad-salvage","uids":ids}),
        )
        .await;
        assert_eq!(status, 400);
        assert_eq!(before, save.read());
    }
}

#[tokio::test]
async fn backpack_ticket_and_resolution_are_persistent_and_do_not_duplicate_or_award_xp() {
    let save = Save::new(json!({}));
    let app = save.app();
    let request = json!({"transactionId":"drop-1","ticket":"bigs-butin-rarete-1"});
    let result = ok(
        &app,
        "POST",
        "/local/progression/characters/1/tickets",
        request.clone(),
    )
    .await;
    assert_eq!(
        result,
        ok(
            &app,
            "POST",
            "/local/progression/characters/1/tickets",
            request
        )
        .await
    );
    let backpack = ok(
        &app,
        "GET",
        "/api/inventory/1000/characters/1/backpack",
        Value::Null,
    )
    .await;
    assert_eq!(backpack["items"].as_array().unwrap().len(), 1);
    drop(app);
    let restarted = save.app();
    let resolved = ok(
        &restarted,
        "POST",
        "/api/inventory/1000/characters/1/backpack/resolve-expedition",
        json!({}),
    )
    .await;
    assert_eq!(resolved["sourceIds"], json!([result["item"]["uid"]]));
    assert_eq!(resolved["targetIds"], resolved["sourceIds"]);
    ok(
        &restarted,
        "POST",
        "/api/inventory/1000/characters/1/backpack/resolve-expedition",
        json!({}),
    )
    .await;
    assert_eq!(view(&restarted).await["items"].as_array().unwrap().len(), 1);
    assert_eq!(view(&restarted).await["xp"], 0);
    let before = save.read();
    let (status, _) = call(
        &restarted,
        "POST",
        "/local/progression/characters/1/tickets",
        json!({"transactionId":"legendary","ticket":"bigs-butin-rarete-6"}),
    )
    .await;
    assert_eq!(status, 400);
    assert_eq!(before, save.read());
}

#[tokio::test]
async fn concurrent_duplicate_crafts_debit_one_time() {
    let save = Save::new(json!({}));
    let app = save.app();
    earn(&app).await;
    let recipe = view(&app).await["recipes"][0]["recipeId"].clone();
    let request = json!({"transactionId":"same-intent","recipeId":recipe});
    let (first, second) = tokio::join!(
        call(
            &app,
            "POST",
            "/local/progression/characters/1/craft",
            request.clone()
        ),
        call(
            &app,
            "POST",
            "/local/progression/characters/1/craft",
            request
        )
    );
    assert_eq!(first.0, 200);
    assert_eq!(first, second);
    assert_eq!(view(&app).await["items"].as_array().unwrap().len(), 2);
    assert_eq!(view(&app).await["materials"]["parts"], 0);
}

#[tokio::test]
async fn new_pilot_starts_at_zero_without_resetting_legacy_pilot_or_other_profile() {
    let save = Save::new(json!({"niveau_pilote":30}));
    let app = save.app();
    let created = ok(&app, "POST", "/api/character/1000/characters", json!({})).await;
    assert_eq!(created["character"]["level"], 1);
    assert_eq!(created["character"]["xp"], 0);
    assert_eq!(view(&app).await["xp"], 270850);
    let new = ok(&app, "GET", "/local/progression/characters/2", Value::Null).await;
    assert_eq!(new["xp"], 0);
    let other = Save::new(json!({}));
    assert_eq!(view(&other.app()).await["xp"], 0);
}

#[tokio::test]
async fn disk_failure_rolls_back_reward_and_quest_persistence() {
    let save = Save::new(json!({"marker":"keep"}));
    let before = save.read();
    let app = save.app();
    let backup = save.0.with_extension("backup");
    std::fs::rename(&save.0, &backup).unwrap();
    std::fs::create_dir(&save.0).unwrap();
    let (status, _) = call(
        &app,
        "PUT",
        "/api/character/1000/persistence",
        json!({"persistenceJsonProp":json!([flags(&quests()[0]["definitionId"],11)]).to_string()}),
    )
    .await;
    assert_eq!(status, 500);
    assert_eq!(view(&app).await["xp"], 0);
    assert_eq!(view(&app).await["items"], json!([]));
    std::fs::remove_dir(&save.0).unwrap();
    std::fs::rename(&backup, &save.0).unwrap();
    assert_eq!(save.read(), before);
}

#[test]
fn damaged_save_is_rejected_without_replacing_it() {
    let save = Save::new(json!({}));
    std::fs::write(&save.0, b"{damaged").unwrap();
    assert!(std::panic::catch_unwind(|| save.app()).is_err());
    assert_eq!(std::fs::read(&save.0).unwrap(), b"{damaged");
}

#[tokio::test]
async fn editor_level_range_is_checked_and_exact_partial_xp_is_retained() {
    let save = Save::new(json!({"niveau_pilote":1,"progression":{"1":{"schema":1,"xp":1500}}}));
    let app = save.app();
    let active = ok(
        &app,
        "GET",
        "/api/character/1000/characters/active",
        Value::Null,
    )
    .await;
    assert_eq!(active["character"]["level"], 2);
    assert_eq!(active["character"]["xp"], 1500);
    for level in [0, 31] {
        assert_eq!(
            call(&app, "PUT", "/editeur/niveau", json!({"level":level}))
                .await
                .0,
            400
        );
    }
    assert_eq!(view(&app).await["xp"], 1500);
    assert_eq!(
        call(
            &app,
            "GET",
            "/local/progression/characters/missing",
            Value::Null
        )
        .await
        .0,
        404
    );
}

#[tokio::test]
async fn only_final_quest_flags_in_a_commit_can_award_rewards() {
    let save = Save::new(json!({}));
    let app = save.app();
    let definition = quests()[0]["definitionId"].clone();
    ok(
        &app,
        "PUT",
        "/api/character/1000/persistence",
        json!({"persistenceJsonProp":json!([
        flags(&definition,11),flags(&definition,5)
    ]).to_string()}),
    )
    .await;
    assert_eq!(view(&app).await["xp"], 0);
}

#[tokio::test]
async fn full_inventory_defers_loot_without_blocking_quest_save_and_claims_it_after_salvage() {
    let save = Save::new(json!({}));
    let app = save.app();
    earn(&app).await;
    drop(app);
    let mut data = save.read();
    let template = data["progression"]["1"]["items"][0].clone();
    data["progression"]["1"]["items"] = json!(
        (0..1000)
            .map(|index| {
                let mut item = template.clone();
                item["uid"] = json!(1_000_000_000_u64 + index);
                item
            })
            .collect::<Vec<_>>()
    );
    data["next_earned_uid"] = json!(1_000_001_000_u64);
    std::fs::write(&save.0, data.to_string()).unwrap();
    let app = save.app();
    complete(&app, &quests()[1]["definitionId"], "game", 11).await;
    let state = view(&app).await;
    assert_eq!(state["xp"], 2000);
    assert_eq!(state["mailbox"].as_array().unwrap().len(), 1);
    ok(
        &app,
        "POST",
        "/local/progression/characters/1/salvage",
        json!({"transactionId":"make-room","uids":[1_000_000_000_u64]}),
    )
    .await;
    ok(
        &app,
        "POST",
        "/local/progression/characters/1/claim",
        json!({}),
    )
    .await;
    let state = view(&app).await;
    assert_eq!(state["items"].as_array().unwrap().len(), 1000);
    assert_eq!(state["mailbox"], json!([]));
    let ids = state["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item["uid"].as_u64().unwrap())
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(ids.len(), 1000);
    assert!(ids.contains(&1_000_001_000_u64));
}

#[tokio::test]
async fn corrupted_progression_record_is_not_silently_reset_by_operations() {
    let save = Save::new(json!({"progression":{"1":{"schema":1,"xp":1000,"items":"broken"}}}));
    let before = save.read();
    let app = save.app();
    assert_eq!(
        call(&app, "GET", "/local/progression/characters/1", Value::Null)
            .await
            .0,
        500
    );
    let (status, _) = call(
        &app,
        "PUT",
        "/api/character/1000/persistence",
        json!({"persistenceJsonProp":"[]"}),
    )
    .await;
    assert_eq!(status, 500);
    assert_eq!(before, save.read());
}

#[tokio::test]
async fn javelin_unlock_budget_grows_with_levels_and_cannot_be_overspent_concurrently() {
    let save = Save::new(json!({}));
    let app = save.app();
    let a = json!({"offers":[{"offerId":"Offer_Exo_Storm"}]});
    let b = json!({"offers":[{"offerId":"Offer_Exo_Colossus"}]});
    let (first, second) = tokio::join!(
        call(&app, "POST", "/api/mtx/1000/characters/1/buyOffers", a),
        call(&app, "POST", "/api/mtx/1000/characters/1/buyOffers", b)
    );
    assert_eq!(first.0, 200);
    assert_eq!(second.0, 200);
    assert_eq!(save.read()["offres_achetees"].as_array().unwrap().len(), 1);
    let balance = ok(
        &app,
        "GET",
        "/api/mtx/1000/balances?currencyAlias=CurrencyType_Javelin",
        Value::Null,
    )
    .await;
    assert_eq!(balance["balances"][0]["balance"], 0);
    ok(&app, "PUT", "/editeur/niveau", json!({"level":8})).await;
    let balance = ok(
        &app,
        "GET",
        "/api/mtx/1000/balances?currencyAlias=CurrencyType_Javelin",
        Value::Null,
    )
    .await;
    assert_eq!(balance["balances"][0]["balance"], 1);
}

#[tokio::test]
async fn unicode_persistence_preview_cannot_panic_or_discard_saved_fields() {
    let save = Save::new(json!({}));
    let app = save.app();
    let payload = json!([{"DefinitionId":123,"RegisteredDefPropArray":[{"PropertyId":99,"Value":"é".repeat(2200)}]}]);
    ok(
        &app,
        "PUT",
        "/api/character/1000/persistence",
        json!({"persistenceJsonProp":payload.to_string()}),
    )
    .await;
    assert_eq!(save.read()["persistence"], payload);
    assert_eq!(view(&app).await["xp"], 0);
}

#[tokio::test]
async fn invalid_or_missing_equipment_counter_cannot_reuse_an_existing_uid() {
    for data in [
        json!({"next_earned_uid":"bad"}),
        json!({"progression":{"1":{"schema":1,"xp":0,"items":[{"uid":1_000_000_000_u64}]}}}),
    ] {
        let save = Save::new(data);
        let before = save.read();
        let app = save.app();
        let (status, _) = call(
            &app,
            "POST",
            "/local/progression/characters/1/tickets",
            json!({"transactionId":"drop","ticket":"bigs-butin-rarete-1"}),
        )
        .await;
        assert_eq!(status, 500);
        assert_eq!(save.read(), before);
    }
}

#[tokio::test]
async fn creating_a_pilot_does_not_reuse_a_retired_progression_record() {
    let save =
        Save::new(json!({"progression":{"2":{"schema":1,"xp":1000,"materials":{"parts":999}}}}));
    let app = save.app();
    let created = ok(&app, "POST", "/api/character/1000/characters", json!({})).await;
    assert_eq!(created["characterId"], "3");
    assert_eq!(created["character"]["xp"], 0);
    assert_eq!(save.read()["progression"]["2"]["materials"]["parts"], 999);
}

#[tokio::test]
async fn mixed_salvage_batch_rolls_back_the_first_valid_item_when_the_last_is_unknown() {
    let save = Save::new(json!({}));
    let app = save.app();
    earn(&app).await;
    let uid = view(&app).await["items"][0]["uid"].clone();
    let before = save.read();
    let (status, _) = call(
        &app,
        "POST",
        "/local/progression/characters/1/salvage",
        json!({"transactionId":"mixed","uids":[uid,9_000_000_000_000_u64]}),
    )
    .await;
    assert_eq!(status, 400);
    assert_eq!(before, save.read());
}

#[tokio::test]
async fn hub_tutorial_skip_commits_do_not_mint_intro_rewards() {
    let save = Save::new(json!({"niveau_pilote":1}));
    let app = save.app();
    for definition in [json!(1835450465_u64), json!(1910188215_u64)] {
        complete(&app, &definition, "game", 11).await;
    }
    assert_eq!(view(&app).await["xp"], 0);
}
