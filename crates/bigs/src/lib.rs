use axum::{
    Json, Router,
    body::Bytes,
    extract::State,
    http::{HeaderMap, Method, StatusCode, Uri},
    routing::{get, post},
};
use std::sync::{Arc, Mutex};

use serde_json::{Value, json};

mod characters;
mod ebs;
mod forge_items;
mod gameconfig;
mod gear;
mod mtx;
mod progression;
mod startup;
mod state;
mod weapon_damage;

const TOKEN_LIFETIME_SECONDS: u64 = 86_400;

#[derive(Clone)]
pub struct Context {
    pub persona_id: u64,
    pub player_name: String,
    characters: Arc<Mutex<Vec<characters::Character>>>,
    events: tokio::sync::broadcast::Sender<Value>,
    persistence_body: Option<Arc<Value>>,
    persistence_status: u16,
    persistence_commits: Arc<Mutex<Vec<String>>>,
    commit_directory: Option<Arc<std::path::PathBuf>>,
    state: Arc<Mutex<state::StateFile>>,
    cosmetics: Arc<Vec<Value>>,
    weapon_damage: Arc<Mutex<weapon_damage::WeaponDamage>>,
    new_game: bool,
    character_creator: bool,
}

impl Context {
    pub fn new(persona_id: u64, player_name: &str) -> Self {
        Self {
            persona_id,
            player_name: player_name.to_owned(),
            characters: Arc::new(Mutex::new(vec![characters::Character::new(1, player_name)])),
            events: tokio::sync::broadcast::channel(64).0,
            persistence_body: None,
            persistence_status: 200,
            persistence_commits: Arc::new(Mutex::new(Vec::new())),
            commit_directory: None,
            state: Arc::new(Mutex::new(state::StateFile::in_memory())),
            cosmetics: Arc::new(Vec::new()),
            weapon_damage: Arc::new(Mutex::new(weapon_damage::WeaponDamage::empty())),
            new_game: false,
            character_creator: false,
        }
    }

    pub fn without_characters(self) -> Self {
        self.characters
            .lock()
            .expect("registre des personnages")
            .clear();
        self
    }

    /// Nouvelle partie : seuls les pilotes créés et gardés dans le fichier d'état, et ni pilote créé ni tutoriel
    /// terminé ajoutés à la persistance servie.
    pub fn as_new_game(mut self) -> Self {
        self.new_game = true;
        self.with_saved_characters()
    }

    /// Skip the intro missions without claiming that pilot customization is complete.
    pub fn as_character_creator(mut self) -> Self {
        self.character_creator = true;
        self.with_saved_characters()
    }

    /// Load saved pilots while retaining the normal Fort Tarsis tutorial skip.
    pub fn with_saved_characters(self) -> Self {
        let pilots = self.state.lock().expect("fichier d'état").pilots();
        *self.characters.lock().expect("registre des personnages") = pilots
            .iter()
            .filter_map(characters::Character::from_saved)
            .collect();
        self
    }

    pub fn with_character_id(self, id: &str) -> Self {
        let id: Value = serde_json::from_str(id).expect("BIGS_PERSONNAGE_ID : JSON invalide");
        if let Some(character) = self
            .characters
            .lock()
            .expect("registre des personnages")
            .first_mut()
        {
            character.id = id;
        }
        self
    }

    pub fn with_commit_directory(mut self, directory: &str) -> Self {
        self.commit_directory = Some(Arc::new(directory.into()));
        self
    }

    pub fn with_state_file(mut self, path: &str) -> Self {
        let state = state::StateFile::open(path);
        let introduction = state.full_introduction();
        self.new_game |= introduction;
        self.state = Arc::new(Mutex::new(state));
        if introduction {
            self = self.with_saved_characters();
        }
        self
    }

    pub fn with_cosmetics(mut self, catalog: Vec<Value>) -> Self {
        self.cosmetics = Arc::new(catalog);
        self
    }

    /// Table des dégâts servie en réglages (GET /api/gameconfig/tuning) et modifiée par /editeur/degats.
    pub fn with_weapon_damage(mut self, path: &str) -> Self {
        self.weapon_damage = Arc::new(Mutex::new(weapon_damage::WeaponDamage::open(path)));
        self
    }

    pub fn last_persistence_commit(&self) -> Option<String> {
        self.persistence_commits
            .lock()
            .expect("sauvegardes")
            .last()
            .cloned()
    }

    pub fn with_persistence_status(mut self, status: u16) -> Self {
        self.persistence_status = status;
        self
    }

    pub fn with_persistence_body(mut self, body: &str) -> Self {
        self.persistence_body = Some(Arc::new(
            serde_json::from_str(body).expect("BIGS_PERSISTANCE : JSON invalide"),
        ));
        self
    }
}

