use std::{
    collections::HashMap,
    env,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};

use blaze::{Context, GameSetupMode, Lobby, Outgoing};

use fire2::{Frame, MessageType};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt},
    net::TcpListener,
    sync::mpsc,
};

type Connections = Arc<Mutex<HashMap<u64, mpsc::UnboundedSender<Frame>>>>;

struct Shared {
    lobby: Lobby,
    connections: Connections,
}

impl Shared {
    /// Trames destinées à d'autres connexions ; celles de l'émetteur sont rendues.
    fn route(&self, peer: &str, outgoing: Vec<Outgoing>) -> Vec<Frame> {
        let connections = self.connections.lock().unwrap_or_else(|e| e.into_inner());
        let mut own = Vec::new();
        for Outgoing { to, frame } in outgoing {
            match to {
                None => own.push(frame),
                Some(persona) => {
                    println!(
                        "{peer} => persona {persona} : {:?} {:#06x}/{:#06x} ({} octets)",
                        frame.msg_type,
                        frame.component,
                        frame.command,
                        frame.payload.len()
                    );
                    if let Some(sender) = connections.get(&persona) {
                        let _ = sender.send(frame);
                    } else {
                        println!("{peer} : persona {persona} non connecté, trame perdue");
                    }
                }
            }
        }
        own
    }
}

const GAME_MANAGER: u16 = 0x0004;

fn env_or(name: &str, default: &str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_owned())
}

fn component_name(component: u16) -> &'static str {
    match component {
        0x0001 => "Authentication",
        0x0004 => "GameManager",
        0x0007 => "Stats",
        0x0009 => "Util",
        0x000F => "Messaging",
        0x0019 => "Association",
        0x001C => "GameReporting",
        0x7802 => "UserSessions",
        _ => "?",
    }
}

async fn log_frame(peer: &str, frame: &Frame, captures: &PathBuf, index: usize) {
    let body = match tdf::decode(&frame.payload) {
        Ok(fields) => tdf::pretty(&fields),
        Err(error) => format!("TDF illisible ({error:?}) : {:02x?}\n", frame.payload),
    };
    println!(
        "{peer} <- {} ({:#06x}) commande {:#06x} {:?} n°{} métadonnées {} octets\n{body}",
        component_name(frame.component),
        frame.component,
        frame.command,
        frame.msg_type,
        frame.msg_num,
        frame.metadata.len()
    );
    let file = captures.join(format!(
        "{:04x}_{:04x}_{index}.bin",
        frame.component, frame.command
    ));
    let _ = tokio::fs::write(file, frame.encode()).await;
}

async fn write_frame<S: AsyncWrite + Unpin>(
    stream: &mut S,
    peer: &str,
    reply: &Frame,
    notification_delay: Duration,
) -> std::io::Result<()> {
    println!(
        "{peer} -> {:?} {:#06x}/{:#06x} n°{} ({} octets)",
        reply.msg_type,
        reply.component,
        reply.command,
        reply.msg_num,
        reply.payload.len()
    );
    if let Ok(fields) = tdf::decode(&reply.payload) {
        print!("{}", tdf::pretty(&fields));
    }
    if reply.msg_type == MessageType::Notification && reply.component == GAME_MANAGER {
        tokio::time::sleep(notification_delay).await;
    }
    stream.write_all(&reply.encode()).await
}

async fn serve<S: AsyncRead + AsyncWrite + Unpin>(
    mut stream: S,
    peer: String,
    captures: PathBuf,
    context: Context,
    shared: Arc<Shared>,
    notification_delay: Duration,
) -> std::io::Result<()> {
    let (sender, mut inbox) = mpsc::unbounded_channel();
    if !context.local_lifecycle || context.mission_pair {
        shared
            .connections
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(context.persona_id, sender.clone());
    }
    println!(
        "{peer} : persona {} ({}), BIGS {}",
        context.persona_id, context.player_name, context.bigs_url
    );
    let result = exchange(
        &mut stream,
        &peer,
        &captures,
        &context,
        &shared,
        &mut inbox,
        &sender,
        notification_delay,
    )
    .await;
    let mut connections = shared.connections.lock().unwrap_or_else(|e| e.into_inner());
    let departure = if connections
        .get(&context.persona_id)
        .is_some_and(|registered| registered.same_channel(&sender))
    {
        connections.remove(&context.persona_id);
        blaze::disconnect(&context, &shared.lobby)
    } else {
        Vec::new()
    };
    drop(connections);
    shared.route(&peer, departure);
    result
}

