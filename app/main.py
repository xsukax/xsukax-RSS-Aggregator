"""HTTP API and static frontend for xsukax RSS Aggregator.

Run with one Uvicorn worker: the in-process scheduler must not be duplicated.
"""
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError,VerificationError
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import DB_FILE,connection,transaction,init_db,fts_update,purge_article,get_settings,log
from .syndication import check_url,fetch,discover,parse_feed,opml_export,opml_parse,text_content,safe_html
from .rules import validate_conditions,validate_actions,match,run_rules,apply_actions
from .worker import enqueue,start,stop

HERE=Path(__file__).parent
APP_SECRET=os.getenv('APP_SECRET','')
SETUP_TOKEN=os.getenv('SETUP_TOKEN','')
PUBLIC_URL=os.getenv('PUBLIC_URL','').rstrip('/')
COOKIE_SECURE=PUBLIC_URL.startswith('https://')
SESSION_SECONDS=7*86400
HASHER=PasswordHasher(time_cost=2,memory_cost=32768,parallelism=1)

@asynccontextmanager
async def lifespan(application):
    init_db();start()
    yield
    stop()

app=FastAPI(title='xsukax RSS Aggregator',version='0.1.1',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
app.mount('/static',StaticFiles(directory=str(HERE/'static')),name='static')

@app.exception_handler(ValueError)
async def error_value(request,exc):
    return JSONResponse({'error':str(exc)},status_code=400)

@app.exception_handler(sqlite3.IntegrityError)
async def error_db(request,exc):
    return JSONResponse({'error':'Duplicate, invalid reference, or constraint violation'},status_code=409)

@app.middleware('http')
async def safety_headers(request,call_next):
    resp=await call_next(request)
    resp.headers['X-Content-Type-Options']='nosniff'
    resp.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    resp.headers['X-Frame-Options']='DENY'
    resp.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    resp.headers['Content-Security-Policy']=("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' https: http: data:; frame-src 'none'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'")
    resp.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/auth') else 'private, max-age=0'
    return resp

@app.get('/',response_class=HTMLResponse)
def index():
    return (HERE/'static/index.html').read_text('utf-8')

@app.get('/healthz')
def health():
    with connection() as db:db.execute('SELECT 1').fetchone()
    return {'ok':True,'version':'0.1.1'}

def session_hash(token):
    return hmac.new(APP_SECRET.encode('utf-8'),token.encode('utf-8'),hashlib.sha256).hexdigest()

def get_data(request):
    raw=request.cookies.get('xsukax_sid','')
    if not raw:return None
    sha=session_hash(raw)
    with connection() as db:
        return db.execute('SELECT csrf,expires_at FROM sessions WHERE token_hash=? AND expires_at>?',
                          (sha,int(time.time()))).fetchone()

def guard(request:Request):
    row=get_data(request)
    if not row:raise HTTPException(401,'Sign in required')
    if request.method not in ('GET','HEAD','OPTIONS'):
        token=request.headers.get('x-csrf-token','')
        if not token or not hmac.compare_digest(token,row['csrf']):
            raise HTTPException(403,'Invalid CSRF token')
    return True

def extract_data(obj,keys):
    return {k:obj[k] for k in keys if k in obj}

def mandatory(data,key,max_length=300):
    v=data.get(key)
    if not isinstance(v,str) or not v.strip() or len(v)>max_length:raise ValueError(key+' is required and must be short text')
    return v.strip()

def password_text(data,key):
    # Preserve the exact user-chosen password, including Unicode and boundary spaces.
    # Normal text fields are trimmed by mandatory(), but passwords must not be.
    raw=data.get(key)
    if not isinstance(raw,str) or not raw.strip() or len(raw)>1024:
        raise ValueError(key+' must be nonempty text of at most 1024 characters')
    return raw

def intval(value,minimum=0,maximum=100000000):
    try: v=int(value)
    except (ValueError,TypeError):raise ValueError('Expected an integer')
    if v<minimum or v>maximum:raise ValueError('Number out of range')
    return v

def boolval(v):return 1 if v in (1,True,'true','1') else 0

def set_cookie(response,token):
    response.set_cookie('xsukax_sid',token,max_age=SESSION_SECONDS,secure=COOKIE_SECURE,
                        httponly=True,samesite='strict',path='/')

def create_session(response):
    token=secrets.token_urlsafe(42);csrf=secrets.token_urlsafe(32);now=int(time.time())
    with transaction() as db:
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(session_hash(token),csrf,now,now+SESSION_SECONDS))
    set_cookie(response,token)
    return csrf

@app.get('/api/auth/state')
def auth_state(request:Request):
    with connection() as db:
        created=bool(db.execute('SELECT id FROM admin WHERE id=1').fetchone())
    sess=get_data(request)
    return {'configured':created,'authenticated':bool(sess),'csrf':sess['csrf'] if sess else None,
            'version':'0.1.1','public_url':PUBLIC_URL}

