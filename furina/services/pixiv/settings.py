"""Hot-read settings; no browser cookies, account passwords or tokens."""
import json
import os
import tempfile
from pathlib import Path

from ...core import db, paths

DATA = paths.DATA_DIR          # data/pixiv/ 下放发送回执库与临时原图缓存
CONFIG = None                  # 设置存 SQLite（docs: pixiv/settings）；传入路径时仍可读写文件（测试用）
_NS, _KEY = 'pixiv', 'settings'
DEFAULTS = {
    'enabled': False,
    'times': ['09:00', '13:00', '16:00', '20:00', '23:00'],
    'interval_seconds': 4,
    'cache_mb': 256,
    'search_cooldown_seconds': 60,
    'aliases': {},
}


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def validate(data):
    import re
    result = dict(DEFAULTS)
    result.update({k: v for k, v in data.items() if k in DEFAULTS})
    if not isinstance(result['enabled'], bool):
        raise ValueError('总开关必须为布尔值')
    times = result['times']
    if not isinstance(times, list) or len(times) != 5 or any(
        not isinstance(t, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t) for t in times
    ) or times != sorted(set(times)):
        raise ValueError('请填写 5 个不重复、从早到晚的 HH:MM 时间（北京时间）')
    for key, low, high in [('interval_seconds', 2, 60), ('cache_mb', 64, 1024),
                           ('search_cooldown_seconds', 10, 3600)]:
        if isinstance(result[key], bool) or not isinstance(result[key], int) or not low <= result[key] <= high:
            raise ValueError(f'{key} 必须是 {low}–{high} 的整数')
    aliases = result['aliases']
    if not isinstance(aliases, dict) or len(aliases) > 500:
        raise ValueError('标签映射须为 JSON 对象，最多 500 项')
    for key, values in aliases.items():
        if not isinstance(key, str) or not 1 <= len(key) <= 80 or not isinstance(values, list) or not 1 <= len(values) <= 4:
            raise ValueError('标签映射格式：{"中文名称": ["日文标签", "英文标签"]}')
        if any(not isinstance(v, str) or not v.strip() or len(v) > 80 for v in values):
            raise ValueError('标签必须为非空字符串，最长 80 字')
    return result


def load(path=CONFIG):
    if path is None:
        data = db.get(_NS, _KEY)
        # 损坏/缺失时按默认（默认关闭），绝不静默开启
        return validate(dict(DEFAULTS) if data is None else data)
    if not Path(path).exists():
        return validate(dict(DEFAULTS))
    with open(path, encoding='utf-8') as f:
        return validate(json.load(f))


def save(data, path=CONFIG):
    value = validate(data)
    if path is None:
        db.put(_NS, _KEY, value)
    else:
        atomic_json(path, value)
    return value
