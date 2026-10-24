"""Small persistent metadata, batch claims and bounded disposable originals."""
import asyncio
import hashlib
import os
import json
from contextlib import contextmanager
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from .settings import DATA, atomic_json


class Store:
    def __init__(self, root=DATA / 'pixiv'):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache = self.root / 'temporary'
        self.cache.mkdir(exist_ok=True)
        self.db = self.root / 'jobs.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, status TEXT, detail TEXT, updated REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS work_receipts (scope TEXT, pid TEXT, updated REAL, PRIMARY KEY(scope,pid))')
            db.execute('CREATE TABLE IF NOT EXISTS chunk_receipts (scope TEXT,pid TEXT,first INTEGER,last INTEGER,signature TEXT,state TEXT,updated REAL,PRIMARY KEY(scope,pid,first,last))')
            # A claimed/in-progress job is deliberately not replayed after a crash.
            db.execute('DELETE FROM jobs WHERE updated < ?', (time.time() - 35 * 86400,))
            db.execute('DELETE FROM work_receipts WHERE updated < ?', (time.time()-35*86400,))
            db.execute("DELETE FROM chunk_receipts WHERE state='sent' AND updated < ?", (time.time()-35*86400,))
        self._last_prune = time.time()
        self.cleanup()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, key, status='started', detail=''):
        with self.connect() as db:
            cur = db.execute('INSERT OR IGNORE INTO jobs VALUES (?, ?, ?, ?)',
                             (key, status, detail[:500], time.time()))
            return bool(cur.rowcount)

    def finish(self, key, status, detail):
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?, detail=?, updated=? WHERE key=?',
                       (status, detail[:500], time.time(), key))

    def recent(self, limit=20):
        with self.connect() as db:
            return db.execute('SELECT key,status,detail FROM jobs ORDER BY updated DESC LIMIT ?', (limit,)).fetchall()

    def work_sent(self, scope, pid):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM work_receipts WHERE scope=? AND pid=?',(scope,str(pid))).fetchone() is not None

    def work_uncertain(self, scope, pid):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM chunk_receipts WHERE scope=? AND pid=? AND state!='sent' LIMIT 1",(scope,str(pid))).fetchone() is not None

    def mark_work_sent(self, scope, pid):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO work_receipts VALUES (?,?,?)',(scope,str(pid),time.time()))

    def begin_chunk(self, scope, pid, first, last, signature):
        """Return False only for an identical *acknowledged* chunk; unknown is a stop."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute('SELECT first,last,signature,state FROM chunk_receipts WHERE scope=? AND pid=? AND first<=? AND last>=?',
                            (scope,str(pid),last,first)).fetchall()
            if rows:
                if len(rows)==1 and rows[0]==(first,last,signature,'sent'):return False
                raise RuntimeError('SEND_RECEIPT_UNCERTAIN: 有未确认或分块变化的记录，需要人工核查，不自动重发')
            db.execute('INSERT INTO chunk_receipts VALUES (?,?,?,?,?,?,?)',(scope,str(pid),first,last,signature,'sending',time.time()))
            return True

    def finish_chunk(self, scope, pid, first, last, state):
        if state not in ('sent','unknown'):raise ValueError('invalid receipt state')
        with self.connect() as db:
            cur=db.execute('UPDATE chunk_receipts SET state=?,updated=? WHERE scope=? AND pid=? AND first=? AND last=? AND state=?',
                           (state,time.time(),scope,str(pid),first,last,'sending'))
            if cur.rowcount!=1:raise RuntimeError('SEND_RECEIPT_UPDATE_FAILED')

    async def download_cached(self, client, url, path, max_bytes, cap):
        """Reuse verified originals between groups, within this plugin's cache quota."""
        key=hashlib.sha256(url.encode()).hexdigest()
        asset=self.cache/('asset-'+key);meta=asset.with_name(asset.name+'.json')
        path=Path(path)
        def digest(file):
            h=hashlib.sha256()
            with file.open('rb') as f:
                for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
            return h.hexdigest()
        def cached():
            try:
                m=json.loads(meta.read_text(encoding='utf-8'))
                size=asset.stat().st_size
                if not 0<size<=max_bytes or m.get('size')!=size or m.get('sha256')!=digest(asset):return None
                try:os.link(asset,path)
                except OSError:shutil.copyfile(asset,path)
                os.utime(asset,None)
                return size
            except (OSError,ValueError,TypeError):return None
        hit=await asyncio.to_thread(cached)
        if hit is not None:return hit
        size=await client.download(url,path,max_bytes)
        # Optional acceleration only: cache errors never turn a valid download into failure.
        def remember():
            try:
                if self.usage()+size>cap:return
                if asset.exists():asset.unlink()
                try:os.link(path,asset)
                except OSError:shutil.copyfile(path,asset)
                atomic_json(meta,{'size':size,'sha256':digest(asset)})
            except (OSError,ValueError):pass
        await asyncio.to_thread(remember)
        return size

    @staticmethod
    def _stat(p):
        """stat() that tolerates files removed concurrently (cache writer thread / cleanup)."""
        try:
            return p.stat()
        except OSError:
            return None

    def evict_assets(self, reserve, cap):
        """Only private original-cache files; live work paths/hardlinks remain untouched.

        Usage is computed once and decremented per eviction (was O(n^2) stat calls every
        10s scheduler tick), and files vanishing mid-scan no longer raise out of send/cleanup.
        """
        assets=[]
        for p in self.cache.glob('asset-*'):
            if p.suffix=='.json':continue
            st=self._stat(p)
            if st is not None:assets.append((st.st_mtime,st.st_size,p))
        assets.sort(key=lambda item:item[0])
        used=self.usage()
        now=time.time()
        for mtime,size,p in assets:
            if used+reserve<=cap and now-mtime<10800:continue
            try:
                p.unlink(missing_ok=True);p.with_name(p.name+'.json').unlink(missing_ok=True)
                used-=size
            except OSError:pass

    def _search_recent_table(self, db):
        """点图去重记录存 jobs.sqlite3；首次使用时把旧的 search_recent.json 导入后改名备份。"""
        db.execute('CREATE TABLE IF NOT EXISTS search_recent (gid TEXT, pid TEXT, updated REAL, PRIMARY KEY(gid,pid))')
        legacy = self.root / 'search_recent.json'
        if not legacy.exists():
            return
        try:
            data = json.loads(legacy.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        now = time.time()
        if isinstance(data, dict):
            for g, pids in data.items():
                if isinstance(pids, list):
                    rows = [(str(g), str(p), now - (len(pids) - n) * 1e-3) for n, p in enumerate(pids)]
                    db.executemany('INSERT OR IGNORE INTO search_recent VALUES (?,?,?)', rows)
        try:
            legacy.replace(legacy.with_name('search_recent.json.imported'))
        except OSError:
            pass

    def recent_search_pids(self, gid, limit=120):
        """本群最近点图发过的 PID，用于换一批新图（读失败就当没记过）。"""
        try:
            with self.connect() as db:
                self._search_recent_table(db)
                rows = db.execute('SELECT pid FROM search_recent WHERE gid=? ORDER BY updated DESC LIMIT ?',
                                  (str(gid), int(limit))).fetchall()
            return [r[0] for r in reversed(rows)]
        except sqlite3.Error:
            return []

    def remember_search_pids(self, gid, pids, limit=120):
        try:
            with self.connect() as db:
                self._search_recent_table(db)
                now = time.time()
                for n, pid in enumerate(pids):
                    db.execute('INSERT INTO search_recent VALUES (?,?,?) ON CONFLICT(gid,pid) DO UPDATE SET updated=excluded.updated',
                               (str(gid), str(pid), now + n * 1e-3))
                db.execute('DELETE FROM search_recent WHERE gid=? AND pid NOT IN '
                           '(SELECT pid FROM search_recent WHERE gid=? ORDER BY updated DESC LIMIT ?)',
                           (str(gid), str(gid), int(limit)))
        except sqlite3.Error:
            pass

    async def ranking(self, client, board):
        path = self.root / ('board-' + board.key + '.json')
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                ranks = [x['rank'] for x in data]
                if (isinstance(data, list) and 147 <= len(data) <= 150
                        and ranks == sorted(set(ranks))
                        and all(isinstance(r, int) and 1 <= r <= 150 for r in ranks)):
                    return data
            except (ValueError, KeyError, TypeError):
                pass
        data = await client.ranking(board)
        atomic_json(path, data)
        for old in sorted(self.root.glob('board-*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[8:]:
            old.unlink(missing_ok=True)
        return data

    def temp_dir(self):
        p = self.cache / ('work-' + uuid.uuid4().hex)
        p.mkdir()
        return p

    def usage(self):
        total=0
        for p in self.cache.rglob('*'):
            st=self._stat(p)
            if st is not None and not p.is_dir():
                total+=st.st_size
        return total

    def cleanup(self, age_seconds=3600):
        # Only our private generated work directories; never QQ/NapCat/browser caches.
        now = time.time()
        if now - self._last_prune > 86400:
            with self.connect() as db:
                db.execute('DELETE FROM jobs WHERE updated < ?', (now - 35 * 86400,))
            self._last_prune = now
        self.evict_assets(0,2**63-1)
        for p in self.cache.glob('work-*'):
            try:
                stamps = [st.st_mtime for st in (self._stat(q) for q in p.rglob('*')) if st is not None]
                newest = max([p.stat().st_mtime] + stamps)
                if now - newest >= age_seconds:
                    shutil.rmtree(p)
            except OSError:
                pass  # Windows may hold a file open; retry on the next cleanup pass.

    def remove_temp(self, path):
        path = Path(path)
        if path.parent.resolve() != self.cache.resolve() or not path.name.startswith('work-'):
            raise ValueError('Refusing cleanup outside Pixiv temporary directory')
        for attempt in range(3):
            try:
                if path.exists():
                    shutil.rmtree(path)
                return
            except OSError:
                time.sleep(.15 * (attempt + 1))
        # Remaining locked files are counted against quota and swept after expiry.