@app.post('/api/auth/setup')
async def setup(request:Request):
    data=await request.json();username=mandatory(data,'username',80)
    password=password_text(data,'password')
    confirmation=password_text(data,'confirm_password')
    token=data.get('setup_token','')
    if len(password)<12:raise ValueError('Administrator password must contain at least 12 characters')
    if not hmac.compare_digest(password.encode('utf-8'),confirmation.encode('utf-8')):raise ValueError('Administrator passwords do not match')
    if not SETUP_TOKEN or not hmac.compare_digest(str(token).encode('utf-8'),SETUP_TOKEN.encode('utf-8')):raise HTTPException(403,'Invalid setup token')
    with transaction() as db:
        if db.execute('SELECT id FROM admin WHERE id=1').fetchone():raise HTTPException(409,'Administrator already configured')
        db.execute('INSERT INTO admin VALUES(1,?,?,?)',(username,HASHER.hash(password),int(time.time())))
    resp=JSONResponse({'ok':True});create_session(resp)
    return resp

@app.post('/api/auth/login')
async def login(request:Request):
    data=await request.json();username=str(data.get('username',''))
    password=str(data.get('password',''))
    ip=request.client.host if request.client else 'unknown'
    now=int(time.time())
    with connection() as db:
        attempts=db.execute('SELECT COUNT(*) n FROM attempts WHERE ip=? AND attempted>?',(ip,now-900)).fetchone()['n']
        user=db.execute('SELECT * FROM admin WHERE id=1').fetchone()
    if attempts>=10:raise HTTPException(429,'Too many attempts; try again later')
    valid=False
    if user and hmac.compare_digest(user['username'].encode('utf-8'),username.encode('utf-8')):
        try:valid=HASHER.verify(user['password_hash'],password)
        except (VerifyMismatchError,VerificationError):pass
    if not valid:
        with transaction() as db:db.execute('INSERT INTO attempts(ip,attempted) VALUES(?,?)',(ip,now))
        raise HTTPException(401,'Invalid username or password')
    with transaction() as db:db.execute('DELETE FROM attempts WHERE ip=?',(ip,))
    resp=JSONResponse({'ok':True});create_session(resp)
    return resp

@app.post('/api/auth/logout')
def logout(request:Request,auth=Depends(guard)):
    sha=session_hash(request.cookies.get('xsukax_sid',''))
    with transaction() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(sha,))
    resp=JSONResponse({'ok':True});resp.delete_cookie('xsukax_sid',path='/');return resp

@app.post('/api/auth/password')
async def password_change(request:Request,auth=Depends(guard)):
    data=await request.json();old=data.get('old_password','');new=password_text(data,'new_password')
    if len(new)<12:raise ValueError('New password must be at least 12 characters')
    with transaction() as db:
        user=db.execute('SELECT * FROM admin WHERE id=1').fetchone()
        try:ok=HASHER.verify(user['password_hash'],old)
        except (VerifyMismatchError,VerificationError):ok=False
        if not ok:raise HTTPException(403,'Incorrect current password')
        db.execute('UPDATE admin SET password_hash=? WHERE id=1',(HASHER.hash(new),))
        db.execute('DELETE FROM sessions')
    resp=JSONResponse({'ok':True,'message':'Password changed. Sign in again.'})
    resp.delete_cookie('xsukax_sid',path='/');return resp

@app.get('/api/tree')
def tree(auth=Depends(guard)):
    with connection() as db:
        accounts=[dict(r) for r in db.execute('SELECT * FROM accounts ORDER BY id')]
        folders=[dict(r) for r in db.execute('SELECT * FROM folders ORDER BY account_id,parent_id,name')]
        feeds=[dict(r) for r in db.execute('''SELECT f.*, (SELECT COUNT(*) FROM articles a WHERE a.feed_id=f.id AND a.deleted=0) AS total,
                                             (SELECT COUNT(*) FROM articles a WHERE a.feed_id=f.id AND a.deleted=0 AND a.is_read=0) AS unread
                                             FROM feeds f ORDER BY account_id,folder_id,title''')]
        total=db.execute('SELECT COUNT(*) n,SUM(CASE WHEN is_read=0 THEN 1 ELSE 0 END) unread FROM articles WHERE deleted=0').fetchone()
        smart=[dict(r) for r in db.execute('SELECT * FROM smart_folders ORDER BY name')]
    return {'accounts':accounts,'folders':folders,'feeds':feeds,'smart':smart,
            'counts':{'total':total['n'],'unread':total['unread'] or 0}}

@app.post('/api/accounts')
async def account_add(request:Request,auth=Depends(guard)):
    data=await request.json();name=mandatory(data,'name')
    with transaction() as db:
        cur=db.execute('INSERT INTO accounts(name,interval_min,created_at) VALUES(?,?,?)',(name,int(get_settings(db).get('refresh_min','30')),int(time.time())))
        return {'id':cur.lastrowid}

