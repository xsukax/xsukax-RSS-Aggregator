'use strict';
const $ = (s,root=document)=>root.querySelector(s);
const $$ = (s,root=document)=>[...root.querySelectorAll(s)];
const esc = (s)=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const attr=esc;
const when=(ts)=>{if(!ts)return 'Unknown date';const d=new Date(ts*1000);return Number.isNaN(+d)?'Unknown date':d.toLocaleDateString(undefined,{month:'short',day:'numeric',year:d.getFullYear()===new Date().getFullYear()?undefined:'numeric'});};
const validURL=(u)=>{try{let x=new URL(u);return ['http:','https:'].includes(x.protocol)?x.href:'#'}catch{return '#'}};
const state={csrf:'',tree:null,tags:[],settings:{},view:{collection:'all'},filters:{unread:false,starred:false,bookmarked:false,enclosures:false},pinned:false,
  search:'',sort:'published',order:'desc',page:1,total:0,articles:[],current:null,detail:null,selected:new Set(),mode:'summary',account:1,navOpen:false};
const notice=(message,error=false)=>{let t=$('#toast');t.textContent=message;t.className='toast'+(error?' error':'');clearTimeout(window.toastT);window.toastT=setTimeout(()=>t.classList.add('hidden'),4200);};
async function api(path,opts={}){
  let headers={...(opts.headers||{})};
  if(opts.body && !(opts.body instanceof FormData))headers['Content-Type']='application/json';
  if(opts.method && !['GET','HEAD'].includes(opts.method.toUpperCase()))headers['X-CSRF-Token']=state.csrf;
  let res=await fetch('/api'+path,{...opts,headers,credentials:'same-origin'});
  if(res.status===401){showAuth(true);throw new Error('Session expired. Please sign in.');}
  let contentType=res.headers.get('content-type')||'';
  let data=contentType.includes('application/json')?await res.json():await res.text();
  if(!res.ok)throw new Error(typeof data==='object'?data.error||data.detail||res.statusText:String(data).slice(0,200));
  return data;
}
async function task(fn){try{return await fn()}catch(e){console.error(e);notice(e.message||String(e),true);return null}}
function formObj(form){return Object.fromEntries(new FormData(form));}
function showAuth(show){$('#auth').classList.toggle('hidden',!show);$('#app').classList.toggle('hidden',show);}
async function boot(){
  try{
    const r=await fetch('/api/auth/state',{credentials:'same-origin'});const d=await r.json();state.csrf=d.csrf||'';
    if(!d.authenticated){showAuth(true);
      const setup=!d.configured;
      $('#token-row').classList.toggle('hidden',!setup);
      $('#confirm-row').classList.toggle('hidden',!setup);
      $('#setup-hint').classList.toggle('hidden',!setup);
      $('#auth-error').classList.add('hidden');
      $('#auth-caption').textContent=setup?'First run: create your administrator account':'Sign in to your RSS workspace';
      $('#auth-submit').textContent=setup?'Create administrator':'Sign in';
      $('#auth-form').elements.confirm_password.required=setup;
      $('#auth-form').elements.setup_token.required=setup;
      $('#auth-form').elements.password.autocomplete=setup?'new-password':'current-password';
      return;}
    showAuth(false);await hydrate();
  }catch(e){showAuth(true);notice('Server unavailable: '+e.message,true)}
}
async function hydrate(){
  await Promise.all([loadTree(),loadTags(),loadSettings()]);
  await loadArticles();applyTheme();
}
async function loadTree(){state.tree=await api('/tree');renderNav();}
async function loadTags(){state.tags=(await api('/tags')).tags;renderNav();}
async function loadSettings(){state.settings=(await api('/settings')).settings;applyTheme();}
function applyTheme(){let t=state.settings.theme||'auto';
  document.body.classList.toggle('dark',t==='dark'||(t==='auto'&&window.matchMedia('(prefers-color-scheme:dark)').matches));}
