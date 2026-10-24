"""Version-checked local Edge driver first; bounded isolated installation fallback.

Never deletes global .wdm locks, profiles or cookies. Driver installs run in a
child process with a deadline, using a private temporary cache, not the legacy
unowned O_EXCL lock that can survive a crash forever.
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

from ...core import paths as _paths
CACHE = _paths.DATA_DIR / 'search_driver'
_lock = threading.Lock()

class DriverSetupError(RuntimeError):pass


def _version(text):
    match=re.search(r'\b(\d+\.\d+\.\d+\.\d+)\b',text or '')
    return tuple(map(int,match[1].split('.'))) if match else None


def browser_version():
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for access in (winreg.KEY_READ,winreg.KEY_READ|winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive,r'SOFTWARE\Microsoft\Edge\BLBeacon',0,access) as key:
                    version=_version(winreg.QueryValueEx(key,'version')[0])
                    if version:return version
            except OSError:pass
    raise DriverSetupError('EDGE_VERSION_UNKNOWN: 无法确认本机 Edge 版本，未盲目安装驱动')


def driver_version(path):
    try:
        p=subprocess.run([str(path),'--version'],capture_output=True,text=True,
                         timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return _version(p.stdout) if p.returncode==0 else None
    except (OSError,subprocess.SubprocessError):return None


def cached_driver(version, roots=None):
    roots=roots or [CACHE,Path.home()/'.wdm'/'drivers'/'edgedriver']
    matches=[]
    for root in roots:
        if not root.exists():continue
        for path in root.rglob('msedgedriver.exe'):
            v=_version(path.parent.name)
            if v and v[:3]==version[:3]:matches.append((v,path))
    for _,path in sorted(matches,key=lambda pair:pair[0],reverse=True):
        actual=driver_version(path)
        if actual and actual[:3]==version[:3]:return str(path)
    return None


def resolve_edge_driver():
    with _lock:
        version=browser_version()
        hit=cached_driver(version)
        if hit:return hit
        CACHE.mkdir(parents=True,exist_ok=True)
        # A fresh *private* cache cannot inherit the global orphaned lock.
        with tempfile.TemporaryDirectory(prefix='install-',dir=CACHE) as tmp:
            code=('import sys,json; '
                  'from webdriver_manager.microsoft import EdgeChromiumDriverManager; '
                  'from webdriver_manager.core.driver_cache import DriverCacheManager; '
                  'p=EdgeChromiumDriverManager(cache_manager=DriverCacheManager(root_dir=sys.argv[1])).install(); '
                  'print(json.dumps({"driver":p}))')
            try:
                result=subprocess.run([sys.executable,'-c',code,tmp],capture_output=True,text=True,
                                      timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except subprocess.TimeoutExpired:
                raise DriverSetupError('DRIVER_SETUP_TIMEOUT: 未找到兼容本地驱动，下载等待超过45秒；请检查驱动下载网络') from None
            if result.returncode:
                raise DriverSetupError('DRIVER_SETUP_FAILED: 本地无兼容驱动且驱动下载失败；请检查网络或更新 Edge 驱动')
            try:
                source=Path(json.loads(result.stdout.strip().splitlines()[-1])['driver']).resolve()
                if not source.is_relative_to(Path(tmp).resolve()):raise ValueError('unexpected install path')
                actual=driver_version(source)
                if not actual or actual[:3]!=version[:3]:raise ValueError('incompatible driver')
            except (OSError,ValueError,IndexError,KeyError):
                raise DriverSetupError('DRIVER_VERSION_MISMATCH: 新驱动未通过本机 Edge 版本校验') from None
            dest=CACHE/'.'.join(map(str,actual))/'msedgedriver.exe';dest.parent.mkdir(exist_ok=True)
            staging=dest.with_suffix('.exe.tmp');shutil.copyfile(source,staging);os.replace(staging,dest)
            return str(dest)
