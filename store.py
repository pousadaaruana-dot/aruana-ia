"""SQLite pilot storage. Use a persistent volume for survival across Render deploys."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def db():
    path = os.getenv('ARU_DB_PATH', 'data/aru.sqlite3')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def init():
    with db() as con:
        con.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS conversations (
            sender TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'aru',
            owner TEXT, epoch INTEGER NOT NULL DEFAULT 0,
            facts TEXT NOT NULL DEFAULT '{}', reason TEXT,
            last_inbound REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, sender TEXT NOT NULL, role TEXT NOT NULL,
            body TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'text',
            state TEXT NOT NULL, epoch INTEGER NOT NULL DEFAULT 0,
            created REAL NOT NULL, provider_id TEXT, error TEXT);
        CREATE INDEX IF NOT EXISTS message_queue ON messages(state, created);
        CREATE INDEX IF NOT EXISTS message_history ON messages(sender, created);
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY, sender TEXT, actor TEXT, action TEXT, created REAL);
        ''')
        # A process may have died after the provider accepted a send. Never replay it.
        con.execute("UPDATE messages SET state='uncertain', error='process_restarted' WHERE state IN ('processing','sending')")


def conversation(sender):
    with db() as con:
        row = con.execute('SELECT * FROM conversations WHERE sender=?', (sender,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data['facts'] = json.loads(data['facts'])
    return data


def receive(sender, mid, text, kind, timestamp):
    now = time.time()
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM messages WHERE id=?', (mid,)).fetchone():
            return 'duplicate'
        count = con.execute("SELECT count(*) FROM messages WHERE sender=? AND role='user' AND created>?",
                            (sender, now - 60)).fetchone()[0]
        if count >= 10:
            return 'rate_limit'
        con.execute('INSERT OR IGNORE INTO conversations(sender,last_inbound,updated) VALUES (?,?,?)',
                    (sender, timestamp, now))
        con.execute('UPDATE conversations SET last_inbound=max(last_inbound,?), updated=? WHERE sender=?',
                    (timestamp, now, sender))
        row = con.execute('SELECT * FROM conversations WHERE sender=?', (sender,)).fetchone()
        state = 'queued' if row['mode'] == 'aru' else 'human'
        con.execute('INSERT INTO messages(id,sender,role,body,kind,state,epoch,created) VALUES (?,?,?,?,?,?,?,?)',
                    (mid, sender, 'user', text[:4000], kind, state, row['epoch'], now))
        return state


def history(sender, before=None):
    with db() as con:
        rows = con.execute('''SELECT role,body FROM messages WHERE sender=? AND created<=?
            AND (role='user' OR (role IN ('assistant','human') AND provider_id IS NOT NULL))
            ORDER BY created DESC LIMIT 20''', (sender, before or time.time())).fetchall()
    return [{'role': 'user' if r['role'] == 'user' else 'assistant', 'content': r['body']}
            for r in reversed(rows)]


def audit(con, sender, actor, action):
    con.execute('INSERT INTO audit(sender,actor,action,created) VALUES (?,?,?,?)',
                (sender, actor, action, time.time()))
