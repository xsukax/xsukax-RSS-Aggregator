"""Pinned-IP network fetching, RSS/Atom parsing, and OPML handling."""
import base64
import calendar
import gzip
import hashlib
import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import bleach
from bs4 import BeautifulSoup
from defusedxml import ElementTree as SafeET

MAX_BYTES = 2 * 1024 * 1024
MAX_EXPANDED = 5 * 1024 * 1024
PORTS = {80,443,8080,8443}
TAG_ALLOW = ['p','div','br','hr','blockquote','pre','code','ul','ol','li','strong','b','em','i','u','h1','h2','h3','h4','h5','h6','a','img','figure','figcaption','table','thead','tbody','tr','td','th','span','sup','sub']
ATTR_ALLOW={'a':['href','title'],'img':['src','alt','title','width','height'],'*':['dir','lang']}


def safe_html(value):
    return bleach.clean(value or '', tags=TAG_ALLOW, attributes=ATTR_ALLOW,
                        protocols=['https','http','mailto'], strip=True)


def text_content(value):
    return BeautifulSoup(value or '', 'html.parser').get_text(' ',strip=True)


def check_url(url):
    if not isinstance(url,str) or len(url)>2048: raise ValueError('URL must be shorter than 2048 characters')
    s=urlsplit(url.strip())
    if s.scheme not in ('http','https') or not s.hostname: raise ValueError('Only HTTP and HTTPS feed URLs are supported')
    if s.username or s.password: raise ValueError('Credentials in feed URLs are not supported')
    if any(ch in url for ch in '\r\n\t\\'): raise ValueError('Unsafe characters in URL')
    try: port=s.port or (443 if s.scheme=='https' else 80)
    except ValueError: raise ValueError('Invalid port')
    if port not in PORTS: raise ValueError('Feed URLs must use ports 80, 443, 8080, or 8443')
    if s.hostname.endswith('.local') or s.hostname.lower() in ('localhost','localhost.localdomain'):
        raise ValueError('Local and private destinations are blocked')
    return s


def public_ips(host,port):
    try: infos=socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc: raise ValueError('DNS lookup failed') from exc
    addresses=[]
    for entry in infos:
        ip=entry[4][0]
        try:
            if not ipaddress.ip_address(ip).is_global:
                raise ValueError('DNS resolution returned a non-public IP address')
        except ValueError as exc:
            if 'not a valid' in str(exc): raise ValueError('Invalid address resolved') from exc
            raise
        if ip not in addresses: addresses.append(ip)
    if not addresses: raise ValueError('No public addresses available')
    # IPv4-first avoids unnecessary failures on tiny VPS hosts without working IPv6 routing.
    return sorted(addresses,key=lambda address: ':' in address)


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self,host,port,ip,timeout):
        super().__init__(host,port,timeout=timeout)
        self.pinned_ip=ip
    def connect(self):
        self.sock=socket.create_connection((self.pinned_ip,self.port),self.timeout)

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,port,ip,timeout):
        super().__init__(host,port,timeout=timeout,context=ssl.create_default_context())
        self.pinned_ip=ip
    def connect(self):
        raw=socket.create_connection((self.pinned_ip,self.port),self.timeout)
        try: self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def unpack(data,encoding):
    if encoding=='gzip':
        decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
    elif encoding=='deflate':
        decoder=zlib.decompressobj()
    else:
        return data
    try:
        data=decoder.decompress(data,MAX_EXPANDED+1)
        if len(data)>MAX_EXPANDED or decoder.unconsumed_tail:
            raise ValueError('Uncompressed response too large')
        data+=decoder.flush(MAX_EXPANDED+1-len(data))
        if len(data)>MAX_EXPANDED: raise ValueError('Uncompressed response too large')
        return data
    except zlib.error as exc: raise ValueError('Invalid compressed response') from exc