@app.patch('/api/accounts/{id}')
async def account_edit(id:int,request:Request,auth=Depends(guard)):
    d=await request.json();parts=[];params=[]
    for k in ('name','paused','auto_tag','tag_prefix','interval_min','retention_days'):
        if k in d:
            val=d[k]
            if k in ('name','tag_prefix'):val=mandatory(d,k) if k=='name' else str(val)[:100]
            if k in ('paused','auto_tag'):val=boolval(val)
            if k=='interval_min':val=intval(val,5,10080)
            if k=='retention_days':val=intval(val,0,36500)
            parts.append(k+'=?');params.append(val)
    if parts:
        with transaction() as db:db.execute('UPDATE accounts SET '+','.join(parts)+' WHERE id=?',(*params,id))
    return {'ok':True}

@app.delete('/api/accounts/{id}')
def account_delete(id:int,auth=Depends(guard)):
    with transaction() as db:
        if db.execute('SELECT COUNT(*) n FROM accounts').fetchone()['n']<=1:raise ValueError('At least one feed account is required')
        ids=[r['id'] for r in db.execute('SELECT a.id FROM articles a JOIN feeds f ON f.id=a.feed_id WHERE f.account_id=?',(id,))]
        for aid in ids:db.execute('DELETE FROM article_fts WHERE rowid=?',(aid,))
        db.execute('DELETE FROM accounts WHERE id=?',(id,))
    return {'ok':True}

@app.post('/api/folders')
async def folder_add(request:Request,auth=Depends(guard)):
    d=await request.json();name=mandatory(d,'name');aid=intval(d.get('account_id'),1)
    parent=d.get('parent_id')
    with transaction() as db:
        if parent is not None and not db.execute('SELECT 1 FROM folders WHERE id=? AND account_id=?',(parent,aid)).fetchone():
            raise ValueError('Parent folder must belong to this account')
        cur=db.execute('INSERT INTO folders(account_id,parent_id,name,created_at) VALUES(?,?,?,?)',(aid,parent,name,int(time.time())))
        return {'id':cur.lastrowid}

@app.patch('/api/folders/{id}')
async def folder_edit(id:int,request:Request,auth=Depends(guard)):
    d=await request.json()
    with transaction() as db:
        folder=db.execute('SELECT * FROM folders WHERE id=?',(id,)).fetchone()
        if not folder:raise HTTPException(404,'Folder not found')
        if 'parent_id' in d:
            pid=d['parent_id']
            if pid==id:raise ValueError('Folder cannot be its own parent')
            seen={id}
            while pid is not None:
                if pid in seen:raise ValueError('Circular folder hierarchy')
                seen.add(pid)
                parent=db.execute('SELECT parent_id,account_id FROM folders WHERE id=?',(pid,)).fetchone()
                if not parent or parent['account_id']!=folder['account_id']:raise ValueError('Invalid parent folder')
                pid=parent['parent_id']
            db.execute('UPDATE folders SET parent_id=? WHERE id=?',(d['parent_id'],id))
        if 'name' in d:db.execute('UPDATE folders SET name=? WHERE id=?',(mandatory(d,'name'),id))
    return {'ok':True}

@app.delete('/api/folders/{id}')
def folder_delete(id:int,recursive:bool=False,auth=Depends(guard)):
    with transaction() as db:
        if not db.execute('SELECT 1 FROM folders WHERE id=?',(id,)).fetchone():raise HTTPException(404,'Folder not found')
        used=db.execute('SELECT COUNT(*) n FROM feeds WHERE folder_id=?',(id,)).fetchone()['n']
        childs=db.execute('SELECT COUNT(*) n FROM folders WHERE parent_id=?',(id,)).fetchone()['n']
        if (used or childs) and not recursive:raise ValueError('Folder is not empty. Delete contents explicitly or choose recursive deletion.')
        if recursive:
            folderids=[id];i=0
            while i<len(folderids):
                folderids.extend([r['id'] for r in db.execute('SELECT id FROM folders WHERE parent_id=?',(folderids[i],))]);i+=1
            for fid in folderids:
                for r in db.execute('''SELECT a.id FROM articles a JOIN feeds f ON a.feed_id=f.id WHERE f.folder_id=?''',(fid,)):
                    db.execute('DELETE FROM article_fts WHERE rowid=?',(r['id'],))
        db.execute('DELETE FROM folders WHERE id=?',(id,))
    return {'ok':True}

@app.get('/api/discover')
def discover_endpoint(url:str,auth=Depends(guard)):
    data,info=fetch(url)
    try:
        meta,items=parse_feed(data,info['url']);return {'feeds':[{'url':info['url'],'title':meta['title']}],'direct':True}
    except ValueError:
        return {'feeds':discover(data,info['url']),'direct':False}

@app.post('/api/feeds')
async def feed_add(request:Request,auth=Depends(guard)):
    d=await request.json();url=mandatory(d,'url',2048);check_url(url)
    title=str(d.get('title') or url)[:300];aid=intval(d.get('account_id'),1);folder=d.get('folder_id')
    with transaction() as db:
        if folder and not db.execute('SELECT 1 FROM folders WHERE id=? AND account_id=?',(folder,aid)).fetchone():raise ValueError('Folder does not belong to account')
        cur=db.execute('INSERT INTO feeds(account_id,folder_id,url,title,created_at) VALUES(?,?,?,?,?)',
                       (aid,folder,url,title,int(time.time())))
        id=cur.lastrowid
    enqueue(id)
    return {'id':id,'queued':True}

