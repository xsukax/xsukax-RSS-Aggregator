"""Single-process bounded refresh scheduler; run web server with exactly one worker."""
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from .db import connection, transaction, fts_update, cleanup, log
from .rules import run_rules
from .syndication import fetch,parse_feed

_lock=threading.Lock()
_pending=set()
_executor=None
_stop=threading.Event()
_loop=None

def tag_article(db,article_id,categories,prefix):
    for name in categories[:30]:
        tag=(prefix+name).strip()[:100]
        if not tag:continue
        db.execute('INSERT OR IGNORE INTO tags(name) VALUES(?)',(tag,))
        db.execute('INSERT OR IGNORE INTO article_tags(article_id,tag_id) SELECT ?,id FROM tags WHERE name=?',(article_id,tag))

def refresh(feed_id):
    try:
        with connection() as db:
            feed=db.execute('''SELECT f.*,a.paused AS account_paused,a.interval_min AS account_interval,
                               a.auto_tag AS account_auto_tag,a.tag_prefix AS account_prefix
                               FROM feeds f JOIN accounts a ON a.id=f.account_id WHERE f.id=?''',(feed_id,)).fetchone()
        if not feed:return
        started=int(time.time())
        request_headers={}
        if feed['etag']:request_headers['If-None-Match']=feed['etag']
        if feed['last_modified']:request_headers['If-Modified-Since']=feed['last_modified']
        data,info=fetch(feed['url'],request_headers)
        meta,articles=parse_feed(data,info['url']) if info['status']!=304 else ({},[])
        inserted=0;changed=0
        with transaction() as db:
            fresh=db.execute('SELECT id FROM feeds WHERE id=?',(feed_id,)).fetchone()
            if not fresh:return
            for article in articles:
                old=db.execute('SELECT id,signature FROM articles WHERE feed_id=? AND guid=?',(feed_id,article['guid'])).fetchone()
                payload=(article['title'],article['link'],article['author'],article['summary'],article['content'],
                         article['published'],article['updated'],json.dumps(article['categories'],ensure_ascii=False),
                         json.dumps(article['enclosures'],ensure_ascii=False),article['signature'])
                if old:
                    if old['signature']!=article['signature']:
                        db.execute('''UPDATE articles SET title=?,link=?,author=?,summary=?,content=?,published=?,updated=?,
                                      categories=?,enclosures=?,signature=?,revision=revision+1 WHERE id=?''',(*payload,old['id']))
                        fts_update(db,old['id']);changed+=1
                else:
                    cur=db.execute('''INSERT INTO articles(feed_id,guid,title,link,author,summary,content,published,updated,
                                      categories,enclosures,signature,fetched) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                                   (feed_id,article['guid'],*payload,started))
                    article_id=cur.lastrowid
                    fts_update(db,article_id)
                    if feed['auto_tag'] and feed['account_auto_tag']:
                        tag_article(db,article_id,article['categories'],feed['tag_prefix'] or feed['account_prefix'])
                    run_rules(db,article_id,feed['account_id'],feed_id)
                    inserted+=1
            interval=feed['interval_min'] or feed['account_interval'] or 30
            db.execute('''UPDATE feeds SET title=COALESCE(NULLIF(?,''),title),site_url=COALESCE(NULLIF(?,''),site_url),
                          description=COALESCE(NULLIF(?,''),description),language=COALESCE(NULLIF(?,''),language),
                          url=?,etag=?,last_modified=?,last_checked=?,last_success=?,next_check=?,failure_count=0,
                          last_error='',last_http_status=? WHERE id=?''',
                       (meta.get('title',''),meta.get('site_url',''),meta.get('description',''),meta.get('language',''),
                        info['url'],info['etag'] or feed['etag'],info['last_modified'] or feed['last_modified'],
                        started,started,started+interval*60,info['status'],feed_id))
        log('INFO','feed '+str(feed_id),f'Refreshed: {inserted} new, {changed} updated, HTTP {info["status"]}')
    except Exception as exc:
        log('ERROR','feed '+str(feed_id),str(exc))
        with transaction() as db:
            old=db.execute('SELECT failure_count,interval_min FROM feeds WHERE id=?',(feed_id,)).fetchone()
            if old:
                fails=min(old['failure_count']+1,12)
                delay=min(21600,max(300,2**min(fails,7)*120))
                db.execute('''UPDATE feeds SET last_checked=?,last_error=?,failure_count=?,next_check=? WHERE id=?''',
                           (int(time.time()),str(exc)[:400],fails,int(time.time())+delay,feed_id))
    finally:
        with _lock:_pending.discard(feed_id)

def enqueue(feed_id):
    global _executor
    with _lock:
        if feed_id in _pending:return False
        _pending.add(feed_id)
    if _executor is None:
        with _lock:_pending.discard(feed_id)
        return False
    _executor.submit(refresh,feed_id)
    return True

def loop():
    last_cleanup=0
    while not _stop.wait(12):
        try:
            now=int(time.time())
            with connection() as db:
                rows=db.execute('''SELECT f.id FROM feeds f JOIN accounts a ON a.id=f.account_id
                                   WHERE f.enabled=1 AND f.paused=0 AND a.paused=0
                                   AND f.next_check<=? ORDER BY f.next_check LIMIT 25''',(now,)).fetchall()
            for r in rows:enqueue(r['id'])
            if now-last_cleanup>3600:
                cleanup();last_cleanup=now
        except Exception as exc:log('ERROR','scheduler',str(exc))

def start():
    global _executor,_loop
    if _executor is not None:return
    workers=max(1,min(4,int(os.environ.get('REFRESH_WORKERS','2'))))
    _stop.clear()
    _executor=ThreadPoolExecutor(max_workers=workers,thread_name_prefix='feed')
    _loop=threading.Thread(target=loop,daemon=True,name='rss-scheduler')
    _loop.start()

def stop():
    global _executor
    _stop.set()
    if _loop:_loop.join(timeout=3)
    if _executor:_executor.shutdown(wait=True,cancel_futures=False)
    _executor=None