const SERVICE_PREFIXES: [&str; 2] = ["/nucleus", "/auth"];

pub fn router(context: Context) -> Router {
    let mut router = service_routes();
    for prefix in SERVICE_PREFIXES {
        router = router.nest(prefix, service_routes());
    }
    router
        .route("/ebs", get(ebs::connect))
        .fallback(log_unknown)
        .layer(axum::middleware::from_fn(log_request))
        .with_state(context)
}

fn service_routes() -> Router<Context> {
    Router::new()
        .route("/progression", get(progression::page))
        .route("/local/onboarding", get(characters::onboarding))
        .route("/progression.js", get(progression::script))
        .route("/local/progression", get(progression::overview))
        .route(
            "/local/progression/characters/{character}",
            get(progression::view),
        )
        .route(
            "/local/progression/characters/{character}/craft",
            post(progression::craft),
        )
        .route(
            "/local/progression/characters/{character}/salvage",
            post(progression::salvage_local),
        )
        .route(
            "/local/progression/characters/{character}/tickets",
            post(progression::stage_ticket),
        )
        .route(
            "/local/progression/characters/{character}/claim",
            post(progression::claim),
        )
        .route("/", get(empty_object).post(exchange_token))
        .route("/token", post(token))
        .route("/connect/token", post(server_token))
        .route(
            "/api/character/{persona}/characters",
            get(characters::list).post(characters::create),
        )
        .route("/api/gameconfig/config", get(gameconfig::config))
        .route("/api/gameconfig/loottables", get(gameconfig::loot_tables))
        .route("/api/gameconfig/recipes", get(gameconfig::recipes))
        .route("/api/gameconfig/infusions", get(gameconfig::infusions))
        .route("/api/gameconfig/salvage", get(gameconfig::salvage))
        .route("/api/gameconfig/tuning", get(gameconfig::tuning))
        .route(
            "/editeur/degats",
            get(gameconfig::editor_damage).put(gameconfig::set_editor_damage),
        )
        .route("/api/gameconfig/mutators", get(gameconfig::mutators))
        .route(
            "/api/gameconfig/factionrewards",
            get(gameconfig::faction_rewards),
        )
        .route("/api/gameconfig/weather/{region}", get(gameconfig::weather))
        .route(
            "/api/gameconfig/weatherevents",
            get(gameconfig::weather_events),
        )
        .route(
            "/api/gameconfig/initialweatherstates",
            get(gameconfig::initial_weather_states),
        )
        .route(
            "/api/localization/{language}/livestrings",
            get(gameconfig::live_strings),
        )
        .route(
            "/api/loadout/{persona}/characters/{character}/skins",
            get(gameconfig::skins),
        )
        .route(
            "/api/character/{persona}/ignore",
            get(gameconfig::ignored_players),
        )
        .route(
            "/api/character/{persona}/favorites",
            get(gameconfig::favorite_players),
        )
        .route(
            "/api/inventory/{persona}/startup",
            post(gameconfig::inventory_startup),
        )
        .route(
            "/api/character/{persona}/characters/{character}/play-time",
            post(gameconfig::play_time),
        )
        .route(
            "/api/gameconfig/questreward/{quest}",
            get(gameconfig::quest_reward),
        )
        .route("/api/playercard/", get(characters::remote_view))
        .route(
            "/api/playercard/persona/{persona}/data",
            get(characters::player_card),
        )
        .route(
            "/api/playercard/persona/{persona}/expedition/",
            get(characters::expedition_remote_view),
        )
        .route(
            "/api/playercard/persona/{persona}/expedition/{expedition}",
            get(characters::expedition_remote_view),
        )
        .route(
            "/api/inventory/{persona}/characters/{character}/backpack/resolve-expedition",
            post(startup::resolve_expedition_backpack),
        )
        .route(
            "/api/activity/{persona}/validate",
            axum::routing::put(startup::validate_meta_milestones),
        )
        .route(
            "/api/socialrewards/{persona}/progress/",
            get(startup::social_rewards_progress),
        )
        .route(
            "/api/guilds/personas/{persona}",
            get(startup::persona_guild),
        )
        .route(
            "/api/character/{character}/persistence",
            get(persistence).put(commit_persistence),
        )
        .route(
            "/api/character/{persona}/characters/active",
            get(characters::active),
        )
        .route(
            "/api/character/{persona}/characters/{character}",
            get(characters::get),
        )
        .route(
            "/api/character/{persona}/characters/{character}/gender",
            axum::routing::put(characters::set_gender),
        )
        .route(
            "/api/character/{persona}/characters/{character}/appearance",
            axum::routing::post(characters::set_appearance),
        )
        .route(
            "/api/character/{persona}/characters/{character}/name",
            axum::routing::put(characters::set_name),
        )
        .route("/api/unlock/{persona}/unlocks", get(startup::unlocks))
        .route(
            "/api/inventory/{persona}/characters/",
            get(startup::inventory),
        )
        .route(
            "/api/inventory/{persona}/characters/{character}",
            get(startup::inventory),
        )
        .route(
            "/api/inventory/{persona}/characters//backpack",
            get(startup::backpack),
        )
        .route(
            "/api/inventory/{persona}/characters/{character}/backpack",
            get(startup::backpack),
        )
        .route(
            "/api/activity/{persona}/characters//challenges",
            get(startup::challenges),
        )
        .route(
            "/api/activity/{persona}/characters/{character}/challenges",
            get(startup::challenges),
        )
        .route(
            "/api/activity/{persona}/characters//progress",
            get(startup::milestone_progress),
        )
        .route(
            "/api/activity/{persona}/characters/{character}/progress",
            get(startup::milestone_progress),
        )
        .route("/api/leaderboards/", get(startup::leaderboard_definitions))
        .route(
            "/api/inventory/{persona}/characters/{character}/items/promosalvaged",
            get(startup::salvaged_promo_items),
        )
        .route(
            "/api/inventory/{persona}/characters/{character}/items/salvage",
            post(startup::salvage_items),
        )
        .route(
            "/api/inventory/{persona}/characters/{character}/junk",
            axum::routing::put(startup::mark_junk),
        )
        .route(
            "/api/loadout/{persona}/characters/{character}/loadouts",
            get(startup::loadouts),
        )
        .route(
            "/api/loadout/{persona}/characters/{character}/loadout/{loadout}",
            axum::routing::put(startup::update_loadout),
        )
        .route(
            "/api/loadout/{persona}/characters/{character}/loadout/{loadout}/default",
            axum::routing::put(startup::set_default_loadout),
        )
        .route(
            "/api/expedition/personas/{persona}/characters/{character}",
            get(startup::active_expedition),
        )
        .route(
            "/api/mtx/{persona}/characters/{character}/claims",
            post(startup::create_claim),
        )
        .route(
            "/editeur/cosmetiques",
            get(startup::editor_cosmetics).put(startup::set_editor_cosmetics),
        )
        .route(
            "/api/unlock/{persona}/unlocks/viewed",
            axum::routing::put(startup::viewed_unlocks),
        )
        .route(
            "/editeur/niveau",
            get(characters::editor_level).put(characters::set_editor_level),
        )
        .route(
            "/editeur/javelins",
            get(mtx::owned_javelins).put(mtx::set_owned_javelins),
        )
        .route("/api/mtx/{persona}/balances", get(mtx::balances))
        .route("/api/mtx/{persona}/offers", get(mtx::offers))
        .route(
            "/api/mtx/{persona}/characters/{character}/buyOffers",
            post(mtx::buy_offers),
        )
        .route("/api/mtx/{persona}/claims/", get(startup::claim))
        .route("/api/mtx/{persona}/claims/{claim}", get(startup::claim))
}

