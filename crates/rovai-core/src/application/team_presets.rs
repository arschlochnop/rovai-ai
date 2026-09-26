//! Named Team Presets. This is a pure addition that never reads or writes
//! `new-conversation-preferences.json`. A preset is a reusable creation input:
//! Camp creation resolves it once into the Initial Camp Membership and Initial
//! Default Lead, and the Camp keeps no link back to the preset.
//! The caller holds the Core database lock across read/modify/atomic
//! publication.
use super::*;
use rovai_core::collaboration::ResolvedCampTeam;
use rusqlite::OptionalExtension;
use serde::Serialize;
use std::io::Read;

const FILE_NAME: &str = "team-presets.json";
const SCHEMA_VERSION: u32 = 1;
const MAX_PRESETS: usize = 32;
const MAX_BYTES: usize = 262_144;
const MAX_NAME_CHARS: usize = 40;
const MAX_DESCRIPTION_CHARS: usize = 200;

/// Stable Team Preset rejection codes. They are surfaced as the RPC
/// `ErrorBody.code` with `kind=domain_rejection` and `retryable=false`; clients
/// must branch on the code, never on the message.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum TeamPresetFailureCode {
    NotFound,
    RevisionConflict,
    NameTaken,
    LimitReached,
    MembersUnavailable,
}

impl TeamPresetFailureCode {
    pub(super) const fn as_str(self) -> &'static str {
        match self {
            Self::NotFound => "team_preset_not_found",
            Self::RevisionConflict => "team_preset_revision_conflict",
            Self::NameTaken => "team_preset_name_taken",
            Self::LimitReached => "team_preset_limit_reached",
            Self::MembersUnavailable => "team_preset_members_unavailable",
        }
    }
}

#[derive(Debug)]
pub(super) struct TeamPresetFailure {
    code: TeamPresetFailureCode,
}

impl TeamPresetFailure {
    pub(super) const fn new(code: TeamPresetFailureCode) -> Self {
        Self { code }
    }

    pub(super) const fn code(&self) -> TeamPresetFailureCode {
        self.code
    }
}

impl std::fmt::Display for TeamPresetFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.code.as_str())
    }
}

impl std::error::Error for TeamPresetFailure {}

fn ensure_team_preset(condition: bool, code: TeamPresetFailureCode) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(TeamPresetFailure::new(code).into())
    }
}

fn not_found() -> anyhow::Error {
    TeamPresetFailure::new(TeamPresetFailureCode::NotFound).into()
}

#[derive(Clone, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Preset {
    id: String,
    name: String,
    /// Optional introduction. Absent in pre-description local records, which
    /// read back as the empty string.
    #[serde(default)]
    description: String,
    member_agent_ids: Vec<String>,
    lead_agent_id: String,
    revision: u64,
}
impl Preset {
    fn validate(&self) -> Result<()> {
        validate_id(&self.id)?;
        validate_name(&self.name)?;
        validate_description(&self.description)?;
        anyhow::ensure!(self.revision >= 1, "Team preset revision is invalid");
        validate_members(&self.member_agent_ids, &self.lead_agent_id)
    }
}