function navItem(type,id,title,icon,count=0,depth=0,extras=''){
  let active=(state.view.type===type && String(state.view.id)===String(id)) || (type==='collection'&&!state.view.type&&state.view.collection===id);
  return `<button class="nav-row ${active?'selected':''} ${extras}" data-nav="${esc(type)}" data-id="${attr(id)}" style="padding-left:${10+Math.min(depth,6)*14}px" title="${attr(title)}"><span class="glyph">${icon}</span><span class="nav-name" dir="auto">${esc(title)}</span>${count?`<span class="count">${count}</span>`:''}</button>`;
}
function renderNav(){if(!state.tree)return;
  const root=$('#smart-nav');root.innerHTML=[['all','All articles','▤',state.tree.counts.total],['unread','Unread','◉',state.tree.counts.unread],['starred','Starred','★',0],['bookmarked','Bookmarks','◆',0],['archive','Archive','▣',0],['trash','Trash','♲',0]].map(x=>navItem('collection',...x)).join('');
  let entries=[];
  function drawFolder(account,folder,parent,depth){
    entries.push(navItem('folder',folder.id,folder.name,'▸',0,depth));
    const ch=state.tree.folders.filter(f=>f.account_id===account.id&&f.parent_id===folder.id);
    for(let f of ch)drawFolder(account,f,folder.id,depth+1);
    for(let f of state.tree.feeds.filter(f=>f.account_id===account.id&&f.folder_id===folder.id))
      entries.push(navItem('feed',f.id,f.title,f.last_error?'⚠':(f.paused?'◌':'◍'),f.unread,depth+1,f.last_error?'error':''));
  }
  for(let a of state.tree.accounts){
    entries.push(navItem('account',a.id,a.name,a.paused?'◌':'▦',state.tree.feeds.filter(f=>f.account_id===a.id).reduce((n,f)=>n+f.unread,0)));
    for(let f of state.tree.folders.filter(f=>f.account_id===a.id&&f.parent_id===null))drawFolder(a,f,null,1);
    for(let f of state.tree.feeds.filter(f=>f.account_id===a.id&&f.folder_id===null))
      entries.push(navItem('feed',f.id,f.title,f.last_error?'⚠':(f.paused?'◌':'◍'),f.unread,1,f.last_error?'error':''));
  }
  $('#account-nav').innerHTML=entries.join('');
  $('#saved-nav').innerHTML=state.tree.smart.map(f=>navItem('smart',f.id,f.name,'⌕')).join('')||'<p class="hint" style="padding:0 14px">No saved searches</p>';
  $('#tags-nav').innerHTML=state.tags.map(f=>navItem('tag',f.id,f.name,'●',f.count)).join('');
  $$('[data-nav]').forEach(b=>{
    b.addEventListener('click',()=>changeView(b.dataset.nav,b.dataset.id,b.querySelector('.nav-name').textContent));
    if(b.dataset.nav==='feed'){b.draggable=true;b.addEventListener('dragstart',e=>e.dataTransfer.setData('text/feed-id',b.dataset.id));}
    if(b.dataset.nav==='folder'||b.dataset.nav==='account'){
      b.addEventListener('dragover',e=>{if(e.dataTransfer.types.includes('text/feed-id')){e.preventDefault();b.classList.add('drag-over')}});
      b.addEventListener('dragleave',()=>b.classList.remove('drag-over'));
      b.addEventListener('drop',async e=>{e.preventDefault();b.classList.remove('drag-over');let id=Number(e.dataTransfer.getData('text/feed-id'));
        let dest=b.dataset.nav==='folder'?Number(b.dataset.id):null;
        let feed=state.tree.feeds.find(f=>f.id===id);
        if(b.dataset.nav==='account' && feed?.account_id!==Number(b.dataset.id)){notice('To move between accounts, add the feed under that account',true);return;}
        if(feed)await task(async()=>{await api(`/feeds/${id}`,{method:'PATCH',body:JSON.stringify({folder_id:dest})});await loadTree();notice('Feed moved');});
      });
    }
  });
}
function changeView(type,id,title){
  state.view=type==='collection'?{collection:id}:{type,id:Number(id),collection:'all'};
  state.viewTitle=title;state.page=1;state.current=null;state.detail=null;state.selected.clear();
  if(!state.pinned){state.filters={unread:false,starred:false,bookmarked:false,enclosures:false};state.search='';$('#search').value='';}
  $('#workspace').classList.remove('show-reader');$('#navigation').classList.remove('open');
  $('#reader-empty').classList.remove('hidden');$('#reader-content').classList.add('hidden');renderNav();renderFilterbar();task(loadArticles);
}
function renderFilterbar(){
  $$('[data-filter]').forEach(b=>b.classList.toggle('on',!!state.filters[b.dataset.filter]));
  $('#pin-filter').classList.toggle('on',state.pinned);
}
function query(){const p=new URLSearchParams({page:state.page,limit:50,sort:state.sort,order:state.order});
  if(state.view.type==='account')p.set('account_id',state.view.id);
  if(state.view.type==='folder')p.set('folder_id',state.view.id);
  if(state.view.type==='feed')p.set('feed_id',state.view.id);
  if(state.view.type==='smart')p.set('smart_id',state.view.id);
  if(state.view.type==='tag')p.set('tag_id',state.view.id);
  if(state.view.type!=='tag'&&!state.view.type)p.set('collection',state.view.collection||'all');
  if(state.view.type==='tag')p.set('collection','all');
  if(state.filters.unread)p.set('unread_only','true');
  if(state.filters.starred)p.set('starred_only','true');
  if(state.filters.bookmarked)p.set('bookmarked_only','true');
  if(state.filters.enclosures)p.set('has_media','true');
  if(state.search.trim())p.set('search',state.search.trim());
  return p;
}
async function loadArticles(){let response=await api('/articles?'+query());
  let list=response.articles;
  // Quick Filters execute on the server before pagination.
  state.articles=list;state.total=response.total;
  const title=state.viewTitle||'All articles';$('#view-title').textContent=title;
  $('#view-info').textContent=`${response.total.toLocaleString()} articles in this collection`;
  $('#status').textContent=`${state.tree?.feeds.length||0} subscriptions · ${state.tree?.counts?.unread||0} unread`;
  renderList();renderNav();
}
function renderList(){
  $('#article-items').innerHTML=state.articles.length?state.articles.map(a=>`<div class="article-row ${a.is_read?'':'unread'} ${state.current===a.id?'active':''}" data-aid="${a.id}" role="option" aria-selected="${state.current===a.id}">
    <input type="checkbox" class="select-row" data-id="${a.id}" ${state.selected.has(a.id)?'checked':''} aria-label="Select article">
    <div class="art-main" dir="auto"><div class="art-top"><span class="art-source" dir="auto">${esc(a.feed_title)}</span><span class="art-date">${when(a.published||a.fetched)}</span></div>
      <div class="art-title">${esc(a.title)}</div><div class="art-excerpt">${esc(a.excerpt)}</div>
      <div class="art-actions"><span>${a.is_read?'':'<i class="read-dot"></i>'}${a.archived?'Archived · ':''}${a.has_enclosures?'▣ Media · ':''}${esc(a.tags||'')}</span>
        <span class="push"></span><button class="quick-star ${a.starred?'on':''}" title="Star article">${a.starred?'★':'☆'}</button><button class="quick-bookmark ${a.bookmarked?'on':''}" title="Bookmark article">${a.bookmarked?'◆':'◇'}</button></div>
    </div></div>`).join(''):'<div class="empty-list"><div style="font-size:40px">▤</div><strong>No articles here yet</strong><p>Add a feed, refresh subscriptions, or adjust your filters.</p></div>';
  $$('[data-aid]').forEach(row=>{
    let id=Number(row.dataset.aid);
    row.addEventListener('click',e=>{if(e.target.closest('button,input'))return;task(()=>openArticle(id));});
    $('.select-row',row).addEventListener('change',e=>{e.target.checked?state.selected.add(id):state.selected.delete(id);updateBulk();});
    $('.quick-star',row).addEventListener('click',()=>task(()=>toggleFromList(id,'starred')));
    $('.quick-bookmark',row).addEventListener('click',()=>task(()=>toggleFromList(id,'bookmarked')));
  });
  $('#pager').textContent=`${state.total===0?0:(state.page-1)*50+1}–${Math.min(state.total,state.page*50)} of ${state.total}`;
  $('#prev-page').disabled=state.page<=1;$('#next-page').disabled=state.page*50>=state.total;
  $('#select-all').checked=state.articles.length>0&&state.articles.every(a=>state.selected.has(a.id));updateBulk();
}
function updateBulk(){
  $('#bulkbar').classList.toggle('hidden',!state.selected.size);
  $('#selection-count').textContent=state.selected.size+' selected';
}
async function toggleFromList(id,field){let a=state.articles.find(x=>x.id===id);if(!a)return;
  await api(`/articles/${id}`,{method:'PATCH',body:JSON.stringify({[field]:!a[field]})});a[field]=a[field]?0:1;
  renderList();if(state.current===id)await openArticle(id,false);}