async fn exchange<S: AsyncRead + AsyncWrite + Unpin>(
    stream: &mut S,
    peer: &str,
    captures: &PathBuf,
    context: &Context,
    shared: &Shared,
    inbox: &mut mpsc::UnboundedReceiver<Frame>,
    sender: &mpsc::UnboundedSender<Frame>,
    notification_delay: Duration,
) -> std::io::Result<()> {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 16 * 1024];
    let mut index = 0;
    let local = context.local_lifecycle && !context.mission_pair;
    let mut registered = !local;
    loop {
        while let Some((frame, consumed)) = Frame::decode(&buffer) {
            buffer.drain(..consumed);
            log_frame(peer, &frame, captures, index).await;
            index += 1;
            let outgoing = if local && frame.component == GAME_MANAGER {
                // Ownership and session mutation share one critical section.
                // Capture logging can yield to a replacement connection, so
                // it must finish before checking who still owns this persona.
                let mut connections = shared.connections.lock().unwrap_or_else(|e| e.into_inner());
                if !registered && frame.msg_type == MessageType::Message {
                    if connections.contains_key(&context.persona_id) {
                        blaze::disconnect(context, &shared.lobby);
                    }
                    connections.insert(context.persona_id, sender.clone());
                    registered = true;
                }
                if registered
                    && !connections
                        .get(&context.persona_id)
                        .is_some_and(|current| current.same_channel(sender))
                {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::ConnectionAborted,
                        "Gameplay connection replaced",
                    ));
                }
                blaze::handle_shared(&frame, context, &shared.lobby)
            } else {
                blaze::handle_shared(&frame, context, &shared.lobby)
            };
            for reply in shared.route(peer, outgoing) {
                write_frame(stream, peer, &reply, notification_delay).await?;
            }
        }
        let read = tokio::select! {
            read = stream.read(&mut chunk) => read?,
            Some(frame) = inbox.recv() => {
                write_frame(stream, peer, &frame, notification_delay).await?;
                continue;
            }
        };
        if read == 0 {
            println!(
                "{peer} : connexion fermée ({} octets non décodés)",
                buffer.len()
            );
            return Ok(());
        }
        buffer.extend_from_slice(&chunk[..read]);
    }
}

