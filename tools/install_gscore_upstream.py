"""One-time installer of untouched upstream repositories; no game implementation."""
from __future__ import annotations
import concurrent.futures, datetime, json, os, pathlib, subprocess, sys, threading, traceback, urllib.request
ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / 'integrations'
LOG = ROOT / 'logs' / 'gscore-install.log'
STATUS = BASE / 'install-status.json'
LOCK = threading.Lock()
SPECS = {
 'gsuid_core': ('Genshin-bots/gsuid_core', 'master', '1153548720a7c04e8f29a0299e90131c81bc0521'),
 'nonebot-genshinuid': ('KimigaiiWuyi/GenshinUID', 'v4-nonebot2', 'e57b76a089365357b3581bf605127842d1bd31dc'),
 'GenshinUID': ('KimigaiiWuyi/GenshinUID', 'v4', '2c04a6536e0c1504aec8f86102f346f4bc948ef3'),
 'StarRailUID': ('baiqwerdvd/StarRailUID', 'master', '739a80a26b7a137b6b7b7d45435c769822de06d0'),
 'ZZZeroUID': ('ZZZure/ZZZeroUID', 'master', '0cd503e6d12f0669cf16013660f8a4c35dbbb4f1'),
 'BBBUID': ('Neroliu/BBBUID', 'main', '28ca081d14a8876a7c06b553321085513a356e4a'),
}
ENV = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', UV_PYTHON_INSTALL_DIR=str(BASE/'.python'), UV_CACHE_DIR=str(BASE/'.uv-cache'), PLAYWRIGHT_BROWSERS_PATH=str(BASE/'playwright'))
# Git does not inherit the Windows Internet Settings proxy; scope it to this installer.
ENV['GIT_TERMINAL_PROMPT']='0'
ENV['UV_DEFAULT_INDEX']='https://pypi.tuna.tsinghua.edu.cn/simple'
ENV['UV_CONCURRENT_DOWNLOADS']='4'
STATE = {'state':'running','pid':os.getpid(),'started':datetime.datetime.now().isoformat(),'completed':[]}
def state(phase, **kw):
 with LOCK:
  STATE.update(phase=phase, **kw); STATUS.write_text(json.dumps(STATE,ensure_ascii=False,indent=2),encoding='utf-8')
def run(args,cwd=ROOT,timeout=1200,capture=False):
 with LOCK:
  with LOG.open('a',encoding='utf-8') as f: f.write('\n> '+' '.join(map(str,args))+'\n')
 with LOG.open('ab') as f:
  p=subprocess.run(list(map(str,args)),cwd=cwd,env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE if capture else f,stderr=f,creationflags=subprocess.CREATE_NO_WINDOW,timeout=timeout)
 if p.returncode: raise RuntimeError(f'Command failed ({p.returncode}): {args[0]} {args[1]}')
 return p.stdout.decode('utf-8').strip() if capture else ''
def repo(name):
 owner,branch,sha=SPECS[name]
 dest=BASE/name if name in ('gsuid_core','nonebot-genshinuid') else BASE/'gsuid_core/gsuid_core/plugins'/name
 dest.mkdir(parents=True,exist_ok=True)
 if not (dest/'.git').exists():
  if any(dest.iterdir()):raise RuntimeError(f'Nonempty source directory: {name}')
  run(['git','init','-b',branch],dest)
  run(['git','remote','add','origin',f'https://github.com/{owner}.git'],dest)
 current=subprocess.run(['git','rev-parse','HEAD'],cwd=dest,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
 if current.returncode or current.stdout.decode().strip()!=sha:
  mirror='https://cnb.cool/gscore-mirror/'+owner.split('/')[-1]
  run(['git','-c','http.proxy=','fetch','--depth=1',mirror,sha],dest,timeout=900)
  run(['git','-c','core.autocrlf=false','checkout','-B',branch,sha],dest)
 run(['git','config','core.autocrlf','false'],dest)
 run(['git','config',f'branch.{branch}.remote','origin'],dest)
 run(['git','config',f'branch.{branch}.merge',f'refs/heads/{branch}'],dest)
 actual=run(['git','rev-parse','HEAD'],dest,capture=True)
 if actual != sha:raise RuntimeError(f'Commit mismatch: {name}')
 run(['git','diff','--exit-code'],dest)
 with LOCK:STATE['completed'].append(name)
 state('repositories')
 return dest

try:
 BASE.mkdir(exist_ok=True); LOG.parent.mkdir(exist_ok=True);state('repositories')
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
  list(pool.map(repo,['gsuid_core','nonebot-genshinuid']))
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  list(pool.map(repo,['GenshinUID','StarRailUID','ZZZeroUID','BBBUID']))
 core=BASE/'gsuid_core'; corepy=core/'.venv/Scripts/python.exe'
 runtime=BASE/'.python/cpython-3.13-windows-x86_64-none/python.exe'
 if not runtime.is_file():raise RuntimeError('Project-local Python 3.13 is absent')
 state('core-dependencies')
 if not corepy.exists():run(['uv','venv','--python',runtime,core/'.venv'],core)
 locked=BASE/'core-locked-requirements.txt'
 locked.write_text(run(['uv','export','--frozen','--no-dev','--no-emit-project','--format','requirements-txt'],core,capture=True),encoding='utf-8')
 run(['uv','pip','sync','--python',corepy,'--require-hashes',locked],core,timeout=2400)
 run(['uv','pip','install','--python',corepy,'--no-deps','--editable',core],core)
 # Upstream plugin dependency declarations, not reimplemented dependencies.
 run(['uv','pip','install','--python',corepy,'-r',core/'gsuid_core/plugins/GenshinUID/pyproject.toml','-r',core/'gsuid_core/plugins/StarRailUID/pyproject.toml','-r',core/'gsuid_core/plugins/ZZZeroUID/pyproject.toml'],core)
 run(['uv','pip','check','--python',corepy],core)
 (BASE/'core-resolved.txt').write_text(run(['uv','pip','freeze','--python',corepy],core,capture=True),encoding='utf-8')
 state('adapter-dependencies')
 botenv=BASE/'nonebot-env';botpy=botenv/'Scripts/python.exe'
 if not botpy.exists():run(['uv','venv','--system-site-packages','--python',sys.executable,botenv])
 from importlib.metadata import version
 constraints=BASE/'nonebot-base-constraints.txt'
 constraints.write_text('\n'.join(n+'=='+version(n) for n in ['nonebot2','nonebot-adapter-onebot','httpx','Pillow','uvicorn','loguru']),encoding='utf-8')
 run(['uv','pip','install','--python',botpy,'--constraint',constraints,'nonebot-adapter-onebot==2.4.6',BASE/'nonebot-genshinuid'])
 run(['uv','pip','check','--python',botpy])
 (BASE/'adapter-resolved.txt').write_text(run(['uv','pip','freeze','--python',botpy],capture=True),encoding='utf-8')
 manifest={'installed':datetime.datetime.now().isoformat(),'repositories':{k:{'url':'https://github.com/'+v[0],'branch':v[1],'commit':v[2]} for k,v in SPECS.items()},'core_python':str(corepy),'bot_python':str(botpy),'activated':False}
 (BASE/'upstream-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 state('ready-for-configuration',state='completed')
except Exception as e:
 with LOG.open('a',encoding='utf-8') as f:traceback.print_exc(file=f)
 state('failed',state='failed',error=str(e))
 sys.exit(1)