async function openArticle(id,mark=true){
  let d=await api('/articles/'+id);state.current=id;state.detail=d;state.mode=state.settings.read_mode||'summary';
  if(mark&&!d.is_read&&state.settings.mark_read!=='0'){
    await api('/articles/'+id,{method:'PATCH',body:JSON.stringify({is_read:true})});d.is_read=1;
    let a=state.articles.find(x=>x.id===id);if(a)a.is_read=1;
  }
  $('#reader-empty').classList.add('hidden');$('#reader-content').classList.remove('hidden');
  $('#reader-origin').textContent=d.feed_title;$('#reader-title').textContent=d.title;
  $('#reader-meta').textContent=[d.author?'By '+d.author:'',when(d.published||d.fetched),d.revision>1?'Updated '+(d.revision-1)+'×':''].filter(Boolean).join(' · ');
  $('#reader-open').href=validURL(d.link);$('#reader-open').classList.toggle('hidden',!d.link);
  renderReadButtons();renderReading();
  $('#workspace').classList.add('show-reader');renderList();task(loadTree);
}
function renderReadButtons(){if(!state.detail)return;let d=state.detail;
  $('#reader-read').textContent=d.is_read?'◌':'◉';$('#reader-read').title=d.is_read?'Mark unread':'Mark read';
  $('#reader-star').textContent=d.starred?'★':'☆';$('#reader-star').classList.toggle('on',!!d.starred);
  $('#reader-bookmark').textContent=d.bookmarked?'◆':'◇';$('#reader-bookmark').classList.toggle('on',!!d.bookmarked);
  $('#reader-archive').textContent=d.archived?'▣':'▢';$('#reader-archive').title=d.archived?'Unarchive':'Archive';
  $('#reader-trash').textContent=d.deleted?'↶':'♲';$('#reader-trash').title=d.deleted?'Restore':'Move to Trash';
}
function renderReading(){let d=state.detail;if(!d)return;
  $$('[data-mode]').forEach(b=>b.classList.toggle('selected',b.dataset.mode===state.mode));
  let content=state.mode==='summary'?(d.summary||d.content):d.content||d.summary;
  let r=$('#reader-html');
  r.classList.toggle('plain',state.mode==='plain');
  if(state.mode==='plain'){let el=document.createElement('div');el.innerHTML=content;r.textContent=el.textContent;}
  else r.innerHTML=content||'<p>No syndicated article text was provided. Select “Website” to read the original page.</p>';
  $('#reader-enclosures').innerHTML=d.enclosures.map(e=>`<div class="enclosure-item">▣ <a target="_blank" rel="noopener noreferrer" href="${attr(validURL(e.url))}">${esc(e.type||'Download enclosure')}</a> ${esc(e.length||'')}</div>`).join('');
  $('#reader-tag-list').innerHTML=d.tags.map(t=>`<span class="tag-pill" style="--tag:${attr(t.color)}">${esc(t.name)}<button type="button" data-removetag="${attr(t.name)}" title="Remove tag">×</button></span>`).join('');
  $$('[data-removetag]').forEach(b=>b.addEventListener('click',()=>task(async()=>{await api(`/articles/${d.id}/tags`,{method:'POST',body:JSON.stringify({name:b.dataset.removetag,remove:true})});
    state.detail=await api('/articles/'+d.id);renderReading();await loadTags();})));
}
async function articleChange(field,value){if(!state.detail)return;let d=state.detail;
  await api(`/articles/${d.id}`,{method:'PATCH',body:JSON.stringify({[field]:value})});d[field]=value;
  renderReadButtons();await Promise.all([loadArticles(),loadTree()]);}