@app.patch('/api/feeds/{id}')
async def feed_edit(id:int,request:Request,auth=Depends(guard)):
    d=await request.json();allowed={'title','url','folder_id','paused','enabled','interval_min','auto_tag','tag_prefix','view_mode'}
    fields=[];params=[]
    with transaction() as db:
        feed=db.execute('SELECT * FROM feeds WHERE id=?',(id,)).fetchone()
        if not feed:raise HTTPException(404,'Feed not found')
        if 'folder_id' in d and d['folder_id'] is not None and not db.execute('SELECT 1 FROM folders WHERE id=? AND account_id=?',(d['folder_id'],feed['account_id'])).fetchone():
            raise ValueError('Invalid folder for feed')
        for k in allowed:
            if k not in d:continue
            v=d[k]
            if k=='url':v=mandatory(d,k,2048);check_url(v)
            if k=='title':v=mandatory(d,k)
            if k in ('enabled','paused','auto_tag'):v=boolval(v)
            if k=='interval_min':v=intval(v,0,10080);v=5 if 0<v<5 else v
            if k=='tag_prefix':v=str(v)[:100]
            if k=='view_mode' and v not in ('summary','content','plain'):raise ValueError('Invalid reading mode')
            fields.append(k+'=?');params.append(v)
        if fields:db.execute('UPDATE feeds SET '+','.join(fields)+' WHERE id=?',(*params,id))
    return {'ok':True}

@app.delete('/api/feeds/{id}')
def feed_delete(id:int,keep_articles:bool=False,auth=Depends(guard)):
    if keep_articles:raise ValueError('This version does not support detached articles; export or archive externally before deleting feed')
    with transaction() as db:
        for row in db.execute('SELECT id FROM articles WHERE feed_id=?',(id,)).fetchall():db.execute('DELETE FROM article_fts WHERE rowid=?',(row['id'],))
        db.execute('DELETE FROM feeds WHERE id=?',(id,))
    return {'ok':True}

@app.post('/api/feeds/{id}/refresh')
def feed_refresh(id:int,auth=Depends(guard)):
    with connection() as db:
        if not db.execute('SELECT 1 FROM feeds WHERE id=?',(id,)).fetchone():raise HTTPException(404,'Feed not found')
    return {'queued':enqueue(id)}

@app.post('/api/refresh')
async def refresh_selected(request:Request,auth=Depends(guard)):
    d=await request.json();scope=d.get('scope','all');id=d.get('id');rows=[]
    with connection() as db:
        if scope=='all':rows=db.execute('SELECT id FROM feeds WHERE enabled=1').fetchall()
        elif scope=='account':rows=db.execute('SELECT id FROM feeds WHERE account_id=?',(id,)).fetchall()
        elif scope=='folder':
            rows=db.execute('''WITH RECURSIVE subtree(id) AS (SELECT id FROM folders WHERE id=? UNION ALL
                               SELECT f.id FROM folders f JOIN subtree s ON f.parent_id=s.id)
                               SELECT id FROM feeds WHERE folder_id IN (SELECT id FROM subtree)''',(id,)).fetchall()
        elif scope=='feed':rows=[{'id':id}]
        else:raise ValueError('Invalid refresh scope')
    n=sum(bool(enqueue(r['id'])) for r in rows)
    return {'queued':n,'total':len(rows)}

