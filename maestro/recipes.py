"""Locally enrolled repair recipes with file rollback and post-action verification."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path

from .checkpoints import file_hash, protected_json
from .security import Denied, digest


class RecipeBook:
    def __init__(self, policy):
        self.policy = policy

    def all(self):
        path = Path(self.policy.data.get('recipes_config', '/etc/maestro/recipes.json'))
        if not path.exists():
            return []
        value = protected_json(path)
        recipes = value.get('recipes', [])
        if not isinstance(recipes, list) or len(recipes) > 50:
            raise Denied('Invalid locally enrolled repair recipe list.')
        ids = set()
        for recipe in recipes:
            if not isinstance(recipe, dict):
                raise Denied('Each repair recipe must be an object.')
            if not re.fullmatch('[a-z][a-z0-9_-]{0,40}', recipe.get('id', '')) or recipe['id'] in ids:
                raise Denied('Repair recipes need unique simple IDs.')
            ids.add(recipe['id'])
            if not re.fullmatch('[a-z][a-z0-9_-]{0,30}', recipe.get('target', '')):
                raise Denied('Every repair recipe needs an enrolled target.')
            for key in ('argv', 'preflight', 'rollback_argv'):
                argv = recipe.get(key, [])
                if key == 'argv' and not argv:
                    raise Denied('Repair action is empty.')
                if not isinstance(argv, list) or len(argv) > 50 or (argv and not Path(argv[0]).is_absolute()) or any(not isinstance(x, str) or '\x00' in x or len(x)>1000 for x in argv):
                    raise Denied('Recipe commands must be fixed absolute argument arrays.')
            if not isinstance(recipe.get('automatic', False), bool) or not isinstance(recipe.get('match_codes', []), list):
                raise Denied('Invalid automatic recipe policy.')
            if any(not isinstance(x, str) or not re.fullmatch('[a-z][a-z0-9_]{0,50}', x) for x in recipe.get('match_codes', [])):
                raise Denied('Recipe triggers must be exact diagnostic codes.')
            if recipe.get('automatic') and not recipe.get('match_codes'):
                raise Denied('An automatic recipe requires explicit diagnostic triggers.')
            if not isinstance(recipe.get('rollback_paths', []), list) or len(recipe.get('rollback_paths', [])) > 20:
                raise Denied('Set a bounded list of code/configuration rollback paths.')
            for path in recipe.get('rollback_paths', []):
                if not isinstance(path, str) or '\x00' in path:
                    raise Denied('Invalid rollback location.')
                candidate = Path(path)
                if not candidate.is_absolute() or '..' in candidate.parts or str(candidate) in ('/', '/etc', '/opt', '/srv', '/root', '/home') or candidate.parts[1] not in ('etc', 'opt', 'srv', 'root', 'home'):
                    raise Denied('Rollback paths must be specific code/configuration locations.')
            for key, low, high, default in [('timeout', 1, 900, 120), ('verify_seconds', 1, 120, 20)]:
                v = recipe.get(key, default)
                if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
                    raise Denied('Invalid recipe deadline.')
        return recipes

    def get(self, key):
        recipe = next((r for r in self.all() if r['id'] == key), None)
        if not recipe:
            raise Denied('Choose a locally enrolled repair recipe.')
        return {**recipe, 'digest': digest(recipe)}


class FileRollback:
    def __init__(self, folder, paths, databases=()):
        self.folder = Path(folder)
        self.folder.mkdir(mode=0o700)
        self.paths = [Path(x) for x in paths]
        if any(a == b or a in b.parents or b in a.parents for i, a in enumerate(self.paths) for b in self.paths[i+1:]):
            raise Denied('Rollback roots cannot overlap.')
        self.databases = {str(Path(x).resolve()) for x in databases if x}
        self.entries = []

    def capture(self):
        total = 0
        for root in self.paths:
            # Parent aliases must not let a recipe roll back an unrelated directory.
            if root.parent.resolve() != root.parent:
                raise Denied('Rollback parents must not contain symlink aliases.')
            if not root.exists() and not root.is_symlink():
                self.entries.append({'path': str(root), 'kind': 'missing'})
                continue
            items = [root]
            if root.is_dir() and not root.is_symlink():
                for parent, directories, files in os.walk(root, followlinks=False):
                    items.extend(Path(parent)/x for x in directories+files)
                    if len(items) > 2000:
                        raise Denied('Recipe rollback exceeds two thousand filesystem entries.')
            for path in items:
                info = path.lstat()
                row = {'path': str(path), 'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid}
                if path.is_symlink():
                    row.update(kind='symlink', link=os.readlink(path))
                elif path.is_dir():
                    row['kind'] = 'directory'
                elif path.is_file():
                    if str(path.resolve()) in self.databases or path.name.endswith(('.sqlite3', '.db', '-wal', '-shm')):
                        raise Denied('Database rollback is an offline restore operation, never a repair recipe.')
                    with path.open('rb') as file:
                        if file.read(16) == b'SQLite format 3\x00':
                            raise Denied('A SQLite database cannot enter a code/configuration rollback.')
                    total += info.st_size
                    if total > 32*1024*1024:
                        raise Denied('Recipe rollback exceeds thirty-two MiB. Stage a release rollback instead.')
                    row.update(kind='file', stored=str(len(self.entries)))
                    shutil.copyfile(path, self.folder/row['stored'])
                    (self.folder/row['stored']).chmod(0o600)
                    row['sha256'] = file_hash(self.folder/row['stored'])
                else:
                    raise Denied('Rollback accepts regular code/configuration files, directories and symlinks.')
                self.entries.append(row)
        (self.folder/'manifest.json').write_text(json.dumps(self.entries, indent=2))
        (self.folder/'manifest.json').chmod(0o600)
        return {'entries': len(self.entries), 'bytes': total}

    def restore(self):
        if not self.entries:
            self.entries = json.loads((self.folder/'manifest.json').read_text())
        for row in self.entries:
            if row['kind'] == 'file':
                stored = self.folder/row['stored']
                if not stored.is_file() or stored.is_symlink() or file_hash(stored) != row['sha256']:
                    raise Denied('A rollback copy failed its checksum. Live files were preserved.')
        # The enrolled roots belong to this recipe. Unknown resources outside them
        # are untouched. Whole databases never enter these roots.
        # Recheck all roots before changing any of them. A repair may have created
        # a database after capture; never delete new database data during rollback.
        for root in self.paths:
            if root.parent.resolve() != root.parent:
                raise Denied('Rollback parent changed during the action. Manual review required.')
            candidates = [root]
            if root.is_dir() and not root.is_symlink():
                for parent, directories, files in os.walk(root, followlinks=False):
                    candidates.extend(Path(parent)/x for x in directories+files)
                    if len(candidates) > 2000:
                        raise Denied('Rollback target changed beyond its entry limit. Manual review required.')
            for path in candidates:
                if path.is_file() and not path.is_symlink():
                    if str(path.resolve()) in self.databases or path.name.endswith(('.sqlite3', '.db', '-wal', '-shm')):
                        raise Denied('New database data appeared. Automatic file rollback was stopped.')
                    with path.open('rb') as file:
                        if file.read(16) == b'SQLite format 3\x00':
                            raise Denied('New SQLite data appeared. Automatic file rollback was stopped.')
        for root in self.paths:
            if root.is_symlink() or root.is_file():
                root.unlink()
            elif root.exists():
                shutil.rmtree(root)
        for row in sorted(self.entries, key=lambda x: len(Path(x['path']).parts)):
            path = Path(row['path'])
            if row['kind'] == 'missing':
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if row['kind'] == 'directory':
                path.mkdir(exist_ok=True)
            elif row['kind'] == 'symlink':
                path.symlink_to(row['link'])
            else:
                shutil.copyfile(self.folder/row['stored'], path)
            os.chown(path, row['uid'], row['gid'], follow_symlinks=False)
            if row['kind'] != 'symlink':
                path.chmod(row['mode'])
        return {'restored': True, 'entries': len(self.entries)}