function modal(title,html){$('#modal-title').textContent=title;$('#modal-body').innerHTML=html;$('#modal-overlay').classList.remove('hidden');}
function closeModal(){$('#modal-overlay').classList.add('hidden');$('#modal-body').innerHTML='';}
function options(items,current=null,blank=false){return (blank?'<option value="">— None —</option>':'')+items.map(x=>`<option value="${x.id}" ${String(x.id)===String(current)?'selected':''}>${esc(x.name||x.title)}</option>`).join('');}
function accountOptions(id=state.account){return options(state.tree.accounts,id)}
function folderOptions(accountId,current=null){return options(state.tree.folders.filter(f=>f.account_id===Number(accountId)),current,true)}
function selectedAccount(){if(state.view.type==='feed')return state.tree.feeds.find(f=>f.id===state.view.id)?.account_id||state.account;
 if(state.view.type==='folder')return state.tree.folders.find(f=>f.id===state.view.id)?.account_id||state.account;
 if(state.view.type==='account')return state.view.id;return state.account;}
function formButtons(text='Save'){return `<div class="form-buttons"><button type="button" class="small-btn" data-cancel>Cancel</button><button class="primary" type="submit">${esc(text)}</button></div>`}
function formCancel(){ $$('[data-cancel]').forEach(b=>b.addEventListener('click',closeModal));}
function addFeed(){
  modal('Subscribe to a feed',`<p class="hint">Enter a direct RSS/Atom address, or a website URL and choose Discover.</p>
    <form id="feed-form"><label>Feed or website URL<input name="url" type="url" placeholder="https://example.com/feed.xml" required></label>
      <div class="form-row"><button type="button" id="discover-btn" class="small-btn">⌕ Discover feeds</button><span class="hint" id="discover-status"></span></div><div id="discovered"></div>
      <label>Custom title (optional)<input name="title" placeholder="Uses publisher title after the first update"></label>
      <div class="form-row"><label>Account<select name="account_id" id="feed-account">${accountOptions(selectedAccount())}</select></label><label>Folder<select name="folder_id" id="feed-folder">${folderOptions(selectedAccount())}</select></label></div>
      ${formButtons('Subscribe')}</form>`);
  formCancel();$('#feed-account').addEventListener('change',()=>$('#feed-folder').innerHTML=folderOptions($('#feed-account').value));
  $('#discover-btn').addEventListener('click',()=>task(async()=>{
    let u=$('#feed-form [name=url]').value;$('#discover-status').textContent='Checking…';
    const r=await api('/discover?url='+encodeURIComponent(u));
    $('#discover-status').textContent=r.feeds.length?`${r.feeds.length} feed(s) found`:'No feeds detected';
    $('#discovered').innerHTML=r.feeds.map(f=>`<button class="small-btn discovered" type="button" data-url="${attr(f.url)}">${esc(f.title)}</button>`).join(' ');
    $$('.discovered').forEach(b=>b.addEventListener('click',()=>{$('#feed-form [name=url]').value=b.dataset.url;$('#feed-form [name=title]').value=b.textContent;}));
  }));
  $('#feed-form').addEventListener('submit',e=>{e.preventDefault();task(async()=>{
    let d=formObj(e.target);d.account_id=+d.account_id;d.folder_id=d.folder_id?+d.folder_id:null;
    await api('/feeds',{method:'POST',body:JSON.stringify(d)});closeModal();await loadTree();notice('Subscription added; first refresh queued');
  });});
}
function addAccount(){modal('Create feed account',`<form id="account-form"><label>Account name<input name="name" required maxlength="300" placeholder="News and blogs"></label>${formButtons('Create account')}</form>`);
 formCancel();$('#account-form').onsubmit=e=>{e.preventDefault();task(async()=>{let a=await api('/accounts',{method:'POST',body:JSON.stringify(formObj(e.target))});state.account=a.id;closeModal();await loadTree();notice('Account created');});};}
function addFolder(){let aid=selectedAccount();modal('Create folder',`<form id="folder-form"><label>Folder name<input name="name" required placeholder="Technology"></label>
   <label>Account<select id="folder-account" name="account_id">${accountOptions(aid)}</select></label>
   <label>Parent folder<select id="parent-folder" name="parent_id">${folderOptions(aid)}</select></label>${formButtons('Create folder')}</form>`);
 formCancel();$('#folder-account').onchange=()=>$('#parent-folder').innerHTML=folderOptions($('#folder-account').value);
 $('#folder-form').onsubmit=e=>{e.preventDefault();task(async()=>{let d=formObj(e.target);d.account_id=+d.account_id;d.parent_id=d.parent_id?+d.parent_id:null;
   await api('/folders',{method:'POST',body:JSON.stringify(d)});closeModal();await loadTree();notice('Folder created');});};}
function manageCurrent(){let t=state.view.type,id=state.view.id;
 if(t==='feed')return manageFeed(id);
 if(t==='folder')return manageFolder(id);
 if(t==='account')return manageAccount(id);
 if(t==='smart')return manageSmart(id);
 notice('Select a feed, folder, account or saved search to manage it');}
