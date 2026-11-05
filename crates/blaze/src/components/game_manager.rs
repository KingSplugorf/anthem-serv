use std::collections::BTreeMap;
use std::sync::{Mutex, PoisonError};

use fire2::Frame;
use tdf::{Field, Tag, Type, Value};

use fire2::MessageType;

use crate::{Context, GameSetupMode, Outgoing, components::user_sessions};

mod local_lifecycle;
use local_lifecycle::LocalSession;
pub use local_lifecycle::{disconnect_local, handle_local};

pub const COMPONENT: u16 = 0x0004;

const DESTROY_GAME: u16 = 0x0002;
const ADVANCE_GAME_STATE: u16 = 0x0003;
const SET_GAME_ATTRIBUTES: u16 = 0x0007;
const SET_PLAYER_ATTRIBUTES: u16 = 0x0008;
const JOIN_GAME: u16 = 0x0009;
const CREATE_GAME_TEMPLATE: u16 = 0x000A;
const REMOVE_PLAYER: u16 = 0x000B;
const FINALIZE_GAME_CREATION: u16 = 0x000F;
const START_MATCHMAKING_SCENARIO: u16 = 0x0010;
const CANCEL_MATCHMAKING_SCENARIO: u16 = 0x0011;
const NOTIFY_MATCHMAKING_FAILED: u16 = 0x000A;
const LEAVE_GAME_BY_GROUP: u16 = 0x0016;
const CREATE_OR_JOIN_GAME: u16 = 0x002D;
const MESH_ENDPOINTS_CONNECTED: u16 = 0x0041;
const MESH_ENDPOINTS_DISCONNECTED: u16 = 0x0042;
const MESH_ENDPOINTS_CONNECTION_LOST: u16 = 0x0043;
const GET_GAME_DATA_BY_USER: u16 = 0x0071;
const TELEMETRY_REPORT: u16 = 0x00AB;
const NOTIFY_GAME_REMOVED: u16 = 0x0010;
const NOTIFY_GAME_SETUP: u16 = 0x0014;
const NOTIFY_PLAYER_JOINING: u16 = 0x0015;
const NOTIFY_JOINING_PLAYER_INITIATE_CONNECTIONS: u16 = 0x0016;
const NOTIFY_GAME_STATE_CHANGE: u16 = 0x0064;
const NOTIFY_GAME_ATTRIBUTE_CHANGE: u16 = 0x0050;
const NOTIFY_PLAYER_ATTRIBUTE_CHANGE: u16 = 0x005A;
const NOTIFY_PLAYER_JOIN_COMPLETED: u16 = 0x001E;
const NOTIFY_PLAYER_STATE_CHANGE: u16 = 0x0074;
const NOTIFY_PLAYER_REMOVED: u16 = 0x0028;
const LOCAL_GAME_ID: u64 = 1;
const GAME_STATE_INITIALIZING: u64 = 0x1;
const GAME_STATE_PRE_GAME: u64 = 0x82;
const GAME_STATE_IN_GAME: u64 = 0x83;
const PLAYER_STATE_ACTIVE_CONNECTING: u64 = 0x2;
const PLAYER_STATE_ACTIVE_CONNECTED: u64 = 0x4;
const UNSPECIFIED_TEAM_INDEX: u64 = 0xFFFF;
const SETUP_CONTEXT_DATALESS: u8 = 0x0;
const SETUP_CONTEXT_INDIRECT_JOIN: u8 = 0x1;
const SETUP_CONTEXT_INDIRECT_MATCHMAKING: u8 = 0x2;
const SETUP_CONTEXT_MATCHMAKING: u8 = 0x3;
const SETUP_CONTEXT_RESET_DEDICATED_SERVER: u8 = 0x4;
const MATCHMAKING_SCENARIO_ID: u64 = 1;
const SUCCESS_CREATED_GAME: u64 = 0;
const SUCCESS_JOINED_NEW_GAME: u64 = 1;
const SUCCESS_JOINED_EXISTING_GAME: u64 = 2;
const JOIN_GAME_SETUP_CONTEXT: u64 = 1;
const JOINED_GAME: u64 = 0;
const HOST_LEAVING: u64 = 4;
/// PlayerRemovedReason GAME_DESTROYED (table 0x14d360fe0), changé en raison Dylan 0xa par 0x141ed2ca0.
const GAME_DESTROYED: u64 = 5;
const SESSION_CANCELED: u64 = 4;
const DATALESS_CREATE_GAME_SETUP: u64 = 0x0;
const GAME_STATE_GAME_GROUP_INITIALIZED: u64 = 0x10;
const HOSTED_SESSION_GAME_ID: u64 = 2;
/// GameStartingInfo.PlayMode du hub (PilotSpace) ; lu par 0x140a9c725 sur chaque partie connue, absent il plante le client.
const PLAY_MODE_PILOT_SPACE: &str = "3";
const GAME_OBJECT_TYPE: u64 = 2;
/// Identifiant de la partie de hub d'un chef : base + son persona ; gdb_active_mission.gdb (tools/hub_game.py) ne
/// prend pas son StartServer pour un lancement de mission.
const HUB_GAME_ID_BASE: u64 = 1_000_000_000;
const HOSTED_SESSION_MAX_PLAYERS: u64 = 4;
const GAME_TYPE_SESSION: u64 = 0;
const GAME_TYPE_GAMEGROUP: u64 = 1;
/// GamegroupMaxParticipants de DylanPresenceGameManagementServiceData (ebx).
const SQUAD_MAX_MEMBERS: usize = 4;
const CLIENT_SERVER_PEER_HOSTED: u64 = 0;
const CLIENT_SERVER_DEDICATED: u64 = 1;
const VOIP_DEDICATED_SERVER: u64 = 1;
const ADDRESS_PAIR: u8 = 2;
const CLIENT_GAME_PORT: u64 = 3659;
const GAME_PROTOCOL_VERSION: &str = "17000";

pub fn handle(command: u16, request: &Frame, context: &Context) -> Option<Vec<Frame>> {
    match command {
        CREATE_OR_JOIN_GAME => Some(create_game(request, context)),
        CREATE_GAME_TEMPLATE => Some(create_game_template(request, context)),
        START_MATCHMAKING_SCENARIO => Some(start_matchmaking_scenario(request, context)),
        CANCEL_MATCHMAKING_SCENARIO => Some(cancel_matchmaking_scenario(request, context)),
        SET_GAME_ATTRIBUTES => Some(set_game_attributes(request)),
        SET_PLAYER_ATTRIBUTES => Some(set_player_attributes(request, context)),
        GET_GAME_DATA_BY_USER => Some(vec![request.reply(tdf::encode(&[field(
            "GDAT",
            Value::List(Type::Group, Vec::new()),
        )]))]),
        ADVANCE_GAME_STATE => Some(advance_game_state(request, context)),
        MESH_ENDPOINTS_CONNECTED => Some(complete_player_join(request, context)),
        FINALIZE_GAME_CREATION => Some(finalize_game_creation(request, context)),
        DESTROY_GAME => Some(destroy_game(request)),
        LEAVE_GAME_BY_GROUP => Some(remove_player(request, context)),
        MESH_ENDPOINTS_DISCONNECTED | MESH_ENDPOINTS_CONNECTION_LOST | TELEMETRY_REPORT => {
            Some(vec![request.reply(Vec::new())])
        }
        _ => None,
    }
}

struct CreateGameRequest {
    fields: Vec<Field>,
}

impl CreateGameRequest {
    fn parse(request: &Frame) -> Self {
        Self {
            fields: tdf::decode(&request.payload).unwrap_or_default(),
        }
    }

    fn value(&self, path: &[&str]) -> Option<&Value> {
        let (last, groups) = path.split_last()?;
        let mut fields = &self.fields;
        for group in groups {
            fields = match find(fields, group)? {
                Value::Group(children) => children,
                _ => return None,
            };
        }
        find(fields, last)
    }

    fn integer(&self, path: &[&str], default: u64) -> u64 {
        match self.value(path) {
            Some(Value::Integer(n)) => *n,
            _ => default,
        }
    }