def fetch(url,headers=None,max_redirects=4):
    """Fetch untrusted HTTP URLs with DNS validation and a pinned TCP destination.

    No ambient HTTP proxy and no access to internal or metadata IP addresses.
    Validate every redirected URL separately. Only explicitly supported public ports.
    """
    headers=headers or {}
    for _ in range(max_redirects+1):
        parsed=check_url(url)
        host=parsed.hostname
        port=parsed.port or (443 if parsed.scheme=='https' else 80)
        ip=public_ips(host,port)[0]
        c=(PinnedHTTPS if parsed.scheme=='https' else PinnedHTTP)(host,port,ip,timeout=12)
        path=urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
        host_header=('['+host+']' if ':' in host else host) + ((':'+str(port)) if port not in (80,443) else '')
        req_headers={'Host':host_header,'User-Agent':'xsukax-RSS-Aggregator/0.1 (+self-hosted feed reader)',
                     'Accept':'application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.2',
                     'Accept-Encoding':'gzip, deflate','Connection':'close'}
        for k,v in headers.items():
            if k.lower() in ('if-none-match','if-modified-since') and isinstance(v,str):
                req_headers[k]=v[:500]
        try:
            c.request('GET',path,headers=req_headers)
            resp=c.getresponse()
            status=resp.status
            location=resp.getheader('Location')
            info={'status':status,'etag':(resp.getheader('ETag') or '')[:500],
                  'last_modified':(resp.getheader('Last-Modified') or '')[:500],
                  'content_type':(resp.getheader('Content-Type') or '').lower(),'url':url}
            if status in (301,302,303,307,308):
                if not location: raise ValueError('Redirect missing Location header')
                url=urljoin(url,location)
                continue
            if status==304: return b'',info
            if status!=200: raise ValueError('Feed HTTP status '+str(status))
            length=resp.getheader('Content-Length')
            if length and length.isdigit() and int(length)>MAX_BYTES: raise ValueError('Feed response exceeds size limit')
            raw=resp.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES: raise ValueError('Feed response exceeds size limit')
            data=unpack(raw,(resp.getheader('Content-Encoding') or '').lower())
            return data,info
        except (OSError,http.client.HTTPException,ssl.SSLError) as exc:
            raise ValueError('Network or TLS request failed: '+str(exc)[:130]) from exc
        finally: c.close()
    raise ValueError('Feed exceeded redirect limit')


def local(tag): return tag.rsplit('}',1)[-1].lower()

def child(parent,name):
    for x in parent:
        if local(x.tag)==name: return x
    return None

def children(parent,name): return [x for x in parent if local(x.tag)==name]

def val(parent,name,default=''):
    el=child(parent,name)
    return ''.join(el.itertext()).strip()[:250000] if el is not None else default

def date_ts(value):
    if not value: return 0
    try:
        dt=parsedate_to_datetime(value)
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError,TypeError,OverflowError): pass
    try:
        v=value.strip().replace('Z','+00:00')
        dt=datetime.fromisoformat(v)
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError,TypeError,OverflowError): return 0

def atom_link(el,rel='alternate'):
    for link in children(el,'link'):
        if link.attrib.get('rel','alternate')==rel:
            return link.attrib.get('href','')
    return ''

def xml_content(el,name):
    item=child(el,name)
    if item is None:return ''
    return (item.text or '').strip() if len(item)==0 else ''.join(SafeET.tostring(x,encoding='unicode') for x in item)


