import json
import os
from pathlib import Path
import tempfile

from ...core import db, paths

DATA = paths.DATA_DIR
CONFIG = None  # 设置存 SQLite（docs: reminder/settings）；传入路径时读写文件（测试用）
DEFAULTS = {'enabled': False, 'natural_language': True, 'max_active': 10,
            'cooldown_seconds': 10, 'grace_minutes': 30}


def validate(value):
    cfg = dict(DEFAULTS)
    if not isinstance(value, dict):
        raise ValueError('提醒配置必须是对象')
    cfg.update({k: v for k, v in value.items() if k in cfg})
    for key in ('enabled', 'natural_language'):
        if not isinstance(cfg[key], bool):
            raise ValueError('开关必须为布尔值')
    for key, low, high in [('max_active',1,50),('cooldown_seconds',5,300),('grace_minutes',1,30)]:
        if isinstance(cfg[key],bool) or not isinstance(cfg[key],int) or not low <= cfg[key] <= high:
            raise ValueError(f'{key} 必须为 {low}–{high} 的整数')
    return cfg


def load(path=CONFIG):
    if path is None:
        data = db.get('reminder', 'settings')
        return validate({} if data is None else data)
    if not Path(path).exists():
        return validate({})
    return validate(json.loads(Path(path).read_text(encoding='utf-8')))


def save(value, path=CONFIG):
    cfg=validate(value)
    if path is None:
        db.put('reminder', 'settings', cfg)
        return cfg
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='settings-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:
            json.dump(cfg,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return cfg