    fn string(&self, path: &[&str]) -> String {
        match self.value(path) {
            Some(Value::String(text)) => text.clone(),
            _ => String::new(),
        }
    }

    fn or_default(&self, path: &[&str], default: Value) -> Value {
        self.value(path).cloned().unwrap_or(default)
    }
}

fn find<'a>(fields: &'a [Field], tag: &str) -> Option<&'a Value> {
    fields
        .iter()
        .find(|field| field.tag.to_string() == tag)
        .map(|field| &field.value)
}

fn field(tag: &str, value: Value) -> Field {
    Field::new(Tag::new(tag), value)
}

/// Empreinte FNV de la version du protocole de jeu, comme compute_version_hash de PocketRelay.
fn version_hash(version: &str) -> u64 {
    version.bytes().fold(2_166_136_261u64, |hash, byte| {
        hash.wrapping_mul(16_777_619) ^ byte as u64
    })
}

fn host_slot(host: u64, connection_group: u64) -> Vec<Field> {
    vec![
        Field::int("CONG", connection_group),
        Field::int("CSID", 0),
        Field::int("HPID", host),
        Field::int("HSLT", 0),
    ]
}

fn dedicated_host(host: u64, connection_group: u64) -> Vec<Field> {
    vec![
        Field::int("CONG", connection_group),
        Field::int("CSID", 0),
        Field::int("HPID", host),
        Field::int("HSES", host),
        Field::int("HSLT", 0),
    ]
}

/// GameSetupReason : l'indice est la position du membre et le membre porte sa propre balise (table en 0x144a74880).
const GAME_SETUP_REASON_TAGS: [&str; 5] = ["DLSC", "IJGS", "IMSC", "MMSC", "RDSC"];

fn game_setup_reason(index: u8, fields: Vec<Field>) -> Value {
    let tag = GAME_SETUP_REASON_TAGS[usize::from(index)];
    Value::Union(index, Some(Box::new(Field::group(tag, fields))))
}

fn setup_reason(context: &Context, index: u8, dataless_context: u64) -> Value {
    let fields = match index {
        SETUP_CONTEXT_INDIRECT_JOIN => vec![
            field("GRID", user_sessions::session_object_id(context)),
            Field::int("RPVC", 0),
        ],
        SETUP_CONTEXT_RESET_DEDICATED_SERVER => vec![Field::int("ERR", 0)],
        _ => vec![Field::int("DCTX", dataless_context)],
    };
    game_setup_reason(index, fields)
}

fn attribute_change(fields: &[Field]) -> Vec<Field> {
    let attributes =
        find(fields, "ATTR")
            .cloned()
            .unwrap_or(Value::Map(Type::String, Type::String, Vec::new()));
    let game_id = find(fields, "GID")
        .cloned()
        .unwrap_or(Value::Integer(LOCAL_GAME_ID));
    vec![field("ATTR", attributes), field("GID", game_id)]
}

fn copied(fields: &[Field], tags: &[&str]) -> Vec<Field> {
    tags.iter()
        .filter_map(|tag| find(fields, tag).map(|value| field(tag, value.clone())))
        .collect()
}

fn destroy_game(request: &Frame) -> Vec<Frame> {
    let fields = tdf::decode(&request.payload).unwrap_or_default();
    vec![
        request.reply(tdf::encode(&copied(&fields, &["GID"]))),
        Frame::notification(
            COMPONENT,
            NOTIFY_GAME_REMOVED,
            tdf::encode(&copied(&fields, &["GID", "REAS"])),
        ),
    ]
}

/// Le joueur qui quitte la partie en est notifié comme les autres membres.
fn remove_player(request: &Frame, context: &Context) -> Vec<Frame> {
    let fields = tdf::decode(&request.payload).unwrap_or_default();
    let mut removed = copied(&fields, &["CNTX", "GID"]);
    removed.push(
        find(&fields, "PID")
            .map(|player| field("PID", player.clone()))
            .unwrap_or_else(|| Field::int("PID", context.persona_id)),
    );
    removed.extend(copied(&fields, &["REAS"]));
    vec![
        request.reply(Vec::new()),
        Frame::notification(COMPONENT, NOTIFY_PLAYER_REMOVED, tdf::encode(&removed)),
    ]
}

fn set_game_attributes(request: &Frame) -> Vec<Frame> {
    let fields = tdf::decode(&request.payload).unwrap_or_default();
    vec![
        request.reply(Vec::new()),
        Frame::notification(
            COMPONENT,
            NOTIFY_GAME_ATTRIBUTE_CHANGE,
            tdf::encode(&attribute_change(&fields)),
        ),
    ]
}

fn set_player_attributes(request: &Frame, context: &Context) -> Vec<Frame> {
    let fields = tdf::decode(&request.payload).unwrap_or_default();
    let player_id = find(&fields, "PID")
        .cloned()
        .unwrap_or(Value::Integer(context.persona_id));
    let mut change = attribute_change(&fields);
    change.push(field("PID", player_id));
    vec![
        request.reply(Vec::new()),
        Frame::notification(
            COMPONENT,
            NOTIFY_PLAYER_ATTRIBUTE_CHANGE,
            tdf::encode(&change),
        ),
    ]
}

/// Attributs d'un modèle de partie (map de valeurs génériques) ramenés à une map de chaînes.
fn template_attributes(template: Option<&Value>) -> Value {
    let entries = match template {
        Some(Value::Map(_, _, entries)) => entries
            .iter()
            .filter_map(|(key, value)| {
                let text = match value {
                    Value::Generic(Some(generic)) => match generic.value.as_ref() {
                        Value::String(text) => text.clone(),
                        Value::Integer(n) => n.to_string(),
                        _ => return None,
                    },
                    Value::String(text) => text.clone(),
                    _ => return None,
                };
                Some((key.clone(), Value::String(text)))
            })
            .collect(),
        _ => Vec::new(),
    };
    Value::Map(Type::String, Type::String, entries)
}

fn mission_description(
    request: &CreateGameRequest,
    common_data: &str,
    attributes: &str,
    context: &Context,
) -> GameDescription {
    let player_network = request.or_default(&[common_data, "PNET"], local_player_address());
    GameDescription {
        game_group_type: request.integer(&[common_data, "GGTY"], GAME_TYPE_SESSION),
        version: request.string(&[common_data, "GVER"]),
        attributes: template_attributes(request.value(&[attributes])),
        host_network: vec![context.host_address.map_or_else(
            || player_network.clone(),
            |(ip, port)| address_pair(ip, port.into()),
        )],
        player_network,
        host_persona: context.mission_server,
        topology: context
            .mission_server
            .map_or(context.hosted_topology, |_| CLIENT_SERVER_DEDICATED),
        ..GameDescription::hosted_session(context.hosted_topology, context)
    }
}

fn create_game_template(request: &Frame, context: &Context) -> Vec<Frame> {
    let description =
        mission_description(&CreateGameRequest::parse(request), "CGD", "TMPA", context);
    vec![
        request.reply(tdf::encode(&[Field::int("GID", HOSTED_SESSION_GAME_ID)])),
        description.setup_notification(
            context,
            setup_reason(context, context.setup_reason, DATALESS_CREATE_GAME_SETUP),
            None,
        ),
    ]
}

fn matchmaking_outcome(context: &Context, result: u64) -> Vec<Field> {
    vec![
        Field::int("MSCD", MATCHMAKING_SCENARIO_ID),
        Field::int("MSID", MATCHMAKING_SCENARIO_ID),
        Field::int("RSLT", result),
        Field::int("USID", context.persona_id),
    ]
}

fn matchmaking_reason(context: &Context) -> Value {
    let result = if context.mission_server.is_some() {
        SUCCESS_JOINED_NEW_GAME
    } else {
        SUCCESS_CREATED_GAME
    };
    game_setup_reason(
        SETUP_CONTEXT_MATCHMAKING,
        matchmaking_outcome(context, result),
    )
}

fn cancel_matchmaking_scenario(request: &Frame, context: &Context) -> Vec<Frame> {
    vec![
        request.reply(Vec::new()),
        Frame::notification(
            COMPONENT,
            NOTIFY_MATCHMAKING_FAILED,
            tdf::encode(&matchmaking_outcome(context, SESSION_CANCELED)),
        ),
    ]
}

