import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mission_hooks import STARTING_INFO_POINTER, STARTUP_STATE_ENTRY, MISSION_CONNECT_STATE
from mission_lifecycle import RepeatingMissionHooks, NATIVE_INTRO_QUESTS, PLAYER_SLOTS, LEAVE_SUCCESS, LEAVE_CODE
from end_expedition import STORY_SUCCESS_CALLBACK, STORY_SUCCESS_CODE
from windows_debugger import mission_breakpoints
import test_mission_lifecycle as fixtures
import test_mission_supervisor as supervisor_fixtures


class NativeIntroductionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.data=Path(temporary.name);(self.data/'logs').mkdir();(self.data/'logs/blaze.log').write_text('')
        self.memory=fixtures.Memory()
        self.memory.put(PLAYER_SLOTS,bytes(24));self.memory.put(0x14096B9A0,bytes(5))
        self.memory.put(STARTING_INFO_POINTER,struct.pack('<Q',0x20000));self.memory.put(0x20000,bytes(16))
        self.memory.put(LEAVE_SUCCESS,LEAVE_CODE);self.memory.put(STORY_SUCCESS_CALLBACK,STORY_SUCCESS_CODE)
        self.hooks=RepeatingMissionHooks(self.memory,123,fixtures.RUN,self.data,native_story=True)
        self.addCleanup(self.hooks.close);self.hooks.mark_ready();self.memory.writes.clear()
        self.quests=list(NATIVE_INTRO_QUESTS)

    def startup(self,quest,state=8,address=0x80000):
        self.memory.put(address+0x18,struct.pack('<IIII',0,quest,1,77))
        self.memory.put(address+0x31,b'\0')
        self.hooks.startup({'rcx':address,'rdx':state})

    def test_direct_tutorial_owns_combat_lease_without_replacing_level_checkpoint_or_starting_info(self):
        self.assertEqual(mission_breakpoints(self.hooks),((MISSION_CONNECT_STATE,STARTUP_STATE_ENTRY),None))
        self.startup(self.quests[0])
        self.assertEqual(self.hooks.phase,'active');self.assertTrue(self.hooks.lease.active(1))
        self.assertEqual(self.hooks.current['origin'],'native-story-direct')
        self.assertIsNone(self.hooks.launched);self.assertIsNone(self.hooks.points)
        self.assertEqual(self.memory.writes,[])
        self.assertEqual(self.memory.read(0x20000,16),bytes(16))
        self.assertEqual(self.memory.read(0x80031,1),b'\0')
        self.assertEqual(mission_breakpoints(self.hooks),((MISSION_CONNECT_STATE,STARTUP_STATE_ENTRY,LEAVE_SUCCESS,STORY_SUCCESS_CALLBACK),None))

    def test_cutscene_or_level_transfer_revokes_old_worker_before_next_tutorial(self):
        self.startup(self.quests[0]);self.assertTrue(self.hooks.lease.active(1))
        self.startup(self.quests[0],state=0)
        self.assertFalse(self.hooks.lease.active(1));self.assertEqual(self.hooks.phase,'returning')
        self.startup(self.quests[1],address=0x90000)
        self.assertTrue(self.hooks.lease.active(2));self.assertFalse(self.hooks.lease.active(1))
        self.startup(self.quests[1],address=0x90000)
        self.assertEqual(self.hooks.generation,2)

    def test_unknown_quest_or_unfinished_startup_never_enables_combat(self):
        self.startup(0);self.startup(self.quests[0],state=4)
        self.assertEqual(self.hooks.generation,0);self.assertEqual(self.memory.writes,[])
        self.assertFalse(self.hooks.lease.active(1))
        snapshot=json.loads((self.data/'native_story_123.json').read_text())
        self.assertEqual(snapshot['state'],4)

    def test_direct_start_does_not_steal_a_real_pending_matchmaking_request(self):
        self.hooks.pending={'game_id':2,'server_run_id':'real','attributes':[1,1401555469,1,0]}
        self.startup(self.quests[0])
        self.assertEqual(self.hooks.pending['game_id'],2);self.assertEqual(self.hooks.generation,0)

    def test_tutorial_retry_after_leave_uses_fresh_lease_even_with_reused_startup_address(self):
        self.startup(self.quests[0]);self.hooks.leave({})
        self.startup(self.quests[0]);self.assertEqual(self.hooks.generation,1)
        self.startup(self.quests[0],state=0);self.startup(self.quests[0])
        self.assertEqual(self.hooks.generation,2);self.assertTrue(self.hooks.lease.active(2))

    def test_real_tutorial_matchmaking_retains_native_level_and_needs_no_hub_point_metadata(self):
        record={'schema':1,'server_run_id':'server','event':'requested','persona_id':1000,
                'game_id':2,'attributes':[0,self.quests[0],1,77]}
        with (self.data/'logs/blaze.log').open('a') as output:output.write('MISSION_EVENT = '+json.dumps(record)+'\n')
        self.memory.put(0x70050,struct.pack('<Q',2))
        self.hooks.connect({'rcx':0x30000,'rsi':0x70000,'rdx':5})
        self.assertEqual(self.hooks.phase,'starting');self.assertIsNone(self.hooks.points)
        self.assertEqual([address for address,_ in self.memory.writes],[0x20000])
        self.startup(self.quests[0]);self.assertEqual(self.hooks.phase,'active')
        self.startup(self.quests[0],state=0);self.assertFalse(self.hooks.lease.active(1))

    def test_opt_in_preserves_observed_tutorial_mode_without_changing_request(self):
        self.test_real_tutorial_matchmaking_retains_native_level_and_needs_no_hub_point_metadata()
        # A separate matched startup exercises the gate used in the reported hang.
        self.hooks.current={'origin':'native-story-matchmaking','attributes':[1,self.quests[0],0,0]}
        self.hooks.launched=(1,self.quests[0],0,0)
        self.memory.put(0x20000,struct.pack('<IIII',3,self.quests[0],0,0))
        self.hooks.native_playmode=True
        self.hooks.gate({})
        self.assertEqual(self.hooks.launched,(3,self.quests[0],0,0))
        self.assertEqual(self.hooks.current['attributes'][0],1)
        self.assertEqual(self.hooks.current['startup_attributes'][0],3)
        self.hooks.launched=(1,self.quests[0],0,0)
        self.hooks.current['origin']='normal'
        self.hooks.adopt_native_playmode((3,self.quests[0],0,0))
        self.assertEqual(self.hooks.launched[0],1)


