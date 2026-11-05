const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { localProfile, initialProfile, listProfiles, newCreatorProfile, newCharacterProfile } = require("./windows_profiles");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "anthem-profile-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "data", "saves"), { recursive: true });
  const preset = JSON.stringify({ niveau_pilote: 30, pilotes: [{ id: 1, name: "Original" }] });
  fs.writeFileSync(path.join(root, "data", "saves", "state.json"), preset);
  return { root, user: path.join(root, "user"), preset };
}

test("fresh male pilot starts at level 1 and preserves both development saves", (t) => {
  const { root, user, preset } = fixture(t);
  const development = localProfile(root, user, {});
  const inherited = { ANTHEM_PROFILE: "fresh", ANTHEM_PROFILE_ID: "kingsplugorf", ANTHEM_PILOT_NAME: "KingSplugorf", ANTHEM_PILOT_GENDER: "Male", BIGS_NOUVELLE_PARTIE: "1", BIGS_SANS_PERSONNAGE: "1", BIGS_PERSISTANCE: "{}", BIGS_PERSONNAGE_ID: "99", ANTHEM_LOAD_LOCAL_CA: "1" };
  const profile = localProfile(root, user, inherited);
  assert.notEqual(profile.state, development.state);
  assert.deepEqual(JSON.parse(fs.readFileSync(profile.state)), {
    niveau_pilote: 1, inventaire_depart_seul: true, pilotes: [{ id: 1, name: "KingSplugorf", gender: "Male", appearance: "" }],
  });
  assert.equal(profile.env.BIGS_PILOTES_SAUVEGARDES, "1");
  assert.equal(profile.env.JOUEUR_NOM, "KingSplugorf");
  assert.equal(profile.env.ANTHEM_LOAD_LOCAL_CA, "1");
  for (const key of ["BIGS_NOUVELLE_PARTIE", "BIGS_SANS_PERSONNAGE", "BIGS_PERSISTANCE", "BIGS_PERSONNAGE_ID"]) assert.equal(profile.env[key], undefined);
  assert.equal(inherited.BIGS_NOUVELLE_PARTIE, "1");
  assert.equal(fs.readFileSync(development.state, "utf8"), preset);
  assert.equal(fs.readFileSync(path.join(root, "data", "saves", "state.json"), "utf8"), preset);
});

test("relaunch resumes saved progress and a different slot gets a new pilot", (t) => {
  const { root, user } = fixture(t);
  const env = { ANTHEM_PROFILE: "fresh", ANTHEM_PILOT_NAME: "KingSplugorf" };
  const profile = localProfile(root, user, env);
  const saved = JSON.parse(fs.readFileSync(profile.state));
  saved.niveau_pilote = 7;
  saved.pilotes[0].name = "Saved name";
  saved.persistence = [{ DefinitionId: 12 }];
  const bytes = JSON.stringify(saved);
  fs.writeFileSync(profile.state, bytes);
  assert.equal(localProfile(root, user, env).state, profile.state);
  assert.equal(fs.readFileSync(profile.state, "utf8"), bytes);
  const other = localProfile(root, user, { ...env, ANTHEM_PROFILE_ID: "pilot-2" });
  assert.notEqual(profile.state, other.state);
  assert.equal(JSON.parse(fs.readFileSync(other.state)).niveau_pilote, 1);
});

test("tutorial profile uses a different slot with no precreated pilot", (t) => {
  const { root, user } = fixture(t);
  const fresh = localProfile(root, user, { ANTHEM_PROFILE: "fresh" });
  const tutorial = localProfile(root, user, { ANTHEM_PROFILE: "tutorial" });
  assert.notEqual(fresh.state, tutorial.state);
  assert.deepEqual(JSON.parse(fs.readFileSync(tutorial.state)).pilotes, []);
  assert.equal(tutorial.env.BIGS_NOUVELLE_PARTIE, "1");
  assert.equal(tutorial.env.ANTHEM_STORY_MODE, "1");
  assert.equal(JSON.parse(fs.readFileSync(tutorial.state)).story_mode, "tutorial");
});