fn start_matchmaking_scenario(request: &Frame, context: &Context) -> Vec<Frame> {
    let description =
        mission_description(&CreateGameRequest::parse(request), "CMGD", "SCNA", context);
    vec![
        request.reply(tdf::encode(&[Field::int("MSID", MATCHMAKING_SCENARIO_ID)])),
        description.setup_notification(context, matchmaking_reason(context), None),
    ]
}

fn finalize_game_creation(request: &Frame, context: &Context) -> Vec<Frame> {
    let fields = tdf::decode(&request.payload).unwrap_or_default();
    let game_id = match find(&fields, "GID") {
        Some(Value::Integer(id)) => *id,
        _ => LOCAL_GAME_ID,
    };
    let mission = game_id != LOCAL_GAME_ID;
    let mut frames = vec![request.reply(Vec::new())];
    if mission {
        for &state in finalized_states(context) {
            let change = vec![Field::int("GID", game_id), Field::int("GSTA", state)];
            frames.push(Frame::notification(
                COMPONENT,
                NOTIFY_GAME_STATE_CHANGE,
                tdf::encode(&change),
            ));
        }
    }
    if context.initiate_connections && mission {
        let reason = setup_reason(context, context.setup_reason, DATALESS_CREATE_GAME_SETUP);
        frames.push(
            GameDescription::hosted_session(context.hosted_topology, context).notification(
                context,
                NOTIFY_JOINING_PLAYER_INITIATE_CONNECTIONS,
                reason,
                None,
            ),
        );
    }
    frames
}

fn finalized_states(context: &Context) -> &'static [u64] {
    if context.mission_in_game {
        &[GAME_STATE_PRE_GAME, GAME_STATE_IN_GAME]
    } else {
        &[GAME_STATE_PRE_GAME]
    }
}

fn complete_player_join(request: &Frame, context: &Context) -> Vec<Frame> {
    let fields = tdf::decode(&request.payload).unwrap_or_default();
    let game_id = find(&fields, "GID")
        .cloned()
        .unwrap_or(Value::Integer(LOCAL_GAME_ID));
    let player_state = vec![
        field("GID", game_id.clone()),
        Field::int("PID", context.persona_id),
        Field::int("STAT", PLAYER_STATE_ACTIVE_CONNECTED),
    ];
    let joined = vec![field("GID", game_id), Field::int("PID", context.persona_id)];
    vec![
        request.reply(Vec::new()),
        Frame::notification(
            COMPONENT,
            NOTIFY_PLAYER_STATE_CHANGE,
            tdf::encode(&player_state),
        ),
        Frame::notification(
            COMPONENT,
            NOTIFY_PLAYER_JOIN_COMPLETED,
            tdf::encode(&joined),
        ),
    ]
}

fn advance_game_state(request: &Frame, context: &Context) -> Vec<Frame> {
    let fields = tdf::decode(&request.payload).unwrap_or_default();
    let game_id = find(&fields, "GID")
        .cloned()
        .unwrap_or(Value::Integer(LOCAL_GAME_ID));
    let state = find(&fields, "GSTA")
        .cloned()
        .unwrap_or(Value::Integer(GAME_STATE_INITIALIZING));
    let group_initialized = state == Value::Integer(GAME_STATE_GAME_GROUP_INITIALIZED);
    let change = vec![field("GID", game_id), field("GSTA", state)];
    let mut frames = vec![
        request.reply(Vec::new()),
        Frame::notification(COMPONENT, NOTIFY_GAME_STATE_CHANGE, tdf::encode(&change)),
    ];
    if let (true, Some(setup_context)) = (group_initialized, context.hosted_session) {
        frames.push(
            GameDescription::hosted_session(context.hosted_topology, context).setup_notification(
                context,
                setup_reason(context, SETUP_CONTEXT_DATALESS, setup_context),
                None,
            ),
        );
    }
    frames
}

struct GameDescription {
    game_id: u64,
    game_group_type: u64,
    topology: u64,
    settings: u64,
    max_players: u64,
    presence: u64,
    voip: u64,
    queue_capacity: u64,
    not_resettable: u64,
    version: String,
    type_name: String,
    attributes: Value,
    capacities: Value,
    host_network: Vec<Value>,
    player_network: Value,
    host_persona: Option<u64>,
    host_connection_group: Option<u64>,
    dedicated_host: bool,
    state: u64,
    roster: Vec<RosterPlayer>,
}

impl GameDescription {
    fn from_request(create: &CreateGameRequest, context: &Context) -> Self {
        Self {
            game_id: LOCAL_GAME_ID,
            game_group_type: create.integer(&["CMGD", "GGTY"], 0),
            topology: create.integer(&["GMCD", "NTOP"], 0),
            settings: create.integer(&["GMCD", "GSET"], 0),
            max_players: create.integer(&["GMCD", "PMAX"], 1),
            presence: create.integer(&["GMCD", "PRES"], 1),
            voip: create.integer(&["GMCD", "VOIP"], 0),
            queue_capacity: create.integer(&["GMCD", "QCAP"], 0),
            not_resettable: create.integer(&["NRES"], 0),
            version: create.string(&["CMGD", "GVER"]),
            type_name: create.string(&["GTYP"]),
            attributes: create.or_default(
                &["GMCD", "ATTR"],
                Value::Map(Type::String, Type::String, Vec::new()),
            ),
            capacities: create.or_default(&["PCAP"], Value::List(Type::Integer, Vec::new())),
            host_network: Vec::new(),
            player_network: create
                .or_default(&["CMGD", "PNET"], Value::Union(Value::UNION_UNSET, None)),
            host_persona: None,
            host_connection_group: None,
            dedicated_host: context.dedicated_host,
            state: GAME_STATE_INITIALIZING,
            roster: Vec::new(),
        }
    }

    /// Session de jeu hébergée par le client lui-même (« InProc Server »), dans laquelle le serveur place le groupe.
    fn hosted_session(topology: u64, context: &Context) -> Self {
        let address = local_player_address();
        Self {
            game_id: HOSTED_SESSION_GAME_ID,
            game_group_type: GAME_TYPE_SESSION,
            topology,
            settings: 0,
            max_players: HOSTED_SESSION_MAX_PLAYERS,
            presence: 1,
            voip: VOIP_DEDICATED_SERVER,
            queue_capacity: 0,
            not_resettable: 0,
            version: GAME_PROTOCOL_VERSION.into(),
            type_name: "frostbite_multiplayer".into(),
            attributes: Value::Map(Type::String, Type::String, Vec::new()),
            capacities: Value::List(
                Type::Integer,
                vec![
                    Value::Integer(HOSTED_SESSION_MAX_PLAYERS),
                    Value::Integer(0),
                    Value::Integer(0),
                    Value::Integer(0),
                ],
            ),
            host_network: vec![address.clone()],
            player_network: address,
            host_persona: None,
            host_connection_group: None,
            dedicated_host: context.dedicated_host,
            state: GAME_STATE_INITIALIZING,
            roster: Vec::new(),
        }
    }