@app.get('/api/articles')
def articles(account_id:int|None=None,folder_id:int|None=None,feed_id:int|None=None,collection:str='all',
             search:str='',tag_id:int|None=None,smart_id:int|None=None,unread_only:bool=False,starred_only:bool=False,
             bookmarked_only:bool=False,has_media:bool=False,page:int=1,limit:int=50,
             sort:str='published',order:str='desc',auth=Depends(guard)):
    page=intval(page,1,100000);limit=intval(limit,1,100)
    clauses=['1=1'];params=[];join='JOIN feeds f ON f.id=a.feed_id'
    if collection=='trash':clauses.append('a.deleted=1')
    else:clauses.append('a.deleted=0')
    if collection=='unread':clauses.append('a.is_read=0')
    if collection=='starred':clauses.append('a.starred=1')
    if collection=='bookmarked':clauses.append('a.bookmarked=1')
    if collection=='archive':clauses.append('a.archived=1')
    if collection not in ('all','unread','starred','bookmarked','archive','trash','recent'):raise ValueError('Invalid collection')
    if account_id:clauses.append('f.account_id=?');params.append(account_id)
    if feed_id:clauses.append('f.id=?');params.append(feed_id)
    if folder_id:
        clauses.append('''COALESCE(a.folder_override,f.folder_id) IN (
          WITH RECURSIVE tree(id) AS (SELECT id FROM folders WHERE id=? UNION ALL
          SELECT f2.id FROM folders f2 JOIN tree t ON f2.parent_id=t.id) SELECT id FROM tree)''');params.append(folder_id)
    if unread_only:clauses.append('a.is_read=0')
    if starred_only:clauses.append('a.starred=1')
    if bookmarked_only:clauses.append('a.bookmarked=1')
    if has_media:clauses.append("a.enclosures<>'[]'")
    if tag_id:
        clauses.append('EXISTS (SELECT 1 FROM article_tags at WHERE at.article_id=a.id AND at.tag_id=?)');params.append(tag_id)
    if smart_id:
        with connection() as db:
            smart=db.execute('SELECT * FROM smart_folders WHERE id=?',(smart_id,)).fetchone()
        if not smart:raise HTTPException(404,'Saved search not found')
        if smart['account_id']:clauses.append('f.account_id=?');params.append(smart['account_id'])
        sf=json.loads(smart['filters'])
        for key,col in [('unread','a.is_read'),('starred','a.starred'),('bookmarked','a.bookmarked')]:
            if sf.get(key):clauses.append(col+'=?');params.append(0 if key=='unread' else 1)
        search=search or smart['query']

    search_term=search.strip()
    fts_requested=bool(search_term)
    if search_term and len(search_term)>250:raise ValueError('Search text too long')
    if sort not in ('published','fetched','title','updated','author'):raise ValueError('Invalid sort column')
    if order.lower() not in ('asc','desc'):raise ValueError('Invalid sort direction')

    def fetch_rows(db, base_join, base_clauses, base_params):
        where=' AND '.join(base_clauses)
        base=f' FROM articles a {base_join} WHERE {where}'
        total=db.execute('SELECT COUNT(*) n'+base,base_params).fetchone()['n']
        rows=[dict(r) for r in db.execute('''SELECT a.id,a.feed_id,a.title,a.link,a.author,a.published,a.updated,a.fetched,
            a.is_read,a.starred,a.bookmarked,a.archived,a.deleted,a.summary,a.categories,a.enclosures,
            f.title AS feed_title,f.account_id, COALESCE(a.folder_override,f.folder_id) AS folder_id,
            (SELECT group_concat(t.name, ', ') FROM article_tags at JOIN tags t ON t.id=at.tag_id WHERE at.article_id=a.id) tags
            '''+base+f' ORDER BY a.{sort} {order.upper()}, a.id DESC LIMIT ? OFFSET ?',(*base_params,limit,(page-1)*limit))]
        return total,rows

    with connection() as db:
        try:
            if fts_requested:
                # Prefer indexed FTS so quotes and boolean operators remain available.
                total,results=fetch_rows(db,join+' JOIN article_fts ON article_fts.rowid=a.id',clauses+['article_fts MATCH ?'],params+[search_term])
            else:
                total,results=fetch_rows(db,join,clauses,params)
        except sqlite3.OperationalError:
            if not fts_requested:raise
            # Ordinary hyphenated or punctuation-heavy text should still search cleanly.
            like=f'%{search_term.lower()}%'
            fallback='(lower(a.title) LIKE ? OR lower(a.summary) LIKE ? OR lower(a.content) LIKE ? OR lower(a.author) LIKE ? OR lower(f.title) LIKE ?)'
            total,results=fetch_rows(db,join,clauses+[fallback],params+[like,like,like,like,like])
    for item in results:
        item['excerpt']=text_content(item.pop('summary'))[:240]
        item['categories']=json.loads(item['categories'])
        item['has_enclosures']=bool(json.loads(item.pop('enclosures')))
    return {'articles':results,'total':total,'page':page,'limit':limit}

@app.get('/api/articles/{id}')
def article_detail(id:int,auth=Depends(guard)):
    with connection() as db:
        item=db.execute('''SELECT a.*,f.title AS feed_title,f.url AS feed_url FROM articles a
                           JOIN feeds f ON f.id=a.feed_id WHERE a.id=?''',(id,)).fetchone()
        if not item:raise HTTPException(404,'Article not found')
        tags=[dict(r) for r in db.execute('''SELECT t.* FROM tags t JOIN article_tags at ON at.tag_id=t.id WHERE at.article_id=?''',(id,))]
    d=dict(item);d['tags']=tags;d['categories']=json.loads(d['categories']);d['enclosures']=json.loads(d['enclosures']);return d

@app.patch('/api/articles/{id}')
async def article_patch(id:int,request:Request,auth=Depends(guard)):
    d=await request.json();allowed={'is_read','starred','bookmarked','archived','deleted','folder_override'}
    sets=[];args=[]
    with transaction() as db:
        item=db.execute('''SELECT a.*,f.account_id FROM articles a JOIN feeds f ON f.id=a.feed_id WHERE a.id=?''',(id,)).fetchone()
        if not item:raise HTTPException(404,'Article not found')
        for key,value in d.items():
            if key not in allowed:raise ValueError('Unrecognized article property')
            if key=='folder_override':
                if value is not None and not db.execute('SELECT 1 FROM folders WHERE id=? AND account_id=?',(value,item['account_id'])).fetchone():
                    raise ValueError('Invalid destination folder')
            else:value=boolval(value)
            sets.append(key+'=?');args.append(value)
            if key=='deleted':sets.append('deleted_at=?');args.append(int(time.time()) if value else 0)
        if sets:db.execute('UPDATE articles SET '+','.join(sets)+' WHERE id=?',(*args,id))
    return {'ok':True}

