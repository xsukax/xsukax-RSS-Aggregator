"""Integration coverage using deterministic RSS fixtures and local TestClient."""
import io
import os
import tempfile
from pathlib import Path

os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='xsukax-rss-tests-')
os.environ['SETUP_TOKEN']='test-setup-token-that-is-not-real'
os.environ['APP_SECRET']='test-secret-only'

from fastapi.testclient import TestClient
from app import worker
from app import main
from app.db import connection
from app.syndication import parse_feed,check_url,opml_parse,safe_html

RSS=b'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
 <channel><title>Product Updates</title><link>https://example.org/</link>
  <description>Software stories</description><language>en</language>
  <item><guid>one</guid><title>Open-source tools release a new update</title>
  <link>https://example.org/story/one</link><pubDate>Wed, 21 Oct 2026 11:00:00 +0300</pubDate>
  <category>Technology</category><category>Software</category><description><![CDATA[<p>News about development.</p>]]></description>
  <content:encoded><![CDATA[<p>The <b>software update</b> is available.</p>]]></content:encoded>
  <enclosure url="https://example.org/audio.mp3" type="audio/mpeg" length="12345"/>
  </item>
  <item><guid>two</guid><title>Daily economics report</title>
  <link>https://example.org/story/two</link><description>Economics summary</description></item>
 </channel></rss>'''
ATOM=b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom example</title>
<link href="https://example.net/"/><entry><id>atom-1</id><title>World news</title>
<link href="https://example.net/world"/><published>2026-10-20T12:00:00Z</published>
<summary>Atom summary</summary><category term="World"/></entry></feed>'''


def request(client,method,path,csrf=None,body=None,**opts):
    h={'x-csrf-token':csrf} if csrf else {}
    if body is not None:return client.request(method,path,headers=h,json=body,**opts)
    return client.request(method,path,headers=h,**opts)


def test_safe_parsers():
    meta,articles=parse_feed(RSS,'https://example.org/rss.xml')
    assert meta['title']=='Product Updates'
    assert len(articles)==2
    assert articles[0]['guid']=='one'
    assert articles[0]['categories']==['Technology','Software']
    assert len(articles[0]['enclosures'])==1
    assert '<b>' in articles[0]['content']
    m,a=parse_feed(ATOM,'https://example.net/atom.xml')
    assert m['title']=='Atom example' and len(a)==1
    assert a[0]['categories']==['World']
    for bad in ('http://localhost/feed','http://127.0.0.1/rss','http://10.0.0.5/feed','file:///etc/passwd','http://example.com:22/rss'):
        try:
            s=check_url(bad)
            if bad.startswith('http://10.') or bad.startswith('http://127.'):
                from app.syndication import public_ips
                public_ips(s.hostname,s.port or 80)
        except ValueError:pass
        else:raise AssertionError('Private/unsafe URL accepted '+bad)
    evil=b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel><title>&x;</title></channel></rss>'
    try:parse_feed(evil,'https://example.com/rss')
    except ValueError:pass
    else:raise AssertionError('Unsafe XML DTD accepted')
    assert 'onerror' not in safe_html('<img src="x" onerror="alert(1)">')
    assert 'javascript:' not in safe_html('<a href="javascript:alert(1)">click</a>')


