const fs = require("node:fs");
const path = require("node:path");

// Separate saves allow a fresh pilot to be tested without resetting the
// development profile. Selecting an existing slot always resumes that slot.
function localProfile(root, userData, inherited) {
  const mode = inherited.ANTHEM_PROFILE || "development";
  if (!["development", "fresh", "tutorial"].includes(mode)) {
    throw new Error("ANTHEM_PROFILE must be development, fresh, or tutorial");
  }
  const env = { ...inherited };
  let runtime = path.join(userData, "server");
  let initial;
  if (mode !== "development") {
    const id = inherited.ANTHEM_PROFILE_ID || "pilot-1";
    if (!/^[a-z0-9][a-z0-9_-]{0,47}$/i.test(id)) {
      throw new Error("ANTHEM_PROFILE_ID must contain 1–48 letters, digits, underscores or hyphens, starting with a letter or digit");
    }
    const name = (inherited.ANTHEM_PILOT_NAME || "Freelancer").trim();
    if (!name || name.length > 64 || /[\x00-\x1f\x7f]/.test(name)) {
      throw new Error("ANTHEM_PILOT_NAME must contain 1–64 characters without control characters");
    }
    const gender = inherited.ANTHEM_PILOT_GENDER || "Male";
    if (!["Male", "Female"].includes(gender)) throw new Error("ANTHEM_PILOT_GENDER must be Male or Female");
    runtime = path.join(runtime, "profiles", `${mode}-${id}`);
    initial = {
      niveau_pilote: 1,
      inventaire_depart_seul: true,
      pilotes: mode === "tutorial" ? [] : [{ id: 1, name, gender, appearance: "" }],
    };
    // These inherited development overrides would defeat a fresh start.
    for (const key of ["BIGS_NOUVELLE_PARTIE", "BIGS_SANS_PERSONNAGE", "BIGS_PERSISTANCE", "BIGS_PERSONNAGE_ID"]) delete env[key];
    if (mode === "tutorial") env.BIGS_NOUVELLE_PARTIE = "1";
    else env.BIGS_PILOTES_SAUVEGARDES = "1";
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
  return {
    mode, state,
    env: { ...env, BIGS_ETAT: state, BIGS_SAUVEGARDES: path.join(runtime, "saves"), BLAZE_CAPTURES: path.join(runtime, "captures") },
  };
}

module.exports = { localProfile };