@app.post('/api/articles/bulk')
async def article_bulk(request:Request,auth=Depends(guard)):
    d=await request.json();ids=d.get('ids',[]);action=d.get('action')
    if not isinstance(ids,list) or not 1<=len(ids)<=500:raise ValueError('Select 1–500 articles')
    ids=[intval(i,1) for i in ids]
    mapping={'read':('is_read',1),'unread':('is_read',0),'star':('starred',1),'unstar':('starred',0),
             'bookmark':('bookmarked',1),'unbookmark':('bookmarked',0),'archive':('archived',1),
             'unarchive':('archived',0),'trash':('deleted',1),'restore':('deleted',0)}
    if action not in mapping:raise ValueError('Unsupported bulk action')
    col,val=mapping[action]
    with transaction() as db:
        placeholders=','.join('?' for _ in ids)
        db.execute(f'UPDATE articles SET {col}=? WHERE id IN ({placeholders})',(val,*ids))
        if action in ('trash','restore'):db.execute(f'UPDATE articles SET deleted_at=? WHERE id IN ({placeholders})',
                    (int(time.time()) if val else 0,*ids))
    return {'ok':True,'updated':len(ids)}

@app.delete('/api/articles/{id}')
def article_purge(id:int,auth=Depends(guard)):
    with transaction() as db:
        r=db.execute('SELECT deleted FROM articles WHERE id=?',(id,)).fetchone()
        if not r or not r['deleted']:raise ValueError('Move article to Trash before permanently deleting')
        purge_article(db,id)
    return {'ok':True}

@app.get('/api/tags')
def tags(auth=Depends(guard)):
    with connection() as db:return {'tags':[dict(r) for r in db.execute('''SELECT t.*,(SELECT COUNT(*) FROM article_tags at WHERE at.tag_id=t.id) AS count FROM tags t ORDER BY name''')]}

@app.post('/api/tags')
async def tag_create(request:Request,auth=Depends(guard)):
    d=await request.json();name=mandatory(d,'name',100);color=str(d.get('color','#4488bb'))
    if not __import__('re').fullmatch('#[0-9a-fA-F]{6}',color):raise ValueError('Color must be #RRGGBB')
    with transaction() as db:
        cur=db.execute('INSERT INTO tags(name,color) VALUES(?,?)',(name,color))
        return {'id':cur.lastrowid}

@app.patch('/api/tags/{id}')
async def tag_edit(id:int,request:Request,auth=Depends(guard)):
    d=await request.json();parts=[];args=[]
    if 'name' in d:parts.append('name=?');args.append(mandatory(d,'name',100))
    if 'color' in d:
        c=str(d['color'])
        if not __import__('re').fullmatch('#[0-9a-fA-F]{6}',c):raise ValueError('Invalid color')
        parts.append('color=?');args.append(c)
    with transaction() as db:
        if parts:db.execute('UPDATE tags SET '+','.join(parts)+' WHERE id=?',(*args,id))
    return {'ok':True}

@app.delete('/api/tags/{id}')
def tag_delete(id:int,auth=Depends(guard)):
    with transaction() as db:db.execute('DELETE FROM tags WHERE id=?',(id,))
    return {'ok':True}

@app.post('/api/articles/{id}/tags')
async def article_tag(id:int,request:Request,auth=Depends(guard)):
    d=await request.json();name=mandatory(d,'name',100);remove=bool(d.get('remove',False))
    with transaction() as db:
        if not db.execute('SELECT 1 FROM articles WHERE id=?',(id,)).fetchone():raise HTTPException(404,'Article not found')
        if remove:db.execute('DELETE FROM article_tags WHERE article_id=? AND tag_id IN (SELECT id FROM tags WHERE name=?)',(id,name))
        else:
            db.execute('INSERT OR IGNORE INTO tags(name) VALUES(?)',(name,))
            db.execute('INSERT OR IGNORE INTO article_tags(article_id,tag_id) SELECT ?,id FROM tags WHERE name=?',(id,name))
    return {'ok':True}

@app.get('/api/rules')
def list_rules(auth=Depends(guard)):
    with connection() as db:return {'rules':[dict(r) for r in db.execute('SELECT * FROM rules ORDER BY priority,id')]}

