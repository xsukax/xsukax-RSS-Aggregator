"""Bounded declarative feed message filtering and actions."""
import json
import time
from urllib.parse import urlsplit
import regex
from .db import log

FIELDS={'title','body','summary','author','source','url','domain','categories','tags','published','is_read','starred','bookmarked','archived','feed_id','account_id'}
OPS={'contains','not_contains','equals','not_equals','starts','ends','regex','not_regex','gt','lt','empty','not_empty'}
ACTIONS={'read','unread','star','unstar','bookmark','unbookmark','archive','unarchive','trash','tag','untag','move','notify','stop'}

def validate_conditions(tree,depth=0):
    if depth>5 or not isinstance(tree,dict):raise ValueError('Invalid or excessively nested rule')
    if 'logic' in tree:
        if tree['logic'] not in ('AND','OR','NOT'):raise ValueError('Unsupported rule logic')
        cs=tree.get('children',[])
        if not isinstance(cs,list) or not 1<=len(cs)<=20:raise ValueError('Rule group requires 1–20 conditions')
        for c in cs:validate_conditions(c,depth+1)
    else:
        if tree.get('field') not in FIELDS or tree.get('op') not in OPS:raise ValueError('Invalid filter field or operator')
        if len(str(tree.get('value','')))>500:raise ValueError('Filter value too long')
        if tree['op'] in ('regex','not_regex'):
            try:regex.compile(str(tree.get('value','')))
            except regex.error:raise ValueError('Invalid regular expression')

def validate_actions(actions):
    if not isinstance(actions,list) or not 1<=len(actions)<=12:raise ValueError('Rule requires 1–12 actions')
    for a in actions:
        if not isinstance(a,dict) or a.get('type') not in ACTIONS:raise ValueError('Invalid filter action')
        if a['type'] in ('tag','untag') and (not str(a.get('value','')).strip() or len(a['value'])>100):
            raise ValueError('Tag action requires a short name')
        if a['type']=='move' and not isinstance(a.get('value'),int):raise ValueError('Move requires a folder ID')

def article_field(article,field,tags=None):
    d=dict(article)
    if field=='body':return d.get('content','')
    if field=='source':return d.get('feed_title','')
    if field=='url':return d.get('link','')
    if field=='domain':return urlsplit(d.get('link','')).hostname or ''
    if field=='categories':return ' '.join(json.loads(d.get('categories') or '[]'))
    if field=='tags':return ' '.join(tags or [])
    return d.get(field,'')

def match(tree,article,tags=None,depth=0):
    if depth>5:return False
    if 'logic' in tree:
        results=[match(c,article,tags,depth+1) for c in tree['children']]
        return all(results) if tree['logic']=='AND' else (any(results) if tree['logic']=='OR' else not any(results))
    a=article_field(article,tree['field'],tags)
    b=tree.get('value','')
    op=tree['op']
    if op=='empty':return not a
    if op=='not_empty':return bool(a)
    if op in ('gt','lt'):
        try:return float(a)>float(b) if op=='gt' else float(a)<float(b)
        except (TypeError,ValueError):return False
    a=str(a or '').casefold();b=str(b or '').casefold()
    if op=='contains':return b in a
    if op=='not_contains':return b not in a
    if op=='equals':return a==b
    if op=='not_equals':return a!=b
    if op=='starts':return a.startswith(b)
    if op=='ends':return a.endswith(b)
    if op in ('regex','not_regex'):
        try:
            result=bool(regex.search(str(tree.get('value','')),a[:20000],flags=regex.IGNORECASE,timeout=0.025))
            return result if op=='regex' else not result
        except (regex.error,TimeoutError):return False
    return False

def apply_actions(db,article_id,actions,account_id):
    stop=False
    for action in actions:
        typ=action['type']; v=action.get('value')
        columns={'read':('is_read',1),'unread':('is_read',0),'star':('starred',1),'unstar':('starred',0),
                 'bookmark':('bookmarked',1),'unbookmark':('bookmarked',0),'archive':('archived',1),
                 'unarchive':('archived',0),'trash':('deleted',1)}
        if typ in columns:
            column,value=columns[typ]
            db.execute(f'UPDATE articles SET {column}=? WHERE id=?',(value,article_id))
            if typ=='trash':db.execute('UPDATE articles SET deleted_at=? WHERE id=?',(int(time.time()),article_id))
        elif typ in ('tag','untag'):
            if typ=='tag':
                db.execute('INSERT OR IGNORE INTO tags(name) VALUES(?)',(str(v).strip(),))
                db.execute('INSERT OR IGNORE INTO article_tags(article_id,tag_id) SELECT ?,id FROM tags WHERE name=?',(article_id,str(v).strip()))
            else:
                db.execute('DELETE FROM article_tags WHERE article_id=? AND tag_id IN (SELECT id FROM tags WHERE name=?)',(article_id,str(v).strip()))
        elif typ=='move':
            folder=db.execute('SELECT id FROM folders WHERE id=? AND account_id=?',(v,account_id)).fetchone()
            if folder:db.execute('UPDATE articles SET folder_override=? WHERE id=?',(v,article_id))
        elif typ=='notify':
            setting=db.execute("SELECT value FROM settings WHERE key='notifications'").fetchone()
            if setting and setting['value']=='0':continue
            db.execute('INSERT INTO notifications(article_id,created_at,message) VALUES(?,?,?)',
                       (article_id,int(time.time()),str(v or 'An article matched a filter')[:300]))
        elif typ=='stop':stop=True
    return stop

def run_rules(db,article_id,account_id,feed_id):
    article=db.execute('''SELECT a.*,f.title AS feed_title,f.account_id FROM articles a
                         JOIN feeds f ON f.id=a.feed_id WHERE a.id=?''',(article_id,)).fetchone()
    if not article:return 0
    matched=0
    rules=db.execute('''SELECT * FROM rules WHERE enabled=1 AND (account_id IS NULL OR account_id=?)
                         AND (feed_id IS NULL OR feed_id=?) ORDER BY priority,id''',(account_id,feed_id)).fetchall()
    for rule in rules:
        try:
            tags=[r['name'] for r in db.execute('SELECT t.name FROM tags t JOIN article_tags at ON at.tag_id=t.id WHERE at.article_id=?',(article_id,))]
            if match(json.loads(rule['conditions']),article,tags):
                matched+=1
                if apply_actions(db,article_id,json.loads(rule['actions']),account_id):break
        except Exception as exc:
            log('ERROR','rule '+str(rule['id']),str(exc)[:250])
    return matched
