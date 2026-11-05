//! Stateful solo sessions with the legacy dedicated advertisement's wire shape.
use super::*;

pub(super) struct LocalSession {
    description: GameDescription,
    command: u16,
    message: u32,
    payload: Vec<u8>,
    attributes: [u32; 4],
}

fn epoch() -> &'static str {
    static EPOCH: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    EPOCH.get_or_init(|| {
        format!(
            "{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        )
    })
}

fn event(event: &str, persona: u64, session: &LocalSession) {
    // One structured line before its network reply. Native hooks never infer
    // a fresh launch or cancellation from an old SCNA attribute map alone.
    println!(
        "MISSION_EVENT = {}",
        serde_json::json!({
            "schema": 1, "server_run_id": epoch(), "event": event,
            "persona_id": persona, "game_id": session.description.game_id,
            "scenario_id": session.description.game_id - 1,
            "attributes": session.attributes,
        })
    );
}

pub fn handle_local(request: &Frame, context: &Context, lobby: &Lobby) -> Option<Vec<Frame>> {
    let mut missions = lobby.0.lock().unwrap_or_else(PoisonError::into_inner);
    let fields = match tdf::decode(&request.payload) {
        Ok(fields) => fields,
        Err(_) => return Some(vec![error_reply(request)]),
    };
    if matches!(
        request.command,
        START_MATCHMAKING_SCENARIO | CREATE_GAME_TEMPLATE
    ) {
        if let Some(current) = missions.local_sessions.get(&context.persona_id)
            && current.command == request.command
            && current.message == request.msg_num
            && current.payload == request.payload
        {
            return Some(responses(request, context, current));
        }
        let parsed = CreateGameRequest::parse(request);
        let mut description = if request.command == START_MATCHMAKING_SCENARIO {
            mission_description(&parsed, "CMGD", "SCNA", context)
        } else {
            mission_description(&parsed, "CGD", "TMPA", context)
        };
        let values: Option<Vec<u32>> = ["playMode", "questId", "desiredDifficulty", "startPoint"]
            .iter()
            .map(|name| attribute(&description.attributes, name).parse().ok())
            .collect();
        let Some(values) = values else {
            return Some(vec![error_reply(request)]);
        };
        if let Some(previous) = missions.local_sessions.remove(&context.persona_id) {
            event("superseded", context.persona_id, &previous);
        }
        description.game_id = missions.next_game_id;
        missions.next_game_id += 1;
        let session = LocalSession {
            description,
            command: request.command,
            message: request.msg_num,
            payload: request.payload.clone(),
            attributes: values.try_into().unwrap(),
        };
        event("requested", context.persona_id, &session);
        let response = responses(request, context, &session);
        missions.local_sessions.insert(context.persona_id, session);
        return Some(response);
    }
    if request.command == CANCEL_MATCHMAKING_SCENARIO {
        let current = missions.local_sessions.get(&context.persona_id);
        let identifier = find(&fields, "MSID").or_else(|| find(&fields, "MSCD"));
        let matches = current.is_some_and(|session| match identifier {
            Some(Value::Integer(id)) => *id == session.description.game_id - 1,
            None => {
                session.description.state == GAME_STATE_INITIALIZING
                    && request.msg_num.wrapping_sub(session.message) & 0xffffff < 0x800000
            }
            _ => false,
        });
        if !matches {
            return Some(vec![request.reply(Vec::new())]);
        }
        let session = missions.local_sessions.remove(&context.persona_id).unwrap();
        event("cancelled", context.persona_id, &session);
        return Some(vec![
            request.reply(Vec::new()),
            Frame::notification(
                COMPONENT,
                NOTIFY_MATCHMAKING_FAILED,
                tdf::encode(&outcome(
                    context,
                    SESSION_CANCELED,
                    session.description.game_id - 1,
                )),
            ),
        ]);
    }
    let game_id = match find(&fields, "GID") {
        Some(Value::Integer(id)) => *id,
        _ => return None,
    };
    if game_id == LOCAL_GAME_ID {
        return None;
    } // Preserve the hub's party.
    let current = missions.local_sessions.get_mut(&context.persona_id);
    let Some(session) = current.filter(|session| session.description.game_id == game_id) else {
        // Late traffic from an old expedition cannot mutate its replacement.
        return matches!(
            request.command,
            DESTROY_GAME
                | LEAVE_GAME_BY_GROUP
                | REMOVE_PLAYER
                | FINALIZE_GAME_CREATION
                | MESH_ENDPOINTS_CONNECTED
                | ADVANCE_GAME_STATE
                | SET_GAME_ATTRIBUTES
                | SET_PLAYER_ATTRIBUTES
        )
        .then(|| vec![request.reply(Vec::new())]);
    };
    match request.command {
        DESTROY_GAME | LEAVE_GAME_BY_GROUP | REMOVE_PLAYER => {
            if matches!(find(&fields, "PID"), Some(Value::Integer(id)) if *id != context.persona_id)
            {
                return Some(vec![error_reply(request)]);
            }
            event("left", context.persona_id, session);
            missions.local_sessions.remove(&context.persona_id);
            Some(if request.command == DESTROY_GAME {
                destroy_game(request)
            } else {
                remove_player(request, context)
            })
        }
        FINALIZE_GAME_CREATION => {
            if let Some(state) = finalized_states(context).last() {
                session.description.state = *state;
            }
            event("finalized", context.persona_id, session);
            Some(finalize_game_creation(request, context))
        }
        MESH_ENDPOINTS_CONNECTED => Some(complete_player_join(request, context)),
        ADVANCE_GAME_STATE => {
            if let Some(Value::Integer(state)) = find(&fields, "GSTA") {
                session.description.state = *state;
            }
            Some(advance_game_state(request, context))
        }
        SET_GAME_ATTRIBUTES => {
            if let (Some(Value::Map(_, _, changes)), Value::Map(_, _, attributes)) =
                (find(&fields, "ATTR"), &mut session.description.attributes)
            {
                merge_attributes(attributes, changes);
            }
            Some(set_game_attributes(request))
        }
        SET_PLAYER_ATTRIBUTES => Some(set_player_attributes(request, context)),
        _ => None,
    }
}