function manageFeed(id){let f=state.tree.feeds.find(x=>x.id===id);if(!f)return;
 modal('Manage subscription',`<form id="manage-feed"><label>Feed title<input name="title" value="${attr(f.title)}" required></label>
  <label>URL<input name="url" type="url" value="${attr(f.url)}" required></label>
  <label>Folder<select name="folder_id">${folderOptions(f.account_id,f.folder_id)}</select></label>
  <div class="form-row"><label>Refresh every (minutes; 0=account default)<input name="interval_min" type="number" min="0" max="10080" value="${f.interval_min}"></label>
    <label>Display mode<select name="view_mode"><option value="summary" ${f.view_mode==='summary'?'selected':''}>Summary</option><option value="content" ${f.view_mode==='content'?'selected':''}>Feed content</option><option value="plain" ${f.view_mode==='plain'?'selected':''}>Plain text</option></select></label></div>
  <label><input name="enabled" type="checkbox" ${f.enabled?'checked':''}> Subscription enabled</label>
  <label><input name="paused" type="checkbox" ${f.paused?'checked':''}> Pause automatic refresh</label>
  <label><input name="auto_tag" type="checkbox" ${f.auto_tag?'checked':''}> Convert publisher categories to tags</label>
  <label>Auto-tag prefix<input name="tag_prefix" value="${attr(f.tag_prefix)}"></label>
  ${f.last_error?`<p class="hint" style="color:#c75b56">Last error: ${esc(f.last_error)}</p>`:''}
  <p class="hint">Last successful refresh: ${f.last_success?when(f.last_success):'Not yet'}. HTTP ${f.last_http_status||'—'}. ${f.total} stored articles.</p>
  ${formButtons('Save changes')}</form><hr class="sep"><div class="form-row"><button id="refresh-feed" class="small-btn">⟳ Refresh now</button><button id="delete-feed" class="danger">Delete subscription and its articles</button></div>`);
 formCancel();$('#manage-feed').onsubmit=e=>{e.preventDefault();task(async()=>{
   let d=formObj(e.target);d.folder_id=d.folder_id?+d.folder_id:null;d.interval_min=+d.interval_min;
   for(let k of ['enabled','paused','auto_tag'])d[k]=!!e.target.elements[k].checked;
   await api(`/feeds/${id}`,{method:'PATCH',body:JSON.stringify(d)});closeModal();await loadTree();notice('Subscription updated');});};
 $('#refresh-feed').onclick=()=>task(async()=>{await api(`/feeds/${id}/refresh`,{method:'POST'});closeModal();notice('Refresh queued');});
 $('#delete-feed').onclick=()=>{if(!confirm('Permanently delete this subscription and all its stored articles?'))return;
  task(async()=>{await api(`/feeds/${id}`,{method:'DELETE'});closeModal();state.view={collection:'all'};state.viewTitle='All articles';await loadTree();await loadArticles();notice('Feed deleted');});};
}
function manageAccount(id){let a=state.tree.accounts.find(x=>x.id===id);if(!a)return;
 modal('Feed account settings',`<form id="manage-account"><label>Account name<input name="name" value="${attr(a.name)}" required></label>
  <label>Default refresh interval (minutes)<input name="interval_min" type="number" value="${a.interval_min}" min="5" max="10080"></label>
  <label>Tag prefix<input name="tag_prefix" value="${attr(a.tag_prefix)}"></label>
  <label><input name="paused" type="checkbox" ${a.paused?'checked':''}> Pause all automatic updates</label>
  <label><input name="auto_tag" type="checkbox" ${a.auto_tag?'checked':''}> Enable automatic publisher-category tagging</label>
  ${formButtons('Save account')}</form><hr class="sep"><div class="form-row"><a class="small-btn" href="/api/export/opml?account_id=${id}" target="_blank">Export account OPML</a><button id="delete-account" class="danger">Delete account</button></div>`);
 formCancel();$('#manage-account').onsubmit=e=>{e.preventDefault();task(async()=>{let d=formObj(e.target);d.interval_min=+d.interval_min;
  for(let k of ['paused','auto_tag'])d[k]=!!e.target.elements[k].checked;
  await api(`/accounts/${id}`,{method:'PATCH',body:JSON.stringify(d)});closeModal();await loadTree();notice('Account updated');});};
 $('#delete-account').onclick=()=>{if(!confirm('Permanently delete the account, subscriptions, and articles?'))return;
  task(async()=>{await api(`/accounts/${id}`,{method:'DELETE'});closeModal();changeView('collection','all','All articles');await loadTree();notice('Account deleted');});};
}
function manageFolder(id){let f=state.tree.folders.find(x=>x.id===id);if(!f)return;
 modal('Manage folder',`<form id="manage-folder"><label>Folder name<input name="name" value="${attr(f.name)}" required></label>
  <label>Parent folder<select name="parent_id"><option value="">— Account root —</option>${state.tree.folders.filter(x=>x.account_id===f.account_id&&x.id!==f.id).map(x=>`<option value="${x.id}" ${x.id===f.parent_id?'selected':''}>${esc(x.name)}</option>`).join('')}</select></label>${formButtons('Save folder')}</form>
  <hr class="sep"><button id="delete-folder" class="danger">Delete folder and everything inside</button>`);
 formCancel();$('#manage-folder').onsubmit=e=>{e.preventDefault();task(async()=>{let d=formObj(e.target);d.parent_id=d.parent_id?+d.parent_id:null;
   await api(`/folders/${id}`,{method:'PATCH',body:JSON.stringify(d)});closeModal();await loadTree();notice('Folder saved');});};
 $('#delete-folder').onclick=()=>{if(!confirm('Permanently delete this folder, nested folders, feeds, and articles?'))return;
 task(async()=>{await api(`/folders/${id}?recursive=true`,{method:'DELETE'});closeModal();changeView('collection','all','All articles');await loadTree();notice('Folder deleted');});};
}
function addSmart(){modal('New saved search',`<form id="smart-form"><label>Name<input name="name" placeholder="Tech news" required></label>
 <label>Full-text search query (SQLite FTS5 syntax)<input name="query" placeholder='"open source" OR "security"'></label>
 <label>Account scope<select name="account_id">${options(state.tree.accounts,null,true)}</select></label>
 <label><input type="checkbox" name="unread"> Only unread</label><label><input type="checkbox" name="starred"> Only starred</label>${formButtons('Create saved search')}</form>`);
 formCancel();$('#smart-form').onsubmit=e=>{e.preventDefault();task(async()=>{let d=formObj(e.target);d.account_id=d.account_id?+d.account_id:null;
 d.filters={unread:e.target.elements.unread.checked,starred:e.target.elements.starred.checked};delete d.unread;delete d.starred;
 await api('/smart-folders',{method:'POST',body:JSON.stringify(d)});closeModal();await loadTree();notice('Saved search created');});};}
