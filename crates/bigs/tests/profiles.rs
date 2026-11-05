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
    fn fresh() -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "anthem-profile-{}-{}.json",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::write(&path, json!({
            "niveau_pilote": 1,
            "inventaire_depart_seul": true,
            "pilotes": [{ "id": 1, "name": "KingSplugorf", "gender": "Male", "appearance": "" }],
        }).to_string()).unwrap();
        Self(path)
    }

    fn context(&self) -> Context {
        Context::new(1000, "Joueur").with_state_file(self.0.to_str().unwrap())
    }
}

impl Drop for Save {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

async fn request(app: &Router, method: &str, uri: &str, body: Value) -> Value {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(uri)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), 200);
    serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap()
}

#[tokio::test]
async fn fresh_hub_pilot_keeps_identity_and_saved_level_across_restarts() {
    let save = Save::fresh();
    let app = router(save.context().with_saved_characters());
    let active = request(
        &app,
        "GET",
        "/api/character/1000/characters/active",
        Value::Null,
    )
    .await;
    assert_eq!(active["character"]["name"], "KingSplugorf");
    assert_eq!(active["character"]["gender"], "Male");
    assert_eq!(active["character"]["level"], 1);
    assert_eq!(active["character"]["xp"], 0);
    let persistence = request(&app, "GET", "/api/character/1000/persistence", Value::Null).await;
    let elements: Vec<Value> =
        serde_json::from_str(persistence["persistenceJsonData"].as_str().unwrap()).unwrap();
    assert!(
        elements
            .iter()
            .any(|element| element["DefinitionId"] == 1963412089_u64)
    );

    request(
        &app,
        "PUT",
        "/api/character/1000/characters/1/name",
        json!({"name":"Saved pilot"}),
    )
    .await;
    request(&app, "PUT", "/editeur/niveau", json!({"level":2})).await;
    drop(app);
    let restarted = router(save.context().with_saved_characters());
    let active = request(
        &restarted,
        "GET",
        "/api/character/1000/characters/active",
        Value::Null,
    )
    .await;
    assert_eq!(active["character"]["name"], "Saved pilot");
    assert_eq!(active["character"]["gender"], "Male");
    assert_eq!(active["character"]["level"], 2);
    assert_eq!(active["character"]["xp"], 1000);
}

#[tokio::test]
async fn fresh_inventory_omits_development_catalog_and_card_agrees_with_pilot() {
    let save = Save::fresh();
    let app = router(save.context().with_saved_characters());
    let fresh = request(&app, "GET", "/api/inventory/1000/characters/1", Value::Null).await;
    let development = router(Context::new(1000, "Joueur"));
    let full = request(
        &development,
        "GET",
        "/api/inventory/1000/characters/1",
        Value::Null,
    )
    .await;
    assert!(fresh["items"].as_array().unwrap().len() > 0);
    assert!(fresh["items"].as_array().unwrap().len() < full["items"].as_array().unwrap().len());
    let card = request(&app, "GET", "/api/playercard/?personaIds=1000", Value::Null).await;
    assert_eq!(
        card["data"][0]["activeCharacter"]["activeCharacterName"],
        "KingSplugorf"
    );
    assert_eq!(
        card["data"][0]["activeCharacter"]["activeCharacterGender"],
        "Male"
    );
    for attachment in card["data"][0]["activeCharacterLoadout"]["gear"]["items"]
        .as_array()
        .unwrap()
    {
        if attachment["uid"] != 0 {
            assert!(
                fresh["items"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|item| item["uid"] == attachment["uid"])
            );
        }
    }
}

#[tokio::test]
async fn tutorial_mode_does_not_inject_hub_tutorial_completion() {
    let save = Save::fresh();
    let app = router(save.context().as_new_game());
    let persistence = request(&app, "GET", "/api/character/1000/persistence", Value::Null).await;
    assert_eq!(persistence["persistenceJsonData"], "[]");
}