def test_end_to_end(monkeypatch):
    def fake_fetch(url,headers=None,max_redirects=4):
        if headers and headers.get('If-None-Match')=='"fixture-v1"':
            return b'',{'status':304,'url':url,'etag':'"fixture-v1"','last_modified':'','content_type':'application/rss+xml'}
        return RSS,{'status':200,'url':url,'etag':'"fixture-v1"','last_modified':'','content_type':'application/rss+xml'}
    monkeypatch.setattr(worker,'fetch',fake_fetch)
    monkeypatch.setattr(main,'enqueue',lambda fid:False) # manually invoke worker for deterministic tests
    with TestClient(main.app) as client:
        st=client.get('/api/auth/state').json()
        assert not st['configured']
        assert client.get('/api/tree').status_code==401
        assert client.post('/api/auth/setup',json={'username':'admin','password':'strong password 123',
                                                   'confirm_password':'strong password 123'}).status_code==403
        assert client.post('/api/auth/setup',json={'username':'admin','password':'strong password 123',
                                                   'confirm_password':'different password',
                                                   'setup_token':os.environ['SETUP_TOKEN']}).status_code==400
        assert client.post('/api/auth/setup',json={'username':'admin','password':'short',
                                                   'confirm_password':'short',
                                                   'setup_token':os.environ['SETUP_TOKEN']}).status_code==400
        assert not client.get('/api/auth/state').json()['configured']
        setup=client.post('/api/auth/setup',json={'username':'site_admin','password':'strong password 123',
                                                   'confirm_password':'strong password 123',
                                                   'setup_token':os.environ['SETUP_TOKEN']})
        assert setup.status_code==200,setup.text
        assert client.get('/api/auth/state').json()['configured']
        with connection() as db:
            saved=db.execute('SELECT username,password_hash FROM admin WHERE id=1').fetchone()
            assert saved['username']=='site_admin'
            assert saved['password_hash'] != 'strong password 123'
            assert saved['password_hash'].startswith('$argon2id$')
        assert client.post('/api/auth/setup',json={'username':'another','password':'different password 123',
                                                   'confirm_password':'different password 123',
                                                   'setup_token':os.environ['SETUP_TOKEN']}).status_code==409
        csrf=client.get('/api/auth/state').json()['csrf']
        assert client.post('/api/accounts',json={'name':'Ignored without CSRF'}).status_code==403
        assert client.get('/healthz').json()['ok']
        acc=request(client,'POST','/api/accounts',csrf,{'name':'Tech News'}).json()['id']
        folder=request(client,'POST','/api/folders',csrf,{'name':'Technology','account_id':acc}).json()['id']
        feed_resp=request(client,'POST','/api/feeds',csrf,{'url':'https://example.org/rss.xml','account_id':acc,'folder_id':folder})
        assert feed_resp.status_code==200,feed_resp.text
        fid=feed_resp.json()['id']
        worker.refresh(fid)
        with connection() as db:
            assert db.execute('SELECT COUNT(*) n FROM articles WHERE feed_id=?',(fid,)).fetchone()['n']==2
            assert db.execute('SELECT etag FROM feeds WHERE id=?',(fid,)).fetchone()['etag']=='"fixture-v1"'
        resp=client.get('/api/articles?folder_id='+str(folder))
        assert resp.status_code==200,resp.text
        assert resp.json()['total']==2
        art=resp.json()['articles'][0]['id']
        search=client.get('/api/articles?search=Open-source')
        assert search.status_code==200,search.text
        assert search.json()['total']==1
        phrase=client.get('/api/articles?search=%22update%22')
        assert phrase.status_code==200,phrase.text
        assert phrase.json()['total']==1
        malformed=client.get('/api/articles?search=Open-source%20OR%20(')
        assert malformed.status_code==200,malformed.text
        item=client.get('/api/articles/'+str(art))
        assert item.status_code==200
        assert client.get('/api/tags').json()['tags']
        patch=request(client,'PATCH','/api/articles/'+str(art),csrf,{'starred':True,'bookmarked':True,'is_read':True})
        assert patch.status_code==200,patch.text
        assert client.get('/api/articles/'+str(art)).json()['starred']==1
        assert request(client,'POST',f'/api/articles/{art}/tags',csrf,{'name':'Selected'}).status_code==200
        assert request(client,'POST','/api/smart-folders',csrf,{'name':'Updates','query':'update','filters':{}}).status_code==200
        condition={'field':'title','op':'contains','value':'economics'}
        actions=[{'type':'tag','value':'Finance'},{'type':'star'}]
        rule=request(client,'POST','/api/rules',csrf,{'name':'Economics','conditions':condition,'actions':actions,'account_id':acc})
        assert rule.status_code==200,rule.text
        result=request(client,'POST',f'/api/rules/{rule.json()["id"]}/run',csrf)
        assert result.status_code==200 and result.json()['matched']==1
        xml=client.get('/api/export/opml?account_id='+str(acc))
        assert xml.status_code==200
        assert opml_parse(xml.content)[0]['url']=='https://example.org/rss.xml'
        # Real multipart upload: this endpoint caused the container startup crash.
        opml=b'<?xml version="1.0"?><opml version="2.0"><head><title>Feeds</title></head><body><outline text="Example Updates" type="rss" xmlUrl="https://example.net/news.xml" /></body></opml>'
        uploaded=client.post('/api/import/opml?account_id='+str(acc),
                             files={'file':('feeds.opml',opml,'text/xml')},
                             headers={'x-csrf-token':csrf})
        assert uploaded.status_code==200,uploaded.text
        assert uploaded.json()['added']==1
        repeat=client.post('/api/import/opml?account_id='+str(acc),
                           files={'file':('feeds.opml',opml,'text/xml')},
                           headers={'x-csrf-token':csrf})
        assert repeat.status_code==200 and repeat.json()['duplicates']==1
        csv=client.get('/api/export/articles.csv')
        assert csv.status_code==200 and 'Open-source' in csv.text
        reidx=request(client,'POST','/api/maintenance/reindex',csrf)
        assert reidx.status_code==200 and reidx.json()['indexed']==2
        settings=request(client,'PATCH','/api/settings',csrf,{'theme':'dark','retention_days':'60'})
        assert settings.status_code==200
        assert client.get('/api/settings').json()['settings']['theme']=='dark'
        worker.refresh(fid)
        with connection() as db:
            assert db.execute('SELECT COUNT(*) n FROM articles WHERE feed_id=?',(fid,)).fetchone()['n']==2
            assert db.execute('SELECT last_http_status FROM feeds WHERE id=?',(fid,)).fetchone()['last_http_status']==304
        assert client.get('/api/stats').status_code==200
        assert client.get('/api/logs').status_code==200
        assert request(client,'POST','/api/auth/logout',csrf).status_code==200
        assert client.get('/api/tree').status_code==401
        assert client.post('/api/auth/login',json={'username':'site_admin','password':'wrong'}).status_code==401
        assert client.post('/api/auth/login',json={'username':'admin','password':'strong password 123'}).status_code==401
        assert client.post('/api/auth/login',json={'username':'site_admin','password':'strong password 123'}).status_code==200
        assert client.get('/api/tree').status_code==200
        # A custom Unicode password must survive hashing and login byte-for-byte.
        unicode_password='my unique passphrase 2026 🔐 '
        csrf=client.get('/api/auth/state').json()['csrf']
        changed=request(client,'POST','/api/auth/password',csrf,
                        {'old_password':'strong password 123','new_password':unicode_password})
        assert changed.status_code==200,changed.text
        assert client.post('/api/auth/login',json={'username':'site_admin','password':'strong password 123'}).status_code==401
        assert client.post('/api/auth/login',json={'username':'site_admin','password':unicode_password}).status_code==200
        assert client.post('/api/auth/login',json={'username':'site_admin','password':unicode_password.strip()}).status_code==401