/// Lit chaque seconde le fichier de commande du hub partagé (tools/hub_partage.sh), l'exécute puis le supprime.
async fn watch_hub_commands(file: PathBuf, context: Context, shared: Arc<Shared>) {
    loop {
        tokio::time::sleep(Duration::from_secs(1)).await;
        let Ok(text) = tokio::fs::read_to_string(&file).await else {
            continue;
        };
        let _ = tokio::fs::remove_file(&file).await;
        match blaze::hub_command(&text, &context, &shared.lobby) {
            Some(outgoing) => {
                println!(
                    "commande du hub partagé « {} » : {} trames",
                    text.trim(),
                    outgoing.len()
                );
                shared.route("commande", outgoing);
            }
            None => println!(
                "commande du hub partagé inconnue : « {} » (ouvrir ou placer)",
                text.trim()
            ),
        }
    }
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let port: u16 = env_or("BLAZE_PORT", "10041")
        .parse()
        .expect("BLAZE_PORT invalide");
    let acceptor = tls::acceptor_from_pem(
        &env_or("TLS_CERT", "data/certs/test.crt"),
        &env_or("TLS_KEY", "data/certs/test.key"),
    );
    let captures = PathBuf::from(env_or("BLAZE_CAPTURES", "data/captures/blaze"));
    tokio::fs::create_dir_all(&captures).await?;
    let context = Arc::new(Context {
        bigs_url: env_or("BIGS_URL", "http://127.0.0.1:8080"),
        player_name: env_or("JOUEUR_NOM", "Joueur"),
        persona_id: env_or("JOUEUR_ID", "1000")
            .parse()
            .expect("JOUEUR_ID invalide"),
        game_setup: GameSetupMode::parse(&env_or("BLAZE_PARTIE", "complete"))
            .expect("BLAZE_PARTIE : complete, minimale ou refus"),
        hosted_topology: env_or("BLAZE_SESSION_NTOP", "0")
            .parse()
            .expect("BLAZE_SESSION_NTOP : topologie réseau (0 pair, 1 dédié)"),
        dedicated_host: env_or("BLAZE_HOTE_DEDIE", "1") != "0",
        initiate_connections: env_or("BLAZE_INITIER_CONNEXIONS", "0") == "1",
        host_address: env::var("BLAZE_HOTE_IP").ok().map(|ip| {
            let ip: std::net::Ipv4Addr = ip.parse().expect("BLAZE_HOTE_IP : adresse IPv4");
            let port = env_or("BLAZE_HOTE_PORT", "25200")
                .parse()
                .expect("BLAZE_HOTE_PORT : port UDP");
            (ip.octets(), port)
        }),
        mission_server: env::var("BLAZE_SERVEUR_MISSION_ID").ok().filter(|id| !id.is_empty()).map(|id| {
            id.parse()
                .expect("BLAZE_SERVEUR_MISSION_ID : identifiant de persona du serveur de mission")
        }),
        mission_in_game: env_or("BLAZE_MISSION_EN_JEU", "0") == "1",
        mission_pair: env_or("BLAZE_MISSION_PAIR", "0") == "1",
        local_lifecycle: env_or("BLAZE_LOCAL_LIFECYCLE", "0") == "1",
        default_identity: None,
        squad_auto: env_or("BLAZE_ESCOUADE_AUTO", "0") == "1",
        hub_sharing: env_or("BLAZE_HUB_PARTAGE", "0") == "1",
        personas: blaze::parse_personas(&env_or("BLAZE_PERSONAS", ""))
            .unwrap_or_else(|error| panic!("BLAZE_PERSONAS : {error}")),
        setup_reason: env_or("BLAZE_REAS", "0")
            .parse()
            .expect("BLAZE_REAS : indice d'union de GameSetupReason (0 dataless, 1 jonction indirecte, 4 reprise de serveur dédié)"),
        hosted_session: env::var("BLAZE_SESSION").ok().map(|setup_context| {
            setup_context.parse().expect(
                "BLAZE_SESSION : contexte de mise en place (0 création, 4 injection d'hôte)",
            )
        }),
    });
    let notification_delay = Duration::from_millis(
        env_or("BLAZE_DELAI_NOTIFICATIONS_MS", "0")
            .parse()
            .expect("BLAZE_DELAI_NOTIFICATIONS_MS : délai en millisecondes avant chaque notification GameManager"),
    );
    let listen_host = env_or("LISTEN_HOST", if cfg!(windows) { "0.0.0.0" } else { "::" });
    let listener = TcpListener::bind((listen_host.as_str(), port)).await?;
    if context.local_lifecycle && !context.mission_pair {
        println!("BLAZE_LOCAL_LIFECYCLE_READY = 1");
    }
    let hub_command_file = PathBuf::from(env_or("BLAZE_COMMANDE_HUB", "data/hub_command"));
    println!(
        "blaze sur {port}, missions en pair hébergé : {}, escouade automatique : {}, hub partagé : {}, personas par adresse : {:?}",
        context.mission_pair, context.squad_auto, context.hub_sharing, context.personas
    );
    let shared = Arc::new(Shared {
        lobby: Lobby::default(),
        connections: Connections::default(),
    });
    if context.hub_sharing {
        tokio::spawn(watch_hub_commands(
            hub_command_file,
            context.as_ref().clone(),
            shared.clone(),
        ));
    }
    loop {
        let (socket, peer) = listener.accept().await?;
        let acceptor = acceptor.clone();
        let captures = captures.clone();
        let context = context.for_peer(peer.ip());
        let shared = shared.clone();
        tokio::spawn(async move {
            let peer = peer.to_string();
            match acceptor.accept(socket).await {
                Ok(stream) => {
                    if let Err(error) = serve(
                        stream,
                        peer.clone(),
                        captures,
                        context,
                        shared,
                        notification_delay,
                    )
                    .await
                    {
                        println!("{peer} : {error}");
                    }
                }
                Err(error) => println!("{peer} : échec TLS {error}"),
            }
        });
    }
}
