from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN_2022 = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
MAINNET_GENESIS = '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d'
KINDS = {'deposit_points', 'platform_points', 'token_balance', 'asset_count'}


class ConfigurationError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def b58decode(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 100:
        raise ConfigurationError('Invalid base58 value.')
    number = 0
    for char in value:
        if char not in ALPHABET:
            raise ConfigurationError('Invalid base58 character.')
        number = number * 58 + ALPHABET.index(char)
    return b'\0' * (len(value)-len(value.lstrip('1'))) + (number.to_bytes((number.bit_length()+7)//8, 'big') if number else b'')


def address(value):
    if len(b58decode(value)) != 32:
        raise ConfigurationError('A Solana address must decode to 32 bytes.')
    return value


def discord_id(value):
    if isinstance(value,str) and re.fullmatch(r'[1-9][0-9]{0,18}',value): value=int(value)
    if type(value) is not int or not 0<value<2**63: raise ConfigurationError('Invalid Discord server ID.')
    return value


def decimal(value, maximum=Decimal('1000000000000')):
    if isinstance(value, (bool, float)):
        raise ConfigurationError('Use a decimal string, not a float or boolean.')
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ConfigurationError('Invalid decimal amount.') from None
    if not out.is_finite() or out < 0 or out > maximum:
        raise ConfigurationError('Amount is outside the supported range.')
    return out


def usd_micros(value):
    amount = decimal(value)
    if amount.as_tuple().exponent < -6:
        raise ConfigurationError('USD receipts support at most six decimal places.')
    return int(amount * 1000000)


def safe_url(value, origin_only=False):
    if not isinstance(value, str) or len(value) > 2048:
        raise ConfigurationError('Invalid URL.')
    p = urlsplit(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment or p.port not in (None,443):
        raise ConfigurationError('Use an HTTPS URL without user credentials or a nonstandard port.')
    host = p.hostname.lower()
    if host in ('localhost',) or host.endswith(('.local','.internal','.localhost')) or '.' not in host:
        raise ConfigurationError('Use a public hostname.')
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ConfigurationError('Private and reserved addresses are not supported.')
    except ValueError as exc:
        if isinstance(exc,ConfigurationError):
            raise
    if origin_only and (p.path not in ('','/') or p.query):
        raise ConfigurationError('Use only the public HTTPS origin.')
    return value.rstrip('/')


DEFAULTS = {
    'enabled': False, 'wallet_linking': True, 'account_linking': False,
    'balance_mode': 'off', 'asset_mode': 'off', 'deposit_mode': 'off',
    'platform_points': False, 'rip_feed': False, 'chain_webhook': False,
    'member_role': 0, 'verification_channel': 0, 'rip_channel': 0, 'log_channel': 0,
    'public_message': 0, 'color': 0x800020, 'brand': 'Rip Cars',
    'platform_connect_url': '', 'sync_seconds': 86400, 'retry_seconds': 300,
    'snapshot_max_age': 3600, 'history_start': 0, 'scan_pages': 3, 'scan_limit': 100,
    'scan_transactions': 100, 'max_wallets': 3, 'error_alert_seconds': 300,
    'source_revision': 1, 'mint': '', 'sources': [], 'collections': [], 'rules': [],
    'texts': {
        'title': 'Connect to Rip Cars',
        'description': 'Connect your wallet or Rip Cars account to check your collection and qualifying roles. Use My status to see your last check.',
        'rip_title': 'A new car was pulled',
    },
}


def defaults():
    return copy.deepcopy(DEFAULTS)


def validate(cfg):
    if set(cfg) != set(DEFAULTS):
        raise ConfigurationError('Settings contain unknown or missing fields.')
    for key in ('enabled','wallet_linking','account_linking','platform_points','rip_feed','chain_webhook'):
        if type(cfg[key]) is not bool:
            raise ConfigurationError(key+' must be true or false.')
    for key in ('balance_mode','asset_mode'):
        allowed = ('off','rpc','platform') if key=='balance_mode' else ('off','das','platform')
        if cfg[key] not in allowed:
            raise ConfigurationError('Invalid '+key+'.')
    if cfg['deposit_mode'] not in ('off','rpc','platform'):
        raise ConfigurationError('Choose off, rpc or platform for deposits.')
    ranges = {'sync_seconds':(300,604800),'retry_seconds':(60,3600),'snapshot_max_age':(60,86400),
              'history_start':(0,4102444800),'scan_pages':(1,20),'scan_limit':(1,1000),
              'scan_transactions':(1,1000),'max_wallets':(1,10),'error_alert_seconds':(30,3600),
              'source_revision':(1,1000000),'color':(0,0xFFFFFF)}
    for key,(lo,hi) in ranges.items():
        if type(cfg[key]) is not int or not lo <= cfg[key] <= hi:
            raise ConfigurationError(f'{key} must be an integer between {lo} and {hi}.')
    for key in ('member_role','verification_channel','rip_channel','log_channel','public_message'):
        if type(cfg[key]) is not int or not 0 <= cfg[key] < 2**63:
            raise ConfigurationError('Invalid Discord ID for '+key+'.')
    if not isinstance(cfg['brand'],str) or not 1 <= len(cfg['brand']) <= 80:
        raise ConfigurationError('Brand must be 1 to 80 characters.')
    if cfg['platform_connect_url']:
        safe_url(cfg['platform_connect_url'])
    if cfg['mint']:
        address(cfg['mint'])
    if not isinstance(cfg['texts'],dict) or set(cfg['texts']) != set(DEFAULTS['texts']):
        raise ConfigurationError('Public text fields are invalid.')
    for key,value in cfg['texts'].items():
        if not isinstance(value,str) or not 1 <= len(value) <= (3500 if key=='description' else 150):
            raise ConfigurationError('Invalid public text: '+key)
    if not isinstance(cfg['sources'],list) or len(cfg['sources'])>20:
        raise ConfigurationError('Use at most 20 deposit sources.')
    names=set()
    for source in cfg['sources']:
        if set(source)!={'id','program_ids','treasury_owners','token_accounts','mints'} or not re.fullmatch('[a-z0-9_-]{1,40}',source['id']) or source['id'] in names:
            raise ConfigurationError('Deposit sources need a unique ID and the documented fields.')
        names.add(source['id'])
        for key in ('program_ids','treasury_owners','token_accounts'):
            if not isinstance(source[key],list) or len(source[key])>100:
                raise ConfigurationError('Invalid source address list.')
            for value in source[key]: address(value)
        if not source['treasury_owners'] and not source['token_accounts']:
            raise ConfigurationError('Select a verified deposit destination.')
        if not isinstance(source['mints'],dict) or not 1 <= len(source['mints']) <= 20:
            raise ConfigurationError('Select the accepted payment mints.')
        for mint,spec in source['mints'].items():
            address(mint)
            if set(spec)!={'decimals','usd_per_token'} or type(spec['decimals']) is not int or not 0 <= spec['decimals']<=12 or decimal(spec['usd_per_token']) <= 0:
                raise ConfigurationError('Payment mint needs decimals and an explicit USD unit valuation.')
    if not isinstance(cfg['collections'],list) or len(cfg['collections'])>100:
        raise ConfigurationError('Use at most 100 verified collection IDs.')
    for value in cfg['collections']: address(value)
    if not isinstance(cfg['rules'],list) or len(cfg['rules'])>50:
        raise ConfigurationError('Use at most 50 role rules.')
    keys=set(); roles=set()
    for rule in cfg['rules']:
        if set(rule)!={'key','name','role_id','kind','threshold','selector','enabled'}:
            raise ConfigurationError('Role rule fields do not match the schema.')
        if not re.fullmatch('[a-z0-9_-]{1,40}',rule['key']) or rule['key'] in keys or rule['kind'] not in KINDS or type(rule['enabled']) is not bool:
            raise ConfigurationError('Role rule key, metric or enabled value is invalid.')
        keys.add(rule['key'])
        if not isinstance(rule['name'],str) or not 1<=len(rule['name'])<=100 or rule['name'].casefold() in ('og','ripper','rippers','admin','team','moderator'):
            raise ConfigurationError('Use a separate qualifying role name, not a staff or Gate role.')
        decimal(rule['threshold'])
        if type(rule['role_id']) is not int or not 0 <= rule['role_id']<2**63 or rule['role_id'] and rule['role_id'] in roles:
            raise ConfigurationError('Each qualifying role must have its own ID.')
        if rule['role_id']: roles.add(rule['role_id'])
        if not isinstance(rule['selector'],dict) or not set(rule['selector']) <= {'asset_ids','collection','attribute','value'}:
            raise ConfigurationError('Use asset IDs, a verified collection or an exact attribute selector.')
        if 'collection' in rule['selector']: address(rule['selector']['collection'])
        if 'asset_ids' in rule['selector'] and (not isinstance(rule['selector']['asset_ids'],list) or not 1<=len(rule['selector']['asset_ids'])<=100):
            raise ConfigurationError('Asset ID selector must contain 1 to 100 IDs.')
        if 'asset_ids' in rule['selector'] and any(not isinstance(v,str) or not 1<=len(v)<=200 for v in rule['selector']['asset_ids']):
            raise ConfigurationError('Each asset selector ID must be a nonempty string.')
        if ('attribute' in rule['selector']) != ('value' in rule['selector']):
            raise ConfigurationError('Attribute filters require both attribute and value.')
        if 'attribute' in rule['selector'] and any(not isinstance(rule['selector'][k],str) or not 1<=len(rule['selector'][k])<=200 for k in ('attribute','value')):
            raise ConfigurationError('Attribute names and values must be nonempty strings.')
        if rule['kind']!='asset_count' and rule['selector']:
            raise ConfigurationError('Asset selectors apply only to asset_count rules.')
        if len(canonical(rule['selector']))>10000:
            raise ConfigurationError('Asset selector is too large.')
    return cfg


def source_hash(cfg):
    return hashlib.sha256(canonical([cfg['sources'],cfg['history_start'],cfg['source_revision']]).encode()).hexdigest()


def role_metric(rule,snapshot):
    kind=rule['kind']
    if not snapshot.get('complete',{}).get(kind,False):
        return None
    if kind!='asset_count':
        return decimal(snapshot.get(kind,'0'),maximum=Decimal('1e30'))
    selector=rule['selector']; ids=set(); unknown=False
    for asset in snapshot.get('assets',[]):
        if 'asset_ids' in selector and asset['id'] not in selector['asset_ids']: continue
        if 'collection' in selector and asset.get('collection') != selector['collection']: continue
        if 'attribute' in selector:
            if selector['attribute'] not in asset.get('attributes',{}): unknown=True; continue
            if str(asset['attributes'][selector['attribute']])!=selector['value']: continue
        ids.add(asset['id'])
    if unknown and Decimal(len(ids))<decimal(rule['threshold']): return None
    return Decimal(len(ids))
