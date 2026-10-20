"""Encrypted, reviewed edits of an adapter's fixed environment fields."""
import os
from pathlib import Path
import secrets

from cryptography.fernet import Fernet

from .adapters.verifier_config import safe_url
from .checkpoints import file_hash
from .security import Denied, digest, read_env

FIELDS = {'PUBLIC_URL':('دامنه اتصال کیف پول (اختیاری)',False), 'RPC_URL':('API سولانا',True),
          'RPC_FALLBACK_URL':('API پشتیبان سولانا (اختیاری)',True),
          'DAS_URL':('API مجموعه‌های سولانا',True), 'PLATFORM_API_URL':('API سایت ریپ‌کارز',False),
          'PLATFORM_API_KEY':('کلید API سایت',True), 'PLATFORM_WEBHOOK_SECRET':('کلید وبهوک سایت',True),
          'CHAIN_WEBHOOK_SECRET':('کلید وبهوک زنجیره',True)}


class Connections:
    def __init__(self, root, applications, redactor):
        self.key_path=Path(root)/'connection-key'
        self.applications,self.redactor=applications,redactor

    def cipher(self):
        if not self.key_path.exists():
            fd=os.open(self.key_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
        if self.key_path.is_symlink() or self.key_path.stat().st_mode&0o077 or self.key_path.stat().st_uid!=os.geteuid():
            raise Denied('Connection encryption key permissions need review.')
        return Fernet(self.key_path.read_bytes())

    def spec(self, adapter):
        spec=self.applications.spec(adapter)
        if spec['kind']!='verifier-v1':raise Denied('This application has no enrolled connection fields.')
        path=Path(spec['env_file'])
        if path.is_symlink() or not path.is_file() or path.stat().st_mode&0o077:
            raise Denied('Environment file must be private and already enrolled.')
        self.redactor.load_env(path)
        return spec,path

    def status(self, adapter):
        _,path=self.spec(adapter);env=read_env(path)
        cfg=self.applications.read(adapter)['settings']
        platform=cfg['account_linking'] or cfg['platform_points'] or 'platform' in (cfg['balance_mode'],cfg['asset_mode'],cfg['deposit_mode'])
        required={'PUBLIC_URL':cfg['wallet_linking'],'RPC_URL':cfg['chain_webhook'] or 'rpc' in (cfg['balance_mode'],cfg['deposit_mode']),'RPC_FALLBACK_URL':False,
                  'DAS_URL':cfg['asset_mode']=='das','PLATFORM_API_URL':platform,'PLATFORM_API_KEY':platform,
                  'PLATFORM_WEBHOOK_SECRET':cfg['account_linking'] or cfg['rip_feed'],'CHAIN_WEBHOOK_SECRET':cfg['chain_webhook']}
        return [{'field':k,'label':name,'configured':bool(env.get(k)),'secret':secret,'required':bool(required[k])} for k,(name,secret) in FIELDS.items()]

    def prepare(self, adapter, field, value):
        spec,path=self.spec(adapter)
        if field not in FIELDS or not isinstance(value,str) or not 1<=len(value)<=2048 or any(c.isspace() or c in '\x00\"\'\\' for c in value):
            raise Denied('Choose a supported connection field and one nonempty value.')
        if field.endswith('URL'):safe_url(value,origin_only=field=='PUBLIC_URL')
        elif len(value)<(32 if 'WEBHOOK' in field else 16):raise Denied('کلید دسترسی کوتاه است. مقدار کامل را از تیم مربوط بگیر.')
        self.redactor.values.add(value)
        return {'adapter':adapter,'field':field,'sealed':self.cipher().encrypt(value.encode()).decode(),
                'env_revision':file_hash(path),'contract_digest':digest(spec)}

    def validate(self, payload):
        spec,path=self.spec(payload.get('adapter'))
        if payload.get('contract_digest')!=digest(spec) or payload.get('field') not in FIELDS or payload.get('env_revision')!=file_hash(path):
            raise Denied('Connection settings changed. Open the connection form again.')
        value=self.cipher().decrypt(payload['sealed'].encode()).decode()
        self.redactor.values.add(value)
        return path,value

    def apply(self, payload):
        path,value=self.validate(payload)
        stat=path.stat();field=payload['field'];lines=path.read_text().splitlines()
        lines=[line for line in lines if line.partition('=')[0].strip()!=field]
        # No shell evaluation. Quotes remain valid in the systemd EnvironmentFile.
        rendered='"'+value+'"'
        temp=path.with_name(path.name+'.maestro-'+secrets.token_hex(6))
        try:
            fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as f:
                f.write('\n'.join(lines+[field+'='+rendered])+'\n');f.flush();os.fsync(f.fileno())
                os.fchown(f.fileno(),stat.st_uid,stat.st_gid)
                os.fchmod(f.fileno(),stat.st_mode&0o777)
            if file_hash(path)!=payload['env_revision']:raise Denied('Connection changed during execution.')
            temp.replace(path)
        finally:temp.unlink(missing_ok=True)
        return {'state':'completed','field':field,'verified':True,
                'detail':'اتصال ذخیره شد. برای خواندن مقدار جدید توسط بات، راه‌اندازی دوباره و بررسی سلامت لازم است.'}