#[tokio::test]
async fn creator_starts_empty_and_preserves_customization_and_completion_on_restart() {
    let save = Save::fresh();
    let mut initial: Value = serde_json::from_slice(&std::fs::read(&save.0).unwrap()).unwrap();
    initial["pilotes"] = json!([]);
    std::fs::write(&save.0, initial.to_string()).unwrap();
    let app = router(save.context().as_character_creator());
    let pilots = request(&app, "GET", "/api/character/1000/characters", Value::Null).await;
    assert_eq!(pilots["characters"], json!([]));
    let persistence = request(&app, "GET", "/api/character/1000/persistence", Value::Null).await;
    let elements: Vec<Value> =
        serde_json::from_str(persistence["persistenceJsonData"].as_str().unwrap()).unwrap();
    let created = elements
        .iter()
        .find(|e| e["DefinitionId"] == 1963412089_u64)
        .unwrap();
    assert_eq!(created["RegisteredDefPropArray"][0]["Value"], false);
    assert!(elements.iter().any(|e| e["DefinitionId"] == 1835450465_u64));

    let created = request(&app, "POST", "/api/character/1000/characters", json!({})).await;
    assert_eq!(created["characterId"], "1");
    request(
        &app,
        "PUT",
        "/api/character/1000/characters/1/name",
        json!({"name":"New pilot"}),
    )
    .await;
    request(
        &app,
        "PUT",
        "/api/character/1000/characters/1/gender",
        json!({"gender":"Male"}),
    )
    .await;
    request(
        &app,
        "POST",
        "/api/character/1000/characters/1/appearance",
        json!({"appearanceData":"(0,6)"}),
    )
    .await;
    request(&app, "PUT", "/api/character/1000/persistence", json!({"persistenceJsonProp":json!([
        {"DefinitionId":1963412089_u64,"RegisteredDefPropArray":[{"PropertyId":3031061379_u64,"Value":true}]}
    ]).to_string()})).await;
    drop(app);

    let restarted = router(save.context().as_character_creator());
    let active = request(
        &restarted,
        "GET",
        "/api/character/1000/characters/active",
        Value::Null,
    )
    .await;
    assert_eq!(active["character"]["name"], "New pilot");
    assert_eq!(active["character"]["gender"], "Male");
    assert_eq!(active["character"]["appearanceData"], "(0,6)");
    assert_eq!(active["character"]["level"], 1);
    let card = request(
        &restarted,
        "GET",
        "/api/playercard/?personaIds=1000",
        Value::Null,
    )
    .await;
    assert_eq!(
        card["data"][0]["activeCharacter"]["activeCharacterAppearanceData"],
        "(0,6)"
    );
    let persistence = request(
        &restarted,
        "GET",
        "/api/character/1000/persistence",
        Value::Null,
    )
    .await;
    let elements: Vec<Value> =
        serde_json::from_str(persistence["persistenceJsonData"].as_str().unwrap()).unwrap();
    let created = elements
        .iter()
        .find(|e| e["DefinitionId"] == 1963412089_u64)
        .unwrap();
    assert_eq!(created["RegisteredDefPropArray"][0]["Value"], true);
}

#[tokio::test]
async fn full_introduction_keeps_native_flags_and_explicit_identity_across_restart() {
    let save = Save::fresh();
    std::fs::write(&save.0, json!({"story_mode":"tutorial","niveau_pilote":1,"inventaire_depart_seul":true,"pilotes":[]}).to_string()).unwrap();
    let app = router(save.context());
    let before = std::fs::read(&save.0).unwrap();
    let status = request(&app, "GET", "/local/onboarding", Value::Null).await;
    assert_eq!(status["mode"], "full-introduction");
    assert_eq!(status["pilotCount"], 0);
    assert_eq!(status["tutorialCompletionInjected"], false);
    assert_eq!(status["heartOfRage"]["savedFlags"], Value::Null);
    assert_eq!(std::fs::read(&save.0).unwrap(), before);
    let persistence = request(&app, "GET", "/api/character/1000/persistence", Value::Null).await;
    assert_eq!(persistence["persistenceJsonData"], "[]");
    let created = request(
        &app,
        "POST",
        "/api/character/1000/characters",
        json!({"character":{"name":"KingSplugorf","gender":"Male","appearanceData":"(0,6)"}}),
    )
    .await;
    assert_eq!(created["character"]["xp"], 0);
    assert_eq!(created["character"]["gender"], "Male");
    for definition in [1835450465_u64, 1910188215_u64] {
        let payload = json!({"persistenceJsonProp":json!([{"DefinitionId":definition,"RegisteredDefPropArray":[{"PropertyId":1,"Value":11}]}]).to_string()});
        request(
            &app,
            "PUT",
            "/api/character/1000/persistence",
            payload.clone(),
        )
        .await;
        request(&app, "PUT", "/api/character/1000/persistence", payload).await;
    }
    drop(app);
    let restarted = router(save.context());
    let active = request(
        &restarted,
        "GET",
        "/api/character/1000/characters/active",
        Value::Null,
    )
    .await;
    assert_eq!(active["character"]["name"], "KingSplugorf");
    assert_eq!(active["character"]["appearanceData"], "(0,6)");
    assert_eq!(active["character"]["xp"], 2000);
    let status = request(&restarted, "GET", "/local/onboarding", Value::Null).await;
    assert_eq!(status["heartOfRage"]["completed"], true);
    assert_eq!(status["shaperRuin"]["completed"], true);
    assert_eq!(status["pilotCreated"], Value::Null);
}

#[tokio::test]
async fn invalid_creation_does_not_change_save_or_create_a_pilot() {
    let save = Save::fresh();
    let app = router(save.context().with_saved_characters());
    let before = std::fs::read(&save.0).unwrap();
    for body in ["{", "[]", "{\"name\":\"bad\\nname\"}"] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/character/1000/characters")
                    .body(Body::from(body))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), 400);
        assert_eq!(std::fs::read(&save.0).unwrap(), before);
    }
}