@app.post('/api/rules')
async def rule_add(request:Request,auth=Depends(guard)):
    d=await request.json();name=mandatory(d,'name');cond=d.get('conditions');actions=d.get('actions')
    validate_conditions(cond);validate_actions(actions)
    aid=d.get('account_id');feed=d.get('feed_id')
    with transaction() as db:
        cur=db.execute('''INSERT INTO rules(name,account_id,feed_id,enabled,priority,conditions,actions,created_at)
                          VALUES(?,?,?,?,?,?,?,?)''',(name,aid,feed,boolval(d.get('enabled',True)),
                          intval(d.get('priority',100),0,10000),json.dumps(cond),json.dumps(actions),int(time.time())))
        return {'id':cur.lastrowid}

@app.patch('/api/rules/{id}')
async def rule_edit(id:int,request:Request,auth=Depends(guard)):
    d=await request.json();parts=[];params=[]
    for k in ('name','enabled','priority','conditions','actions','account_id','feed_id'):
        if k not in d:continue
        v=d[k]
        if k=='name':v=mandatory(d,'name')
        if k=='enabled':v=boolval(v)
        if k=='priority':v=intval(v,0,10000)
        if k=='conditions':validate_conditions(v);v=json.dumps(v)
        if k=='actions':validate_actions(v);v=json.dumps(v)
        parts.append(k+'=?');params.append(v)
    with transaction() as db:
        if parts:db.execute('UPDATE rules SET '+','.join(parts)+' WHERE id=?',(*params,id))
    return {'ok':True}

@app.delete('/api/rules/{id}')
def rule_delete(id:int,auth=Depends(guard)):
    with transaction() as db:db.execute('DELETE FROM rules WHERE id=?',(id,))
    return {'ok':True}

@app.post('/api/rules/{id}/run')
def rule_run(id:int,auth=Depends(guard)):
    with transaction() as db:
        rule=db.execute('SELECT * FROM rules WHERE id=?',(id,)).fetchone()
        if not rule:raise HTTPException(404,'Rule not found')
        cond=json.loads(rule['conditions']);actions=json.loads(rule['actions']);count=0
        sql='''SELECT a.id FROM articles a JOIN feeds f ON f.id=a.feed_id WHERE a.deleted=0'''
        args=[]
        if rule['account_id']:sql+=' AND f.account_id=?';args.append(rule['account_id'])
        if rule['feed_id']:sql+=' AND f.id=?';args.append(rule['feed_id'])
        for row in db.execute(sql+' ORDER BY a.id LIMIT 10000',args).fetchall():
            item=db.execute('''SELECT a.*,f.account_id,f.title AS feed_title FROM articles a
                               JOIN feeds f ON a.feed_id=f.id WHERE a.id=?''',(row['id'],)).fetchone()
            tags=[r['name'] for r in db.execute('SELECT t.name FROM tags t JOIN article_tags at ON at.tag_id=t.id WHERE at.article_id=?',(row['id'],))]
            if match(cond,item,tags):
                apply_actions(db,item['id'],actions,item['account_id']);count+=1
    return {'matched':count}

@app.get('/api/smart-folders')
def smart_list(auth=Depends(guard)):
    with connection() as db:return {'smart':[dict(r) for r in db.execute('SELECT * FROM smart_folders ORDER BY name')]}

@app.post('/api/smart-folders')
async def smart_add(request:Request,auth=Depends(guard)):
    d=await request.json();name=mandatory(d,'name');query=str(d.get('query',''))[:250];filters=d.get('filters',{})
    if not isinstance(filters,dict):raise ValueError('Filters must be an object')
    with transaction() as db:
        cur=db.execute('INSERT INTO smart_folders(name,account_id,query,filters) VALUES(?,?,?,?)',
                      (name,d.get('account_id'),query,json.dumps(filters)))
        return {'id':cur.lastrowid}

@app.delete('/api/smart-folders/{id}')
def smart_delete(id:int,auth=Depends(guard)):
    with transaction() as db:db.execute('DELETE FROM smart_folders WHERE id=?',(id,))
    return {'ok':True}

@app.get('/api/settings')
def settings(auth=Depends(guard)):
    with connection() as db:return {'settings':get_settings(db)}

@app.patch('/api/settings')
async def settings_edit(request:Request,auth=Depends(guard)):
    d=await request.json();allowed={'refresh_min','retention_days','theme','read_mode','mark_read','notifications'}
    with transaction() as db:
        for k,v in d.items():
            if k not in allowed:raise ValueError('Unknown setting: '+k)
            if k in ('refresh_min','retention_days'):v=str(intval(v,0,100000))
            elif k=='theme' and v not in ('auto','light','dark'):raise ValueError('Invalid theme')
            elif k=='read_mode' and v not in ('summary','content','plain'):raise ValueError('Invalid reading mode')
            elif k in ('mark_read','notifications'):v=str(boolval(v))
            db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,str(v)))
    return {'ok':True}

@app.get('/api/logs')
def logs(limit:int=100,auth=Depends(guard)):
    limit=intval(limit,1,500)
    with connection() as db:return {'logs':[dict(r) for r in db.execute('SELECT * FROM logs ORDER BY id DESC LIMIT ?',(limit,))]}

