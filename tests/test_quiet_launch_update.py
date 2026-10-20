import sqlite3
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from maestro.store import Store
from scripts import update_quiet_launch as update


class QuietUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'run/lock').mkdir(parents=True)
        self.sources={k:self.root/'sources'/k for k in update.VERSIONS}
        self.previous={}
        for key in update.VERSIONS:
            base=self.root/'opt'/('maestro' if key=='maestro' else 'ripcars-'+key)
            old=base/'releases/old';old.mkdir(parents=True);(old/'marker').write_text(key)
            (base/'current').symlink_to(old,target_is_directory=True);self.previous[key]=old
        for key in ('gate','crew','verifier'):
            db=self.root/('var/lib/ripcars-'+key+'/'+key+'.sqlite3');db.parent.mkdir(parents=True)
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE retained(value TEXT)');c.execute("INSERT INTO retained VALUES('keep data')")
            env=self.root/('etc/ripcars-'+key+'.env');env.parent.mkdir(exist_ok=True)
            env.write_text(('DATABASE_PATH' if key=='verifier' else 'DB_PATH')+'=/var/lib/ripcars-'+key+'/'+key+'.sqlite3\nDISCORD_TOKEN=retained-secret\n')
        self.state=self.root/'var/lib/maestro-agent/maestro.sqlite3'
        store=Store(self.state,recover=False)
        self.target={'id':'verifier','unit':update.VERIFIER,'enabled':True,'expected_running':True,'auto_restart':True}
        store.save_target(self.target);store.save_target({'id':'raffle','unit':'ripcars-raffle.service','enabled':True});store.close()
        self.active=set(update.UNITS)|{update.VERIFIER};self.enabled=True;self.calls=[]

    def command(self,*args,check=True):
        self.calls.append(args)
        value='';code=0
        if args[:2]==('systemctl','is-active'):code=int(args[-1] not in self.active)
        elif args[:2]==('systemctl','is-enabled'):value='enabled' if self.enabled else 'disabled'
        elif args[:2]==('systemctl','show'):value='root'
        elif args[:2]==('systemctl','stop'):self.active.difference_update(args[2:])
        elif args[:2]==('systemctl','start'):self.active.update(args[2:])
        elif args[:2]==('systemctl','disable'):self.enabled=False
        elif args[:2]==('systemctl','enable'):self.enabled=True
        if code and check:raise subprocess.CalledProcessError(code,args)
        return subprocess.CompletedProcess(args,code,value)

    def stage(self,source,current,destination,key):
        destination.mkdir();(destination/'marker').write_text('new '+key);return destination

    def run_update(self,checkpoint_error=None,command=None,stage=None):
        with patch.object(update,'validate_sources'),patch.object(update,'stage',side_effect=stage or self.stage),patch.object(update,'command',side_effect=command or self.command),patch.object(update.subprocess,'run',side_effect=checkpoint_error):
            update.update(self.sources,self.root)

    def test_success_retires_only_verifier_and_preserves_all_application_data(self):
        self.run_update()
        self.assertNotIn(update.VERIFIER,self.active);self.assertFalse(self.enabled)
        self.assertTrue(set(update.UNITS)<=self.active)
        store=Store(self.state,recover=False)
        self.assertFalse(store.target('verifier')['enabled']);self.assertFalse(store.target('verifier')['auto_restart'])
        self.assertTrue(store.target('raffle')['enabled']);store.close()
        self.assertFalse(any('ripcars-raffle.service' in c for c in self.calls))
        for key in ('gate','crew','verifier'):
            db=self.root/('var/lib/ripcars-'+key+'/'+key+'.sqlite3')
            with sqlite3.connect(db) as c:self.assertEqual(c.execute('SELECT value FROM retained').fetchone()[0],'keep data')
            self.assertIn('retained-secret',(self.root/('etc/ripcars-'+key+'.env')).read_text())
        backups=list((self.root/'var/backups').iterdir());self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].stat().st_mode & 0o777,0o700)
        self.assertTrue((backups[0]/'maestro.sqlite3').is_file())

    def test_failed_start_restores_code_targets_and_service_state(self):
        failed=False
        def command(*args,**kwargs):
            nonlocal failed
            if args[:2]==('systemctl','start') and len(args)>3 and not failed:
                failed=True;raise subprocess.CalledProcessError(1,args)
            return self.command(*args,**kwargs)
        with self.assertRaises(subprocess.CalledProcessError):self.run_update(command=command)
        for key,old in self.previous.items():self.assertEqual((old.parent.parent/'current').resolve(),old)
        store=Store(self.state,recover=False);self.assertEqual(store.target('verifier'),self.target);store.close()
        self.assertEqual(self.active,set(update.UNITS)|{update.VERIFIER});self.assertTrue(self.enabled)

    def test_failed_checkpoint_never_stops_companion_services(self):
        with self.assertRaises(RuntimeError):self.run_update(checkpoint_error=RuntimeError('backup failed'))
        self.assertEqual(self.active,set(update.UNITS)|{update.VERIFIER});self.assertTrue(self.enabled)
        self.assertFalse(any(c[:2]==('systemctl','disable') for c in self.calls))
        for key,old in self.previous.items():self.assertEqual((old.parent.parent/'current').resolve(),old)

    def test_failed_staging_never_stops_any_service(self):
        def fail(*args):raise ValueError('test failed')
        with self.assertRaises(ValueError):self.run_update(stage=fail)
        self.assertFalse(any(c[:2]==('systemctl','stop') for c in self.calls))

    def test_partial_frontend_stop_restores_running_interfaces(self):
        failed=False
        def command(*args,**kwargs):
            nonlocal failed
            if args[:2]==('systemctl','stop') and not failed:
                failed=True;self.active.discard(args[2]);raise subprocess.CalledProcessError(1,args)
            return self.command(*args,**kwargs)
        with self.assertRaises(subprocess.CalledProcessError):self.run_update(command=command)
        self.assertEqual(self.active,set(update.UNITS)|{update.VERIFIER})

    def test_stage_reuses_runtime_and_excludes_private_state(self):
        source=self.root/'input';source.mkdir()
        current=self.previous['crew']
        for folder in (source,current):(folder/'requirements-lock.txt').write_text('unchanged')
        (source/'VERSION').write_text('1.1.0')
        (source/'.env').write_text('private')
        (source/'data').mkdir();(source/'data/private').write_text('private')
        (current/'.venv/bin').mkdir(parents=True)
        (current/'.venv/bin/python').write_text('runtime')
        destination=current.parent/'candidate'
        with patch.object(update.subprocess,'run'):
            update.stage(source,current,destination,'crew')
        self.assertEqual((destination/'.venv').resolve(),(current/'.venv').resolve())
        self.assertFalse((destination/'.env').exists());self.assertFalse((destination/'data').exists())
        (source/'requirements-lock.txt').write_text('different')
        with self.assertRaisesRegex(ValueError,'Dependencies differ'):
            update.stage(source,current,current.parent/'rejected','crew')
        self.assertFalse((current.parent/'rejected').exists())
