"""Root-private output spools, redacted exports and bounded transport-sized parts."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
import time
from pathlib import Path

from .security import Denied

PART_BYTES = 6 * 1024 * 1024


class OutputVault:
    def __init__(self, root, redactor):
        self.root, self.redactor = Path(root), redactor
        self.root.mkdir(exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def path(self, key, suffix):
        if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{24}', key):
            raise Denied('Invalid job output ID.')
        return self.root / (key + suffix)

    def finalize(self, key, limit, total, state):
        raw = self.path(key, '.raw')
        # Raw source is never served. Retaining whole bounded text during redaction avoids
        # a secret spanning two read chunks. Only one export finalizer runs at a time.
        content = raw.read_bytes()[:limit] if raw.exists() else b''
        text = self.redactor.clean(content.decode('utf-8', 'replace'))
        if state == 'output_limited':
            text += '\n[Job stopped at the configured output limit. Output is incomplete.]\n'
        data = text.encode()
        target = self.path(key, '.txt')
        target.write_bytes(data)
        target.chmod(0o600)
        compressed = gzip.compress(data, mtime=0)
        self.path(key, '.txt.gz').write_bytes(compressed)
        self.path(key, '.txt.gz').chmod(0o600)
        metadata = {'bytes': len(data), 'observed_bytes': total, 'sha256': hashlib.sha256(data).hexdigest(),
                    'compressed_bytes': len(compressed), 'incomplete': state in ('output_limited', 'timed_out', 'cancelled', 'interrupted'), 'state': state}
        self.path(key, '.json').write_text(json.dumps(metadata))
        self.path(key, '.json').chmod(0o600)
        raw.unlink(missing_ok=True)
        return metadata

    def part(self, key, offset=0, compressed=False):
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise Denied('Output offset must be a nonnegative integer.')
        path = self.path(key, '.txt.gz' if compressed else '.txt')
        if not path.is_file() or path.is_symlink():
            raise Denied('Output is not ready. Refresh the job after it finishes.')
        size = path.stat().st_size
        if offset > size or (compressed and (offset or size > PART_BYTES)):
            raise Denied('Use uncompressed text parts for a large export.')
        with path.open('rb') as file:
            file.seek(offset)
            data = file.read(PART_BYTES)
        if not compressed and offset + len(data) < size:
            while data:
                try:
                    data.decode('utf-8')
                    break
                except UnicodeDecodeError as exc:
                    if exc.end != len(data):
                        raise Denied('Choose the next offset returned by the previous part.') from None
                    data = data[:exc.start]
        return {'name': f'job-{key}-{offset}.txt' + ('.gz' if compressed else ''),
                'data': base64.b64encode(data).decode(), 'offset': offset, 'next_offset': offset + len(data),
                'total_bytes': size, 'done': offset + len(data) >= size,
                'sha256': hashlib.sha256(data).hexdigest(), 'metadata': json.loads(self.path(key, '.json').read_text())}

    def prune(self, days, active):
        cutoff = time.time() - days * 86400
        for path in self.root.iterdir():
            if path.is_file() and not path.is_symlink() and path.name[:24] not in active and path.stat().st_mtime < cutoff:
                path.unlink()