/// Le client lit la réponse en cherchant les motifs « "access_token" : " » et « "expires_in" : », espaces compris.
async fn server_token(State(context): State<Context>, body: Bytes) -> axum::response::Response {
    use axum::response::IntoResponse;
    let grant_type = form_value(&body, "grant_type").unwrap_or_default();
    println!("POST /connect/token : grant_type={grant_type}");
    let token = jwt(context.persona_id, "serveur");
    (
        [(axum::http::header::CONTENT_TYPE, "application/json")],
        format!("{{\"access_token\" : \"{token}\", \"token_type\" : \"Bearer\", \"expires_in\" : {TOKEN_LIFETIME_SECONDS}}}"),
    )
        .into_response()
}

async fn token(State(context): State<Context>, body: Bytes) -> Json<Value> {
    let grant_type = form_value(&body, "grant_type").unwrap_or_default();
    println!(
        "POST /token : grant_type={grant_type}, joueur {}",
        context.persona_id
    );
    Json(json!({
        "access_token": jwt(context.persona_id, "acces"),
        "token_type": "Bearer",
        "expires_in": TOKEN_LIFETIME_SECONDS,
        "refresh_token": jwt(context.persona_id, "renouvellement"),
        "id_token": jwt(context.persona_id, "identite"),
    }))
}

async fn commit_persistence(State(context): State<Context>, body: Bytes) -> progression::Reply {
    let text = String::from_utf8_lossy(&body).into_owned();
    println!(
        "sauvegarde de persistance reçue ({} octets) : {}",
        text.len(),
        text.chars().take(2000).collect::<String>()
    );
    if let Some(directory) = &context.commit_directory {
        let _ = std::fs::create_dir_all(directory.as_ref());
        let _ = std::fs::write(
            directory.join(format!("save_{}.json", epoch_seconds())),
            &text,
        );
    }
    let request: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
    let changes: Vec<Value> = request["persistenceJsonProp"]
        .as_str()
        .and_then(|elements| serde_json::from_str(elements).ok())
        .unwrap_or_default();
    progression::commit_quests(&context, &changes, request["source"] != "editor")
        .map_err(progression::error)?;
    context
        .persistence_commits
        .lock()
        .expect("sauvegardes")
        .push(text);
    Ok(Json(
        json!({ "transactionId": request["transactionId"], "serverEpochTime": epoch_seconds() }),
    ))
}