function manageSmart(id){let f=state.tree.smart.find(x=>x.id===id);modal('Saved search',`<p><strong>${esc(f?.name||'Saved search')}</strong></p><p>Query: <code>${esc(f?.query||'')}</code></p><button id="delete-smart" class="danger">Delete saved search</button>`);
 $('#delete-smart').onclick=()=>task(async()=>{await api(`/smart-folders/${id}`,{method:'DELETE'});closeModal();changeView('collection','all','All articles');await loadTree();});}
function addTag(){modal('Create a tag',`<form id="tag-form"><label>Tag name<input name="name" required maxlength="100"></label><label>Color<input type="color" name="color" value="#4488bb"></label>${formButtons('Create tag')}</form>`);
 formCancel();$('#tag-form').onsubmit=e=>{e.preventDefault();task(async()=>{await api('/tags',{method:'POST',body:JSON.stringify(formObj(e.target))});closeModal();await loadTags();notice('Tag created');});};}
function ruleManager(){task(async()=>{let response=await api('/rules');
 modal('Message filters',`<p class="hint">Rules run when new articles arrive. Use the basic editor, or provide nested condition/action JSON for advanced rules.</p>
 <div class="form-row"><button class="primary" id="rule-create">＋ New filter</button><button class="small-btn" id="rule-reload">Reload</button></div>
 <table><thead><tr><th>Rule</th><th>Scope</th><th>Priority</th><th>Actions</th></tr></thead><tbody>${response.rules.map(r=>`<tr><td>${esc(r.name)} ${r.enabled?'':'(disabled)'}</td><td>${r.feed_id?'Feed #'+r.feed_id:(r.account_id?'Account #'+r.account_id:'All')}</td><td>${r.priority}</td><td><button class="small-btn" data-run-rule="${r.id}">Run</button> <button class="small-btn" data-toggle-rule="${r.id}" data-enabled="${r.enabled}">${r.enabled?'Pause':'Enable'}</button> <button class="small-btn" data-del-rule="${r.id}">Delete</button></td></tr>`).join('')}</tbody></table>`);
 $('#rule-create').onclick=createRule;$('#rule-reload').onclick=ruleManager;
 $$('[data-run-rule]').forEach(b=>b.onclick=()=>task(async()=>{let d=await api(`/rules/${b.dataset.runRule}/run`,{method:'POST'});notice(`Rule matched ${d.matched} articles`);await loadArticles();}));
 $$('[data-toggle-rule]').forEach(b=>b.onclick=()=>task(async()=>{await api(`/rules/${b.dataset.toggleRule}`,{method:'PATCH',body:JSON.stringify({enabled:b.dataset.enabled!=='1'})});ruleManager();}));
 $$('[data-del-rule]').forEach(b=>b.onclick=()=>{if(!confirm('Delete this rule?'))return;task(async()=>{await api(`/rules/${b.dataset.delRule}`,{method:'DELETE'});ruleManager();});});
 });}
function createRule(){modal('Create message filter',`<form id="rule-form"><label>Rule name<input name="name" required placeholder="Regional security"></label>
  <div class="form-row"><label>Account<select name="account_id">${options(state.tree.accounts,null,true)}</select></label><label>Feed<select name="feed_id">${options(state.tree.feeds,null,true)}</select></label></div>
  <div class="form-row"><label>Field<select id="rule-field">${['title','body','summary','author','source','url','domain','categories','tags','published'].map(x=>`<option value="${x}">${x}</option>`).join('')}</select></label>
    <label>Match<select id="rule-op">${['contains','not_contains','equals','starts','ends','regex','not_regex','empty'].map(x=>`<option>${x}</option>`).join('')}</select></label></div>
  <label>Match value<input id="rule-value" placeholder="keyword"></label>
  <div class="form-row"><label>Action<select id="rule-action">${['tag','read','unread','star','bookmark','archive','trash','notify'].map(x=>`<option>${x}</option>`).join('')}</select></label><label>Action value (for tag/notify)<input id="rule-action-value" placeholder="Important"></label></div>
  <label>Optional advanced conditions JSON<textarea rows="4" id="rule-conditions" placeholder='{"logic":"AND","children":[{"field":"title","op":"contains","value":"news"}]}'></textarea></label>
  <label>Optional advanced actions JSON<textarea rows="3" id="rule-actions" placeholder='[{"type":"tag","value":"News"}]'></textarea></label>
  <label>Priority (lower number runs first)<input name="priority" type="number" min="0" max="10000" value="100"></label>${formButtons('Create filter')}</form>`);
 formCancel();$('#rule-form').onsubmit=e=>{e.preventDefault();task(async()=>{let d=formObj(e.target);
  d.account_id=d.account_id?+d.account_id:null;d.feed_id=d.feed_id?+d.feed_id:null;d.priority=+d.priority;
  d.conditions=$('#rule-conditions').value.trim()?JSON.parse($('#rule-conditions').value):{field:$('#rule-field').value,op:$('#rule-op').value,value:$('#rule-value').value};
  d.actions=$('#rule-actions').value.trim()?JSON.parse($('#rule-actions').value):[{type:$('#rule-action').value,value:$('#rule-action-value').value}];
  await api('/rules',{method:'POST',body:JSON.stringify(d)});notice('Filter created');ruleManager();});};}
