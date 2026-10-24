"""One-time configuration through upstream configuration APIs; no game APIs or secrets exported."""
from __future__ import annotations
import asyncio, copy, importlib.util, json, os, pathlib, sqlite3, sys
ROOT = pathlib.Path(os.environ.get('FURINA_DEPLOY_ROOT') or pathlib.Path(__file__).resolve().parents[1])
CORE = ROOT / 'integrations/gsuid_core'
os.chdir(CORE);sys.path.insert(0,str(CORE))

def legacy_config():
 p=ROOT/'data/furina.db'
 if not p.is_file():return {}
 with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as con:
  row=con.execute("SELECT value FROM docs WHERE ns='mihoyo' AND key='config'").fetchone()
 return json.loads(row[0]) if row else {}


def main():
 from gsuid_core.config import core_config, plugin_config_store, plugins_sample
 from gsuid_core.utils.plugins_config.gs_config import core_plugins_config, log_config, StringConfig
 from gsuid_core.data_store import get_res_path
 marker=ROOT/'integrations/configured.json'
 if marker.exists():raise RuntimeError('Already configured; inspect existing state instead of overwriting it.')
 cfg=legacy_config()
 for k,v in {'HOST':'127.0.0.1','PORT':'8765','ENABLE_HTTP':False,'enable_empty_start':True,'command_start':['#','/']}.items():core_config.set_config(k,v)
 # Read owner identifiers locally; never print values or the .env contents.
 from dotenv import dotenv_values
 env=dotenv_values(ROOT/'.env')
 owners={str(env.get(k) or '') for k in ('AI_CHAT_GROUP_OWNER_QQ','RELAY_OWNER_QQ')}
 owners={x for x in owners if x.isdigit()}
 try:owners.update(str(x) for x in json.loads(env.get('SUPERUSERS') or '[]') if str(x).isdigit())
 except (ValueError,TypeError):pass
 if owners:core_config.set_config('masters',sorted(owners));core_config.set_config('superusers',sorted(owners))
 for key in ('AutoUpdateCore','AutoUpdatePlugins','AutoRestartCore','AutoInstallDep','AutoUpdateDep'):
  core_plugins_config.set_config(key,False)
 core_plugins_config.set_config('GitMirror','https://cnb.cool/gscore-mirror/')
 log_config.set_config('ShowReceive',False)
 specs=[('GenshinUID','genshin',['原神','ys'],'genshinuid_config','CONIFG_DEFAULT'),('StarRailUID','sr',['星铁','崩铁','铁道'],'starrailuid_config','CONIFG_DEFAULT'),('ZZZeroUID','zzz',[],'zzzerouid_config','CONFIG_DEFAULT'),('BBBUID','bh3',['崩3'],'bbb_config','CONFIG_DEFAULT')]
 time=str(cfg.get('sign_time') or '10:00').split(':')
 if len(time)!=2 or not all(x.isdigit() for x in time):time=['10','00']
 for name,key,prefix,config_dir,attribute in specs:
  conf=copy.deepcopy(plugins_sample);conf.update(name=name,prefix=prefix,allow_empty_prefix=False,enabled=bool((cfg.get('games') or {}).get(key,True)))
  plugin_config_store.set(name,conf);plugin_config_store.save(name)
  path=CORE/'gsuid_core/plugins'/name/name/config_dir/'config_default.py'
  spec=importlib.util.spec_from_file_location('_upstream_defaults_'+name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  settings=StringConfig(name,get_res_path(name)/'config.json',getattr(module,attribute))
  settings.set_config('SignTime',[int(x) for x in time] if name=='GenshinUID' else time)
  # Staging is intentionally inert. Enable only after old scheduler has been stopped.
  settings.set_config('SchedSignin',False)
  settings.set_config('PrivateSignReport',True)
 conf=copy.deepcopy(plugins_sample);conf.update(name='core_command',allow_empty_prefix=False)
 conf['sv']['扫码登陆']={'priority':5,'enabled':True,'pm':6,'black_list':[],'area':'DIRECT','white_list':[]}
 plugin_config_store.set('core_command',conf);plugin_config_store.save('core_command')
 marker.write_text(json.dumps({'configured':True,'auto_sign_staged':False,'owner_configured':bool(owners),'game_plugins':[x[0] for x in specs]},ensure_ascii=False,indent=2),encoding='utf-8')
 print('CONFIGURED: loopback only; private QR; upstream prefixes; auto sign disabled until cutover; auto update disabled; no AI enabled.')

if __name__=='__main__':main()
