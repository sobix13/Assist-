import unittest

from maestro.notifications import daily_only, incident_notifications, notification


class RealWorldNotificationTests(unittest.TestCase):
    def incident(self, target, code, detail):
        return {'key': target + ':' + code, 'target': target, 'code': code, 'detail': detail}

    def apply(self, snapshot, history, stamp):
        plans = incident_notifications(snapshot, history, stamp)
        for _, key, record in plans:
            history[key] = record
        return plans

    def test_user_reported_hourly_examples_do_not_repeat_over_two_days(self):
        rows = [self.incident('s-user-runtime-dir-1-0b5ace4c', 'unit_down', 'Expected service is not active: inactive'),
                self.incident('s-user-1000-3b1335a4', 'unit_down', 'Expected service is not active: inactive'),
                self.incident('melee-zone', 'endpoint_unhealthy', 'ClientConnectorError'),
                self.incident('futarchist-web', 'backup_failed', 'No backup found'),
                self.incident('futarchist-bot', 'backup_failed', 'No backup found'),
                self.incident('backups', 'checkpoint_failed', 'Denied'),
                self.incident('maestro', 'database_failed', 'OperationalError: interrupted'),
                self.incident('assistant', 'scheduler', 'Denied: A monitoring cycle is already running.')]
        history = {}
        messages = []
        for minute in range(2 * 24 * 60):
            messages += self.apply({'open_incidents': rows}, history, 100000 + minute * 60)
        self.assertEqual(len(messages), 2)
        self.assertEqual({key for _, key, _ in messages}, {'melee-zone:endpoint_unhealthy', 'backups:checkpoint_failed'})

    def test_v2_history_migrates_without_renotifying_persistent_issues(self):
        row = self.incident('backups', 'checkpoint_failed', 'Denied')
        history = {row['key']: {'last_alert': 10000, 'announced_open': True, 'closed_since': None}}
        self.assertEqual(self.apply({'open_incidents': [row]}, history, 200000), [])

    def test_brief_workers_do_not_alert_but_sustained_workers_do(self):
        row = self.incident('futarchist-bot', 'worker_unhealthy', 'Unhealthy workers: poller')
        history = {}
        for stamp in (10000, 10100, 10200):
            self.assertEqual(self.apply({'open_incidents': [row]}, history, stamp), [])
        self.assertEqual(self.apply({'open_incidents': []}, history, 10240), [])
        self.assertEqual(self.apply({'open_incidents': [row]}, history, 10300), [])
        self.assertEqual(self.apply({'open_incidents': [row]}, history, 10899), [])
        plans = self.apply({'open_incidents': [row]}, history, 10900)
        self.assertEqual(len(plans), 1)
        self.assertIn('ALERT', plans[0][0])

    def test_hourly_short_worker_oscillation_does_not_make_a_notification_storm(self):
        row = self.incident('futarchist-bot', 'worker_unhealthy', 'Unhealthy workers: poller')
        history, messages = {}, []
        for minute in range(24 * 60):
            active = [row] if minute % 60 < 5 else []
            messages += self.apply({'open_incidents': active}, history, 100000 + minute * 60)
        self.assertEqual(messages, [])

    def test_new_worker_episode_uses_new_first_seen(self):
        row = self.incident('worker', 'worker_unhealthy', 'degraded')
        history = {}
        self.assertEqual(self.apply({'open_incidents': [{**row, 'first_seen': 10000}]}, history, 10000), [])
        self.assertEqual(self.apply({'open_incidents': [{**row, 'first_seen': 10500}]}, history, 10600), [])
        self.assertEqual(len(self.apply({'open_incidents': [{**row, 'first_seen': 10500}]}, history, 11100)), 1)

    def test_actual_database_corruption_escalates_after_inconclusive_probe(self):
        history = {}
        old = self.incident('maestro', 'database_failed', 'OperationalError: interrupted')
        self.assertEqual(self.apply({'open_incidents': [old]}, history, 10000), [])
        bad = self.incident('maestro', 'database_failed', 'DatabaseError: database disk image is malformed')
        self.assertEqual(len(self.apply({'open_incidents': [bad]}, history, 10030)), 1)

    def test_real_down_daemon_and_central_backup_are_never_filtered_as_reviews(self):
        for row in [self.incident('gate', 'unit_down', 'Expected service is not active: failed'),
                    self.incident('backups', 'checkpoint_failed', 'Denied'),
                    self.incident('assistant', 'scheduler', 'Unexpected failure'),
                    self.incident('host', 'resource_pressure', 'Disk critically low')]:
            self.assertFalse(daily_only(row))
            self.assertEqual(len(self.apply({'open_incidents': [row]}, {}, 10000)), 1)

    def test_known_backup_event_duplicate_and_manual_coordination_only_appear_in_digest(self):
        for kind, payload in [('checkpoint_review', {'ok': False, 'detail': 'Denied'}),
                              ('coordination_review', {'issues': ['role manual']})]:
            self.assertIsNone(notification({'kind': kind, 'payload': payload}, {}))
        report = {'kind': 'daily', 'targets': [{'id': 'maestro', 'ok': False,
                  'issues': [{'code': 'database_failed', 'detail': 'OperationalError: interrupted'}]}],
                  'backup_repository': {'ok': False, 'detail': 'Denied'},
                  'coordination': {'issues': ['role manual']}}
        message, _, _ = notification({'kind': 'scheduled_report', 'payload': report, 'at': 100000}, {})
        self.assertIn('interrupted', message)
        self.assertIn('Denied', message)
        self.assertIn('Coordination reviews: 1', message)

    def test_changing_checkpoint_ids_do_not_evade_event_deduplication(self):
        values = [notification({'kind': 'recovery', 'payload': {'target': 'worker', 'action': 'blocked',
                  'checkpoint': stamp, 'at': stamp}}, {}) for stamp in (10000, 20000)]
        self.assertEqual(values[0][1], values[1][1])
        self.assertEqual(values[0][2], 86400)

    def test_interrupted_checkpoint_with_paused_writers_still_alerts(self):
        row = {'kind': 'checkpoint_review', 'payload': {'checkpoint': 'abc', 'stopped_units': ['worker.service']}}
        self.assertIsNotNone(notification(row, {}))