test("New character defaults to an isolated full introduction and explicit hub mode stays available", (t) => {
  const { root, user, preset } = fixture(t);
  const original = localProfile(root, user, {});
  const inherited = { ANTHEM_PROFILE: "fresh", ANTHEM_PILOT_NAME: "KingSplugorf",
    ANTHEM_SKIP_PATCHES: "1", ANTHEM_TLS_DIAGNOSTICS: "1", ANTHEM_MISSION_LIFECYCLE: "0" };
  const key = newCharacterProfile(root, user, inherited);
  assert.match(key, /^tutorial-/);
  const intro = localProfile(root, user, inherited, key);
  assert.deepEqual(JSON.parse(fs.readFileSync(intro.state)).pilotes, []);
  assert.equal(intro.env.ANTHEM_MISSION_LIFECYCLE, "1");
  assert.equal(intro.env.ANTHEM_SKIP_PATCHES, undefined);
  assert.equal(intro.env.ANTHEM_TLS_DIAGNOSTICS, undefined);
  assert.equal(intro.env.BIGS_CREATION_PERSONNAGE, undefined);
  const bytes = fs.readFileSync(intro.state, "utf8");
  localProfile(root, user, inherited, key);
  assert.equal(fs.readFileSync(intro.state, "utf8"), bytes);
  assert.equal(fs.readFileSync(original.state, "utf8"), preset);
  const hub = newCharacterProfile(root, user, inherited, "creator");
  assert.match(hub, /^creator-/);
  assert.equal(localProfile(root, user, inherited, hub).env.BIGS_CREATION_PERSONNAGE, "1");
  assert.throws(() => newCharacterProfile(root, user, inherited, "invalid"));
});

test("completed tutorial profiles resume without the direct-intro observer and Original drops inherited story flags", (t) => {
  const { root, user } = fixture(t);
  const intro = localProfile(root, user, {}, "tutorial-resume");
  const saved = JSON.parse(fs.readFileSync(intro.state));
  saved.pilotes = [{ id: 1, name: "Saved pilot", gender: "Male", appearance: "saved" }];
  saved.persistence = [1835450465, 1910188215].map((id) => ({ DefinitionId: id,
    RegisteredDefPropArray: [{ PropertyId: 1, Value: 11 }] }));
  fs.writeFileSync(intro.state, JSON.stringify(saved));
  const resumed = localProfile(root, user, {}, intro.key);
  assert.equal(resumed.env.ANTHEM_STORY_MODE, undefined);
  assert.equal(resumed.env.BIGS_NOUVELLE_PARTIE, "1");
  const original = localProfile(root, user, { ANTHEM_STORY_MODE: "1" }, "development");
  assert.equal(original.env.ANTHEM_STORY_MODE, undefined);
});

test("explicit Original selection escapes inherited fresh/creator flags and returns the old save unchanged", (t) => {
  const { root, user, preset } = fixture(t);
  const original = localProfile(root, user, {});
  const inherited = { ANTHEM_PROFILE: "fresh", ANTHEM_PROFILE_ID: "kingsplugorf", BIGS_CREATION_PERSONNAGE: "1", BIGS_NOUVELLE_PARTIE: "1", BIGS_SANS_PERSONNAGE: "1", ANTHEM_LOAD_LOCAL_CA: "1" };
  const fresh = localProfile(root, user, inherited);
  const restored = localProfile(root, user, inherited, "development");
  assert.equal(restored.state, original.state);
  assert.equal(fs.readFileSync(original.state, "utf8"), preset);
  assert.equal(JSON.parse(fs.readFileSync(fresh.state)).niveau_pilote, 1);
  assert.equal(restored.env.ANTHEM_PROFILE, "development");
  assert.equal(restored.env.ANTHEM_LOAD_LOCAL_CA, "1");
  for (const key of ["BIGS_CREATION_PERSONNAGE", "BIGS_NOUVELLE_PARTIE", "BIGS_SANS_PERSONNAGE", "BIGS_PILOTES_SAUVEGARDES"]) assert.equal(restored.env[key], undefined);
});