    fn game_fields(&self, context: &Context) -> Vec<Field> {
        let empty_blob = || Value::Blob(Vec::new());
        let host_network = Value::List(
            if self.host_network.is_empty() {
                Type::Group
            } else {
                Type::Union
            },
            self.host_network.clone(),
        );
        let mut fields = vec![
            field(
                "ADMN",
                Value::List(Type::Integer, vec![Value::Integer(self.host(context))]),
            ),
            field("ATTR", self.attributes.clone()),
            field("CAP", self.capacities.clone()),
            Field::int("GGTY", self.game_group_type),
            Field::int("GID", self.game_id),
            Field::string("GNAM", &self.host_name(context)),
            Field::int("GPVH", version_hash(&self.version)),
            Field::int("GSET", self.settings),
            Field::int("GSID", self.game_id),
            Field::int("GSTA", self.state),
            Field::string("GTYP", &self.type_name),
            field("HNET", host_network.clone()),
            Field::int("MCAP", self.max_players),
            Field::group(
                "NQOS",
                vec![
                    Field::int("BWHR", 0),
                    Field::int("DBPS", 0),
                    Field::int("NAHR", 0),
                    Field::int("NATT", 0),
                    Field::int("UBPS", 0),
                ],
            ),
            Field::int("NRES", self.not_resettable),
            Field::int("NTOP", self.topology),
            Field::string("PGID", ""),
            field("PGSR", empty_blob()),
            Field::group("PHST", host_slot(self.host(context), self.host(context))),
            Field::int("PRES", self.presence),
            Field::string("PSAS", ""),
            Field::int("QCAP", self.queue_capacity),
            Field::int("SEED", 0),
            Field::group(
                "THST",
                host_slot(self.host(context), self.connection_group(context)),
            ),
            Field::string(
                "UUID",
                &format!("00000000-0000-0000-0000-{:012}", self.game_id),
            ),
            Field::int("VOIP", self.voip),
            Field::string("VSTR", &self.version),
        ];
        if self.dedicated_host {
            let after_capacities = fields
                .iter()
                .position(|field| field.tag.to_string() == "CAP")
                .map_or(0, |index| index + 1);
            fields.splice(
                after_capacities..after_capacities,
                [
                    Field::group(
                        "DHST",
                        dedicated_host(self.host(context), self.connection_group(context)),
                    ),
                    field("DNET", host_network.clone()),
                ],
            );
        }
        fields
    }

    fn host(&self, context: &Context) -> u64 {
        self.host_persona.unwrap_or(context.persona_id)
    }

    fn connection_group(&self, context: &Context) -> u64 {
        self.host_connection_group
            .unwrap_or_else(|| self.host(context))
    }

    fn host_name(&self, context: &Context) -> String {
        self.roster
            .iter()
            .find(|player| player.persona == self.host(context))
            .map_or_else(|| context.player_name.clone(), |player| player.name.clone())
    }

    fn players(&self, context: &Context) -> Vec<Value> {
        if self.roster.is_empty() {
            let player = RosterPlayer::new(
                context,
                self.player_network.clone(),
                0,
                PLAYER_STATE_ACTIVE_CONNECTED,
            );
            return vec![player.value(self.game_id)];
        }
        self.roster
            .iter()
            .map(|player| player.value(self.game_id))
            .collect()
    }

    fn setup_notification(&self, context: &Context, reason: Value, keep: Option<&[&str]>) -> Frame {
        self.notification(context, NOTIFY_GAME_SETUP, reason, keep)
    }

    fn notification(
        &self,
        context: &Context,
        command: u16,
        reason: Value,
        keep: Option<&[&str]>,
    ) -> Frame {
        let game = self
            .game_fields(context)
            .into_iter()
            .filter(|field| keep.is_none_or(|tags| tags.contains(&field.tag.to_string().as_str())))
            .collect();
        let setup = vec![
            Field::group("GAME", game),
            field("PROS", Value::List(Type::Group, self.players(context))),
            field("REAS", reason),
        ];
        Frame::notification(COMPONENT, command, tdf::encode(&setup))
    }
}

fn local_player_address() -> Value {
    address_pair([127, 0, 0, 1], CLIENT_GAME_PORT)
}

fn address_pair(ip: [u8; 4], port: u64) -> Value {
    let endpoint = || {
        vec![
            Field::int("IP", u32::from_be_bytes(ip) as u64),
            Field::int("MACI", 0),
            Field::int("PORT", port),
        ]
    };
    Value::Union(
        ADDRESS_PAIR,
        Some(Box::new(Field::group(
            "VALU",
            vec![
                Field::group("EXIP", endpoint()),
                Field::group("INIP", endpoint()),
                Field::int("MACI", 0),
            ],
        ))),
    )
}

fn error_reply(request: &Frame) -> Frame {
    Frame {
        msg_type: MessageType::ErrorReply,
        metadata: Vec::new(),
        payload: Vec::new(),
        ..request.clone()
    }
}

fn create_game(request: &Frame, context: &Context) -> Vec<Frame> {
    if context.game_setup == GameSetupMode::Refuse {
        return vec![error_reply(request)];
    }
    let description = GameDescription::from_request(&CreateGameRequest::parse(request), context);
    let keep: Option<&[&str]> =
        (context.game_setup == GameSetupMode::Minimal).then_some(&["GID", "NTOP", "THST"]);
    vec![
        request.reply(tdf::encode(&[Field::int("GID", LOCAL_GAME_ID)])),
        description.setup_notification(
            context,
            setup_reason(context, SETUP_CONTEXT_DATALESS, DATALESS_CREATE_GAME_SETUP),
            keep,
        ),
    ]
}

#[derive(Clone)]
struct RosterPlayer {
    persona: u64,
    name: String,
    network: Value,
    slot: u64,
    state: u64,
    attributes: Vec<(Value, Value)>,
}

impl RosterPlayer {
    fn new(context: &Context, network: Value, slot: u64, state: u64) -> Self {
        Self {
            persona: context.persona_id,
            name: context.player_name.clone(),
            network,
            slot,
            state,
            attributes: Vec::new(),
        }
    }

    /// ReplicatedGamePlayer ; CSID choisit le client ConnApi du joueur (ConnApi + 0x2b0 + CSID × 0x198, 0x141ed3585).
    /// PATT (attributs de joueur, ex. Ready de l'escouade) n'est envoyé que s'il est renseigné.
    fn value(&self, game_id: u64) -> Value {
        let mut fields = vec![
            field("BLOB", Value::Blob(Vec::new())),
            Field::int("CONG", self.persona),
            Field::int("CSID", self.slot),
            Field::int("EXID", self.persona),
            Field::int("GID", game_id),
            Field::int("LOC", 0),
            Field::string("NAME", &self.name),
            Field::int("PID", self.persona),
            field("PNET", self.network.clone()),
            Field::int("SID", self.slot),
            Field::int("SLOT", 0),
            Field::int("STAT", self.state),
            Field::int("TIDX", UNSPECIFIED_TEAM_INDEX),
            Field::int("TIME", 0),
            field(
                "UGID",
                Value::ObjectId {
                    component: 0,
                    kind: 0,
                    id: 0,
                },
            ),
            Field::int("UID", self.persona),
        ];
        if !self.attributes.is_empty() {
            let before_player_id = fields
                .iter()
                .position(|field| field.tag.to_string() == "PID")
                .unwrap_or(fields.len());
            fields.insert(
                before_player_id,
                field(
                    "PATT",
                    Value::Map(Type::String, Type::String, self.attributes.clone()),
                ),
            );
        }
        Value::Group(fields)
    }
}

fn merge_attributes(attributes: &mut Vec<(Value, Value)>, changes: &[(Value, Value)]) {
    for (key, value) in changes {
        match attributes.iter_mut().find(|(known, _)| known == key) {
            Some(entry) => entry.1 = value.clone(),
            None => attributes.push((key.clone(), value.clone())),
        }
    }
}

/// Missions hébergées par un joueur, partagées entre les connexions (mode pair).
pub struct Lobby(Mutex<Missions>);

impl Default for Lobby {
    fn default() -> Self {
        Self(Mutex::new(Missions {
            next_game_id: HOSTED_SESSION_GAME_ID,
            games: Vec::new(),
            regrouping: Vec::new(),
            hub_after_regroup: false,
            local_sessions: BTreeMap::new(),
        }))
    }
}

struct Missions {
    next_game_id: u64,
    games: Vec<Mission>,
    /// Membres retirés de leur escouade par « ouvrir », dont la prochaine demande d'escouade rejoint celle du chef.
    regrouping: Vec<u64>,
    /// Le hub du chef s'ouvre quand un membre a rejoint son escouade (« ouvrir »), pas après « escouade ».
    hub_after_regroup: bool,
    local_sessions: BTreeMap<u64, LocalSession>,
}

struct Mission {
    owner: u64,
    quest: String,
    description: GameDescription,
    connected: Vec<u64>,
    /// Partie de hub partagé (BLAZE_HUB_PARTAGE) : Fort Tarsis du chef, pas une mission.
    hub: bool,
}

