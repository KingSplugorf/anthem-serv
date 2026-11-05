const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

const PROFILE_MODES = ["development", "fresh", "creator", "tutorial"];
const PROFILE_FLAGS = ["BIGS_NOUVELLE_PARTIE", "BIGS_CREATION_PERSONNAGE", "BIGS_PILOTES_SAUVEGARDES", "BIGS_SANS_PERSONNAGE", "BIGS_PERSISTANCE", "BIGS_PERSONNAGE_ID"];

function decodeProfile(key) {
  if (key === "development") return { mode: "development" };
  const match = /^(fresh|creator|tutorial)-([a-z0-9][a-z0-9_-]{0,47})$/i.exec(key);
  if (!match) throw new Error("Invalid profile selection");
  return { mode: match[1].toLowerCase(), id: match[2] };
}

function initialProfile(config, inherited) {
  if (!inherited.ANTHEM_PROFILE) return config.profileKey || "development";
  const mode = inherited.ANTHEM_PROFILE;
  if (!PROFILE_MODES.includes(mode)) throw new Error("Unknown ANTHEM_PROFILE");
  const key = mode === "development" ? mode : `${mode}-${inherited.ANTHEM_PROFILE_ID || "pilot-1"}`;
  decodeProfile(key);
  return key;
}

function profileEntry(userData, key) {
  const { mode } = decodeProfile(key);
  const directory = mode === "development" ? path.join(userData, "server") : path.join(userData, "server", "profiles", key);
  let saved;
  try { saved = JSON.parse(fs.readFileSync(path.join(directory, "state.json"), "utf8")); } catch {}
  const name = saved?.pilotes?.[0]?.name;
  const level = saved?.niveau_pilote;
  const title = mode === "development" ? "Original save" : name || (mode === "creator" ? "New character" : mode === "tutorial" ? "Intro profile" : "Fresh profile");
  return { key, label: `${title}${Number.isInteger(level) ? ` — Level ${level}` : ""}${mode === "development" ? "" : ` (${key})`}` };
}

function listProfiles(userData, selected = "development") {
  decodeProfile(selected);
  const keys = new Set(["development", selected]);
  try {
    for (const entry of fs.readdirSync(path.join(userData, "server", "profiles"), { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      try { decodeProfile(entry.name); keys.add(entry.name); } catch {}
    }
  } catch (error) { if (error.code !== "ENOENT") throw error; }
  return [...keys].map((key) => profileEntry(userData, key));
}

// Separate saves allow a fresh pilot to be tested without resetting the
// development profile. Selecting an existing slot always resumes that slot.
function localProfile(root, userData, inherited, selection) {
  const key = selection === undefined ? initialProfile({}, inherited) : selection;
  const { mode, id } = decodeProfile(key);
  const env = { ...inherited };
  if (mode !== "development" || selection !== undefined || inherited.ANTHEM_PROFILE === "development") {
    for (const flag of PROFILE_FLAGS) delete env[flag];
  }
  env.ANTHEM_PROFILE = mode;
  let runtime = path.join(userData, "server");
  let initial;
  if (mode !== "development") {
    const name = (mode === "fresh" ? inherited.ANTHEM_PILOT_NAME || "Freelancer" : "Freelancer").trim();
    if (!name || name.length > 64 || /[\x00-\x1f\x7f]/.test(name)) {
      throw new Error("ANTHEM_PILOT_NAME must contain 1–64 characters without control characters");
    }
    const gender = mode === "fresh" ? inherited.ANTHEM_PILOT_GENDER || "Male" : "Male";
    if (!["Male", "Female"].includes(gender)) throw new Error("ANTHEM_PILOT_GENDER must be Male or Female");
    runtime = path.join(runtime, "profiles", `${mode}-${id}`);
    initial = {
      niveau_pilote: 1,
      inventaire_depart_seul: true,
      pilotes: mode === "fresh" ? [{ id: 1, name, gender, appearance: "" }] : [],
    };
    if (mode === "tutorial") env.BIGS_NOUVELLE_PARTIE = "1";
    else if (mode === "creator") env.BIGS_CREATION_PERSONNAGE = "1";
    else env.BIGS_PILOTES_SAUVEGARDES = "1";
    env.ANTHEM_PROFILE_ID = id;
    env.JOUEUR_NOM = name;
  }
  fs.mkdirSync(runtime, { recursive: true });
  const state = path.join(runtime, "state.json");
  if (initial) {
    try { fs.writeFileSync(state, `${JSON.stringify(initial, null, 2)}\n`, { flag: "wx" }); }
    catch (error) { if (error.code !== "EEXIST") throw error; }
    // Never silently replace a damaged save with a new pilot.
    let saved;
    try { saved = JSON.parse(fs.readFileSync(state, "utf8")); }
    catch { throw new Error(`Could not read profile save: ${state}. Preserve it and select a different ANTHEM_PROFILE_ID.`); }
    if (!saved || !Array.isArray(saved.pilotes) || !Number.isInteger(saved.niveau_pilote) || saved.niveau_pilote < 1) {
      throw new Error(`Invalid profile save: ${state}. Preserve it and select a different ANTHEM_PROFILE_ID.`);
    }
    if (saved.pilotes[0]?.name) env.JOUEUR_NOM = saved.pilotes[0].name;
  } else if (!fs.existsSync(state)) {
    fs.copyFileSync(path.join(root, "data", "saves", "state.json"), state, fs.constants.COPYFILE_EXCL);
  }
  if (!initial) {
    // BIGS otherwise treats malformed JSON as an empty save. Refuse startup
    // instead of allowing a later request to overwrite the original progress.
    let saved;
    try { saved = JSON.parse(fs.readFileSync(state, "utf8")); }
    catch { throw new Error(`Could not read original save: ${state}. Preserve it before attempting recovery.`); }
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) throw new Error(`Invalid original save: ${state}`);
  }
  return {
    mode, key, state,
    env: { ...env, BIGS_ETAT: state, BIGS_SAUVEGARDES: path.join(runtime, "saves"), BLAZE_CAPTURES: path.join(runtime, "captures") },
  };
}

function newCreatorProfile(root, userData, inherited) {
  const key = `creator-${randomUUID()}`;
  localProfile(root, userData, inherited, key);
  return key;
}

module.exports = { localProfile, initialProfile, listProfiles, newCreatorProfile };