test("New character makes independent empty slots rather than cloning the inherited KingSplugorf preset", (t) => {
  const { root, user } = fixture(t);
  const inherited = { ANTHEM_PROFILE: "fresh", ANTHEM_PILOT_NAME: "KingSplugorf" };
  const first = newCreatorProfile(root, user, inherited);
  const second = newCreatorProfile(root, user, inherited);
  assert.notEqual(first, second);
  for (const key of [first, second]) {
    const profile = localProfile(root, user, inherited, key);
    assert.deepEqual(JSON.parse(fs.readFileSync(profile.state)).pilotes, []);
    assert.equal(profile.env.BIGS_CREATION_PERSONNAGE, "1");
    assert.equal(profile.env.BIGS_NOUVELLE_PARTIE, undefined);
    assert.equal(profile.env.JOUEUR_NOM, "Freelancer");
  }
  const entries = listProfiles(user);
  assert.ok(entries.some((entry) => entry.key === "development"));
  assert.ok(entries.some((entry) => entry.key === first));
  assert.ok(entries.some((entry) => entry.key === second));
});

test("profile listing shows saved names and levels without creating or changing files", (t) => {
  const { root, user } = fixture(t);
  localProfile(root, user, {});
  localProfile(root, user, { ANTHEM_PROFILE: "fresh", ANTHEM_PILOT_NAME: "KingSplugorf" });
  const before = fs.readFileSync(path.join(user, "server", "state.json"), "utf8");
  const entries = listProfiles(user, "creator-not-created");
  assert.match(entries.find((entry) => entry.key === "development").label, /Level 30/);
  assert.match(entries.find((entry) => entry.key === "fresh-pilot-1").label, /KingSplugorf.*Level 1/);
  assert.equal(fs.existsSync(path.join(user, "server", "profiles", "creator-not-created")), false);
  assert.equal(fs.readFileSync(path.join(user, "server", "state.json"), "utf8"), before);
  assert.equal(initialProfile({ profileKey: "creator-not-created" }, {}), "creator-not-created");
  assert.equal(initialProfile({ profileKey: "creator-not-created" }, { ANTHEM_PROFILE: "development" }), "development");
});

test("invalid input and damaged saves are rejected without replacing progress", (t) => {
  const { root, user } = fixture(t);
  for (const env of [
    { ANTHEM_PROFILE: "typo" },
    { ANTHEM_PROFILE: "fresh", ANTHEM_PROFILE_ID: "../state" },
    { ANTHEM_PROFILE: "fresh", ANTHEM_PILOT_NAME: "   " },
    { ANTHEM_PROFILE: "fresh", ANTHEM_PILOT_GENDER: "typo" },
  ]) assert.throws(() => localProfile(root, user, env));
  const env = { ANTHEM_PROFILE: "fresh" };
  const profile = localProfile(root, user, env);
  for (const bytes of ["broken JSON", "{}", '{"niveau_pilote":0,"pilotes":[]}']) {
    fs.writeFileSync(profile.state, bytes);
    assert.throws(() => localProfile(root, user, env), /profile save/);
    assert.equal(fs.readFileSync(profile.state, "utf8"), bytes);
  }
});

test("a damaged Original save is rejected rather than silently reset", (t) => {
  const { root, user } = fixture(t);
  const original = localProfile(root, user, {});
  fs.writeFileSync(original.state, "damaged original");
  assert.throws(() => localProfile(root, user, { ANTHEM_PROFILE: "fresh" }, "development"), /original save/);
  assert.equal(fs.readFileSync(original.state, "utf8"), "damaged original");
});
