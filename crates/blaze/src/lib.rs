mod components;

use std::net::IpAddr;

use fire2::{Frame, MessageType};

pub use components::game_manager::{Lobby, open_shared_hubs, place_squad_members};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GameSetupMode {
    Complete,
    Minimal,
    Refuse,
}

impl GameSetupMode {
    pub fn parse(text: &str) -> Option<Self> {
        match text {
            "complete" => Some(Self::Complete),
            "minimale" => Some(Self::Minimal),
            "refus" => Some(Self::Refuse),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Persona {
    pub id: u64,
    pub name: String,
    pub bigs_url: Option<String>,
}

/// Lit `IP=ID:NOM[:URL_BIGS]` séparés par `;` (BLAZE_PERSONAS).
pub fn parse_personas(text: &str) -> Result<Vec<(IpAddr, Persona)>, String> {
    text.split(';')
        .map(str::trim)
        .filter(|entry| !entry.is_empty())
        .map(|entry| {
            let (ip, rest) = entry
                .split_once('=')
                .ok_or_else(|| format!("{entry} : IP=ID:NOM[:URL_BIGS] attendu"))?;
            let mut parts = rest.splitn(3, ':');
            let id = parts
                .next()
                .and_then(|id| id.parse().ok())
                .ok_or_else(|| format!("{entry} : identifiant de persona invalide"))?;
            let name = parts
                .next()
                .filter(|name| !name.is_empty())
                .ok_or_else(|| format!("{entry} : nom absent"))?;
            let ip = ip
                .parse()
                .map_err(|_| format!("{entry} : adresse IP invalide"))?;
            Ok((
                ip,
                Persona {
                    id,
                    name: name.to_owned(),
                    bigs_url: parts.next().map(str::to_owned),
                },
            ))
        })
        .collect()
}

#[derive(Debug, Clone)]
pub struct Context {
    pub bigs_url: String,
    pub player_name: String,
    pub persona_id: u64,
    pub game_setup: GameSetupMode,
    pub hosted_session: Option<u64>,
    pub hosted_topology: u64,
    pub setup_reason: u8,
    pub dedicated_host: bool,
    pub initiate_connections: bool,
    pub host_address: Option<([u8; 4], u16)>,
    pub mission_server: Option<u64>,
    pub mission_in_game: bool,
    pub mission_pair: bool,
    pub local_lifecycle: bool,
    pub personas: Vec<(IpAddr, Persona)>,
    /// Identité par défaut (JOUEUR_ID, JOUEUR_NOM) quand `for_peer` l'a remplacée par un persona déclaré.
    pub default_identity: Option<(u64, String)>,
    /// BLAZE_ESCOUADE_AUTO : la partie de groupe créée au démarrage rejoint l'escouade d'un autre persona connecté.
    pub squad_auto: bool,
    /// BLAZE_HUB_PARTAGE : un membre de l'escouade est placé dans une partie de hub hébergée par le chef.
    pub hub_sharing: bool,
}

impl Context {
    /// Contexte d'une connexion : le persona déclaré pour l'adresse du client, sinon celui par défaut.
    pub fn for_peer(&self, address: IpAddr) -> Context {
        let address = address.to_canonical();
        let mut context = self.clone();
        if let Some((_, persona)) = self.personas.iter().find(|(ip, _)| *ip == address) {
            context.default_identity = Some((self.persona_id, self.player_name.clone()));
            context.persona_id = persona.id;
            context.player_name = persona.name.clone();
            if let Some(url) = &persona.bigs_url {
                context.bigs_url = url.clone();
            }
        }
        context
    }

    /// Personas connus, sans doublon : le sien, celui par défaut et ceux de BLAZE_PERSONAS.
    pub(crate) fn known_personas(&self) -> Vec<(u64, String)> {
        let mut known: Vec<(u64, String)> = Vec::new();
        let candidates = std::iter::once((self.persona_id, self.player_name.clone()))
            .chain(self.default_identity.iter().cloned())
            .chain(self.personas.iter().map(|(_, p)| (p.id, p.name.clone())));
        for (id, name) in candidates {
            if known.iter().all(|(seen, _)| *seen != id) {
                known.push((id, name));
            }
        }
        known
    }

    /// Nom du persona `id` parmi ceux connus.
    pub(crate) fn persona_name(&self, id: u64) -> Option<String> {
        self.known_personas()
            .into_iter()
            .find(|(known, _)| *known == id)
            .map(|(_, name)| name)
    }

    pub(crate) fn with_identity(&self, persona_id: u64, player_name: &str) -> Context {
        Context {
            persona_id,
            player_name: player_name.to_owned(),
            ..self.clone()
        }
    }
}

/// Trame à envoyer : à l'émetteur de la requête (`to` = None) ou à la connexion d'un autre persona.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Outgoing {
    pub to: Option<u64>,
    pub frame: Frame,
}

impl Outgoing {
    pub(crate) fn sender(frame: Frame) -> Self {
        Self { to: None, frame }
    }

    pub(crate) fn persona(persona: u64, frame: Frame) -> Self {
        Self {
            to: Some(persona),
            frame,
        }
    }
}

/// Comme `handle`, avec les parties de mission partagées entre connexions quand le mode pair est actif.
pub fn handle_shared(frame: &Frame, context: &Context, lobby: &Lobby) -> Vec<Outgoing> {
    if context.local_lifecycle
        && !context.mission_pair
        && frame.msg_type == MessageType::Message
        && frame.component == components::game_manager::COMPONENT
        && let Some(frames) = components::game_manager::handle_local(frame, context, lobby)
    {
        return frames.into_iter().map(Outgoing::sender).collect();
    }
    let shared = (context.mission_pair
        && frame.msg_type != MessageType::Ping
        && frame.component == components::game_manager::COMPONENT)
        .then(|| components::game_manager::handle_shared(frame.command, frame, context, lobby))
        .flatten();
    match shared {
        Some(outgoing) => outgoing
            .into_iter()
            .map(|out| Outgoing {
                to: out.to.filter(|persona| *persona != context.persona_id),
                frame: out.frame,
            })
            .collect(),
        None => handle(frame, context)
            .into_iter()
            .map(Outgoing::sender)
            .collect(),
    }
}

/// Commande du hub partagé lue dans le fichier BLAZE_COMMANDE_HUB : « ouvrir » (hub du chef rechargé en hôte pair),
/// puis « placer » (membres de l'escouade dans ce hub) ; None pour un texte inconnu.
pub fn hub_command(text: &str, context: &Context, lobby: &Lobby) -> Option<Vec<Outgoing>> {
    match text.trim() {
        "ouvrir" => Some(open_shared_hubs(context, lobby)),
        "placer" => Some(place_squad_members(context, lobby)),
        "escouade" => Some(components::game_manager::gather_shared_squads(
            context, lobby,
        )),
        "en_jeu" => Some(components::game_manager::start_shared_hubs(lobby)),
        _ => None,
    }
}

/// Fermeture de la connexion d'un persona : il quitte les missions partagées.
pub fn disconnect(context: &Context, lobby: &Lobby) -> Vec<Outgoing> {
    if context.local_lifecycle && !context.mission_pair {
        components::game_manager::disconnect_local(context, lobby);
        return Vec::new();
    }
    if !context.mission_pair {
        return Vec::new();
    }
    components::game_manager::leave_all(context, lobby)
}
pub fn handle(frame: &Frame, context: &Context) -> Vec<Frame> {
    if frame.msg_type == MessageType::Ping {
        return vec![Frame {
            msg_type: MessageType::PingReply,
            payload: Vec::new(),
            metadata: Vec::new(),
            ..frame.clone()
        }];
    }
    match (frame.component, frame.command) {
        (components::util::COMPONENT, command) => components::util::handle(command, frame, context),
        (components::user_sessions::COMPONENT, command) => {
            components::user_sessions::handle(command, frame, context)
        }
        (components::association::COMPONENT, command) => {
            components::association::handle(command, frame, context)
        }
        (components::authentication::COMPONENT, command) => {
            components::authentication::handle(command, frame, context)
        }
        (components::game_manager::COMPONENT, command) => {
            components::game_manager::handle(command, frame, context)
        }
        _ => None,
    }
    .unwrap_or_default()
}