impl Mission {
    fn is_topology_host(&self, persona: u64) -> bool {
        self.description
            .host_connection_group
            .or(self.description.host_persona)
            == Some(persona)
    }

    fn has_member(&self, persona: u64) -> bool {
        self.description
            .roster
            .iter()
            .any(|player| player.persona == persona)
    }

    fn members(&self) -> Vec<u64> {
        self.description
            .roster
            .iter()
            .map(|player| player.persona)
            .collect()
    }

    fn others(&self, persona: u64) -> Vec<u64> {
        self.members()
            .into_iter()
            .filter(|member| *member != persona)
            .collect()
    }

    fn has_room(&self) -> bool {
        let roster = self.description.roster.len();
        if self.is_squad() {
            return roster < SQUAD_MAX_MEMBERS;
        }
        (roster as u64) < self.description.max_players
    }

    /// Partie de groupe : l'escouade, créée sans réseau par chaque client au démarrage.
    fn is_squad(&self) -> bool {
        self.description.game_group_type == GAME_TYPE_GAMEGROUP
    }
}

pub fn handle_shared(
    command: u16,
    request: &Frame,
    context: &Context,
    lobby: &Lobby,
) -> Option<Vec<Outgoing>> {
    let mut missions = lobby.0.lock().unwrap_or_else(PoisonError::into_inner);
    match command {
        START_MATCHMAKING_SCENARIO => return Some(matchmake(request, context, &mut missions)),
        JOIN_GAME => return Some(join_game(request, context, &mut missions)),
        CREATE_OR_JOIN_GAME => return create_squad(request, context, &mut missions),
        _ => {}
    }
    let fields = tdf::decode(&request.payload).unwrap_or_default();
    let Some(Value::Integer(game_id)) = find(&fields, "GID") else {
        return None;
    };
    let index = missions
        .games
        .iter()
        .position(|mission| mission.description.game_id == *game_id)?;
    let mission = &mut missions.games[index];
    let others = mission.others(context.persona_id);
    let shared = |frames: Vec<Frame>, notifications: &[u16]| {
        let mut outgoing: Vec<Outgoing> = frames.iter().cloned().map(Outgoing::sender).collect();
        for frame in frames.iter().filter(|frame| {
            frame.msg_type == MessageType::Notification && notifications.contains(&frame.command)
        }) {
            outgoing.extend(
                others
                    .iter()
                    .map(|member| Outgoing::persona(*member, frame.clone())),
            );
        }
        outgoing
    };
    match command {
        MESH_ENDPOINTS_CONNECTED => Some(mesh_connected(request, &fields, context, mission)),
        FINALIZE_GAME_CREATION if mission.is_squad() => {
            Some(vec![Outgoing::sender(request.reply(Vec::new()))])
        }
        SET_PLAYER_ATTRIBUTES => {
            let player_id = match find(&fields, "PID") {
                Some(Value::Integer(persona)) => *persona,
                _ => context.persona_id,
            };
            if let Some(Value::Map(_, _, changes)) = find(&fields, "ATTR")
                && let Some(player) = mission
                    .description
                    .roster
                    .iter_mut()
                    .find(|player| player.persona == player_id)
            {
                merge_attributes(&mut player.attributes, changes);
            }
            Some(shared(
                set_player_attributes(request, context),
                &[NOTIFY_PLAYER_ATTRIBUTE_CHANGE],
            ))
        }
        REMOVE_PLAYER => {
            let Some(Value::Integer(removed)) = find(&fields, "PID").cloned() else {
                return Some(vec![Outgoing::sender(request.reply(Vec::new()))]);
            };
            let reason = find(&fields, "REAS").cloned();
            let game_id = mission.description.game_id;
            let mut outgoing = vec![Outgoing::sender(request.reply(Vec::new()))];
            outgoing.extend(remove_member(
                &mut missions,
                index,
                removed,
                reason.as_ref(),
            ));
            if removed != context.persona_id {
                outgoing.push(Outgoing::persona(
                    removed,
                    player_removed(game_id, removed, reason.as_ref()),
                ));
            }
            Some(outgoing)
        }
        LEAVE_GAME_BY_GROUP => {
            let leaving = match find(&fields, "PID") {
                Some(Value::Integer(persona)) => *persona,
                _ => context.persona_id,
            };
            let mut outgoing: Vec<Outgoing> = remove_player(request, context)
                .into_iter()
                .map(Outgoing::sender)
                .collect();
            outgoing.extend(
                remove_member(&mut missions, index, leaving, find(&fields, "REAS"))
                    .into_iter()
                    .filter(|out| out.to != Some(context.persona_id)),
            );
            Some(outgoing)
        }
        SET_GAME_ATTRIBUTES => {
            if let (Some(Value::Map(_, _, changes)), Value::Map(_, _, attributes)) =
                (find(&fields, "ATTR"), &mut mission.description.attributes)
            {
                merge_attributes(attributes, changes);
            }
            Some(shared(
                set_game_attributes(request),
                &[NOTIFY_GAME_ATTRIBUTE_CHANGE],
            ))
        }
        FINALIZE_GAME_CREATION => {
            if let Some(state) = finalized_states(context).last() {
                mission.description.state = *state;
            }
            let mut outgoing = shared(
                finalize_game_creation(request, context),
                &[NOTIFY_GAME_STATE_CHANGE],
            );
            if mission.is_topology_host(context.persona_id) {
                outgoing.extend(complete_join(mission, context.persona_id));
            }
            Some(outgoing)
        }
        ADVANCE_GAME_STATE => {
            if let Some(Value::Integer(state)) = find(&fields, "GSTA") {
                mission.description.state = *state;
            }
            Some(shared(
                advance_game_state(request, context),
                &[NOTIFY_GAME_STATE_CHANGE],
            ))
        }
        _ => None,
    }
}

/// Mission hébergée par le demandeur. Avec un serveur de mission déclaré, annonce dédiée dont l'adresse est le PNET
/// du demandeur et le groupe de connexion le sien (le client détourne la connexion vers son serveur interne et son
/// ConnApi devient hôte du jeu) ; sinon NTOP = CLIENT_SERVER_PEER_HOSTED,
/// le demandeur étant l'hôte (THST, PHST).
fn pair_description(request: &CreateGameRequest, context: &Context) -> GameDescription {
    let mut description = mission_description(request, "CMGD", "SCNA", context);
    if context.mission_server.is_none() {
        description.topology = CLIENT_SERVER_PEER_HOSTED;
        description.host_persona = Some(context.persona_id);
        description.dedicated_host = false;
    } else {
        description.host_connection_group = Some(context.persona_id);
    }
    if context.host_address.is_none() {
        description.host_network = vec![description.player_network.clone()];
    }
    description.roster = vec![RosterPlayer::new(
        context,
        description.player_network.clone(),
        0,
        PLAYER_STATE_ACTIVE_CONNECTED,
    )];
    description
}

fn attribute(attributes: &Value, name: &str) -> String {
    match attributes {
        Value::Map(_, _, entries) => entries
            .iter()
            .find_map(|(key, value)| match (key, value) {
                (Value::String(key), Value::String(value)) if key == name => Some(value.clone()),
                _ => None,
            })
            .unwrap_or_default(),
        _ => String::new(),
    }
}