function showSettings(){let s=state.settings;
 modal('Settings & data',`<form id="settings-form"><div class="form-row"><label>Theme<select name="theme"><option value="auto">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
   <label>Default reading mode<select name="read_mode"><option value="summary">Summary</option><option value="content">Feed content</option><option value="plain">Plain text</option></select></label></div>
  <div class="form-row"><label>Default refresh (minutes)<input type="number" name="refresh_min" value="${s.refresh_min||30}" min="5"></label><label>Article retention (days; 0=keep)<input type="number" name="retention_days" value="${s.retention_days||0}" min="0"></label></div>
  <label><input type="checkbox" name="mark_read" ${s.mark_read!=='0'?'checked':''}> Automatically mark opened articles read</label>
  <label><input type="checkbox" name="notifications" ${s.notifications!=='0'?'checked':''}> Enable filter notifications</label>
  ${formButtons('Save settings')}</form><hr class="sep"><h3>Data management</h3><div class="form-row">
  <a class="small-btn" href="/api/export/opml" target="_blank">Export OPML</a><a class="small-btn" href="/api/export/articles.csv" target="_blank">Export article CSV</a><button class="small-btn" id="reindex">Rebuild search index</button></div>
  <form id="opml-form"><label>Import OPML feed subscriptions<input type="file" name="file" accept=".opml,.xml,text/xml" required></label>
    <label>Import into account<select name="account_id">${accountOptions()}</select></label><button type="submit" class="small-btn">Import subscriptions</button></form>
    <p class="hint">Database backups use the provided backup.sh script. For direct Cloudflare Tunnel access, set PUBLIC_URL to the HTTPS hostname in .env. Settings persist on the server.</p>
    <hr class="sep"><h3>Administrator password</h3><form id="password-form"><label>Current password<input name="old_password" type="password" required></label><label>New password (12+ characters)<input name="new_password" type="password" minlength="12" required></label><button class="small-btn">Change password and sign out</button></form>`);
 $('#settings-form [name=theme]').value=s.theme||'auto';$('#settings-form [name=read_mode]').value=s.read_mode||'summary';formCancel();
 $('#settings-form').onsubmit=e=>{e.preventDefault();task(async()=>{let d=formObj(e.target);d.mark_read=e.target.elements.mark_read.checked?1:0;d.notifications=e.target.elements.notifications.checked?1:0;
  await api('/settings',{method:'PATCH',body:JSON.stringify(d)});closeModal();await loadSettings();notice('Settings saved');});};
 $('#reindex').onclick=()=>task(async()=>{let d=await api('/maintenance/reindex',{method:'POST'});notice(`${d.indexed} articles reindexed`);});
 $('#opml-form').onsubmit=e=>{e.preventDefault();task(async()=>{let d=new FormData(e.target);let account=d.get('account_id');d.delete('account_id');
  let r=await api('/import/opml?account_id='+account,{method:'POST',body:d});notice(`${r.added} feeds imported · ${r.duplicates} skipped`);await loadTree();});};
 $('#password-form').onsubmit=e=>{e.preventDefault();task(async()=>{await api('/auth/password',{method:'POST',body:JSON.stringify(formObj(e.target))});closeModal();boot();});};
}
function showLogs(){task(async()=>{let [l,s,n]=await Promise.all([api('/logs?limit=100'),api('/stats'),api('/notifications')]);
 modal('Activity & diagnostics',`<p>Feeds: ${s.feeds} · Articles: ${s.articles} · Unread: ${s.unread} · Errors: ${s.failed_feeds} · Database: ${(s.db_bytes/1024/1024).toFixed(2)} MB</p>
 <h3>Filter alerts</h3><div class="scroll-text">${esc(n.notifications.length?n.notifications.map(x=>`${new Date(x.created_at*1000).toLocaleString()} · ${x.message}`).join('\n'):'No notifications')}</div>
 <h3>Recent events</h3><div class="scroll-text">${esc(l.logs.length?l.logs.map(x=>`${new Date(x.created_at*1000).toLocaleString()}  ${x.level.padEnd(7)}  ${x.source}  ${x.message}`).join('\n'):'No activity logged')}</div>`);
 });}