class NativeLaunchPreparationTests(unittest.TestCase):
    def test_story_attaches_before_heap_scans_but_normal_launch_retains_them(self):
        import auto_patches
        normal=auto_patches.launch_steps({})
        story=auto_patches.launch_steps({'ANTHEM_STORY_MODE':'1'})
        self.assertEqual(story,normal[:3])
        self.assertEqual(normal[3:],[('missions_inproc.py',),('default_point.py','--quetes')])


class NativePreparationTests(unittest.TestCase):
    setUp = supervisor_fixtures.SupervisorTests.setUp
    state = supervisor_fixtures.SupervisorTests.state
    def test_initial_preparation_runs_before_matchmaking_and_can_finish_during_native_intro(self):
        self.supervisor.prepare_initial=True;self.supervisor.prepared_generation=-1
        self.state('idle',0);self.assertEqual(len(self.preparers),1)
        self.owner.activate(1)
        (self.data/'mission_state_123.json').write_text(json.dumps({'run_id':fixtures.RUN,'phase':'active','generation':1,
            'mission':{'origin':'native-story-direct'},'reason':'native'}))
        self.supervisor.step();self.assertNotIn(200,self.stopped)
        self.preparers[0].returncode=0;self.supervisor.step()
        self.assertEqual(self.supervisor.prepared_generation,0)
        self.assertEqual(self.supervisor.worker.pid,101)

    def test_late_initial_preparation_still_has_generation_zero_and_refreshes_after_intro(self):
        self.supervisor.prepare_initial=True;self.supervisor.prepared_generation=-1
        self.owner.activate(1)
        (self.data/'mission_state_123.json').write_text(json.dumps({'run_id':fixtures.RUN,'phase':'active','generation':1,
            'mission':{'origin':'native-story-direct'},'reason':'native'}))
        self.supervisor.step();self.assertEqual(self.preparers[0].pid,200)
        self.preparers[0].returncode=0;self.supervisor.step()
        self.assertEqual(self.supervisor.prepared_generation,0)
        self.state('returning',1);self.assertEqual(self.preparers[-1].pid,201)
