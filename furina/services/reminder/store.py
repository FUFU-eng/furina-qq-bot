"""SQLite ownership checks, atomic claims and bounded completed-task history."""
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3

from .domain import CST, Clarify, next_occurrence
from ...core import paths
from datetime import datetime


class Store:
    def __init__(self,path=None):
        # 默认与主数据库同一个文件（表名 reminders），传入路径时单独建库（测试用）
        self.path=Path(path) if path is not None else paths.DB_PATH;self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, gid TEXT NOT NULL, uid TEXT NOT NULL,
                text TEXT NOT NULL, schedule TEXT NOT NULL, next_at REAL NOT NULL,
                state TEXT NOT NULL DEFAULT 'active', last_status TEXT NOT NULL DEFAULT 'created',
                created REAL NOT NULL, updated REAL NOT NULL, claim_at REAL,
                source_key TEXT UNIQUE NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS reminder_due ON reminders(state,next_at)')
            db.execute('CREATE INDEX IF NOT EXISTS reminder_owner ON reminders(gid,uid,state)')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def create(self,gid,uid,proposal,now,source_key,limit=10):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT * FROM reminders WHERE source_key=? AND gid=? AND uid=?',
                                (source_key,str(gid),str(uid))).fetchone()
            if existing:return dict(existing),False
            count=db.execute("SELECT COUNT(*) FROM reminders WHERE gid=? AND uid=? AND state IN ('active','sending')",
                             (str(gid),str(uid))).fetchone()[0]
            if count>=limit:raise Clarify(f'你在本群已有 {limit} 条有效提醒，请先取消不需要的提醒。')
            cur=db.execute('''INSERT INTO reminders (gid,uid,text,schedule,next_at,created,updated,source_key)
                              VALUES (?,?,?,?,?,?,?,?)''',(str(gid),str(uid),proposal.text,
                            json.dumps(proposal.schedule()),proposal.next_at,now.timestamp(),now.timestamp(),source_key))
            return dict(db.execute('SELECT * FROM reminders WHERE id=?',(cur.lastrowid,)).fetchone()),True

    def list_own(self,gid,uid):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT * FROM reminders WHERE gid=? AND uid=?
                ORDER BY CASE WHEN state IN ('active','sending') THEN 0 ELSE 1 END,
                CASE WHEN state IN ('active','sending') THEN next_at ELSE -updated END LIMIT 55''',(str(gid),str(uid)))]

    def cancel(self,gid,uid,rid,now):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if rid=='latest':
                row=db.execute("SELECT * FROM reminders WHERE gid=? AND uid=? AND state IN ('active','sending') ORDER BY id DESC LIMIT 1",
                               (str(gid),str(uid))).fetchone()
            else:
                row=db.execute('SELECT * FROM reminders WHERE id=? AND gid=? AND uid=?',
                               (rid,str(gid),str(uid))).fetchone()
            if not row or row['state'] not in ('active','sending'):return None
            db.execute("UPDATE reminders SET state='cancelled',last_status='cancelled',updated=? WHERE id=?",(now.timestamp(),row['id']))
            return dict(row)

    def due(self,now,limit=100):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM reminders WHERE state='active' AND next_at<=? ORDER BY next_at,id LIMIT ?",
                                               (now.timestamp(),limit))]

    def claim(self,row,now):
        with self.connect() as db:
            return bool(db.execute("UPDATE reminders SET state='sending',claim_at=?,updated=? WHERE id=? AND state='active' AND next_at=?",
                                   (now.timestamp(),now.timestamp(),row['id'],row['next_at'])).rowcount)

    def claimed(self,row):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM reminders WHERE id=? AND state='sending' AND next_at=?",(row['id'],row['next_at'])).fetchone() is not None

    def advance(self,row,now,status,expected='sending'):
        schedule=json.loads(row['schedule']);next_at=next_occurrence(schedule,now)
        with self.connect() as db:
            return bool(db.execute('''UPDATE reminders SET state=?,last_status=?,next_at=?,claim_at=NULL,updated=?
                WHERE id=? AND state=? AND next_at=?''',('active' if next_at is not None else 'done',status,
                   next_at if next_at is not None else row['next_at'],now.timestamp(),row['id'],expected,row['next_at'])).rowcount)

    def recover(self,now):
        # A prior process may have sent successfully before losing its acknowledgement.
        # Do NOT replay an orphaned sending occurrence. Allow 5 minutes for live senders.
        with self.connect() as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM reminders WHERE state='sending' AND claim_at<?",(now.timestamp()-300,))]
            db.execute("DELETE FROM reminders WHERE state IN ('done','cancelled') AND updated<?",(now.timestamp()-30*86400,))
        for row in rows:self.advance(row,now,'unknown_after_restart')