function initEvents(){
 $('#auth-form').onsubmit=e=>{e.preventDefault();task(async()=>{
    let d=formObj(e.target);const st=await (await fetch('/api/auth/state',{credentials:'same-origin'})).json();
    const setup=!st.configured;
    const err=$('#auth-error');err.classList.add('hidden');
    if(setup && d.password!==d.confirm_password){
      err.textContent='Passwords do not match. Please enter the same password twice.';
      err.classList.remove('hidden');return;
    }
    const url=setup?'/api/auth/setup':'/api/auth/login';
    const payload=setup?d:{username:d.username,password:d.password};
    let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),credentials:'same-origin'});
    let response=await r.json();if(!r.ok){
      err.textContent=response.error||response.detail||'Authentication failed';
      err.classList.remove('hidden');return;
    }
    e.target.reset();await boot();});};
 $('#logout-btn').onclick=()=>task(async()=>{await api('/auth/logout',{method:'POST'});state.csrf='';await boot();});
 $('#add-btn').onclick=addFeed;$('#add-account').onclick=addAccount;$('#add-folder').onclick=addFolder;
 $('#add-smart').onclick=addSmart;$('#add-tag').onclick=addTag;
 $('#settings-btn').onclick=showSettings;$('#rules-btn').onclick=ruleManager;$('#logs-btn').onclick=showLogs;
 $('#more-current').onclick=manageCurrent;
 $('#modal-close').onclick=closeModal;$('#modal-overlay').addEventListener('click',e=>{if(e.target.id==='modal-overlay')closeModal();});
 $('#refresh-all').onclick=()=>task(async()=>{let r=await api('/refresh',{method:'POST',body:JSON.stringify({scope:'all'})});notice(`${r.queued} refresh jobs queued`);setTimeout(()=>task(async()=>{await loadTree();await loadArticles();}),1500);});
 $('#pin-filter').onclick=()=>{state.pinned=!state.pinned;renderFilterbar();};
 $$('[data-filter]').forEach(b=>b.onclick=()=>{state.filters[b.dataset.filter]=!state.filters[b.dataset.filter];renderFilterbar();renderList();task(loadArticles);});
 let searchTimer;$('#search').addEventListener('input',e=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.search=e.target.value;state.page=1;task(loadArticles);},420);});
 $('#sort').onchange=e=>{state.sort=e.target.value;task(loadArticles)};
 $('#order').onclick=()=>{state.order=state.order==='desc'?'asc':'desc';$('#order').textContent=state.order==='desc'?'↓':'↑';task(loadArticles);};
 $('#select-all').onchange=e=>{for(let a of state.articles){if(e.target.checked)state.selected.add(a.id);else state.selected.delete(a.id);}renderList();};
 $('#bulk-apply').onclick=()=>task(async()=>{let ids=[...state.selected],action=$('#bulk-action').value;
   if(action==='trash'&&!confirm('Move selected articles to Trash?'))return;
   await api('/articles/bulk',{method:'POST',body:JSON.stringify({ids,action})});state.selected.clear();await loadArticles();await loadTree();notice(`Updated ${ids.length} articles`);});
 $('#prev-page').onclick=()=>{if(state.page>1){state.page--;task(loadArticles)}};
 $('#next-page').onclick=()=>{state.page++;task(loadArticles)};
 $('#mark-all-read').onclick=()=>task(async()=>{if(!state.articles.length)return;
   await api('/articles/bulk',{method:'POST',body:JSON.stringify({ids:state.articles.map(a=>a.id),action:'read'})});await loadArticles();await loadTree();notice('Visible articles marked read');});
 $('#reader-read').onclick=()=>task(()=>articleChange('is_read',!state.detail.is_read));
 $('#reader-star').onclick=()=>task(()=>articleChange('starred',!state.detail.starred));
 $('#reader-bookmark').onclick=()=>task(()=>articleChange('bookmarked',!state.detail.bookmarked));
 $('#reader-archive').onclick=()=>task(()=>articleChange('archived',!state.detail.archived));
 $('#reader-trash').onclick=()=>task(()=>articleChange('deleted',!state.detail.deleted));
 $('#reader-print').onclick=()=>window.print();
 $('#tag-article-form').onsubmit=e=>{e.preventDefault();task(async()=>{let name=$('#tag-article').value.trim();if(!name)return;
   await api(`/articles/${state.current}/tags`,{method:'POST',body:JSON.stringify({name})});$('#tag-article').value='';
   state.detail=await api('/articles/'+state.current);renderReading();await loadTags();notice('Tag added');});};
 $$('[data-mode]').forEach(b=>b.onclick=()=>{state.mode=b.dataset.mode;renderReading();});
 $('#mobile-nav').onclick=()=>$('#navigation').classList.toggle('open');
 $('#reader-back').onclick=()=>$('#workspace').classList.remove('show-reader');
 document.addEventListener('keydown',e=>{
   if(e.key==='Escape'){closeModal();$('#navigation').classList.remove('open');}
   if($('#auth').classList.contains('hidden')&&$('#modal-overlay').classList.contains('hidden') &&
      !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){
      if(e.key.toLowerCase()==='r'){e.preventDefault();$('#refresh-all').click();}
      if(e.key==='/'){e.preventDefault();$('#search').focus();}
      if(e.key.toLowerCase()==='j'||e.key.toLowerCase()==='k'){
        let ix=state.articles.findIndex(a=>a.id===state.current);let next=e.key.toLowerCase()==='j'?ix+1:Math.max(0,ix-1);
        if(state.articles[next])task(()=>openArticle(state.articles[next].id));}
      if(e.key.toLowerCase()==='s'&&state.detail)$('#reader-star').click();
    }
 });
 function resize(id,left,right){let bar=$('#'+id);bar.addEventListener('pointerdown',e=>{if(innerWidth<751)return;e.preventDefault();bar.setPointerCapture(e.pointerId);
   let start=e.clientX,style=getComputedStyle($('#workspace')).gridTemplateColumns.split(' ').map(parseFloat);
   bar.onpointermove=ev=>{let dx=ev.clientX-start;let cols=style.slice();cols[left]=Math.max(left===0?195:230,style[left]+dx);
     if(right!==null)cols[right]=Math.max(230,style[right]-dx);
     $('#workspace').style.gridTemplateColumns=`${cols[0]}px 5px ${cols[2]}px 5px minmax(250px,1fr)`;};
   bar.onpointerup=()=>{bar.onpointermove=null;bar.onpointerup=null;};
 });}
 resize('resize-left',0,2);resize('resize-right',2,null);
}
initEvents();boot();
