// Decode only bounded lifecycle records from this launcher's own patch process.
function missionStatusStream(callback) {
  let pending = "", run;
  const phases = new Set(["attaching", "idle", "queued", "starting", "active", "returning", "failed", "stopped"]);
  return (chunk) => {
    pending += chunk;
    const lines = pending.split("\n");
    pending = lines.pop().slice(-65536);
    for (const line of lines) {
      if (!line.startsWith("MISSION_STATUS = ") || line.length > 65536) continue;
      try {
        const value = JSON.parse(line.slice(17));
        if (value.schema !== 1 || !/^[a-f0-9]{32}$/.test(value.run_id) || !phases.has(value.phase)
            || !Number.isSafeInteger(value.generation) || value.generation < 0 || typeof value.ready_for_launch !== "boolean") continue;
        if (run && run !== value.run_id) continue;
        run = value.run_id;
        callback({ phase: value.phase, generation: value.generation, ready: value.ready_for_launch,
          error: value.worker_error || value.prepare_error || null });
      } catch {}
    }
  };
}

module.exports = { missionStatusStream };
