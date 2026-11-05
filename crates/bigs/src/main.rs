use std::env;

use bigs::{Context, router};
use tokio::net::TcpListener;

fn env_or(name: &str, default: &str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_owned())
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let port: u16 = env_or("BIGS_PORT", "8080")
        .parse()
        .expect("BIGS_PORT invalide");
    let context = Context::new(
        env_or("JOUEUR_ID", "1000")
            .parse()
            .expect("JOUEUR_ID invalide"),
        &env_or("JOUEUR_NOM", "Joueur"),
    );
    let context = context.with_persistence_status(
        env_or("BIGS_PERSISTANCE_STATUT", "200")
            .parse()
            .expect("BIGS_PERSISTANCE_STATUT : code HTTP"),
    );
    let context = context.with_commit_directory(&env_or("BIGS_SAUVEGARDES", "data/saves"));
    let context = context.with_state_file(&env_or("BIGS_ETAT", "data/saves/state.json"));
    let cosmetics_path = env_or("BIGS_COSMETIQUES", "data/game/cosmetics.json");
    let context = match std::fs::read(&cosmetics_path) {
        Ok(bytes) => context.with_cosmetics(
            serde_json::from_slice(&bytes).expect("BIGS_COSMETIQUES : tableau JSON attendu"),
        ),
        Err(_) => {
            eprintln!(
                "catalogue des cosmétiques {cosmetics_path} absent : tools/catalogue_cosmetiques.py le crée"
            );
            context
        }
    };
    let context =
        context.with_weapon_damage(&env_or("BIGS_DEGATS", "data/game/weapon_damage.json"));
    let context = match env::var("BIGS_PERSISTANCE") {
        Ok(body) => context.with_persistence_body(&body),
        Err(_) => context,
    };
    let context = match env::var("BIGS_PERSONNAGE_ID") {
        Ok(id) => context.with_character_id(&id),
        Err(_) => context,
    };
    let context = if env::var("BIGS_NOUVELLE_PARTIE").is_ok() {
        context.as_new_game()
    } else {
        context
    };
    let context = if env::var("BIGS_SANS_PERSONNAGE").is_ok() {
        context.without_characters()
    } else {
        context
    };
    let listen_host = env_or("LISTEN_HOST", if cfg!(windows) { "0.0.0.0" } else { "::" });
    let listener = TcpListener::bind((listen_host.as_str(), port)).await?;
    println!("bigs sur {listen_host}:{port}");
    axum::serve(listener, router(context)).await
}