/// Le second persona sur la même quête rejoint la mission existante ; sinon le demandeur en héberge une nouvelle.
/// Lancée par le chef d'une escouade (PLJD.BTPL), elle reçoit aussi les autres membres.
fn matchmake(request: &Frame, context: &Context, missions: &mut Missions) -> Vec<Outgoing> {
    let parsed = CreateGameRequest::parse(request);
    let mut description = pair_description(&parsed, context);
    let quest = attribute(&description.attributes, "questId");
    let reply = request.reply(tdf::encode(&[Field::int("MSID", MATCHMAKING_SCENARIO_ID)]));
    let mut outgoing = leave_missions(context.persona_id, missions);
    let squad = parsed.value(&["PLJD", "BTPL"]).cloned();
    let followers = squad_followers(squad.as_ref(), context, missions);
    let existing = missions.games.iter().position(|mission| {
        !mission.is_squad() && !mission.hub && mission.quest == quest && mission.has_room()
    });
    let index = match existing {
        Some(index) => {
            let reason = game_setup_reason(
                SETUP_CONTEXT_MATCHMAKING,
                matchmaking_outcome(context, SUCCESS_JOINED_EXISTING_GAME),
            );
            outgoing.extend(join(
                &mut missions.games[index],
                context,
                description.player_network,
                Some(reply),
                reason,
                None,
            ));
            index
        }
        None => {
            description.game_id = missions.next_game_id;
            missions.next_game_id += 1;
            let reason = matchmaking_reason(context);
            outgoing.push(Outgoing::sender(reply));
            outgoing.push(Outgoing::sender(
                description.setup_notification(context, reason, None),
            ));
            missions.games.push(Mission {
                owner: context.persona_id,
                quest,
                description,
                connected: Vec::new(),
                hub: false,
            });
            missions.games.len() - 1
        }
    };
    let game_id = missions.games[index].description.game_id;
    for follower in followers {
        outgoing.extend(leave_missions(follower.persona, missions));
        let Some(mission) = missions
            .games
            .iter_mut()
            .find(|mission| mission.description.game_id == game_id && mission.has_room())
        else {
            break;
        };
        let member = context.with_identity(follower.persona, &follower.name);
        let reason = game_setup_reason(
            SETUP_CONTEXT_INDIRECT_MATCHMAKING,
            indirect_matchmaking_outcome(&member, squad.clone().unwrap_or(Value::Integer(0))),
        );
        outgoing.extend(join(
            mission,
            &member,
            follower.network,
            None,
            reason,
            Some(follower.persona),
        ));
    }
    outgoing
}

/// Autres membres de l'escouade `squad` (BTPL = objet 4/2/GID) quand le demandeur en est le chef.
fn squad_followers(
    squad: Option<&Value>,
    context: &Context,
    missions: &Missions,
) -> Vec<RosterPlayer> {
    let Some(Value::ObjectId { id, .. }) = squad else {
        return Vec::new();
    };
    missions
        .games
        .iter()
        .find(|mission| {
            mission.is_squad()
                && mission.description.game_id == *id
                && mission.description.host_persona == Some(context.persona_id)
        })
        .map(|mission| {
            mission
                .description
                .roster
                .iter()
                .filter(|player| player.persona != context.persona_id)
                .cloned()
                .collect()
        })
        .unwrap_or_default()
}

/// IndirectMatchmakingSetupContext d'un membre placé par le chef : GRID = l'escouade, RSLT = SUCCESS_JOINED_NEW_GAME.
fn indirect_matchmaking_outcome(member: &Context, squad: Value) -> Vec<Field> {
    vec![
        field("GRID", squad),
        Field::int("MSCD", MATCHMAKING_SCENARIO_ID),
        Field::int("MSID", MATCHMAKING_SCENARIO_ID),
        Field::int("RSLT", SUCCESS_JOINED_NEW_GAME),
        Field::int("USID", member.persona_id),
    ]
}

/// joinGame (JoinGameRequest {CMGD, GID, JMET, PLJD…}) : JoinGameResponse {GID, JGS = JOINED_GAME}. Rejoindre une
/// escouade quitte la précédente ; rejoindre une mission quitte les autres missions, et un membre déjà présent (placé
/// par le chef, puis jonction d'après ExpeditionGameId) reçoit la même réponse sans nouvelle entrée.
fn join_game(request: &Frame, context: &Context, missions: &mut Missions) -> Vec<Outgoing> {
    let parsed = CreateGameRequest::parse(request);
    let game_id = parsed.integer(&["GID"], 0);
    let network = parsed.or_default(&["CMGD", "PNET"], local_player_address());
    let target = missions
        .games
        .iter()
        .find(|mission| mission.description.game_id == game_id)
        .map(|mission| (mission.is_squad(), mission.has_member(context.persona_id)));
    let reply = request.reply(tdf::encode(&[
        Field::int("GID", game_id),
        Field::int("JGS", JOINED_GAME),
    ]));
    let reason = game_setup_reason(
        SETUP_CONTEXT_DATALESS,
        vec![Field::int("DCTX", JOIN_GAME_SETUP_CONTEXT)],
    );
    let mut outgoing = match target {
        Some((_, true)) => {
            let mission = missions
                .games
                .iter()
                .find(|mission| mission.description.game_id == game_id)
                .expect("partie trouvée ci-dessus");
            return vec![
                Outgoing::sender(reply),
                Outgoing::sender(
                    mission
                        .description
                        .setup_notification(context, reason, None),
                ),
            ];
        }
        Some((true, false)) => leave_squads(context.persona_id, missions),
        _ => leave_missions(context.persona_id, missions),
    };
    let Some(mission) = missions
        .games
        .iter_mut()
        .find(|mission| mission.description.game_id == game_id && mission.has_room())
    else {
        outgoing.push(Outgoing::sender(error_reply(request)));
        return outgoing;
    };
    outgoing.extend(join(mission, context, network, Some(reply), reason, None));
    outgoing
}

/// createOrJoinGame d'une partie de groupe (GGTY 1) : l'escouade du demandeur, partagée sous son persona ; avec
/// BLAZE_ESCOUADE_AUTO, il rejoint plutôt l'escouade d'un autre persona qui a de la place, au hub partagé seulement s'il
/// en a été retiré par « ouvrir » (le hub du chef s'ouvre alors).
fn create_squad(
    request: &Frame,
    context: &Context,
    missions: &mut Missions,
) -> Option<Vec<Outgoing>> {
    let parsed = CreateGameRequest::parse(request);
    if context.game_setup != GameSetupMode::Complete
        || parsed.integer(&["CMGD", "GGTY"], GAME_TYPE_SESSION) != GAME_TYPE_GAMEGROUP
    {
        return None;
    }
    let mut outgoing = leave_squads(context.persona_id, missions);
    let regrouped = missions
        .regrouping
        .iter()
        .position(|persona| *persona == context.persona_id)
        .map(|index| missions.regrouping.remove(index))
        .is_some();
    if context.squad_auto
        && (!context.hub_sharing || regrouped)
        && let Some(squad) = missions
            .games
            .iter_mut()
            .find(|mission| mission.is_squad() && mission.has_room())
    {
        let network = parsed.or_default(&["CMGD", "PNET"], local_player_address());
        let reply = request.reply(tdf::encode(&[Field::int("GID", squad.description.game_id)]));
        let reason = game_setup_reason(
            SETUP_CONTEXT_DATALESS,
            vec![Field::int("DCTX", JOIN_GAME_SETUP_CONTEXT)],
        );
        outgoing.extend(join(squad, context, network, Some(reply), reason, None));
        if regrouped && missions.hub_after_regroup {
            outgoing.extend(open_hubs(context, missions));
        }
        return Some(outgoing);
    }
    let mut description = GameDescription::from_request(&parsed, context);
    description.game_id = context.persona_id;
    description.host_persona = Some(context.persona_id);
    description.roster = vec![RosterPlayer::new(
        context,
        description.player_network.clone(),
        0,
        PLAYER_STATE_ACTIVE_CONNECTED,
    )];
    outgoing.push(Outgoing::sender(
        request.reply(tdf::encode(&[Field::int("GID", description.game_id)])),
    ));
    outgoing.push(Outgoing::sender(description.setup_notification(
        context,
        setup_reason(context, SETUP_CONTEXT_DATALESS, DATALESS_CREATE_GAME_SETUP),
        None,
    )));
    missions.games.push(Mission {
        owner: context.persona_id,
        quest: String::new(),
        description,
        connected: Vec::new(),
        hub: false,
    });
    Some(outgoing)
}

