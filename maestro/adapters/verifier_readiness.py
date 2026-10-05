"""Pinned static readiness rules from the owned Verifier 1.0.1 source."""
def readiness(cfg,env):
    blockers=[]
    if not env.get('PUBLIC_URL'): blockers.append('Set PUBLIC_URL to the verifier HTTPS origin on the VPS.')
    if not cfg['member_role']: blockers.append('Choose the Gate member role.')
    if not cfg['verification_channel']: blockers.append('Choose the connection panel channel.')
    if not cfg['public_message']: blockers.append('Publish the connection panel before activation.')
    if not cfg['log_channel']: blockers.append('Choose a staff-only log channel.')
    if cfg['balance_mode']=='rpc' and not cfg['mint']: blockers.append('Set the verified $CARS mint.')
    if cfg['balance_mode']=='rpc' or cfg['deposit_mode']=='rpc':
        if not env.get('RPC_URL'): blockers.append('Configure RPC_URL on the VPS.')
    if cfg['asset_mode']=='das':
        if not env.get('DAS_URL'): blockers.append('Configure DAS_URL on the VPS.')
        if not cfg['collections']: blockers.append('Add verified Rip Cars collection IDs.')
    platform=cfg['account_linking'] or cfg['platform_points'] or 'platform' in (cfg['balance_mode'],cfg['asset_mode'],cfg['deposit_mode'])
    if platform and not (env.get('PLATFORM_API_URL') and env.get('PLATFORM_API_KEY')): blockers.append('Configure the Rip Cars backend bridge URL and key.')
    if cfg['account_linking'] and not cfg['platform_connect_url']: blockers.append('Set the team-provided account connect page URL.')
    if cfg['account_linking'] or cfg['rip_feed']:
        if len(env.get('PLATFORM_WEBHOOK_SECRET',''))<32: blockers.append('Configure PLATFORM_WEBHOOK_SECRET on both servers.')
    if cfg['deposit_mode']=='rpc' and not cfg['sources']: blockers.append('Add verified payment sources and destination addresses.')
    if cfg['chain_webhook'] and (len(env.get('CHAIN_WEBHOOK_SECRET',''))<32 or not env.get('RPC_URL')): blockers.append('Configure the signed chain webhook secret and RPC URL.')
    if cfg['rip_feed'] and not cfg['rip_channel']: blockers.append('Choose the rip announcement channel.')
    if cfg['rules'] and any(r['enabled'] and not r['role_id'] for r in cfg['rules']): blockers.append('Bind or create all enabled qualifying roles.')
    for rule in cfg['rules']:
        if not rule['enabled']: continue
        mode={'deposit_points':cfg['deposit_mode']!='off','platform_points':cfg['platform_points'],'token_balance':cfg['balance_mode']!='off','asset_count':cfg['asset_mode']!='off'}[rule['kind']]
        if not mode: blockers.append(f'Rule {rule["key"]}: enable its data source or disable the rule.')
    return blockers