/// Jeton au format JWT (en-tête, revendications, signature factice en base64url sans remplissage).
fn jwt(persona_id: u64, usage: &str) -> String {
    use base64::Engine;
    let encode =
        |value: &Value| base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(value.to_string());
    let issued = epoch_seconds();
    let header = json!({ "alg": "HS256", "typ": "JWT" });
    let claims = json!({
        "iss": "anthem-serveur",
        "sub": persona_id.to_string(),
        "pid": persona_id.to_string(),
        "psid": persona_id,
        "usage": usage,
        "iat": issued,
        "exp": issued + TOKEN_LIFETIME_SECONDS,
    });
    let signature =
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(format!("anthem-serveur-{usage}"));
    format!("{}.{}.{signature}", encode(&header), encode(&claims))
}

fn epoch_seconds() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_secs())
}

async fn persistence(State(context): State<Context>) -> axum::response::Response {
    use axum::response::IntoResponse;
    let status = StatusCode::from_u16(context.persistence_status).unwrap_or(StatusCode::OK);
    if status == StatusCode::OK {
        let body = match &context.persistence_body {
            Some(body) => (**body).clone(),
            None => {
                let state = context.state.lock().expect("fichier d'état");
                let elements = if context.new_game {
                    state.saved_persistence()
                } else if context.character_creator {
                    state.served_creator_persistence()
                } else {
                    state.served_persistence()
                };
                json!({ "persistenceJsonData": Value::Array(elements).to_string(), "serverEpochTime": epoch_seconds() })
            }
        };
        Json(body).into_response()
    } else {
        status.into_response()
    }
}

async fn empty_object() -> Json<Value> {
    Json(json!({}))
}

async fn exchange_token(State(context): State<Context>, body: Bytes) -> Json<Value> {
    let request: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    println!(
        "POST / (échange de jeton) : tokenType={}, clientId={}",
        request["tokenType"], request["clientId"]
    );
    Json(json!({
        "token": jwt(context.persona_id, "bigs"),
        "expires": TOKEN_LIFETIME_SECONDS,
        "expiresInSec": TOKEN_LIFETIME_SECONDS,
    }))
}

pub(crate) fn form_value(body: &[u8], key: &str) -> Option<String> {
    String::from_utf8_lossy(body)
        .split('&')
        .filter_map(|pair| pair.split_once('='))
        .find(|(name, _)| *name == key)
        .map(|(_, value)| value.to_owned())
}

async fn log_request(
    request: axum::extract::Request,
    next: axum::middleware::Next,
) -> axum::response::Response {
    let line = format!("{} {}", request.method(), request.uri());
    let headers: Vec<String> = request
        .headers()
        .iter()
        .filter(|(name, _)| name.as_str() != "authorization")
        .map(|(name, value)| format!("{name}={}", value.to_str().unwrap_or("?")))
        .collect();
    let (parts, body) = request.into_parts();
    let request_body = axum::body::to_bytes(body, usize::MAX)
        .await
        .unwrap_or_default();
    let response = next
        .run(axum::extract::Request::from_parts(
            parts,
            axum::body::Body::from(request_body.clone()),
        ))
        .await;
    let (parts, body) = response.into_parts();
    let response_body = axum::body::to_bytes(body, usize::MAX)
        .await
        .unwrap_or_default();
    println!(
        "{line} -> {}  [{}]\n  requête ({} octets) : {}\n  réponse ({} octets) : {}",
        parts.status.as_u16(),
        headers.join("; "),
        request_body.len(),
        String::from_utf8_lossy(&request_body),
        response_body.len(),
        String::from_utf8_lossy(&response_body)
    );
    axum::response::Response::from_parts(parts, axum::body::Body::from(response_body))
}

async fn log_unknown(method: Method, uri: Uri, headers: HeaderMap, body: Bytes) -> Json<Value> {
    let header_lines: Vec<String> = headers
        .iter()
        .filter(|(name, _)| name.as_str() != "authorization")
        .map(|(name, value)| format!("  {name}: {}", value.to_str().unwrap_or("?")))
        .collect();
    println!(
        "route inconnue : {method} {uri}\n{}\n  corps ({} octets) : {}",
        header_lines.join("\n"),
        body.len(),
        String::from_utf8_lossy(&body)
    );
    Json(json!({}))
}