#[derive(Clone, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Store {
    schema_version: u32,
    presets: Vec<Preset>,
}
impl Default for Store {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            presets: Vec::new(),
        }
    }
}
impl Store {
    fn validate(&self) -> Result<()> {
        anyhow::ensure!(
            self.schema_version == SCHEMA_VERSION,
            "Team presets schema version is unsupported"
        );
        anyhow::ensure!(
            self.presets.len() <= MAX_PRESETS,
            "Team presets exceed the limit"
        );
        let mut names: HashSet<String> = HashSet::new();
        let mut ids: HashSet<&str> = HashSet::new();
        for preset in &self.presets {
            anyhow::ensure!(ids.insert(&preset.id), "Team preset id is duplicated");
            anyhow::ensure!(
                names.insert(preset.name.to_lowercase()),
                "Team preset name is duplicated"
            );
            preset.validate()?;
        }
        Ok(())
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Snapshot {
    presets: Vec<Preset>,
}

fn normalize_name(name: &str) -> String {
    name.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn validate_name(name: &str) -> Result<()> {
    let normalized = normalize_name(name);
    anyhow::ensure!(normalized == name, "Team preset name is not normalized");
    let length = normalized.chars().count();
    anyhow::ensure!(
        (1..=MAX_NAME_CHARS).contains(&length),
        "Team preset name is invalid"
    );
    Ok(())
}

/// Trims only the ends and keeps body newlines, so a multi-line introduction
/// survives while accidental padding does not.
fn normalize_description(description: &str) -> String {
    description.trim().to_string()
}

fn validate_description(description: &str) -> Result<()> {
    anyhow::ensure!(
        normalize_description(description) == description,
        "Team preset description is not normalized"
    );
    anyhow::ensure!(
        description.chars().count() <= MAX_DESCRIPTION_CHARS,
        "Team preset description is invalid"
    );
    Ok(())
}

fn validate_id(id: &str) -> Result<()> {
    let raw = id
        .strip_prefix("tp_")
        .context("Team preset id is invalid")?;
    let parsed = uuid::Uuid::parse_str(raw).context("Team preset id is invalid")?;
    anyhow::ensure!(
        parsed.get_version_num() == 4 && parsed.hyphenated().to_string() == raw,
        "Team preset id is invalid"
    );
    Ok(())
}

fn validate_members(member_agent_ids: &[String], lead_agent_id: &str) -> Result<()> {
    anyhow::ensure!(
        !member_agent_ids.is_empty() && member_agent_ids.len() <= 100,
        "Team preset members are invalid"
    );
    anyhow::ensure!(
        member_agent_ids
            .iter()
            .all(|id| !id.is_empty() && id.chars().count() <= 200)
            && member_agent_ids.iter().collect::<HashSet<_>>().len() == member_agent_ids.len()
            && member_agent_ids.iter().any(|id| id == lead_agent_id),
        "Team preset members or Lead is invalid"
    );
    Ok(())
}

fn ensure_name_available(store: &Store, name: &str, exclude_id: Option<&str>) -> Result<()> {
    let lowered = name.to_lowercase();
    ensure_team_preset(
        !store.presets.iter().any(|preset| {
            Some(preset.id.as_str()) != exclude_id && preset.name.to_lowercase() == lowered
        }),
        TeamPresetFailureCode::NameTaken,
    )
}

fn read_store(root: &Path) -> Result<Store> {
    let path = root.join(FILE_NAME);
    match std::fs::File::open(&path) {
        Ok(file) => {
            let mut bytes = Vec::new();
            file.take((MAX_BYTES + 1) as u64).read_to_end(&mut bytes)?;
            anyhow::ensure!(
                bytes.len() <= MAX_BYTES,
                "Team presets exceed the size limit"
            );
            let store: Store = serde_json::from_slice(&bytes)?;
            store.validate()?;
            Ok(store)
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(Store::default()),
        Err(error) => Err(error.into()),
    }
}

fn write_store(root: &Path, store: &Store) -> Result<()> {
    crate::platform::private_storage::atomic_write_private_json(&root.join(FILE_NAME), store)
}

fn snapshot(store: &Store) -> Result<Value> {
    Ok(serde_json::to_value(Snapshot {
        presets: store.presets.clone(),
    })?)
}

/// Resolves a preset selection into the members and Lead used to create a Camp.
/// Runs under the same Core database lock as the creation command, after the
/// caller has already replayed any recorded `commandId` result.
pub(super) fn resolve(
    root: &Path,
    database: &Database,
    selection: &TeamPresetSelection,
) -> Result<ResolvedCampTeam> {
    let store = read_store(root)?;
    let preset = store
        .presets
        .iter()
        .find(|preset| preset.id == selection.id)
        .ok_or_else(not_found)?;
    ensure_team_preset(
        preset.revision == selection.expected_revision,
        TeamPresetFailureCode::RevisionConflict,
    )?;
    for member_agent_id in &preset.member_agent_ids {
        let presence = database
            .connection()
            .query_row(
                "SELECT profile_status FROM agent_profile WHERE id = ?1",
                [member_agent_id],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        ensure_team_preset(
            presence.as_deref() == Some("present"),
            TeamPresetFailureCode::MembersUnavailable,
        )?;
    }
    Ok(ResolvedCampTeam {
        member_agent_ids: preset.member_agent_ids.clone(),
        default_lead_agent_id: preset.lead_agent_id.clone(),
    })
}

/// Decides how a `camps.create` command must run. A recorded `commandId` result
/// wins over preset resolution, so a retry survives a later preset edit or
/// delete. The selection stays inside the command's request digest, so the
/// replay is still idempotency-checked against the original request.
#[derive(Debug)]
pub(super) enum CampCreationResolution {
    Custom,
    Resolved(ResolvedCampTeam),
    Replay(rovai_core::command::CommandExecution),
}

pub(super) fn resolve_camp_creation(
    root: &Path,
    database: &Database,
    envelope: &CommandEnvelope<CreateCampCommand>,
) -> Result<CampCreationResolution> {
    let Some(selection) = envelope.payload.team_preset_selection.as_ref() else {
        return Ok(CampCreationResolution::Custom);
    };
    if let Some(replay) = DomainCommandGateway.replay_if_recorded(database, envelope)? {
        return Ok(CampCreationResolution::Replay(replay));
    }
    Ok(CampCreationResolution::Resolved(resolve(
        root, database, selection,
    )?))
}

/// Returns `(snapshot, changed)`. `changed` is true for a successful mutating
/// request; the caller emits `preferences.team_presets_changed`.
pub(super) fn execute(
    root: &Path,
    database: &Database,
    method: &str,
    params: Value,
) -> Result<(Value, bool)> {
    let saved_store = read_store(root)?;
    let mut store = saved_store.clone();
    let mut mutating = true;
    match method {
        "preferences.teamPresets.list" => {
            #[derive(Deserialize)]
            #[serde(deny_unknown_fields)]
            struct Empty {}
            let _: Empty = serde_json::from_value(params)?;
            mutating = false;
        }
        "preferences.teamPresets.save" => {
            #[derive(Deserialize)]
            #[serde(rename_all = "camelCase", deny_unknown_fields)]
            struct SavePreset {
                id: Option<String>,
                name: String,
                #[serde(default)]
                description: Option<String>,
                member_agent_ids: Vec<String>,
                lead_agent_id: String,
            }
            #[derive(Deserialize)]
            #[serde(rename_all = "camelCase", deny_unknown_fields)]
            struct Save {
                preset: SavePreset,
                expected_revision: Option<u64>,
            }
            let input: Save = serde_json::from_value(params)?;
            let name = normalize_name(&input.preset.name);
            validate_name(&name)?;
            let description =
                normalize_description(input.preset.description.as_deref().unwrap_or_default());
            validate_description(&description)?;
            validate_members(&input.preset.member_agent_ids, &input.preset.lead_agent_id)?;
            ensure_members_exist(database, &input.preset.member_agent_ids)?;
            match &input.preset.id {
                None => {
                    ensure_team_preset(
                        input.expected_revision.is_none(),
                        TeamPresetFailureCode::RevisionConflict,
                    )?;
                    ensure_team_preset(
                        store.presets.len() < MAX_PRESETS,
                        TeamPresetFailureCode::LimitReached,
                    )?;
                    ensure_name_available(&store, &name, None)?;
                    store.presets.push(Preset {
                        id: format!("tp_{}", uuid::Uuid::new_v4()),
                        name,
                        description,
                        member_agent_ids: input.preset.member_agent_ids,
                        lead_agent_id: input.preset.lead_agent_id,
                        revision: 1,
                    });
                }
                Some(id) => {
                    let index = store
                        .presets
                        .iter()
                        .position(|preset| &preset.id == id)
                        .ok_or_else(not_found)?;
                    ensure_team_preset(
                        input.expected_revision == Some(store.presets[index].revision),
                        TeamPresetFailureCode::RevisionConflict,
                    )?;
                    ensure_name_available(&store, &name, Some(id))?;
                    let preset = &mut store.presets[index];
                    preset.name = name;
                    preset.description = description;
                    preset.member_agent_ids = input.preset.member_agent_ids;
                    preset.lead_agent_id = input.preset.lead_agent_id;
                    preset.revision += 1;
                }
            }
        }
        "preferences.teamPresets.delete" => {
            #[derive(Deserialize)]
            #[serde(rename_all = "camelCase", deny_unknown_fields)]
            struct Delete {
                id: String,
                expected_revision: u64,
            }
            let input: Delete = serde_json::from_value(params)?;
            let index = store
                .presets
                .iter()
                .position(|preset| preset.id == input.id)
                .ok_or_else(not_found)?;
            ensure_team_preset(
                store.presets[index].revision == input.expected_revision,
                TeamPresetFailureCode::RevisionConflict,
            )?;
            store.presets.remove(index);
        }
        _ => anyhow::bail!("Unsupported team preset operation"),
    }
    if store != saved_store {
        write_store(root, &store)?;
    }
    Ok((snapshot(&store)?, mutating))
}

/// Save-time member check: each id must resolve to an AgentProfile that is not
/// removed, matching the existing default-team rule. A member that is absent or
/// no longer present is reported with the stable `team_preset_members_unavailable`
/// code, the same one creation uses.
fn ensure_members_exist(database: &Database, member_agent_ids: &[String]) -> Result<()> {
    for id in member_agent_ids {
        ensure_team_preset(
            AgentProfileService::default()
                .get_profile(database, id)?
                .is_some(),
            TeamPresetFailureCode::MembersUnavailable,
        )?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn test_root() -> PathBuf {
        let root =
            std::env::temp_dir().join(format!("rovai-team-presets-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    fn insert_member(database: &Database, id: &str, order: i64) {
        database
            .connection()
            .execute(
                "INSERT INTO agent_profile(id, slug, handle, display_name, avatar_ref, team_role, professional_responsibilities, personality_traits_json, working_principles, growth_topic, default_capabilities_json, accent, runtime_enabled, profile_status, member_order, created_at, updated_at) VALUES (?1, ?2, ?3, ?4, 'rovai://member-avatar/managed/123e4567-e89b-12d3-a456-426614174000', 'role', 'responsibility', '[]', 'principles', 'growth', '[]', '#123456', 0, 'present', ?5, '2026-09-24T00:00:00Z', '2026-09-24T00:00:00Z')",
                rusqlite::params![id, id, id, id, order],
            )
            .unwrap();
    }

    fn set_member_status(database: &Database, id: &str, status: &str) {
        database
            .connection()
            .execute(
                "UPDATE agent_profile SET profile_status = ?2 WHERE id = ?1",
                rusqlite::params![id, status],
            )
            .unwrap();
    }

    fn setup() -> (PathBuf, Database) {
        let root = test_root();
        let database = Database::open(&root).unwrap();
        insert_member(&database, "agent_a", 0);
        insert_member(&database, "agent_b", 1);
        insert_member(&database, "agent_c", 2);
        (root, database)
    }

    fn save_new(
        root: &Path,
        database: &Database,
        name: &str,
        members: &[&str],
        lead: &str,
    ) -> Value {
        execute(
            root,
            database,
            "preferences.teamPresets.save",
            json!({
                "preset": {
                    "id": null,
                    "name": name,
                    "memberAgentIds": members,
                    "leadAgentId": lead,
                },
                "expectedRevision": null,
            }),
        )
        .unwrap()
        .0
    }

    fn error_of(root: &Path, database: &Database, method: &str, params: Value) -> String {
        execute(root, database, method, params)
            .unwrap_err()
            .to_string()
    }

    fn camp_envelope(
        command_id: &str,
        selection: TeamPresetSelection,
        root: &Path,
    ) -> CommandEnvelope<CreateCampCommand> {
        CommandEnvelope {
            command_id: command_id.to_string(),
            actor: ActorRef::User {
                user_id: "local_user".to_string(),
            },
            camp_id: None,
            expected_versions: Vec::new(),
            execution_epoch: None,
            payload: CreateCampCommand {
                name: None,
                project_binding_kind: ProjectBindingKind::Directory,
                project_path: root.join("workspace").to_string_lossy().into_owned(),
                member_agent_ids: Vec::new(),
                default_lead_agent_id: String::new(),
                collaboration_mode: CampCollaborationMode::Peer,
                activation_state: CampActivationState::Active,
                team_preset_selection: Some(selection),
            },
        }
    }

    #[test]
    fn store_validation_rejects_unknown_fields_bad_ids_and_versions() {
        assert!(
            serde_json::from_value::<Store>(json!({
                "schemaVersion": 1,
                "presets": [],
                "extra": true,
            }))
            .is_err()
        );
        let bad_id: Store = serde_json::from_value(json!({
            "schemaVersion": 1,
            "presets": [{
                "id": "not-a-preset",
                "name": "Alpha",
                "memberAgentIds": ["agent_a"],
                "leadAgentId": "agent_a",
                "revision": 1,
            }],
        }))
        .unwrap();
        assert!(bad_id.validate().is_err());
        let bad_version: Store = serde_json::from_value(json!({
            "schemaVersion": 2,
            "presets": [],
        }))
        .unwrap();
        assert!(bad_version.validate().is_err());
        let lead_outside: Store = serde_json::from_value(json!({
            "schemaVersion": 1,
            "presets": [{
                "id": format!("tp_{}", uuid::Uuid::new_v4()),
                "name": "Alpha",
                "memberAgentIds": ["agent_a"],
                "leadAgentId": "agent_b",
                "revision": 1,
            }],
        }))
        .unwrap();
        assert!(lead_outside.validate().is_err());
    }

    #[test]
    fn name_is_normalized_and_uniqueness_ignores_case_and_whitespace() {
        let (root, database) = setup();
        let snapshot = save_new(
            &root,
            &database,
            "  Backend   Dev  ",
            &["agent_a"],
            "agent_a",
        );
        assert_eq!(snapshot["presets"][0]["name"], "Backend Dev");
        assert_eq!(
            error_of(
                &root,
                &database,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": null,
                        "name": "backend dev",
                        "memberAgentIds": ["agent_a"],
                        "leadAgentId": "agent_a",
                    },
                    "expectedRevision": null,
                }),
            ),
            "team_preset_name_taken"
        );
    }

    #[test]
    fn create_update_and_delete_enforce_revision() {
        let (root, database) = setup();
        let snapshot = save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        assert_eq!(snapshot["presets"][0]["revision"].as_u64(), Some(1));

        assert_eq!(
            error_of(
                &root,
                &database,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": id,
                        "name": "Alpha",
                        "memberAgentIds": ["agent_b"],
                        "leadAgentId": "agent_b",
                    },
                    "expectedRevision": 99,
                }),
            ),
            "team_preset_revision_conflict"
        );

        let updated = execute(
            &root,
            &database,
            "preferences.teamPresets.save",
            json!({
                "preset": {
                    "id": id,
                    "name": "Alpha",
                    "memberAgentIds": ["agent_b"],
                    "leadAgentId": "agent_b",
                },
                "expectedRevision": 1,
            }),
        )
        .unwrap()
        .0;
        assert_eq!(updated["presets"][0]["revision"].as_u64(), Some(2));
        assert_eq!(updated["presets"][0]["memberAgentIds"][0], "agent_b");

        assert_eq!(
            error_of(
                &root,
                &database,
                "preferences.teamPresets.delete",
                json!({
                    "id": "tp_00000000-0000-4000-8000-000000000000",
                    "expectedRevision": 1,
                }),
            ),
            "team_preset_not_found"
        );

        let deleted = execute(
            &root,
            &database,
            "preferences.teamPresets.delete",
            json!({ "id": id, "expectedRevision": 2 }),
        )
        .unwrap()
        .0;
        assert!(deleted["presets"].as_array().unwrap().is_empty());
    }

    #[test]
    fn new_preset_must_not_carry_expected_revision() {
        let (root, database) = setup();
        assert_eq!(
            error_of(
                &root,
                &database,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": null,
                        "name": "Zeta",
                        "memberAgentIds": ["agent_a"],
                        "leadAgentId": "agent_a",
                    },
                    "expectedRevision": 3,
                }),
            ),
            "team_preset_revision_conflict"
        );
    }

    #[test]
    fn save_rejects_unavailable_members_and_closed_params() {
        let (root, database) = setup();
        let missing = error_of(
            &root,
            &database,
            "preferences.teamPresets.save",
            json!({
                "preset": {
                    "id": null,
                    "name": "Ghost",
                    "memberAgentIds": ["agent_missing"],
                    "leadAgentId": "agent_missing",
                },
                "expectedRevision": null,
            }),
        );
        assert!(missing.contains("unavailable"));
        assert!(
            execute(
                &root,
                &database,
                "preferences.teamPresets.list",
                json!({ "extra": true })
            )
            .is_err()
        );
    }

    #[test]
    fn preset_count_limit_is_enforced() {
        let (root, database) = setup();
        for index in 0..MAX_PRESETS {
            save_new(
                &root,
                &database,
                &format!("Preset {index}"),
                &["agent_a"],
                "agent_a",
            );
        }
        assert_eq!(
            error_of(
                &root,
                &database,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": null,
                        "name": "One too many",
                        "memberAgentIds": ["agent_a"],
                        "leadAgentId": "agent_a",
                    },
                    "expectedRevision": null,
                }),
            ),
            "team_preset_limit_reached"
        );
    }

    #[test]
    fn oversized_store_is_rejected() {
        let (root, database) = setup();
        std::fs::write(root.join(FILE_NAME), vec![b'a'; MAX_BYTES + 1]).unwrap();
        let error =
            execute(&root, &database, "preferences.teamPresets.list", json!({})).unwrap_err();
        assert!(error.to_string().contains("size limit"));
    }

    #[test]
    fn list_without_store_returns_empty_snapshot() {
        let (root, database) = setup();
        let snapshot = execute(&root, &database, "preferences.teamPresets.list", json!({}))
            .unwrap()
            .0;
        assert!(snapshot["presets"].as_array().unwrap().is_empty());
        assert!(!root.join(FILE_NAME).exists());
    }

    #[test]
    fn preset_requests_never_touch_default_preferences() {
        let (root, database) = setup();
        // Create the unrelated default preference file first.
        conversation_preferences::execute(
            &root,
            &database,
            "preferences.newConversation.setDefaults",
            json!({
                "defaults": {
                    "memberAgentIds": ["agent_a"],
                    "defaultLeadAgentId": "agent_a",
                },
                "enableOneClick": true,
            }),
        )
        .unwrap();
        let default_path = root.join("new-conversation-preferences.json");
        let before = std::fs::read(&default_path).unwrap();

        let snapshot = save_new(&root, &database, "Epsilon", &["agent_b"], "agent_b");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        execute(
            &root,
            &database,
            "preferences.teamPresets.save",
            json!({
                "preset": {
                    "id": id,
                    "name": "Epsilon",
                    "memberAgentIds": ["agent_c"],
                    "leadAgentId": "agent_c",
                },
                "expectedRevision": 1,
            }),
        )
        .unwrap();
        execute(
            &root,
            &database,
            "preferences.teamPresets.delete",
            json!({ "id": id, "expectedRevision": 2 }),
        )
        .unwrap();

        assert_eq!(before, std::fs::read(&default_path).unwrap());
    }

    #[test]
    fn a_fresh_preset_store_does_not_create_default_preferences() {
        let (root, database) = setup();
        save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        assert!(!root.join("new-conversation-preferences.json").exists());
    }

    #[test]
    fn resolve_maps_selection_to_members_and_lead() {
        let (root, database) = setup();
        let snapshot = save_new(
            &root,
            &database,
            "Alpha",
            &["agent_b", "agent_a"],
            "agent_b",
        );
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        let resolved = resolve(
            &root,
            &database,
            &TeamPresetSelection {
                id,
                expected_revision: 1,
            },
        )
        .unwrap();
        assert_eq!(
            resolved.member_agent_ids,
            vec!["agent_b".to_string(), "agent_a".to_string()]
        );
        assert_eq!(resolved.default_lead_agent_id, "agent_b");
    }

    #[test]
    fn resolve_reports_missing_changed_and_unavailable_presets() {
        let (root, database) = setup();
        let snapshot = save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        assert_eq!(
            resolve(
                &root,
                &database,
                &TeamPresetSelection {
                    id: "tp_00000000-0000-4000-8000-000000000000".to_string(),
                    expected_revision: 1,
                },
            )
            .unwrap_err()
            .to_string(),
            "team_preset_not_found"
        );
        assert_eq!(
            resolve(
                &root,
                &database,
                &TeamPresetSelection {
                    id: id.clone(),
                    expected_revision: 2,
                },
            )
            .unwrap_err()
            .to_string(),
            "team_preset_revision_conflict"
        );
        set_member_status(&database, "agent_a", "away");
        assert_eq!(
            resolve(
                &root,
                &database,
                &TeamPresetSelection {
                    id,
                    expected_revision: 1,
                },
            )
            .unwrap_err()
            .to_string(),
            "team_preset_members_unavailable"
        );
    }

    #[test]
    fn camps_create_accepts_exactly_one_team_input() {
        let custom: CreateCampParams = serde_json::from_value(json!({
            "commandId": "cmd",
            "memberAgentIds": ["agent_a"],
            "defaultLeadAgentId": "agent_a",
            "collaborationMode": "peer",
        }))
        .unwrap();
        assert!(custom.validate_creation_input().is_ok());

        let preset: CreateCampParams = serde_json::from_value(json!({
            "commandId": "cmd",
            "teamPresetSelection": { "id": "tp_x", "expectedRevision": 1 },
            "collaborationMode": "peer",
        }))
        .unwrap();
        assert!(preset.validate_creation_input().is_ok());

        let both: CreateCampParams = serde_json::from_value(json!({
            "commandId": "cmd",
            "memberAgentIds": ["agent_a"],
            "defaultLeadAgentId": "agent_a",
            "teamPresetSelection": { "id": "tp_x", "expectedRevision": 1 },
            "collaborationMode": "peer",
        }))
        .unwrap();
        assert!(both.validate_creation_input().is_err());

        let neither: CreateCampParams = serde_json::from_value(json!({
            "commandId": "cmd",
            "collaborationMode": "peer",
        }))
        .unwrap();
        assert!(neither.validate_creation_input().is_err());

        let partial: CreateCampParams = serde_json::from_value(json!({
            "commandId": "cmd",
            "memberAgentIds": ["agent_a"],
            "collaborationMode": "peer",
        }))
        .unwrap();
        assert!(partial.validate_creation_input().is_err());
    }

    #[test]
    fn custom_camp_creation_still_runs_without_a_preset() {
        let (root, mut database) = setup();
        let envelope = CommandEnvelope {
            command_id: "cmd-custom".to_string(),
            actor: ActorRef::User {
                user_id: "local_user".to_string(),
            },
            camp_id: None,
            expected_versions: Vec::new(),
            execution_epoch: None,
            payload: CreateCampCommand {
                name: None,
                project_binding_kind: ProjectBindingKind::Directory,
                project_path: root.join("workspace").to_string_lossy().into_owned(),
                member_agent_ids: vec!["agent_a".to_string()],
                default_lead_agent_id: "agent_a".to_string(),
                collaboration_mode: CampCollaborationMode::Peer,
                activation_state: CampActivationState::Active,
                team_preset_selection: None,
            },
        };
        assert!(matches!(
            resolve_camp_creation(&root, &database, &envelope).unwrap(),
            CampCreationResolution::Custom
        ));
        let created = CollaborationService::default()
            .create_camp(&mut database, &envelope)
            .unwrap();
        assert_eq!(created.result.status, CommandResultStatus::Applied);
        assert!(created.result.payload["campId"].is_string());
    }

    #[test]
    fn camps_create_resolves_a_preset_before_creating() {
        let (root, mut database) = setup();
        let snapshot = save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        let envelope = camp_envelope(
            "cmd-preset",
            TeamPresetSelection {
                id,
                expected_revision: 1,
            },
            &root,
        );
        let resolved = match resolve_camp_creation(&root, &database, &envelope).unwrap() {
            CampCreationResolution::Resolved(resolved) => resolved,
            _ => panic!("a new preset command must resolve the preset"),
        };
        let created = CollaborationService::default()
            .create_camp_with_team(&mut database, &envelope, resolved)
            .unwrap();
        assert_eq!(created.result.status, CommandResultStatus::Applied);
        assert!(created.result.payload["campId"].is_string());
    }

    #[test]
    fn camps_create_replays_recorded_result_before_resolving_a_changed_preset() {
        let (root, mut database) = setup();
        let snapshot = save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        let envelope = camp_envelope(
            "cmd-retry",
            TeamPresetSelection {
                id: id.clone(),
                expected_revision: 1,
            },
            &root,
        );
        let resolved = resolve(
            &root,
            &database,
            &TeamPresetSelection {
                id: id.clone(),
                expected_revision: 1,
            },
        )
        .unwrap();
        let first = CollaborationService::default()
            .create_camp_with_team(&mut database, &envelope, resolved)
            .unwrap();
        let camp_id = first.result.payload["campId"].as_str().unwrap().to_string();

        // The preset changes and is then deleted; the retry must still replay.
        execute(
            &root,
            &database,
            "preferences.teamPresets.save",
            json!({
                "preset": {
                    "id": id,
                    "name": "Alpha",
                    "memberAgentIds": ["agent_b"],
                    "leadAgentId": "agent_b",
                },
                "expectedRevision": 1,
            }),
        )
        .unwrap();
        execute(
            &root,
            &database,
            "preferences.teamPresets.delete",
            json!({ "id": id, "expectedRevision": 2 }),
        )
        .unwrap();

        match resolve_camp_creation(&root, &database, &envelope).unwrap() {
            CampCreationResolution::Replay(replay) => {
                assert!(replay.replayed);
                assert_eq!(
                    replay.result.payload["campId"].as_str(),
                    Some(camp_id.as_str())
                );
            }
            _ => panic!("a recorded commandId must replay before preset resolution"),
        }
    }

    #[test]
    fn camps_create_rejects_a_selection_that_no_longer_resolves() {
        let (root, database) = setup();
        let snapshot = save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        let envelope = camp_envelope(
            "cmd-stale",
            TeamPresetSelection {
                id,
                expected_revision: 2,
            },
            &root,
        );
        assert_eq!(
            resolve_camp_creation(&root, &database, &envelope)
                .unwrap_err()
                .to_string(),
            "team_preset_revision_conflict"
        );
    }

    fn save_with(
        root: &Path,
        database: &Database,
        name: &str,
        members: &[String],
        lead: &str,
    ) -> Result<Value> {
        execute(
            root,
            database,
            "preferences.teamPresets.save",
            json!({
                "preset": {
                    "id": null,
                    "name": name,
                    "memberAgentIds": members,
                    "leadAgentId": lead,
                },
                "expectedRevision": null,
            }),
        )
        .map(|(snapshot, _)| snapshot)
    }

    fn assert_typed(error: anyhow::Error, expected: &str) {
        let failure = error
            .downcast_ref::<TeamPresetFailure>()
            .expect("team preset failures must stay typed through anyhow");
        assert_eq!(failure.code().as_str(), expected);
        let body = request_error_body(&error);
        assert_eq!(body.kind, "domain_rejection");
        assert_eq!(body.code, expected);
        assert!(!body.retryable);
        assert_eq!(body.message, expected);
    }

    fn drain_events(output: &mut mpsc::UnboundedReceiver<String>) -> Vec<Value> {
        let mut events = Vec::new();
        while let Ok(line) = output.try_recv() {
            events.push(serde_json::from_str(&line).unwrap());
        }
        events
    }

    #[test]
    fn invalid_store_is_an_error_and_is_not_rewritten() {
        let (root, database) = setup();
        let path = root.join(FILE_NAME);
        for bytes in [
            b"not json".to_vec(),
            serde_json::to_vec(&json!({ "schemaVersion": 2, "presets": [] })).unwrap(),
        ] {
            std::fs::write(&path, &bytes).unwrap();
            assert!(execute(&root, &database, "preferences.teamPresets.list", json!({})).is_err());
            assert!(
                save_with(
                    &root,
                    &database,
                    "Alpha",
                    &["agent_a".to_string()],
                    "agent_a",
                )
                .is_err()
            );
            assert_eq!(std::fs::read(&path).unwrap(), bytes);
        }
    }

    #[test]
    fn presets_keep_creation_order() {
        let (root, database) = setup();
        for name in ["First", "Second", "Third"] {
            save_new(&root, &database, name, &["agent_a"], "agent_a");
        }
        let snapshot = execute(&root, &database, "preferences.teamPresets.list", json!({}))
            .unwrap()
            .0;
        let names: Vec<&str> = snapshot["presets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|preset| preset["name"].as_str().unwrap())
            .collect();
        assert_eq!(names, vec!["First", "Second", "Third"]);
    }

    #[test]
    fn name_length_and_unicode_whitespace_rules() {
        let (root, database) = setup();
        assert!(
            save_with(
                &root,
                &database,
                &"a".repeat(40),
                &["agent_a".to_string()],
                "agent_a",
            )
            .is_ok()
        );
        assert!(
            save_with(
                &root,
                &database,
                &"a".repeat(41),
                &["agent_a".to_string()],
                "agent_a",
            )
            .is_err()
        );
        assert!(save_with(&root, &database, "   ", &["agent_a".to_string()], "agent_a").is_err());
        let snapshot = save_with(
            &root,
            &database,
            "  后端\u{3000}开发  ",
            &["agent_a".to_string()],
            "agent_a",
        )
        .unwrap();
        assert_eq!(snapshot["presets"][1]["name"], "后端 开发");
    }

    #[test]
    fn member_boundaries_are_enforced() {
        let (root, database) = setup();
        let too_many: Vec<String> = (0..101).map(|index| format!("agent_{index}")).collect();
        assert!(save_with(&root, &database, "TooMany", &too_many, "agent_0").is_err());

        for index in 0..100 {
            insert_member(&database, &format!("member_{index}"), index);
        }
        let maximum: Vec<String> = (0..100).map(|index| format!("member_{index}")).collect();
        assert!(save_with(&root, &database, "Max", &maximum, "member_0").is_ok());

        assert!(
            save_with(
                &root,
                &database,
                "Duplicate",
                &["agent_a".to_string(), "agent_a".to_string()],
                "agent_a",
            )
            .is_err()
        );
        let overlong = "x".repeat(201);
        assert!(save_with(&root, &database, "Overlong", &[overlong.clone()], &overlong).is_err());
        assert!(save_with(&root, &database, "Empty", &[], "agent_a").is_err());
        assert!(
            save_with(
                &root,
                &database,
                "OutsideLead",
                &["agent_a".to_string()],
                "agent_b",
            )
            .is_err()
        );
    }

    #[test]
    fn failed_requests_leave_the_store_unchanged() {
        let (root, database) = setup();
        let snapshot = save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        let path = root.join(FILE_NAME);
        let before = std::fs::read(&path).unwrap();

        assert!(
            execute(
                &root,
                &database,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": id,
                        "name": "Beta",
                        "memberAgentIds": ["agent_b"],
                        "leadAgentId": "agent_b",
                    },
                    "expectedRevision": 99,
                }),
            )
            .is_err()
        );
        assert_eq!(std::fs::read(&path).unwrap(), before);

        save_new(&root, &database, "Gamma", &["agent_a"], "agent_a");
        let before = std::fs::read(&path).unwrap();
        assert!(
            save_with(
                &root,
                &database,
                "gamma",
                &["agent_a".to_string()],
                "agent_a"
            )
            .is_err()
        );
        assert_eq!(std::fs::read(&path).unwrap(), before);

        assert!(
            execute(
                &root,
                &database,
                "preferences.teamPresets.delete",
                json!({ "id": id, "expectedRevision": 99 }),
            )
            .is_err()
        );
        assert_eq!(std::fs::read(&path).unwrap(), before);
    }

    #[test]
    fn update_requires_matching_expected_revision() {
        let (root, database) = setup();
        let snapshot = save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        for expected in [json!(null), json!(2)] {
            assert!(
                execute(
                    &root,
                    &database,
                    "preferences.teamPresets.save",
                    json!({
                        "preset": {
                            "id": id,
                            "name": "Alpha",
                            "memberAgentIds": ["agent_a"],
                            "leadAgentId": "agent_a",
                        },
                        "expectedRevision": expected,
                    }),
                )
                .is_err()
            );
        }
        let updated = execute(
            &root,
            &database,
            "preferences.teamPresets.save",
            json!({
                "preset": {
                    "id": id,
                    "name": "Alpha",
                    "memberAgentIds": ["agent_a"],
                    "leadAgentId": "agent_a",
                },
                "expectedRevision": 1,
            }),
        )
        .unwrap()
        .0;
        assert_eq!(updated["presets"][0]["revision"].as_u64(), Some(2));
    }

    #[test]
    fn request_params_are_closed_for_all_operations() {
        let (root, database) = setup();
        let snapshot = save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        assert!(
            execute(
                &root,
                &database,
                "preferences.teamPresets.list",
                json!({ "extra": true })
            )
            .is_err()
        );
        assert!(
            execute(
                &root,
                &database,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": null,
                        "name": "X",
                        "memberAgentIds": ["agent_a"],
                        "leadAgentId": "agent_a",
                    },
                    "expectedRevision": null,
                    "extra": 1,
                }),
            )
            .is_err()
        );
        assert!(
            execute(
                &root,
                &database,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": null,
                        "name": "X",
                        "memberAgentIds": ["agent_a"],
                        "leadAgentId": "agent_a",
                        "extra": 1,
                    },
                    "expectedRevision": null,
                }),
            )
            .is_err()
        );
        assert!(
            execute(
                &root,
                &database,
                "preferences.teamPresets.delete",
                json!({ "id": id, "expectedRevision": 1, "extra": 1 }),
            )
            .is_err()
        );
    }

    #[test]
    fn camp_keeps_no_link_to_the_preset() {
        let (root, mut database) = setup();
        let snapshot = save_new(
            &root,
            &database,
            "Alpha",
            &["agent_a", "agent_b"],
            "agent_a",
        );
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        let selection = TeamPresetSelection {
            id: id.clone(),
            expected_revision: 1,
        };
        let envelope = camp_envelope("cmd-link", selection.clone(), &root);
        let resolved = resolve(&root, &database, &selection).unwrap();
        let created = CollaborationService::default()
            .create_camp_with_team(&mut database, &envelope, resolved)
            .unwrap();
        let camp_id = created.result.payload["campId"]
            .as_str()
            .unwrap()
            .to_string();

        execute(
            &root,
            &database,
            "preferences.teamPresets.delete",
            json!({ "id": id, "expectedRevision": 1 }),
        )
        .unwrap();

        let mut members: Vec<String> = {
            let connection = database.connection();
            let mut statement = connection
                .prepare("SELECT agent_id FROM camp_member WHERE camp_id = ?1")
                .unwrap();
            let rows = statement
                .query_map([&camp_id], |row| row.get(0))
                .unwrap()
                .collect::<rusqlite::Result<Vec<_>>>()
                .unwrap();
            rows
        };
        members.sort();
        assert_eq!(members, vec!["agent_a".to_string(), "agent_b".to_string()]);
        let lead: String = database
            .connection()
            .query_row(
                "SELECT default_lead_agent_id FROM camp WHERE id = ?1",
                [&camp_id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(lead, "agent_a");
        let preset_columns: i64 = database
            .connection()
            .query_row(
                "SELECT COUNT(*) FROM pragma_table_info('camp') WHERE name LIKE '%preset%'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(preset_columns, 0);
    }

    #[test]
    fn team_preset_failures_are_typed_rpc_rejections() {
        let (root, database) = setup();
        let snapshot = save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();

        assert_typed(
            execute(
                &root,
                &database,
                "preferences.teamPresets.delete",
                json!({
                    "id": "tp_00000000-0000-4000-8000-000000000000",
                    "expectedRevision": 1,
                }),
            )
            .unwrap_err(),
            "team_preset_not_found",
        );
        assert_typed(
            execute(
                &root,
                &database,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": id,
                        "name": "Alpha",
                        "memberAgentIds": ["agent_a"],
                        "leadAgentId": "agent_a",
                    },
                    "expectedRevision": 99,
                }),
            )
            .unwrap_err(),
            "team_preset_revision_conflict",
        );
        assert_typed(
            execute(
                &root,
                &database,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": null,
                        "name": "alpha",
                        "memberAgentIds": ["agent_a"],
                        "leadAgentId": "agent_a",
                    },
                    "expectedRevision": null,
                }),
            )
            .unwrap_err(),
            "team_preset_name_taken",
        );
        assert_typed(
            save_with(
                &root,
                &database,
                "Missing",
                &["agent_missing".to_string()],
                "agent_missing",
            )
            .unwrap_err(),
            "team_preset_members_unavailable",
        );

        for index in 0..(MAX_PRESETS - 1) {
            save_new(
                &root,
                &database,
                &format!("Filler {index}"),
                &["agent_a"],
                "agent_a",
            );
        }
        assert_typed(
            save_with(
                &root,
                &database,
                "Overflow",
                &["agent_a".to_string()],
                "agent_a",
            )
            .unwrap_err(),
            "team_preset_limit_reached",
        );
    }

    #[test]
    fn camps_create_preset_failures_are_typed_rpc_rejections() {
        let (root, database) = setup();
        let snapshot = save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();

        assert_typed(
            resolve(
                &root,
                &database,
                &TeamPresetSelection {
                    id: "tp_00000000-0000-4000-8000-000000000000".to_string(),
                    expected_revision: 1,
                },
            )
            .unwrap_err(),
            "team_preset_not_found",
        );
        assert_typed(
            resolve(
                &root,
                &database,
                &TeamPresetSelection {
                    id: id.clone(),
                    expected_revision: 2,
                },
            )
            .unwrap_err(),
            "team_preset_revision_conflict",
        );
        set_member_status(&database, "agent_a", "away");
        assert_typed(
            resolve(
                &root,
                &database,
                &TeamPresetSelection {
                    id,
                    expected_revision: 1,
                },
            )
            .unwrap_err(),
            "team_preset_members_unavailable",
        );
    }

    #[test]
    fn write_requests_signal_changed_for_event_emission() {
        let (root, database) = setup();
        let snapshot = save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let id = snapshot["presets"][0]["id"].as_str().unwrap().to_string();
        assert!(
            !execute(&root, &database, "preferences.teamPresets.list", json!({}))
                .unwrap()
                .1
        );
        assert!(
            execute(
                &root,
                &database,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": id,
                        "name": "Alpha",
                        "memberAgentIds": ["agent_a"],
                        "leadAgentId": "agent_a",
                    },
                    "expectedRevision": 1,
                }),
            )
            .unwrap()
            .1
        );
        assert!(
            execute(
                &root,
                &database,
                "preferences.teamPresets.delete",
                json!({ "id": id, "expectedRevision": 2 }),
            )
            .unwrap()
            .1
        );
    }

    #[test]
    fn team_presets_changed_event_frame_is_stable() {
        let (sender, mut receiver) = mpsc::unbounded_channel();
        emit(&sender, "preferences.team_presets_changed", json!({}));
        let frame: Value = serde_json::from_str(&receiver.try_recv().unwrap()).unwrap();
        assert_eq!(
            frame,
            json!({ "method": "preferences.team_presets_changed", "params": {} })
        );
    }

    #[cfg(all(feature = "slow-tests", any(target_os = "macos", windows)))]
    #[tokio::test]
    async fn rpc_team_preset_writes_emit_only_team_presets_changed() {
        let root = test_root();
        let (core, mut output) =
            super::super::tests::runtime_resolution_test_core_with_output(&root).unwrap();
        let core = std::sync::Arc::new(core);
        {
            let database = core.database.lock().await;
            insert_member(&database, "agent_a", 0);
        }
        let request = |id: i64, method: &str, params: Value| Request {
            client: rovai_core::draft_client::DraftClient::default(),
            id: json!(id),
            method: method.to_string(),
            params,
        };

        core.handle(&request(1, "preferences.teamPresets.list", json!({})))
            .await
            .unwrap();
        assert!(drain_events(&mut output).is_empty());

        let saved = core
            .handle(&request(
                2,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": null,
                        "name": "Alpha",
                        "description": "负责后端接口",
                        "memberAgentIds": ["agent_a"],
                        "leadAgentId": "agent_a",
                    },
                    "expectedRevision": null,
                }),
            ))
            .await
            .unwrap();
        assert_eq!(saved["presets"][0]["description"], "负责后端接口");
        let events = drain_events(&mut output);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0]["method"], "preferences.team_presets_changed");
        assert_eq!(events[0]["params"], json!({}));

        let error = core
            .handle(&request(
                3,
                "preferences.teamPresets.save",
                json!({
                    "preset": {
                        "id": null,
                        "name": "alpha",
                        "memberAgentIds": ["agent_a"],
                        "leadAgentId": "agent_a",
                    },
                    "expectedRevision": null,
                }),
            ))
            .await
            .unwrap_err();
        assert!(drain_events(&mut output).is_empty());
        assert_eq!(request_error_body(&error).code, "team_preset_name_taken");

        let id = saved["presets"][0]["id"].as_str().unwrap().to_string();
        core.handle(&request(
            4,
            "preferences.teamPresets.delete",
            json!({ "id": id, "expectedRevision": 1 }),
        ))
        .await
        .unwrap();
        let events = drain_events(&mut output);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0]["method"], "preferences.team_presets_changed");
        assert!(
            events
                .iter()
                .all(|event| event["method"] != "preferences.new_conversation_changed")
        );
    }

    fn save_description(
        root: &Path,
        database: &Database,
        name: &str,
        description: &str,
        id: Option<&str>,
        expected_revision: Option<u64>,
    ) -> Result<Value> {
        execute(
            root,
            database,
            "preferences.teamPresets.save",
            json!({
                "preset": {
                    "id": id,
                    "name": name,
                    "description": description,
                    "memberAgentIds": ["agent_a"],
                    "leadAgentId": "agent_a",
                },
                "expectedRevision": expected_revision,
            }),
        )
        .map(|(snapshot, _)| snapshot)
    }

    #[test]
    fn missing_description_reads_as_empty_and_save_writes_the_field() {
        let (root, database) = setup();
        let legacy = json!({
            "schemaVersion": 1,
            "presets": [{
                "id": format!("tp_{}", uuid::Uuid::new_v4()),
                "name": "Alpha",
                "memberAgentIds": ["agent_a"],
                "leadAgentId": "agent_a",
                "revision": 1,
            }],
        });
        let legacy_bytes = serde_json::to_vec(&legacy).unwrap();
        std::fs::write(root.join(FILE_NAME), &legacy_bytes).unwrap();

        let listed = execute(&root, &database, "preferences.teamPresets.list", json!({}))
            .unwrap()
            .0;
        assert_eq!(listed["presets"][0]["description"], "");
        // Reading a pre-description record must not rewrite it.
        assert_eq!(std::fs::read(root.join(FILE_NAME)).unwrap(), legacy_bytes);

        let id = listed["presets"][0]["id"].as_str().unwrap().to_string();
        let saved = save_description(
            &root,
            &database,
            "Alpha",
            "新介绍",
            Some(id.as_str()),
            Some(1),
        )
        .unwrap();
        assert_eq!(saved["presets"][0]["description"], "新介绍");
        assert_eq!(saved["presets"][0]["revision"].as_u64(), Some(2));
        let persisted: Value =
            serde_json::from_slice(&std::fs::read(root.join(FILE_NAME)).unwrap()).unwrap();
        assert_eq!(persisted["presets"][0]["description"], "新介绍");
    }

    #[test]
    fn description_length_and_newline_rules() {
        let (root, database) = setup();
        assert!(save_description(&root, &database, "Empty", "", None, None).is_ok());
        assert!(save_description(&root, &database, "Max", &"a".repeat(200), None, None).is_ok());
        assert!(save_description(&root, &database, "Over", &"a".repeat(201), None, None).is_err());
        let trimmed = save_description(
            &root,
            &database,
            "Trimmed",
            "  第一行\n第二行  ",
            None,
            None,
        )
        .unwrap();
        let trimmed_preset = trimmed["presets"]
            .as_array()
            .unwrap()
            .iter()
            .find(|preset| preset["name"] == "Trimmed")
            .unwrap();
        assert_eq!(trimmed_preset["description"], "第一行\n第二行");

        let blank = save_description(&root, &database, "Blank", "   \n  ", None, None).unwrap();
        let blank_preset = blank["presets"]
            .as_array()
            .unwrap()
            .iter()
            .find(|preset| preset["name"] == "Blank")
            .unwrap();
        assert_eq!(blank_preset["description"], "");
    }

    #[test]
    fn description_only_change_increments_revision() {
        let (root, database) = setup();
        let saved = save_description(&root, &database, "Alpha", "旧介绍", None, None).unwrap();
        let id = saved["presets"][0]["id"].as_str().unwrap().to_string();
        let updated = save_description(
            &root,
            &database,
            "Alpha",
            "新介绍",
            Some(id.as_str()),
            Some(1),
        )
        .unwrap();
        assert_eq!(updated["presets"][0]["revision"].as_u64(), Some(2));
        assert_eq!(updated["presets"][0]["description"], "新介绍");
    }

    #[test]
    fn description_does_not_affect_name_uniqueness() {
        let (root, database) = setup();
        assert!(save_description(&root, &database, "Alpha", "同一个介绍", None, None).is_ok());
        assert!(save_description(&root, &database, "Beta", "同一个介绍", None, None).is_ok());
    }

    #[test]
    fn description_failure_leaves_the_store_unchanged() {
        let (root, database) = setup();
        save_new(&root, &database, "Alpha", &["agent_a"], "agent_a");
        let path = root.join(FILE_NAME);
        let before = std::fs::read(&path).unwrap();
        assert!(save_description(&root, &database, "Beta", &"a".repeat(201), None, None).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), before);
    }
}
