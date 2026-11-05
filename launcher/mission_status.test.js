const test = require("node:test");
const assert = require("node:assert/strict");
const { missionStatusStream } = require("./mission_status");

test("lifecycle records survive arbitrary stream chunk boundaries", () => {
  const states = [], read = missionStatusStream((state) => states.push(state));
  const line = "MISSION_STATUS = " + JSON.stringify({ schema:1, run_id:"1".repeat(32), phase:"active", generation:2, ready_for_launch:false }) + "\n";
  for (let index=0; index<line.length; index+=7) read(line.slice(index,index+7));
  assert.deepEqual(states,[{ phase:"active", generation:2, ready:false, error:null }]);
});

test("malformed, oversized and foreign-run records cannot replace current expedition status", () => {
  const states=[], read=missionStatusStream((state)=>states.push(state));
  const record=(values)=>"MISSION_STATUS = "+JSON.stringify({schema:1,run_id:"1".repeat(32),phase:"idle",generation:0,ready_for_launch:true,...values})+"\n";
  read("ordinary output\nMISSION_STATUS = broken\n"+record({generation:-1})+record({phase:"invented"}));
  read(record({})); read(record({run_id:"2".repeat(32)}));
  read(record({reason:"x".repeat(70000)}));
  assert.equal(states.length,1);
});