fn responses(request: &Frame, context: &Context, session: &LocalSession) -> Vec<Frame> {
    let matchmaking = request.command == START_MATCHMAKING_SCENARIO;
    let scenario = session.description.game_id - 1;
    vec![
        request.reply(tdf::encode(&[if matchmaking {
            Field::int("MSID", scenario)
        } else {
            Field::int("GID", session.description.game_id)
        }])),
        session.description.setup_notification(
            context,
            if matchmaking {
                game_setup_reason(
                    SETUP_CONTEXT_MATCHMAKING,
                    outcome(
                        context,
                        if context.mission_server.is_some() {
                            SUCCESS_JOINED_NEW_GAME
                        } else {
                            SUCCESS_CREATED_GAME
                        },
                        scenario,
                    ),
                )
            } else {
                setup_reason(context, context.setup_reason, DATALESS_CREATE_GAME_SETUP)
            },
            None,
        ),
    ]
}

fn outcome(context: &Context, result: u64, scenario: u64) -> Vec<Field> {
    let mut fields = matchmaking_outcome(context, result);
    for field in &mut fields {
        if matches!(field.tag.to_string().as_str(), "MSID" | "MSCD") {
            field.value = Value::Integer(scenario);
        }
    }
    fields
}

pub fn disconnect_local(context: &Context, lobby: &Lobby) {
    let mut missions = lobby.0.lock().unwrap_or_else(PoisonError::into_inner);
    if let Some(session) = missions.local_sessions.remove(&context.persona_id) {
        event("disconnected", context.persona_id, &session);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tdf::Generic;

    fn context() -> Context {
        Context {
            bigs_url: "http://127.0.0.1:8080".into(),
            player_name: "KingSplugorf".into(),
            persona_id: 1000,
            game_setup: GameSetupMode::Complete,
            hosted_session: None,
            hosted_topology: 0,
            setup_reason: 0,
            dedicated_host: true,
            initiate_connections: false,
            host_address: None,
            mission_server: Some(2000000),
            mission_in_game: false,
            mission_pair: false,
            local_lifecycle: true,
            personas: vec![],
            default_identity: None,
            squad_auto: false,
            hub_sharing: false,
        }
    }
    fn request(command: u16, message: u32, fields: Vec<Field>) -> Frame {
        Frame {
            component: COMPONENT,
            command,
            msg_num: message,
            msg_type: MessageType::Message,
            metadata: vec![],
            payload: tdf::encode(&fields),
        }
    }
    fn launch(message: u32, quest: &str) -> Frame {
        let values = [
            ("playMode", "1"),
            ("questId", quest),
            ("desiredDifficulty", "1"),
            ("startPoint", "0"),
        ];
        request(
            START_MATCHMAKING_SCENARIO,
            message,
            vec![
                Field::group("CMGD", vec![Field::string("GVER", "17000")]),
                field(
                    "SCNA",
                    Value::Map(
                        Type::String,
                        Type::Generic,
                        values
                            .into_iter()
                            .map(|(key, value)| {
                                (
                                    Value::String(key.into()),
                                    Value::Generic(Some(Generic {
                                        type_id: 1,
                                        flag: 0,
                                        value: Box::new(Value::String(value.into())),
                                    })),
                                )
                            })
                            .collect(),
                    ),
                ),
            ],
        )
    }
    fn game_id(frames: &[Frame]) -> u64 {
        let setup = frames
            .iter()
            .find(|f| f.msg_type == MessageType::Notification && f.command == NOTIFY_GAME_SETUP)
            .unwrap();
        let fields = tdf::decode(&setup.payload).unwrap();
        let Some(Value::Group(game)) = find(&fields, "GAME") else {
            panic!("missing game")
        };
        let Some(Value::Integer(id)) = find(game, "GID") else {
            panic!("missing id")
        };
        *id
    }
    fn send(lobby: &Lobby, context: &Context, request: &Frame) -> Vec<Frame> {
        handle_local(request, context, lobby).unwrap()
    }

    #[test]
    fn first_launch_preserves_the_working_legacy_dedicated_advertisement_byte_for_byte() {
        let c = context();
        let r = launch(1, "1401555469");
        assert_eq!(
            send(&Lobby::default(), &c, &r),
            start_matchmaking_scenario(&r, &c)
        );
    }
    #[test]
    fn eight_expeditions_get_distinct_ids_and_leave_preserves_the_party() {
        let c = context();
        let lobby = Lobby::default();
        for index in 0..8 {
            let frames = send(&lobby, &c, &launch(index * 10 + 1, "1401555469"));
            let id = game_id(&frames);
            assert_eq!(id, 2 + index as u64);
            let reply = tdf::decode(&frames[0].payload).unwrap();
            assert_eq!(find(&reply, "MSID"), Some(&Value::Integer(id - 1)));
            send(
                &lobby,
                &c,
                &request(
                    FINALIZE_GAME_CREATION,
                    index * 10 + 2,
                    vec![Field::int("GID", id)],
                ),
            );
            assert!(
                handle_local(
                    &request(
                        LEAVE_GAME_BY_GROUP,
                        index * 10 + 3,
                        vec![Field::int("GID", 1)]
                    ),
                    &c,
                    &lobby
                )
                .is_none()
            );
            assert_eq!(lobby.0.lock().unwrap().local_sessions.len(), 1);
            send(
                &lobby,
                &c,
                &request(
                    LEAVE_GAME_BY_GROUP,
                    index * 10 + 4,
                    vec![Field::int("GID", id)],
                ),
            );
            assert!(lobby.0.lock().unwrap().local_sessions.is_empty());
        }
    }
    #[test]
    fn duplicate_request_is_idempotent_but_a_fresh_request_for_same_quest_is_new() {
        let c = context();
        let lobby = Lobby::default();
        let r = launch(1, "1401555469");
        assert_eq!(game_id(&send(&lobby, &c, &r)), 2);
        assert_eq!(game_id(&send(&lobby, &c, &r)), 2);
        assert_eq!(game_id(&send(&lobby, &c, &launch(2, "1401555469"))), 3);
        assert_eq!(lobby.0.lock().unwrap().local_sessions.len(), 1);
    }
    #[test]
    fn late_leave_destroy_finalize_and_cancel_cannot_change_the_next_expedition() {
        let c = context();
        let lobby = Lobby::default();
        send(&lobby, &c, &launch(1, "1401555469"));
        send(&lobby, &c, &launch(10, "680915601"));
        for command in [
            LEAVE_GAME_BY_GROUP,
            DESTROY_GAME,
            FINALIZE_GAME_CREATION,
            REMOVE_PLAYER,
        ] {
            let frames = send(
                &lobby,
                &c,
                &request(command, 12, vec![Field::int("GID", 2)]),
            );
            assert_eq!(frames.len(), 1);
        }
        send(
            &lobby,
            &c,
            &request(CANCEL_MATCHMAKING_SCENARIO, 13, vec![Field::int("MSID", 1)]),
        );
        let games = lobby.0.lock().unwrap();
        let current = games.local_sessions.get(&1000).unwrap();
        assert_eq!(current.description.game_id, 3);
        assert_eq!(current.description.state, GAME_STATE_INITIALIZING);
    }
    #[test]
    fn cancellation_and_disconnect_remove_only_the_owning_session_and_allow_retry() {
        let c = context();
        let mut other = c.clone();
        other.persona_id = 1001;
        let lobby = Lobby::default();
        send(&lobby, &c, &launch(1, "1401555469"));
        send(&lobby, &other, &launch(1, "1401555469"));
        send(
            &lobby,
            &c,
            &request(CANCEL_MATCHMAKING_SCENARIO, 2, vec![Field::int("MSID", 1)]),
        );
        assert!(lobby.0.lock().unwrap().local_sessions.contains_key(&1001));
        assert_eq!(game_id(&send(&lobby, &c, &launch(3, "1401555469"))), 4);
        disconnect_local(&c, &lobby);
        assert_eq!(lobby.0.lock().unwrap().local_sessions.len(), 1);
    }
    #[test]
    fn malformed_parameters_cannot_replace_a_valid_session() {
        let c = context();
        let lobby = Lobby::default();
        send(&lobby, &c, &launch(1, "1401555469"));
        for quest in ["invalid", "4294967296", "-1"] {
            assert_eq!(
                send(&lobby, &c, &launch(2, quest))[0].msg_type,
                MessageType::ErrorReply
            );
        }
        assert_eq!(
            lobby
                .0
                .lock()
                .unwrap()
                .local_sessions
                .get(&1000)
                .unwrap()
                .description
                .game_id,
            2
        );
    }
    #[test]
    fn another_persona_cannot_remove_the_local_player() {
        let c = context();
        let lobby = Lobby::default();
        send(&lobby, &c, &launch(1, "1401555469"));
        let frames = send(
            &lobby,
            &c,
            &request(
                REMOVE_PLAYER,
                2,
                vec![Field::int("GID", 2), Field::int("PID", 1001)],
            ),
        );
        assert_eq!(frames[0].msg_type, MessageType::ErrorReply);
        assert_eq!(lobby.0.lock().unwrap().local_sessions.len(), 1);
    }
}
