
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const store={get(k){try{return localStorage.getItem(k)}catch(e){return null}},set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
const ROWS=15;
function fit(){$$('section.on .scroll').forEach(b=>{const rows=$$('tbody tr',b);if(rows.length<=ROWS){b.style.maxHeight='none';return}
  const th=$('thead',b).offsetHeight,rh=(rows.find(r=>r.offsetHeight)||rows[0]).offsetHeight;if(!rh)return;
  const sb=b.scrollWidth>b.clientWidth?14:0;   // 需要橫向捲動時, 捲軸(14px)也佔框高
  b.style.maxHeight=(th+rh*ROWS+2+sb)+'px'})}   // 表頭 + 15 列 + 框線 (+橫向捲軸); 超過的列在框內垂直捲動
function show(id){$$('section').forEach(s=>s.classList.toggle('on',s.id===id));$$('nav button').forEach(b=>b.classList.toggle('on',b.dataset.t===id));store.set('tab',id);history.replaceState(null,'','#tab-'+id);fit()}
$$('nav button').forEach(b=>b.onclick=()=>show(b.dataset.t));
show(location.hash.startsWith('#tab-')&&$('#'+location.hash.slice(5))?location.hash.slice(5):($('#'+store.get('tab'))?store.get('tab'):'s1'));
$$('table.sortable').forEach(t=>{$$('th',t).forEach((th,i)=>th.onclick=()=>{
  const dir=th.classList.contains('asc')?'desc':'asc';$$('th',t).forEach(x=>x.classList.remove('asc','desc'));th.classList.add(dir);
  const num=th.dataset.k==='n',tb=$('tbody',t),rows=$$('tr',tb);
  rows.sort((a,b)=>{const x=a.children[i].dataset.v,y=b.children[i].dataset.v;const c=num?(parseFloat(x)-parseFloat(y)):x.localeCompare(y);return dir==='asc'?c:-c});
  rows.forEach(r=>tb.appendChild(r))})});
$$('table.sortable').forEach(t=>{const rows=$$('tbody tr',t);if(rows.length<=60)return;
  const box=document.createElement('div');box.className='filter';box.innerHTML='<input type="search" placeholder="篩選: 輸入代號 / 公司 / 評級 / 板塊…" spellcheck="false"> <span></span>';
  t.parentElement.before(box);const inp=$('input',box),cnt=$('span',box);
  inp.oninput=()=>{const q=inp.value.trim().toLowerCase();let n=0;rows.forEach(r=>{const ok=!q||r.textContent.toLowerCase().includes(q);r.style.display=ok?'':'none';if(ok)n++});cnt.textContent=q?n+' / '+rows.length+' 列':''}});
window.addEventListener('load',fit);window.addEventListener('resize',fit);
const root=document.documentElement;
function applyColor(m){root.dataset.color=m;$('#cbtn').textContent=m==='us'?'配色: 綠漲紅跌':'配色: 紅漲綠跌'}
applyColor(store.get('color')||'tw');
$('#cbtn').onclick=()=>{const m=root.dataset.color==='tw'?'us':'tw';store.set('color',m);applyColor(m)};

// ── 更新按鈕 / 進度 ──
let wasRunning=false,timer=null;
function render(s){
  const msg=$('#msg');
  $$('[data-task],#wlsave,#cbsave').forEach(b=>b.disabled=s.running);
  if(s.running){const sec=Math.max(0,Math.round(Date.now()/1000-s.started));
    msg.className='run';msg.innerHTML='<span class="spin"></span>'+s.label+' — '+s.step+'（已 '+sec+' 秒）'}
  else if(s.error){msg.className='err';msg.textContent='上次更新有錯誤: '+s.error}
  else{msg.className='';msg.textContent=s.finished?'上次更新完成 '+new Date(s.finished*1000).toLocaleString('zh-TW'):''}
}
async function poll(){
  let s;try{const r=await fetch('/api/status');if(r.status===401){location.href='/login';return}s=await r.json()}catch(e){timer=setTimeout(poll,3000);return}
  render(s);
  if(s.running){wasRunning=true;timer=setTimeout(poll,1500)}
  else if(wasRunning){location.reload()}
}
$$('[data-task]').forEach(b=>b.onclick=async()=>{
  b.disabled=true;
  const r=await fetch('/api/update?task='+b.dataset.task,{method:'POST',headers:{'X-Requested-With':'dashboard'}});
  if(r.status===409)alert('已有更新在進行中');
  clearTimeout(timer);poll()});
$$('.seg button[data-mode]').forEach(b=>b.onclick=async()=>{
  if(b.classList.contains('on'))return;
  const r=await fetch('/api/universe',{method:'POST',headers:{'X-Requested-With':'dashboard','Content-Type':'application/json'},body:JSON.stringify({mode:b.dataset.mode})});
  if(r.ok)location.reload()});
const cbb=$('#cbsave');
if(cbb)cbb.onclick=async()=>{
  const msg=$('#cmsg');msg.textContent='';
  const r=await fetch('/api/cooling',{method:'POST',headers:{'X-Requested-With':'dashboard','Content-Type':'application/json'},body:JSON.stringify({warn:$('#cw').value,remove:$('#cr').value})});
  const j=await r.json();
  if(!r.ok){msg.textContent=j.error||'儲存失敗';return}
  const u=await fetch('/api/update?task=nav',{method:'POST',headers:{'X-Requested-With':'dashboard'}});
  if(u.status===409)alert('已有更新在進行中，門檻已儲存，稍後請按「更新評級」');
  clearTimeout(timer);poll()};
function toast(m){let t=$('#toast');if(!t){t=document.createElement('div');t.id='toast';document.body.appendChild(t)}
  t.textContent=m;t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),4500)}
