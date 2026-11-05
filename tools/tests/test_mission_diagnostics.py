import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'windows'))
import inspect_mission


class MissionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'data/logs').mkdir(parents=True)

    def api(self, route):
        if route.endswith('/active'):
            return {'characterId': '1', 'character': {'name': 'KingSplugorf', 'gender': 'Male', 'level': 1, 'xp': 0, 'appearanceData': 'not exported'}}
        return {'persistenceJsonData': json.dumps([
            {'DefinitionId': inspect_mission.FREEPLAY_DEFINITION, 'RegisteredDefPropArray': [{'PropertyId': 1, 'Value': 5}]},
            {'DefinitionId': inspect_mission.FREEPLAY_GATE_DEFINITION, 'RegisteredDefPropArray': [{'PropertyId': inspect_mission.FREEPLAY_GATE_PROPERTY, 'Value': True}]},
        ])}

    def write(self, relative, text):
        (self.root / relative).write_text(text, encoding='utf-8')

    def test_selection_and_ready_do_not_count_as_a_launch_request(self):
        self.write('data/logs/blaze.log', 'ATTR = {"QuestId": "1401555469"}\nATTR = {"Ready": "true"}\n')
        report = inspect_mission.collect(self.root, 123, self.api)
        self.assertEqual(report['observed_stage'], 'no_launch_attributes_in_blaze_tail')
        self.assertIsNone(report['blaze']['parsed_launch_attributes'])
        self.assertEqual(len(report['blaze']['recent_selection_updates']), 2)
        self.assertEqual(report['active_character']['name'], 'KingSplugorf')
        self.assertNotIn('appearanceData', report['active_character'])
        self.assertEqual(report['persistence']['freeplay_quest_state'], 5)
        self.assertIs(report['persistence']['freeplay_eligibility_flag'], True)

    def test_reports_request_hook_and_startup_stages_and_current_pid_points(self):
        self.write('data/logs/blaze.log', 'SCNA = {"playMode": générique(2), "questId": générique(1401555469), "desiredDifficulty": générique(1), "startPoint": générique(0)}\n')
        self.write('data/quest_points_123.json', json.dumps({'composants': [4096], 'quetes': {'1401555469': {'point': 'Freeplay', 'chaine': 8192}}}))
        report = inspect_mission.collect(self.root, 123, self.api)
        self.assertEqual(report['observed_stage'], 'launch_attributes_seen_no_connection_hook_seen')
        self.assertEqual(report['blaze']['parsed_launch_attributes']['questId'], 1401555469)
        self.assertEqual(report['quest_points']['point']['point'], 'Freeplay')
        self.write('data/active_mission_123.txt', '00:00:01 lancement de mission : GameStartingInfo à poser (2, 1401555469, 1, 0)\n')
        self.assertEqual(inspect_mission.collect(self.root, 123, self.api)['observed_stage'], 'connection_hook_reached_no_startup_8_seen')
        with (self.root / 'data/active_mission_123.txt').open('a', encoding='utf-8') as output:
            output.write('00:00:02 état de démarrage 8 : ready\n')
        self.assertEqual(inspect_mission.collect(self.root, 123, self.api)['observed_stage'], 'internal_server_started_once')

    def test_missing_logs_and_service_failures_produce_report_without_writes(self):
        before = set(self.root.rglob('*'))
        def offline(route):
            raise OSError('connection refused')
        report = inspect_mission.collect(self.root, 123, offline)
        self.assertIn('connection refused', report['active_character']['error'])
        self.assertFalse(report['mission_hook']['exists'])
        self.assertEqual(set(self.root.rglob('*')), before)

    def test_large_logs_are_bounded_and_http_bodies_are_not_exported(self):
        self.write('data/logs/bigs.log', 'response: private body\nGET /api/playercard/ -> 200\n')
        self.write('data/logs/blaze.log', 'old line\n' * 100 + 'ATTR = {"Ready": "true"}\n')
        with patch.object(inspect_mission, 'TAIL_BYTES', 100):
            report = inspect_mission.collect(self.root, 123, self.api)
        self.assertTrue(report['blaze']['tail_truncated'])
        self.assertEqual(report['bigs']['recent_http_requests'], ['GET /api/playercard/ -> 200'])
        self.assertNotIn('private body', json.dumps(report))


if __name__ == '__main__':
    unittest.main()