/// Partie de hub du chef `leader`, annoncée comme une mission partagée qu'il héberge (pair_description) : NTOP 1,
/// DHST/THST CONG = chef, HPID du serveur de mission, DNET = son PNET ; sans serveur de mission déclaré, pair hébergé.
fn hub_description(leader: &RosterPlayer, version: &str, context: &Context) -> GameDescription {
    let mut description = GameDescription::hosted_session(context.hosted_topology, context);
    if !version.is_empty() {
        description.version = version.into();
    }
    description.attributes = Value::Map(
        Type::String,
        Type::String,
        [
            ("playMode", PLAY_MODE_PILOT_SPACE),
            ("questId", "0"),
            ("desiredDifficulty", "0"),
            ("startPoint", "0"),
        ]
        .into_iter()
        .map(|(key, value)| (Value::String(key.into()), Value::String(value.into())))
        .collect(),
    );
    description.player_network = leader.network.clone();
    description.host_network = vec![context.host_address.map_or_else(
        || leader.network.clone(),
        |(ip, port)| address_pair(ip, port.into()),
    )];
    match context.mission_server {
        Some(server) => {
            description.topology = CLIENT_SERVER_DEDICATED;
            description.host_persona = Some(server);
            description.host_connection_group = Some(leader.persona);
        }
        None => {
            description.topology = CLIENT_SERVER_PEER_HOSTED;
            description.host_persona = Some(leader.persona);
            description.dedicated_host = false;
        }
    }
    description.roster = vec![RosterPlayer {
        slot: 0,
        state: PLAYER_STATE_ACTIVE_CONNECTED,
        attributes: Vec::new(),
        ..leader.clone()
    }];
    description
}

fn squad_object(squad_id: u64) -> Value {
    Value::ObjectId {
        component: u64::from(COMPONENT),
        kind: GAME_OBJECT_TYPE,
        id: squad_id,
    }
}

/// Commande « ouvrir » (tools/hub_partage.sh) : réunit les escouades (gather_squads), puis, pour chaque escouade d'au
/// moins deux membres dont le chef n'a pas de partie de hub, Blaze la lui donne (IMSC, GRID = l'escouade) avec sa fin de jonction (0x74, 0x1e). Son écouteur
/// 0x14815d7b0 poste alors ClientJoinMultiplayer (0x14815d812 levé par --pair), que peer_launch.s change en
/// StartServer en hôte pair (il est hôte de topologie) : son hub recharge une fois, en Peer.
pub fn open_shared_hubs(context: &Context, lobby: &Lobby) -> Vec<Outgoing> {
    if !context.hub_sharing {
        return Vec::new();
    }
    let mut missions = lobby.0.lock().unwrap_or_else(PoisonError::into_inner);
    missions.hub_after_regroup = true;
    let mut outgoing = gather_squads(context, &mut missions);
    outgoing.extend(open_hubs(context, &mut missions));
    outgoing
}

/// Commande « escouade » : réunit les escouades comme « ouvrir », sans ouvrir de hub partagé.
pub fn gather_shared_squads(context: &Context, lobby: &Lobby) -> Vec<Outgoing> {
    if !context.hub_sharing {
        return Vec::new();
    }
    let mut missions = lobby.0.lock().unwrap_or_else(PoisonError::into_inner);
    missions.hub_after_regroup = false;
    gather_squads(context, &mut missions)
}

fn open_hubs(context: &Context, missions: &mut Missions) -> Vec<Outgoing> {
    let mut outgoing = Vec::new();
    let leaders: Vec<(u64, RosterPlayer, String)> = missions
        .games
        .iter()
        .filter(|squad| squad.is_squad() && squad.description.roster.len() > 1)
        .filter_map(|squad| {
            let leader = squad
                .description
                .roster
                .iter()
                .find(|player| Some(player.persona) == squad.description.host_persona)?;
            Some((
                squad.description.game_id,
                leader.clone(),
                squad.description.version.clone(),
            ))
        })
        .collect();
    for (squad_id, leader, version) in leaders {
        if missions
            .games
            .iter()
            .any(|mission| mission.hub && mission.owner == leader.persona)
        {
            continue;
        }
        let leader_context = context.with_identity(leader.persona, &leader.name);
        let mut description = hub_description(&leader, &version, context);
        description.game_id = HUB_GAME_ID_BASE + leader.persona;
        let reason = game_setup_reason(
            SETUP_CONTEXT_INDIRECT_MATCHMAKING,
            indirect_matchmaking_outcome(&leader_context, squad_object(squad_id)),
        );
        outgoing.push(Outgoing::persona(
            leader.persona,
            description.setup_notification(&leader_context, reason, None),
        ));
        missions.games.push(Mission {
            owner: leader.persona,
            quest: String::new(),
            description,
            connected: Vec::new(),
            hub: true,
        });
        let hub = missions.games.last_mut().expect("partie de hub ajoutée");
        outgoing.extend(complete_join(hub, leader.persona));
    }
    outgoing
}

/// Escouade automatique du hub partagé, différée à « ouvrir » : le client ne garde une escouade que s'il l'a demandée
/// (0x14285e070, sinon Game::leaveGame). Chaque joueur seul dans son escouade, hors la plus ancienne (celle du chef), en
/// est retiré (NotifyPlayerRemoved REAS GAME_DESTROYED) : son écouteur 0x141ed2ca0 lui fait renvoyer createOrJoinGame
/// (0x140ada3c4), que create_squad dirige vers l'escouade du chef.
fn gather_squads(context: &Context, missions: &mut Missions) -> Vec<Outgoing> {
    let mut outgoing = Vec::new();
    if !context.squad_auto {
        return outgoing;
    }
    let Some(leader_squad) = missions
        .games
        .iter()
        .find(|mission| mission.is_squad())
        .map(|squad| squad.description.game_id)
    else {
        return outgoing;
    };
    let lone: Vec<(u64, u64)> = missions
        .games
        .iter()
        .filter(|mission| {
            mission.is_squad()
                && mission.description.game_id != leader_squad
                && mission.description.roster.len() == 1
        })
        .map(|squad| {
            (
                squad.description.game_id,
                squad.description.roster[0].persona,
            )
        })
        .collect();
    for (squad_id, persona) in lone {
        missions
            .games
            .retain(|mission| mission.description.game_id != squad_id);
        if !missions.regrouping.contains(&persona) {
            missions.regrouping.push(persona);
        }
        let removed = [
            Field::int("CNTX", 0),
            Field::int("GID", squad_id),
            Field::int("PID", persona),
            Field::int("REAS", GAME_DESTROYED),
        ];
        outgoing.push(Outgoing::persona(
            persona,
            Frame::notification(COMPONENT, NOTIFY_PLAYER_REMOVED, tdf::encode(&removed)),
        ));
    }
    outgoing
}

/// Commande « en_jeu » : chaque partie de hub, restée PRE_GAME faute d'advanceGameState du chef, passe IN_GAME chez
/// tous ses membres.
pub fn start_shared_hubs(lobby: &Lobby) -> Vec<Outgoing> {
    let mut missions = lobby.0.lock().unwrap_or_else(PoisonError::into_inner);
    let mut outgoing = Vec::new();
    for hub in missions.games.iter_mut().filter(|mission| mission.hub) {
        hub.description.state = GAME_STATE_IN_GAME;
        let change = Frame::notification(
            COMPONENT,
            NOTIFY_GAME_STATE_CHANGE,
            tdf::encode(&[
                Field::int("GID", hub.description.game_id),
                Field::int("GSTA", GAME_STATE_IN_GAME),
            ]),
        );
        outgoing.extend(
            hub.members()
                .into_iter()
                .map(|member| Outgoing::persona(member, change.clone())),
        );
    }
    outgoing
}

