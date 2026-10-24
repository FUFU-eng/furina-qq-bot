"""把旧项目（D:\\old-qq-bot）的数据导入 v2 的 SQLite 数据库。

只读旧项目，绝不修改旧文件。默认只预览（dry-run），加 --apply 才真正写入。

用法：
    python tools/migrate.py                       # 预览
    python tools/migrate.py --apply               # 导入（已有数据库会先备份）
    python tools/migrate.py --old D:\\old-qq-bot --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from furina.core import db, paths  # noqa: E402

# 旧代码里写死的默认值：旧 .env 没写这两个键时，旧版就是用它们。迁移时补进 v2 .env，行为不变。
_LEGACY_ENV_DEFAULTS = {
    "AI_CHAT_GROUP_OWNER_QQ": "200000001",
    "AI_CHAT_SPECIAL_QQ": "200000002",
    "MAIN_GROUP_ID": "100000001",     # 强化：大群强化后自动转发到小群
    "SUB_GROUP_ID": "100000002",
    "MIYOUSHE_GROUPS": "100000001",
    "FENGSHEN_GROUPS": "100000001,100000002",
    "RELAY_SOURCE_GROUP": "100000001",  # 群主发图自动转发
    "RELAY_TARGET_GROUP": "100000002",
    "NAPCAT_DIR": r"E:\NapCat",          # 旧面板写死的 NapCat 目录（v2 面板从 .env 读取）
    "RELAY_OWNER_QQ": "200000001",
}
_DROP_ENV_PREFIXES = ("JM_",)
_DROP_FEATURES = {"jm"}


def _read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"  ! 读取失败 {path}: {e}")
        return None


def _first(*cands: Path) -> Path | None:
    for p in cands:
        if p and p.exists():
            return p
    return None


class Migrator:
    def __init__(self, old_root: Path, apply: bool):
        self.old = old_root
        self.bot = old_root / "bot"
        self.shared = self.bot / "data" / "_shared"
        self.apply = apply
        self.report: list[str] = []

    def log(self, msg: str) -> None:
        print(msg)
        self.report.append(msg)

    # ---------- .env ----------
    def env(self) -> None:
        src = self.bot / ".env"
        dst = paths.ENV_PATH
        if not src.exists():
            self.log("[.env] 旧 .env 不存在，跳过")
            return
        lines = src.read_text(encoding="utf-8").splitlines()
        kept, dropped, keys = [], 0, set()
        for ln in lines:
            k = ln.split("=", 1)[0].strip() if "=" in ln and not ln.lstrip().startswith("#") else ""
            if k and k.startswith(_DROP_ENV_PREFIXES):
                dropped += 1
                continue
            if k:
                keys.add(k)
            kept.append(ln)
        added = [f"{k}={v}" for k, v in _LEGACY_ENV_DEFAULTS.items() if k not in keys]
        if "ASSETS_ROOT" not in keys:
            # 浏览器配置 / Real-ESRGAN / 站点 cookies 暂时继续使用旧项目里的原件（只读引用，不复制不修改）
            added.append(f"ASSETS_ROOT={self.old}")
        if added:
            kept += ["", "# v2 迁移：以下键旧版写死在代码里，这里显式写出，行为保持不变"] + added
        self.log(f"[.env] 保留 {len(keys)} 个键，去掉 JM 相关 {dropped} 行，补充 {len(added)} 个旧默认键"
                 + (f"（{', '.join(a.split('=')[0] for a in added)}）" if added else ""))
        if dst.exists():
            self.log("[.env] v2 已有 .env，不覆盖（如需重新生成请先手动改名）")
            return
        if self.apply:
            dst.write_text("\n".join(kept) + "\n", encoding="utf-8")

    # ---------- 功能权限 ----------
    def permissions(self) -> None:
        src = _first(self.shared / "feature_permissions.json", self.bot / "feature_permissions.json")
        data = _read_json(src) if src else None
        groups = (data or {}).get("groups") if isinstance(data, dict) else None
        if not isinstance(groups, dict):
            self.log("[权限] 无旧数据")
            return
        out = {}
        for gid, g in groups.items():
            if not isinstance(g, dict):
                continue
            feats = {k: bool(v) for k, v in (g.get("features") or {}).items() if k not in _DROP_FEATURES}
            out[str(gid)] = {"enabled": bool(g.get("enabled", True)), "features": feats}
        self.log(f"[权限] {len(out)} 个群")
        if self.apply and out:
            db.put_many("perm", out)

    # ---------- 次数与统计 ----------
    def usage(self) -> None:
        src = _first(self.shared / "daily_limits.json", self.bot / "daily_limits.json")
        data = _read_json(src) if src else None
        if not isinstance(data, dict):
            self.log("[统计] 无旧数据")
            return
        rows = []
        day = str(data.get("date") or time.strftime("%Y-%m-%d"))
        for kind in ("chat", "poke"):
            for key, n in (data.get(kind) or {}).items():
                gid, _, uid = str(key).partition(":")
                rows.append((day, kind, gid, uid, int(n or 0)))
        def add_stats(d, stats):
            for gid, kinds in (stats or {}).items():
                for k, n in (kinds or {}).items():
                    rows.append((d, f"stat:{k}", str(gid), "", int(n or 0)))
        add_stats(day, data.get("stats"))
        for entry in data.get("history") or []:
            if isinstance(entry, dict) and entry.get("date"):
                add_stats(str(entry["date"]), entry.get("stats"))
        self.log(f"[统计] {len(rows)} 条计数（含近 {len(data.get('history') or [])} 天历史）")
        if self.apply and rows:
            with db.transaction() as conn:
                conn.executemany(
                    "INSERT INTO usage(day, kind, group_id, user_id, count) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(day, kind, group_id, user_id) DO UPDATE SET count=excluded.count",
                    rows,
                )

    # ---------- AI 聊天记忆 ----------
    def _doc_map(self, label: str, src: Path, ns: str, pick=lambda d: d) -> None:
        data = _read_json(src)
        items = pick(data) if isinstance(data, dict) else None
        if not isinstance(items, dict) or not items:
            self.log(f"[{label}] 无旧数据（{src.name}）")
            return
        self.log(f"[{label}] {len(items)} 条")
        if self.apply:
            db.put_many(ns, {str(k): v for k, v in items.items()})

    def chat(self) -> None:
        plugins = self.bot / "plugins"
        self._doc_map("群词条/外号", plugins / "learned.json", "learned")
        self._doc_map("好感", plugins / "favor.json", "favor", lambda d: d.get("users"))
        self._doc_map("每日纪要", plugins / "digest.json", "digest",
                      lambda d: {k: v for k, v in d.items() if isinstance(v, dict)})
        emo = _read_json(plugins / "emotion.json")
        if isinstance(emo, dict):
            self.log(f"[心情] 当前心情 {emo.get('mood')}，日记 {len(emo.get('journal') or [])} 天")
            if self.apply:
                db.put("emotion", "state", emo)
        buf = _read_json(plugins / "digest_buffer.json")
        if isinstance(buf, dict):
            n = sum(len(v) for v in (buf.get("groups") or {}).values() if isinstance(v, list))
            self.log(f"[纪要缓冲] {n} 条当天消息")
            if self.apply:
                db.put("digest_buffer", "buffer", buf)

    def persona(self) -> None:
        for key, name in (("roles", "roles.json"), ("profiles", "bot_qq_profiles.json"),
                          ("shared_lore", "shared_lore.json")):
            data = _read_json(self.bot / name)
            if data is None:
                self.log(f"[角色] {name} 不存在")
                continue
            size = len(data.get("roles") or data.get("items") or []) if isinstance(data, dict) else len(data)
            self.log(f"[角色] {name}: {size} 条")
            if self.apply:
                db.put("persona", key, data)

    # ---------- Pixiv ----------
    def pixiv(self) -> None:
        cfg = _read_json(self.shared / "pixiv_settings.json")
        if isinstance(cfg, dict):
            self.log(f"[Pixiv] 设置：总开关={'开' if cfg.get('enabled') else '关'}，推送时间 {cfg.get('times')}")
            if self.apply:
                db.put("pixiv", "settings", cfg)
        else:
            self.log("[Pixiv] 无设置文件（按默认关闭）")
        src = self.shared / "pixiv"
        dst = paths.DATA_DIR / "pixiv"
        if not src.exists():
            return
        files = [src / "search_recent.json"] + sorted(src.glob("board-*.json"))
        files = [f for f in files if f.exists()]
        receipts = src / "jobs.sqlite3"
        self.log(f"[Pixiv] 发送回执库{'存在' if receipts.exists() else '不存在'}，榜单缓存/点图去重 {len(files)} 个文件")
        if not self.apply:
            return
        dst.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dst / f.name)
        if receipts.exists():
            import sqlite3

            target = dst / "jobs.sqlite3"
            if target.exists():
                self.log("[Pixiv] v2 已有回执库，不覆盖")
            else:
                with sqlite3.connect(receipts) as a, sqlite3.connect(target) as b:
                    a.backup(b)  # 用 SQLite 在线备份，WAL 里的内容也会带上

    # ---------- 阶段 3：米游社 / 封神榜 / 成员缓存 / 定时提醒 ----------
    def _doc(self, label: str, src: Path | None, key: str, summary) -> None:
        data = _read_json(src) if src else None
        if data is None:
            self.log(f"[{label}] 无旧数据，跳过")
            return
        self.log(f"[{label}] {src.name}：{summary(data)}")
        if self.apply:
            db.put("files", key, data)

    def phase3(self) -> None:
        for name, what in (("miyoushe_seen.json", lambda d: f"{len(d)} 个版块、{sum(len(v) for v in d.values())} 条已推送记录"),
                           ("miyoushe_rate.json", lambda d: f"上传速率估计 {float(d.get('rate') or 0) / 1048576:.1f} MB/s"),
                           ("miyoushe_known_groups.json", lambda d: f"{len(d)} 个已建基线的群"),
                           ("miyoushe_baselines.json", lambda d: f"{len(d)} 个群基线")):
            self._doc("米游社", _first(self.bot / name), name, what)
        self._doc("封神榜", _first(self.shared / "fengshen_data.json", self.bot / "plugins" / "fengshen_data.json"),
                  "fengshen_data.json", lambda d: f"{len(d)} 个群的统计/历史榜单")
        self._doc("成员缓存", _first(self.shared / "group_members.json", self.bot / "group_members.json"),
                  "group_members.json", lambda d: f"{len(d.get('groups') or {})} 个群")
        rdir = self.shared / "reminder"
        cfg = _read_json(rdir / "settings.json")
        if isinstance(cfg, dict):
            self.log(f"[定时提醒] 设置：{'开' if cfg.get('enabled') else '关'}")
            if self.apply:
                db.put("reminder", "settings", cfg)
        src = rdir / "reminders.sqlite3"
        if not src.exists():
            self.log("[定时提醒] 无提醒库，跳过")
            return
        import sqlite3

        with sqlite3.connect(src) as a:
            a.row_factory = sqlite3.Row
            try:
                rows = [dict(r) for r in a.execute("SELECT * FROM reminders")]
                schema = [r[0] for r in a.execute(
                    "SELECT sql FROM sqlite_master WHERE tbl_name='reminders' AND sql IS NOT NULL")]
            except sqlite3.Error:
                rows, schema = [], []
        active = sum(1 for r in rows if r.get("state") == "active")
        self.log(f"[定时提醒] 提醒 {len(rows)} 条（进行中 {active}）")
        if not self.apply or not rows:
            return
        # 与插件同一张表结构（取旧库的建表语句）；插入主数据库，已有 id 不覆盖
        with sqlite3.connect(paths.DB_PATH, timeout=10) as b:
            for sql in schema:
                b.execute(sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
                             .replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1))
            cols = list(rows[0])
            b.executemany(f"INSERT OR IGNORE INTO reminders ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          [[r[c] for c in cols] for r in rows])

    def wallpapers(self) -> None:
        """旧面板壁纸库 → SQLite panel/wallpapers；图片按原路径引用，不复制。"""
        src = _first(self.old / "wallpaper_library.json", self.bot / "wallpaper_library.json")
        data = _read_json(src) if src else None
        if not isinstance(data, dict):
            self.log("[壁纸] 无旧壁纸库")
            return
        wps = [p for p in (data.get("wallpapers") or []) if isinstance(p, str)]
        present = [p for p in wps if Path(p).exists()]
        pl = [p for p in (data.get("playlist") or []) if p in present] or list(present)
        try:
            interval = max(3, int(data.get("interval_seconds", 30) or 30))
        except (TypeError, ValueError):
            interval = 30
        self.log(f"[壁纸] {len(present)}/{len(wps)} 张可用，轮播 {len(pl)} 张，间隔 {interval} 秒")
        if self.apply and db.get("panel", "wallpapers") is None:
            db.put("panel", "wallpapers", {"interval_seconds": interval, "wallpapers": present, "playlist": pl})

    def search_driver(self) -> None:
        src = self.shared / "search_driver"
        if not src.exists():
            return
        dst = paths.DATA_DIR / "search_driver"
        self.log(f"[搜图] Edge 驱动缓存：{', '.join(p.name for p in src.iterdir())}")
        if self.apply and not dst.exists():
            shutil.copytree(src, dst)

    def run(self) -> None:
        if not self.bot.exists():
            raise SystemExit(f"找不到旧项目目录：{self.bot}")
        paths.ensure_dirs()
        if self.apply and paths.DB_PATH.exists():
            backup = paths.DB_PATH.with_name(f"furina.before-migrate-{time.strftime('%Y%m%d-%H%M%S')}.db")
            db.close()
            shutil.copy2(paths.DB_PATH, backup)
            self.log(f"已备份现有数据库 → {backup.name}")
        self.log(("== 正式导入 ==" if self.apply else "== 预览（未写入，确认后加 --apply） ==")
                 + f"  旧项目: {self.old}")
        self.env()
        self.permissions()
        self.usage()
        self.chat()
        self.persona()
        self.pixiv()
        self.search_driver()
        self.phase3()
        self.wallpapers()
        if self.apply:
            db.put("meta", "migrated_from_v1", {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "old": str(self.old)})
            self.log("完成。")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="旧项目数据导入 v2")
    ap.add_argument("--old", default=r"D:\old-qq-bot", help="旧项目根目录")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只预览）")
    args = ap.parse_args(argv)
    Migrator(Path(args.old), args.apply).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