@app.get('/api/notifications')
def notifications(auth=Depends(guard)):
    with connection() as db:return {'notifications':[dict(r) for r in db.execute('SELECT * FROM notifications ORDER BY id DESC LIMIT 100')]}

@app.post('/api/notifications/read')
def notifications_read(auth=Depends(guard)):
    with transaction() as db:db.execute('UPDATE notifications SET seen=1')
    return {'ok':True}

@app.get('/api/export/opml')
def export_opml(account_id:int|None=None,auth=Depends(guard)):
    with connection() as db:
        folders=[dict(r) for r in db.execute('SELECT * FROM folders '+('WHERE account_id=? ' if account_id else '')+'ORDER BY parent_id,id', (account_id,) if account_id else ())]
        feeds=[dict(r) for r in db.execute('SELECT * FROM feeds '+('WHERE account_id=?' if account_id else ''),(account_id,) if account_id else ())]
    xml=opml_export(feeds,folders)
    return Response(xml,media_type='text/xml',headers={'Content-Disposition':'attachment; filename="xsukax-feeds.opml"'})

@app.post('/api/import/opml')
async def import_opml(request:Request,file:UploadFile=File(...),auth=Depends(guard)):
    content=await file.read(2*1024*1024+1)
    if len(content)>2*1024*1024:raise ValueError('OPML file too large')
    data=opml_parse(content)
    account_id=intval(request.query_params.get('account_id','1'),1)
    added=0;skipped=0
    with transaction() as db:
        if not db.execute('SELECT 1 FROM accounts WHERE id=?',(account_id,)).fetchone():raise ValueError('Account does not exist')
        pathmap={}
        for item in data:
            parent=None
            for name in item['folders']:
                pathkey=(parent,name)
                if pathkey not in pathmap:
                    old=db.execute('SELECT id FROM folders WHERE account_id=? AND parent_id IS ? AND name=?',(account_id,parent,name)).fetchone()
                    if old:pathmap[pathkey]=old['id']
                    else:pathmap[pathkey]=db.execute('INSERT INTO folders(account_id,parent_id,name,created_at) VALUES(?,?,?,?)',
                                          (account_id,parent,name,int(time.time()))).lastrowid
                parent=pathmap[pathkey]
            if db.execute('SELECT id FROM feeds WHERE account_id=? AND url=?',(account_id,item['url'])).fetchone():skipped+=1
            else:
                db.execute('INSERT INTO feeds(account_id,folder_id,url,title,created_at) VALUES(?,?,?,?,?)',
                           (account_id,parent,item['url'],item['title'],int(time.time())));added+=1
    return {'added':added,'duplicates':skipped}

@app.get('/api/export/articles.csv')
def export_articles_csv(auth=Depends(guard)):
    mem=io.StringIO();writer=csv.writer(mem)
    writer.writerow(['id','feed','title','url','author','published','read','starred','bookmarked','archived'])
    with connection() as db:
        for r in db.execute('''SELECT a.id,f.title feed,a.title,a.link,a.author,a.published,a.is_read,a.starred,a.bookmarked,a.archived
                               FROM articles a JOIN feeds f ON a.feed_id=f.id WHERE a.deleted=0 ORDER BY a.id'''):
            writer.writerow(list(r))
    return Response(mem.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="xsukax-articles.csv"'})

@app.get('/api/export/article/{id}.html')
def export_article_html(id:int,auth=Depends(guard)):
    from html import escape
    with connection() as db:
        r=db.execute('SELECT title,content,summary,link FROM articles WHERE id=?',(id,)).fetchone()
    if not r:raise HTTPException(404,'Article not found')
    link=r['link'] if r['link'].startswith(('http://','https://')) else ''
    html=f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(r["title"])}</title></head><body><h1>{escape(r["title"])}</h1><p><a href="{escape(link,quote=True)}">Original source</a></p>{safe_html(r["content"] or r["summary"])}</body></html>'
    return Response(html,media_type='text/html',headers={'Content-Disposition':f'attachment; filename="article-{id}.html"',
                    'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; img-src https: http:"})

@app.post('/api/maintenance/reindex')
def reindex(auth=Depends(guard)):
    with transaction() as db:
        db.execute('DELETE FROM article_fts')
        for row in db.execute('SELECT id FROM articles').fetchall():fts_update(db,row['id'])
        count=db.execute('SELECT COUNT(*) n FROM article_fts').fetchone()['n']
    return {'indexed':count}

@app.get('/api/stats')
def stats(auth=Depends(guard)):
    with connection() as db:
        counts={}
        for table in ('accounts','folders','feeds','articles','tags','rules','smart_folders'):
            counts[table]=db.execute('SELECT COUNT(*) n FROM '+table).fetchone()['n']
        counts['unread']=db.execute('SELECT COUNT(*) n FROM articles WHERE deleted=0 AND is_read=0').fetchone()['n']
        counts['failed_feeds']=db.execute("SELECT COUNT(*) n FROM feeds WHERE last_error<>''").fetchone()['n']
        counts['db_bytes']=DB_FILE.stat().st_size if DB_FILE.exists() else 0
        return counts
