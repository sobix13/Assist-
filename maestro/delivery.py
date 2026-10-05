"""Shared output delivery choices for both owner interfaces."""
import base64

from .security import Denied


def binary_parts(name, data, limit=6*1024*1024):
    for index, offset in enumerate(range(0, len(data), limit)):
        yield name if len(data) <= limit else name + '.part' + str(index+1).zfill(3), data[offset:offset+limit]


def text_parts(name, text, limit=6*1024*1024):
    data, offset, index = text.encode(), 0, 1
    while offset < len(data):
        part = data[offset:offset+limit]
        if offset + len(part) < len(data):
            while part:
                try:
                    part.decode('utf-8')
                    break
                except UnicodeDecodeError as exc:
                    part = part[:exc.start]
        if not part:
            raise Denied('Text export could not advance.')
        yield name if len(data) <= limit else str(index).zfill(3) + '-' + name, part
        offset, index = offset+len(part), index+1


async def job_attachments(api, key):
    row = await api.call('job', id=key)
    for package in row.get('operation',{}).get('data',{}).get('packages',[]):
        offset=0
        while True:
            part=await api.call('package_part',id=package['id'],offset=offset)
            data=base64.b64decode(part['data'])
            import hashlib
            if hashlib.sha256(data).hexdigest()!=part['part_sha256']:
                raise Denied('A package transport part failed its checksum.')
            yield part['name'],data
            if part['done']:break
            if part['next_offset']<=offset:raise Denied('Package download stopped making progress.')
            offset=part['next_offset']
    metadata = row.get('operation', {}).get('data', {}).get('output')
    if not metadata:
        if row['output']:
            yield 'job-' + key + '.txt', row['output'].encode()
        return
    if metadata['compressed_bytes'] <= 6*1024*1024 and metadata['bytes'] > 6*1024*1024:
        part = await api.call('job_output', id=key, compressed=True)
        yield part['name'], base64.b64decode(part['data'])
        return
    offset = 0
    while True:
        part = await api.call('job_output', id=key, offset=offset)
        yield part['name'], base64.b64decode(part['data'])
        if part['done']:
            break
        if part['next_offset'] <= offset:
            raise Denied('Output export stopped making progress.')
        offset = part['next_offset']