/// Commande « placer », une fois le hub du chef rechargé en Peer : chaque membre de son escouade qui ne partage encore
/// aucune partie avec lui entre dans sa partie de hub (IMSC). La fin de jonction du membre (liaison) lui fait poster
/// ClientJoinMultiplayer vers le serveur du chef (0x14815d7b0, pas hôte de topologie).
pub fn place_squad_members(context: &Context, lobby: &Lobby) -> Vec<Outgoing> {
    if !context.hub_sharing {
        return Vec::new();
    }
    let mut missions = lobby.0.lock().unwrap_or_else(PoisonError::into_inner);
    let mut placements: Vec<(u64, u64, RosterPlayer)> = Vec::new();
    for hub in missions.games.iter().filter(|mission| mission.hub) {
        let Some(squad) = missions
            .games
            .iter()
            .find(|squad| squad.is_squad() && squad.description.host_persona == Some(hub.owner))
        else {
            continue;
        };
        for member in squad
            .description
            .roster
            .iter()
            .filter(|player| player.persona != hub.owner)
        {
            let shares_a_game = missions.games.iter().any(|mission| {
                !mission.is_squad()
                    && mission.owner == hub.owner
                    && mission.has_member(member.persona)
            });
            if !shares_a_game {
                placements.push((
                    hub.description.game_id,
                    squad.description.game_id,
                    member.clone(),
                ));
            }
        }
    }
    let mut outgoing = Vec::new();
    for (hub_id, squad_id, member) in placements {
        outgoing.extend(leave_missions(member.persona, &mut missions));
        let Some(hub) = missions
            .games
            .iter_mut()
            .find(|mission| mission.description.game_id == hub_id && mission.has_room())
        else {
            continue;
        };
        let member_context = context.with_identity(member.persona, &member.name);
        let reason = game_setup_reason(
            SETUP_CONTEXT_INDIRECT_MATCHMAKING,
            indirect_matchmaking_outcome(&member_context, squad_object(squad_id)),
        );
        outgoing.extend(join(
            hub,
            &member_context,
            member.network,
            None,
            reason,
            Some(member.persona),
        ));
    }
    outgoing
}

/// Membres présents : NotifyUserAdded puis NotifyPlayerJoining ; nouveau joueur (l'émetteur, ou `recipient` quand un
/// autre l'y place) : réponse éventuelle, NotifyUserAdded de chaque membre, puis NotifyGameSetup de la partie
/// complète (ordre de add_player dans PocketRelay).
fn join(
    mission: &mut Mission,
    context: &Context,
    network: Value,
    reply: Option<Frame>,
    reason: Value,
    recipient: Option<u64>,
) -> Vec<Outgoing> {
    let roster = &mission.description.roster;
    let slot = (0..)
        .find(|slot| roster.iter().all(|player| player.slot != *slot))
        .unwrap_or_default();
    let joiner = RosterPlayer::new(
        context,
        network.clone(),
        slot,
        PLAYER_STATE_ACTIVE_CONNECTING,
    );
    let joining = vec![
        Field::int("GID", mission.description.game_id),
        field("PDAT", joiner.value(mission.description.game_id)),
    ];
    let to_joiner = |frame: Frame| Outgoing {
        to: recipient,
        frame,
    };
    let mut outgoing: Vec<Outgoing> = reply.into_iter().map(to_joiner).collect();
    for member in roster {
        outgoing.push(Outgoing::persona(
            member.persona,
            user_sessions::user_added(context, network.clone()),
        ));
        outgoing.push(Outgoing::persona(
            member.persona,
            Frame::notification(COMPONENT, NOTIFY_PLAYER_JOINING, tdf::encode(&joining)),
        ));
        outgoing.push(to_joiner(user_sessions::user_added(
            &context.with_identity(member.persona, &member.name),
            member.network.clone(),
        )));
    }
    mission.description.roster.push(joiner);
    outgoing.push(to_joiner(
        mission
            .description
            .setup_notification(context, reason, None),
    ));
    outgoing
}

/// meshEndpointsConnected {GID, TCG} : la liaison hôte-joueur achève la jonction du joueur qui n'est pas l'hôte.
fn mesh_connected(
    request: &Frame,
    fields: &[Field],
    context: &Context,
    mission: &mut Mission,
) -> Vec<Outgoing> {
    let target = match find(fields, "TCG") {
        Some(Value::ObjectId { id, .. }) => *id,
        _ => context.persona_id,
    };
    let completed = if mission.is_topology_host(context.persona_id) {
        target
    } else {
        context.persona_id
    };
    let mut outgoing = vec![Outgoing::sender(request.reply(Vec::new()))];
    outgoing.extend(complete_join(mission, completed));
    outgoing
}

/// Le joueur passe ACTIVE_CONNECTED : NotifyGamePlayerStateChange puis NotifyPlayerJoinCompleted à chaque membre, une seule fois.
/// Jamais pour le chef dans son escouade : la fin de jonction de son joueur lui ferait poster ClientJoinMultiplayer
/// (0x14815d7b0, saut 0x14815d812 levé par launch_from_hub.py --pair), donc redémarrer son serveur interne ; dans
/// sa partie de hub, c'est ce redémarrage en hôte pair qu'on veut (open_shared_hubs). Chaque trame vise son membre :
/// handle_shared rend à l'émetteur celles qui le visent.
fn complete_join(mission: &mut Mission, completed: u64) -> Vec<Outgoing> {
    if mission.connected.contains(&completed)
        || !mission.has_member(completed)
        || (mission.is_squad() && mission.is_topology_host(completed))
    {
        return Vec::new();
    }
    mission.connected.push(completed);
    for player in &mut mission.description.roster {
        if player.persona == completed {
            player.state = PLAYER_STATE_ACTIVE_CONNECTED;
        }
    }
    let game_id = mission.description.game_id;
    let notifications = [
        Frame::notification(
            COMPONENT,
            NOTIFY_PLAYER_STATE_CHANGE,
            tdf::encode(&[
                Field::int("GID", game_id),
                Field::int("PID", completed),
                Field::int("STAT", PLAYER_STATE_ACTIVE_CONNECTED),
            ]),
        ),
        Frame::notification(
            COMPONENT,
            NOTIFY_PLAYER_JOIN_COMPLETED,
            tdf::encode(&[Field::int("GID", game_id), Field::int("PID", completed)]),
        ),
    ];
    let mut outgoing = Vec::new();
    for member in mission.members() {
        let to = Some(member);
        outgoing.extend(notifications.iter().map(|frame| Outgoing {
            to,
            frame: frame.clone(),
        }));
    }
    outgoing
}

/// Retire un membre : l'hôte qui part supprime la partie (NotifyGameRemoved), un autre est notifié retiré.
fn remove_member(
    missions: &mut Missions,
    index: usize,
    persona: u64,
    reason: Option<&Value>,
) -> Vec<Outgoing> {
    let mission = &mut missions.games[index];
    let game_id = mission.description.game_id;
    if mission.owner == persona {
        let removed = missions.games.remove(index);
        let notification = Frame::notification(
            COMPONENT,
            NOTIFY_GAME_REMOVED,
            tdf::encode(&[Field::int("GID", game_id), Field::int("REAS", HOST_LEAVING)]),
        );
        return removed
            .others(persona)
            .into_iter()
            .map(|member| Outgoing::persona(member, notification.clone()))
            .collect();
    }
    mission
        .description
        .roster
        .retain(|player| player.persona != persona);
    mission.connected.retain(|member| *member != persona);
    let notification = player_removed(game_id, persona, reason);
    mission
        .members()
        .into_iter()
        .map(|member| Outgoing::persona(member, notification.clone()))
        .collect()
}

fn player_removed(game_id: u64, persona: u64, reason: Option<&Value>) -> Frame {
    let mut removed = vec![Field::int("GID", game_id), Field::int("PID", persona)];
    if let Some(reason) = reason {
        removed.push(field("REAS", reason.clone()));
    }
    Frame::notification(COMPONENT, NOTIFY_PLAYER_REMOVED, tdf::encode(&removed))
}

fn leave_games(persona: u64, missions: &mut Missions, squads: Option<bool>) -> Vec<Outgoing> {
    let mut outgoing = Vec::new();
    while let Some(index) = missions.games.iter().position(|mission| {
        mission.has_member(persona) && squads.is_none_or(|squad| mission.is_squad() == squad)
    }) {
        outgoing.extend(remove_member(missions, index, persona, None));
    }
    outgoing
}

fn leave_missions(persona: u64, missions: &mut Missions) -> Vec<Outgoing> {
    leave_games(persona, missions, Some(false))
}

fn leave_squads(persona: u64, missions: &mut Missions) -> Vec<Outgoing> {
    leave_games(persona, missions, Some(true))
}

pub fn leave_all(context: &Context, lobby: &Lobby) -> Vec<Outgoing> {
    let mut missions = lobby.0.lock().unwrap_or_else(PoisonError::into_inner);
    leave_games(context.persona_id, &mut missions, None)
}
