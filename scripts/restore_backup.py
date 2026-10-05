#!/usr/bin/env python3
"""Decrypt and verify an Maestro backup to a new offline file only."""
import argparse
import base64
import getpass
import hashlib
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def restore(source, destination, password):
    data = Path(source).read_bytes()
    out = Path(destination)
    if out.exists() or data[:4] != b"RCA1":
        raise ValueError("Use a new destination and an RCA1 encrypted backup.")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=data[4:20], iterations=600000)
    raw = Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode()))).decrypt(data[20:])
    out.write_bytes(raw)
    out.chmod(0o600)
    try:
        db = sqlite3.connect(out.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Restored SQLite file failed integrity verification.")
        finally:
            db.close()
    except BaseException:
        out.unlink()
        raise
    return hashlib.sha256(raw).hexdigest()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source")
    p.add_argument("destination")
    a = p.parse_args()
    print("Verified offline copy: " + restore(a.source, a.destination, getpass.getpass("Backup password: ")))
