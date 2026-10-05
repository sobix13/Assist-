from __future__ import annotations

import ast
import hashlib
import io
import json
import secrets
import shutil
import stat
import tarfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from .security import Denied

MAX_UPLOAD = 8 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
MAX_FILES = 200


def safe_name(name):
    p = PurePosixPath(name)
    if not name or "\\" in name or any(ord(c) < 32 for c in name) or ":" in name or p.is_absolute() or ".." in p.parts or len(name) > 240:
        raise Denied("Unsafe archive or file path.")
    if any(part in ("", ".") for part in name.rstrip("/").split("/")):
        raise Denied("Unsafe archive or file path.")
    return p


class Files:
    def __init__(self, root, store, same_owner=None):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.store = store
        self.same_owner = same_owner or (lambda first, second: first == second)

    def stage(self, actor, name, data):
        safe_name(name)
        if "/" in name or len(data) > MAX_UPLOAD or not data:
            raise Denied("Upload one nonempty file of at most 8 MiB.")
        key = secrets.token_hex(12)
        folder = self.root / key
        folder.mkdir(mode=0o700)
        seen = set()
        total = 0
        entries = []

        def add(member_name, content, directory=False):
            nonlocal total
            p = safe_name(member_name)
            normalized = str(p)
            if normalized in seen:
                raise Denied("Duplicate archive member.")
            seen.add(normalized)
            if len(seen) > MAX_FILES:
                raise Denied("Archive has more than 200 entries.")
            destination = folder / p
            if directory:
                destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                return
            total += len(content)
            if total > MAX_EXPANDED or len(content) > MAX_UPLOAD:
                raise Denied("Archive exceeds expanded size limits.")
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.write_bytes(content)
            destination.chmod(0o600)
            item = {"name": normalized, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            if destination.suffix == ".py":
                try:
                    ast.parse(content.decode("utf-8"))
                    item["syntax"] = "Python syntax valid; code was not executed"
                except (SyntaxError, UnicodeError, ValueError) as exc:
                    item["syntax"] = type(exc).__name__
            entries.append(item)

        try:
            source = io.BytesIO(data)
            if zipfile.is_zipfile(source):
                with zipfile.ZipFile(source) as z:
                    for item in z.infolist():
                        mode = item.external_attr >> 16
                        if item.flag_bits & 1 or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                            raise Denied("Encrypted files, links and special archive entries are rejected.")
                        if item.file_size > MAX_UPLOAD or total + item.file_size > MAX_EXPANDED:
                            raise Denied("Archive size limit exceeded.")
                        add(item.filename.rstrip("/"), b"" if item.is_dir() else z.read(item), item.is_dir())
            elif name.endswith((".tar", ".tar.gz", ".tgz")):
                source.seek(0)
                with tarfile.open(fileobj=source, mode="r:*") as tar:
                    for item in tar:
                        if not (item.isfile() or item.isdir()) or item.issparse():
                            raise Denied("Archive links, devices and sparse files are rejected.")
                        if item.size > MAX_UPLOAD or total + item.size > MAX_EXPANDED:
                            raise Denied("Archive size limit exceeded.")
                        add(item.name.rstrip("/"), b"" if item.isdir() else tar.extractfile(item).read(), item.isdir())
            else:
                add(name, data)
            if not entries:
                raise Denied("Upload contains no regular files.")
            meta = {"id": key, "actor": actor, "source_name": name, "source_sha256": hashlib.sha256(data).hexdigest(),
                    "files": entries, "bytes": total}
            self.store.run("INSERT INTO uploads VALUES(?,?,?,?)", (key, actor, time.time(), json.dumps(meta)))
            return meta
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def metadata(self, actor, key):
        row = self.store.one("SELECT data,actor FROM uploads WHERE id=?", (key,))
        if not row or not self.same_owner(row['actor'], actor):
            raise Denied("Upload is missing, expired or owned by someone else.")
        return json.loads(row["data"])

    def materialize(self, actor, key, destination):
        meta = self.metadata(actor, key)
        source = self.root / key
        for item in meta["files"]:
            p = source / item["name"]
            if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != item["sha256"]:
                raise Denied("An uploaded file changed after review.")
        for item in meta["files"]:
            p = Path(destination) / item["name"]
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / item["name"], p)
            p.chmod(0o600)
        return meta

    def preview(self, actor, key, name):
        meta = self.metadata(actor, key)
        if name not in {f["name"] for f in meta["files"]}:
            raise Denied("Choose a file listed in this upload.")
        p = self.root / key / name
        data = p.read_bytes()
        return data[:16000].decode("utf-8", "replace")

    def prune(self, days):
        cutoff = time.time() - days * 86400
        for row in self.store.rows("SELECT id FROM uploads WHERE at<?", (cutoff,)):
            shutil.rmtree(self.root / row["id"], ignore_errors=True)
            self.store.run("DELETE FROM uploads WHERE id=?", (row["id"],))