def parse_feed(data,url):
    if len(data)>MAX_EXPANDED: raise ValueError('Feed too large')
    try: root=SafeET.fromstring(data,forbid_dtd=True,forbid_entities=True,forbid_external=True)
    except Exception as exc: raise ValueError('Invalid or unsafe feed XML: '+str(exc)[:130]) from exc
    root_type=local(root.tag)
    if root_type=='feed':
        is_atom=True
        meta={'title':val(root,'title') or url, 'site_url':atom_link(root), 'description':val(root,'subtitle'),
              'language':root.attrib.get('{http://www.w3.org/XML/1998/namespace}lang','')}
        nodes=children(root,'entry')
    elif root_type in ('rss','rdf'):
        is_atom=False
        channel=child(root,'channel')
        if channel is None: raise ValueError('RSS feed has no channel')
        meta={'title':val(channel,'title') or url,'site_url':val(channel,'link'),
              'description':val(channel,'description'),'language':val(channel,'language')}
        nodes=children(channel,'item') or children(root,'item')
    else: raise ValueError('Not an RSS or Atom feed')
    articles=[]
    for item in nodes[:1000]:
        if is_atom:
            title=val(item,'title') or '(Untitled)'
            link=atom_link(item)
            guid=val(item,'id') or link
            summary=xml_content(item,'summary')
            content=xml_content(item,'content') or summary
            author_el=child(item,'author')
            author=val(author_el,'name') if author_el is not None else ''
            published=date_ts(val(item,'published'))
            updated=date_ts(val(item,'updated'))
            cats=[c.attrib.get('term','') for c in children(item,'category') if c.attrib.get('term')]
            ens=[{'url':urljoin(url,l.attrib['href']),'type':l.attrib.get('type',''),'length':l.attrib.get('length','')}
                 for l in children(item,'link') if l.attrib.get('rel')=='enclosure' and l.attrib.get('href','').startswith(('http://','https://'))]
        else:
            title=val(item,'title') or '(Untitled)'
            link=val(item,'link')
            guid=val(item,'guid') or link
            summary=val(item,'description')
            content=val(item,'encoded') or summary
            author=val(item,'creator') or val(item,'author')
            published=date_ts(val(item,'pubdate') or val(item,'date'))
            updated=date_ts(val(item,'modified') or val(item,'updated'))
            cats=[(c.text or '').strip() for c in children(item,'category') if (c.text or '').strip()]
            ens=[{'url':urljoin(url,e.attrib['url']),'type':e.attrib.get('type',''),'length':e.attrib.get('length','')}
                 for e in children(item,'enclosure') if e.attrib.get('url','').startswith(('http://','https://'))]
        link=urljoin(url,link) if link else ''
        if link and not link.startswith(('http://','https://')): link=''
        guid=(guid or hashlib.sha256((title+link+str(published)).encode()).hexdigest())[:1024]
        summary=safe_html(summary)[:150000]; content=safe_html(content)[:300000]
        signature=hashlib.sha256((title+summary+content).encode('utf-8')).hexdigest()
        articles.append({'guid':guid,'title':text_content(title)[:400], 'link':link[:2048],
                         'author':text_content(author)[:300],'summary':summary,'content':content,
                         'published':published,'updated':updated,'categories':cats[:50],
                         'enclosures':ens[:30],'signature':signature})
    return meta,articles


def discover(data,url):
    """Return candidate RSS/Atom feed links from an HTML webpage."""
    soup=BeautifulSoup(data[:MAX_BYTES],'html.parser')
    found=[];seen=set()
    for a in soup.find_all('link',href=True):
        typ=(a.get('type') or '').lower()
        if 'rss+xml' not in typ and 'atom+xml' not in typ and 'rdf+xml' not in typ: continue
        target=urljoin(url,a['href'])
        try: check_url(target)
        except ValueError:continue
        if target in seen:continue
        seen.add(target); found.append({'url':target,'title':a.get('title') or target})
    return found[:40]


def opml_export(feeds,folders):
    from xml.etree import ElementTree as ET
    opml=ET.Element('opml',version='2.0');head=ET.SubElement(opml,'head')
    ET.SubElement(head,'title').text='xsukax RSS Aggregator'
    body=ET.SubElement(opml,'body')
    fmap={}
    for f in folders:
        parent=fmap.get(f['parent_id'],body)
        fmap[f['id']]=ET.SubElement(parent,'outline',text=f['name'],title=f['name'])
    for f in feeds:
        p=fmap.get(f['folder_id'],body)
        ET.SubElement(p,'outline',text=f['title'],title=f['title'],type='rss',xmlUrl=f['url'],htmlUrl=f['site_url'] or '')
    return ET.tostring(opml,encoding='utf-8',xml_declaration=True)


def opml_parse(content):
    root=SafeET.fromstring(content,forbid_dtd=True,forbid_entities=True,forbid_external=True)
    if local(root.tag)!='opml':raise ValueError('Invalid OPML document')
    body=child(root,'body')
    if body is None:raise ValueError('OPML has no body')
    found=[]
    def walk(node,path):
        for o in children(node,'outline'):
            url=o.attrib.get('xmlUrl','') or o.attrib.get('xmlurl','')
            name=(o.attrib.get('title') or o.attrib.get('text') or url)[:300]
            if url:
                check_url(url)
                found.append({'url':url,'title':name,'folders':path[:]})
            else:walk(o,path+[name])
            if len(found)>2000:raise ValueError('OPML contains too many feeds')
    walk(body,[])
    return found
