import asyncio
import sqlite3
from unittest.mock import AsyncMock, patch

from maestro.checkpoints import RepositoryBusy
from maestro.process import Result
from maestro.security import Denied, read_env
from .helpers import EngineCase, OWNER, fixture_engine, policy


class RuntimeFixTests(EngineCase):
    async def test_installer_fixture_excludes_real_install_paths_and_private_configuration(self):
        from maestro.probes import suite_targets
        targets = suite_targets()
        installed = self.folder/'installed-crew';installed.mkdir()
        (installed/'SUITE_TEST_RESULTS.txt').write_text('Host file outside the test snapshot')
        next(t for t in targets if t['id'] == 'crew')['install'] = str(installed)
        with patch('maestro.engine.suite_targets', return_value=targets):
            engine = fixture_engine(policy(), self.folder/'isolated-state')
        try:
            self.assertEqual(engine.packages.plan('all')['files'], {})
            self.assertEqual(engine.applications.all(), [])
            self.assertEqual(engine.policy.data['secret_envs'], [])
            for key in ('backup_config', 'applications_config', 'recipes_config', 'coordination_path'):
                self.assertTrue(engine.policy.data[key].startswith(str(self.folder)+'/'))
            self.assertEqual(engine.inventory.roots, [str(self.folder)])
            self.assertEqual(engine.inventory.database_roots, [str(self.folder)])
            self.assertTrue(all(not t['database'] or t['database'] == str(engine.store.path)
                                for t in engine.store.targets()))
        finally:
            await engine.close()

    async def test_fake_probe_runner_also_covers_inventory_commands(self):
        real_runner = self.engine.runner
        real_runner.run = AsyncMock(side_effect=AssertionError('Host command escaped fixture'))
        fake = self.use_fake_probes()
        await self.engine.inventory.collect()
        real_runner.run.assert_not_awaited()
        self.assertIn(['systemctl', 'list-units', '--all', '--type=service', '--no-pager', '--no-legend', '--plain'], fake.calls)

    async def test_unchanged_watch_history_is_compacted_but_transitions_and_hourly_reports_remain(self):
        report={'issues':[],'fields':{'ActiveState':'active','MainPID':'42'},'checks':{}}
        for _ in range(50):self.engine.record_snapshot('watch','gate',report)
        self.assertEqual(len(self.engine.store.rows('SELECT * FROM snapshots')),1)
        self.engine.record_snapshot('watch','gate',{**report,'issues':[{'code':'unit_down','detail':'failed'}]})
        self.engine.record_snapshot('hourly','gate',report)
        self.assertEqual(len(self.engine.store.rows('SELECT * FROM snapshots')),3)

    async def test_environment_reader_accepts_real_service_keys_without_executing_values(self):
        path=self.folder/'system.env';path.write_text('; comment\nnetworkd_dispatcher_args = "--run-startup-triggers"\nVALUE=$(touch /tmp/never-execute-this)\n')
        values=read_env(path)
        self.assertEqual(values['networkd_dispatcher_args'],'--run-startup-triggers')
        self.assertEqual(values['VALUE'],'$(touch /tmp/never-execute-this)')
        path.write_text('# header\nMALFORMED_WITHOUT_VALUE\n')
        with self.assertRaisesRegex(Denied,':2'):read_env(path)

    async def test_timeout_is_an_inconclusive_database_check_not_corruption(self):
        self.use_fake_probes()
        target=self.engine.store.target('gate');target['database']='/fixture/gate.sqlite3'
        with patch('maestro.probes.database_check',side_effect=sqlite3.OperationalError('interrupted')):
            report=await self.engine.probes.service(target,daily=True)
        codes={row['code'] for row in report['issues']}
        self.assertIn('database_review',codes);self.assertNotIn('database_failed',codes)
        self.assertTrue(report['checks']['database']['inconclusive'])
        self.assertFalse(report['ok'])

    async def test_real_database_error_is_not_downgraded(self):
        self.use_fake_probes()
        target=self.engine.store.target('gate');target['database']='/fixture/gate.sqlite3'
        with patch('maestro.probes.database_check',side_effect=sqlite3.DatabaseError('database disk image is malformed')):
            report=await self.engine.probes.service(target,daily=True)
        self.assertIn('database_failed',{row['code'] for row in report['issues']})

    async def test_successful_restart_command_does_not_claim_unhealthy_bot_completed(self):
        self.engine.runner.run=AsyncMock(return_value=Result(0,'restart accepted'))
        self.engine.outputs.finalize=lambda *a,**k:{}
        self.engine.probes.service=AsyncMock(return_value={'ok':False,'issues':[{'code':'endpoint_unhealthy','detail':'Not ready'}]})
        job=await self.execute(kind='service',unit='ripcars-gate.service',action='restart')
        self.assertEqual(job['state'],'failed');self.assertEqual(job['exit_code'],1)
        self.assertFalse(job['operation']['data']['verification']['ok'])
        self.assertTrue(self.engine.probes.service.call_args.args[0]['expected_running'])

    async def test_external_repository_lock_is_deferred_without_claiming_verification(self):
        with patch.object(self.engine.checkpoints,'process_lock',side_effect=RepositoryBusy('Busy')):
            result=await self.engine.checkpoints.inspect_repository()
        self.assertTrue(result['deferred'])
        self.assertIn('not been checked',result['detail'])

    async def test_read_only_repository_diagnostic_never_runs_a_mutating_job(self):
        self.engine.checkpoints.inspect_repository=AsyncMock(return_value={'ok':False,'detail':'pack checksum mismatch'})
        result=await self.engine.dispatch(OWNER,'backup_check',{})
        self.assertFalse(result['ok']);self.assertEqual(self.engine.jobs,{})
        self.engine.checkpoints.inspect_repository.assert_awaited_once_with(read_data=False)
