"""SQLite database initialization, migrations, and conservative defaults."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get('DATA_DIR', './data')).resolve()
DB_FILE = DATA_DIR / 'rss.sqlite3'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS accounts (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0,
 interval_min INTEGER NOT NULL DEFAULT 30 CHECK(interval_min BETWEEN 5 AND 10080),
 auto_tag INTEGER NOT NULL DEFAULT 1, tag_prefix TEXT NOT NULL DEFAULT '',
 retention_days INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS folders (
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
 name TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS folders_parent ON folders(account_id,parent_id);
CREATE TABLE IF NOT EXISTS feeds (
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
 title TEXT NOT NULL, url TEXT NOT NULL, site_url TEXT NOT NULL DEFAULT '',
 description TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT '',
 enabled INTEGER NOT NULL DEFAULT 1, paused INTEGER NOT NULL DEFAULT 0,
 interval_min INTEGER NOT NULL DEFAULT 0, auto_tag INTEGER NOT NULL DEFAULT 1,
 tag_prefix TEXT NOT NULL DEFAULT '', view_mode TEXT NOT NULL DEFAULT 'summary',
 etag TEXT NOT NULL DEFAULT '', last_modified TEXT NOT NULL DEFAULT '',
 last_checked INTEGER NOT NULL DEFAULT 0, last_success INTEGER NOT NULL DEFAULT 0,
 next_check INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
 failure_count INTEGER NOT NULL DEFAULT 0, last_http_status INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL, UNIQUE(account_id,url)
);
CREATE INDEX IF NOT EXISTS feeds_next ON feeds(enabled,paused,next_check);
CREATE TABLE IF NOT EXISTS articles (
 id INTEGER PRIMARY KEY, feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
 guid TEXT NOT NULL, title TEXT NOT NULL, link TEXT NOT NULL DEFAULT '',
 author TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
 content TEXT NOT NULL DEFAULT '', published INTEGER NOT NULL DEFAULT 0,
 updated INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL,
 is_read INTEGER NOT NULL DEFAULT 0, starred INTEGER NOT NULL DEFAULT 0,
 bookmarked INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
 deleted INTEGER NOT NULL DEFAULT 0, deleted_at INTEGER NOT NULL DEFAULT 0,
 folder_override INTEGER REFERENCES folders(id) ON DELETE SET NULL,
 categories TEXT NOT NULL DEFAULT '[]', enclosures TEXT NOT NULL DEFAULT '[]',
 signature TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
 UNIQUE(feed_id,guid)
);
CREATE INDEX IF NOT EXISTS articles_feed ON articles(feed_id,published DESC);
CREATE INDEX IF NOT EXISTS articles_status ON articles(deleted,archived,is_read,published DESC);
CREATE INDEX IF NOT EXISTS articles_fetched ON articles(fetched DESC);
CREATE TABLE IF NOT EXISTS tags (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
 color TEXT NOT NULL DEFAULT '#4488bb'
);
CREATE TABLE IF NOT EXISTS article_tags (
 article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
 tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
 PRIMARY KEY(article_id,tag_id)
);
CREATE INDEX IF NOT EXISTS article_tags_tag ON article_tags(tag_id,article_id);
CREATE TABLE IF NOT EXISTS rules (
 id INTEGER PRIMARY KEY, account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
 feed_id INTEGER REFERENCES feeds(id) ON DELETE CASCADE,
 name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 priority INTEGER NOT NULL DEFAULT 100,
 conditions TEXT NOT NULL, actions TEXT NOT NULL,
 created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS smart_folders (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL,
 account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
 query TEXT NOT NULL DEFAULT '', filters TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL,
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS admin (
 id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, password_hash TEXT NOT NULL,
 created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
 ip TEXT NOT NULL, attempted INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_ip ON attempts(ip,attempted);
CREATE TABLE IF NOT EXISTS logs (
 id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, level TEXT NOT NULL,
 source TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS logs_time ON logs(created_at DESC);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY, article_id INTEGER REFERENCES articles(id) ON DELETE CASCADE,
 created_at INTEGER NOT NULL, seen INTEGER NOT NULL DEFAULT 0,
 message TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS article_fts USING fts5(
 title, summary, content, author, tokenize='unicode61 remove_diacritics 2'
);
'''

def connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_FILE), timeout=20, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=20000')
    return db

@contextmanager
def transaction():
    db = connection()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    with transaction() as db:
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if version>1:raise RuntimeError('Database schema is newer than this application')
        db.executescript(SCHEMA)
        if version<1:db.execute('PRAGMA user_version=1')
        if not db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone():
            db.execute('INSERT INTO accounts(name,created_at) VALUES(?,?)',('Feeds',int(time.time())))
        for k,v in [('refresh_min','30'),('retention_days','0'),('theme','auto'),('read_mode','summary'),('mark_read','1'),('notifications','1')]:
            db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',(k,v))
        db.execute('INSERT INTO article_fts(article_fts) VALUES(?)',('integrity-check',)) if False else None
    with connection() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=NORMAL')

def log(level,source,message):
    try:
        with transaction() as db:
            db.execute('INSERT INTO logs(created_at,level,source,message) VALUES(?,?,?,?)',
                       (int(time.time()),level[:20],source[:80],str(message)[:1200]))
            db.execute('DELETE FROM logs WHERE id IN (SELECT id FROM logs ORDER BY id DESC LIMIT -1 OFFSET 5000)')
    except Exception:
        pass

def get_settings(db):
    return {r['key']:r['value'] for r in db.execute('SELECT key,value FROM settings')}

def fts_update(db, article_id):
    r = db.execute('SELECT title,summary,content,author FROM articles WHERE id=?',(article_id,)).fetchone()
    db.execute('DELETE FROM article_fts WHERE rowid=?',(article_id,))
    if r:
        db.execute('INSERT INTO article_fts(rowid,title,summary,content,author) VALUES(?,?,?,?,?)',
                   (article_id,r['title'],r['summary'],r['content'],r['author']))

def purge_article(db,article_id):
    db.execute('DELETE FROM article_fts WHERE rowid=?',(article_id,))
    db.execute('DELETE FROM articles WHERE id=?',(article_id,))

def cleanup():
    now=int(time.time())
    with transaction() as db:
        db.execute('DELETE FROM sessions WHERE expires_at<?',(now,))
        db.execute('DELETE FROM attempts WHERE attempted<?',(now-900,))
        st=get_settings(db)
        global_days=int(st.get('retention_days','0'))
        # An account-specific positive retention period overrides the global default.
        for r in db.execute('''SELECT a.id FROM articles a JOIN feeds f ON f.id=a.feed_id
           JOIN accounts ac ON ac.id=f.account_id
           WHERE a.deleted=0 AND a.archived=0 AND a.starred=0 AND a.bookmarked=0
           AND COALESCE(NULLIF(ac.retention_days,0),?)>0
           AND a.fetched<?-COALESCE(NULLIF(ac.retention_days,0),?)*86400 LIMIT 1000''',
           (global_days,now,global_days)).fetchall():
            purge_article(db,r['id'])
        for r in db.execute('SELECT id FROM articles WHERE deleted=1 AND deleted_at<? LIMIT 1000',(now-30*86400,)).fetchall():
            purge_article(db,r['id'])