const JH={'X-Requested-With':'dashboard','Content-Type':'application/json'};

// ── 自訂觀察清單編輯器 (清單沒有檔數上限; 前後端都會驗證代號格式) ──
const wled=$('#wled');
if(wled){
  const saved0=JSON.parse(wled.dataset.list),miss=new Set(JSON.parse(wled.dataset.missing||'[]'));
  let saved=[...saved0],draft=[...saved0];
  const RE=/^[A-Z0-9][A-Z0-9-]{0,9}$/;
  const norm=t=>{t=t.normalize('NFKC').trim().replace(/^\$/,'').toUpperCase().replace(/\./g,'-');return RE.test(t)?t:null};
  const split=s=>s.normalize('NFKC').split('\n').flatMap(l=>l.split('#')[0].split(/[\s,;、]+/)).filter(Boolean);
  const dirty=()=>draft.length!==saved.length||draft.some((t,i)=>t!==saved[i]);
  const msg=(m,err)=>{const e=$('#wlmsg');e.textContent=m||'';e.style.color=err?'var(--red)':'var(--mut)'};
  function draw(){
    $('#wlchips').innerHTML=draft.length?draft.map(t=>'<span class="chip'+(miss.has(t)?' bad':'')+'"'+(miss.has(t)?' title="上次更新查不到價格資料"':'')+'>'+t+
      '<button type="button" data-rm="'+t+'" aria-label="移除 '+t+'">×</button></span>').join(''):'<span class="stamp">清單是空的，請在下方輸入代號</span>';
    $('#wlcount').textContent=draft.length+' 檔';
    $('#wldirty').textContent=dirty()?'● 有未儲存的變更':'';
    $('#wlsave').disabled=!draft.length;
  }
  function add(text){
    const good=[],bad=[],dup=[];
    for(const tok of split(text)){const t=norm(tok);
      if(!t)bad.push(tok);else if(draft.includes(t)||good.includes(t))dup.push(t);else good.push(t)}
    draft.push(...good);draw();
    const parts=[];
    if(good.length)parts.push('已加入 '+good.length+' 檔');
    if(dup.length)parts.push('已在清單中: '+dup.join('、'));
    if(bad.length)parts.push('無法辨識: '+bad.join('、'));
    msg(parts.join('；'),bad.length>0);
  }
  const inp=$('#wladd');
  inp.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();add(inp.value);inp.value=''}});
  inp.addEventListener('paste',()=>setTimeout(()=>{add(inp.value);inp.value=''},0));
  $('#wladdbtn').onclick=()=>{add(inp.value);inp.value='';inp.focus()};
  $('#wlchips').addEventListener('click',e=>{const t=e.target.dataset&&e.target.dataset.rm;if(!t)return;
    draft=draft.filter(x=>x!==t);draw();msg('')});
  const openEd=()=>{draft=[...saved];draw();msg('');wled.hidden=false;$('#wlbar').hidden=true;inp.focus()};
  const closeEd=()=>{wled.hidden=true;$('#wlbar').hidden=false};
  $('#wledit').onclick=openEd;
  $('#wlcancel').onclick=()=>{if(dirty()&&!confirm('放棄未儲存的變更?'))return;draft=[...saved];draw();msg('');closeEd()};
  $('#wlreset').onclick=()=>{draft=[...saved];draw();msg('已還原為上次儲存的清單')};
  $('#wlclear').onclick=()=>{if(draft.length&&confirm('清除全部 '+draft.length+' 檔?(按「儲存並更新」才會生效)')){draft=[];draw();msg('')}};
  $('#wlsave').onclick=async()=>{
    if(inp.value.trim()){add(inp.value);inp.value=''}
    if(!draft.length){msg('清單不能是空的',true);return}
    msg('儲存中…');
    const r=await fetch('/api/watchlist',{method:'POST',headers:JH,body:JSON.stringify({tickers:draft})});
    const j=await r.json().catch(()=>({}));
    if(!r.ok){msg(j.error||'儲存失敗',true);return}
    saved=[...j.tickers];draft=[...saved];draw();msg('');closeEd();   // 儲存後收起編輯器; 進度顯示在頁首
    const u=await fetch('/api/update?task=nav',{method:'POST',headers:{'X-Requested-With':'dashboard'}});
    if(u.status===409)alert('已有更新在進行中，清單已儲存，稍後請按「更新評級」');
    clearTimeout(timer);poll()};
  window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue=''}});
  draw();
}

// ── 大股票池表格: 點「清單」欄的 ☆/★ 加入/移出自訂觀察清單 ──
document.addEventListener('click',async e=>{
  const td=e.target.closest&&e.target.closest('td.star');if(!td)return;
  const t=td.dataset.tk;
  const r=await fetch('/api/watchlist/toggle',{method:'POST',headers:JH,body:JSON.stringify({ticker:t})});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){toast(j.error||'操作失敗');return}
  $$('td.star[data-tk="'+t+'"]').forEach(c=>{c.classList.toggle('on',j.member);c.textContent=j.member?'★':'☆';c.dataset.v=j.member?1:0});
  toast((j.member?'已加入 ':'已移出 ')+t+'，自訂觀察清單現有 '+j.tickers.length+' 檔（切到「自訂觀察清單」並按「更新評級」後生效）');
});
poll();
