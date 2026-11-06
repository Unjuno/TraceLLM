'use strict';
const $ = id => document.getElementById(id);
const state = {pages: [], page: null, view: 'pages', dirty: false, busy: false,
  responses: [], response: 0, visible: false, checkpoint: null};
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;',
  '>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = seconds => new Date(seconds * 1000).toLocaleString('ja-JP');
let toastTimer;
function toast(message) {
  $('toast').textContent = message; $('toast').style.display = 'block';
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').style.display = 'none', 4500);
}
async function api(path, method = 'GET', body) {
  const response = await fetch('/api' + path, {method, headers: {'Content-Type':'application/json',
    'X-Trace-Request':'1'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) {
    const message = typeof data.detail === 'string' ? data.detail : '入力内容を確認してください。';
    throw new Error(message);
  }
  return data;
}
function busy(value) {
  state.busy = value;
  ['save','respond','newPage','firstPage'].forEach(id => $(id).disabled = value);
  $('title').readOnly = value; $('body').readOnly = value;
}
async function action(work) {
  if (state.busy) return;
  busy(true);
  try { await work(); } catch (error) { toast(error.message || '処理に失敗しました。'); }
  finally { busy(false); }
}
async function refreshPages() { state.pages = await api('/pages'); drawList(); }
async function save() {
  if (!state.page || !state.dirty) return;
  const updated = await api('/pages/' + state.page.id, 'PUT', {title:$('title').value.trim() ||
    '無題のページ', body:$('body').value, revision:state.page.revision});
  state.page = updated; state.dirty = false; $('saveStatus').textContent = '保存済み';
  $('revision').textContent = '本文の版 ' + updated.revision;
  $('updated').textContent = date(updated.updated); $('topTitle').textContent = updated.title;
  await refreshPages();
}
function setView(view) {
  state.view = view;
  $('welcome').hidden = view !== 'pages' || Boolean(state.page);
  $('pageView').hidden = view !== 'pages' || !state.page;
  $('historyView').hidden = view !== 'history';
  $('healthView').hidden = view !== 'health';
  $('healthNav').classList.toggle('active', view === 'health');
  $('breadcrumb').textContent = view === 'pages' ? 'ページ /' : 'モデル /';
  $('topTitle').textContent = view === 'health' ? '学習の状態' :
    view === 'history' ? '学習履歴' : state.page?.title || 'TraceLLM';
  $('pagesNav').classList.toggle('active', view === 'pages');
  $('historyNav').classList.toggle('active', view === 'history');
}
async function openPage(id) {
  await save();
  state.page = await api('/pages/' + id); state.dirty = false;
  state.responses = await api('/pages/' + id + '/responses');
  state.response = 0; state.visible = false;
  $('title').value = state.page.title; $('body').value = state.page.body;
  $('topTitle').textContent = state.page.title; $('updated').textContent = date(state.page.updated);
  $('revision').textContent = '本文の版 ' + state.page.revision;
  $('saveStatus').textContent = '保存済み'; setView('pages'); drawList(); drawAnswer();
  if (matchMedia('(max-width:720px)').matches) $('sidebar').classList.add('hidden');
}
async function addPage() {
  await save();
  const page = await api('/pages', 'POST', {title:'無題のページ', body:''});
  await refreshPages(); await openPage(page.id); $('body').focus();
}
async function feed(id) {
  await save();
  const page = await api('/pages/' + id);
  const repeats = Number($('repeats').value);
  const request = {page_id:id, revision:page.revision, repeats,
    request_key:crypto.randomUUID()};
  // Retry exactly the same event on an uncertain network failure, never create a new event.
  try { await api('/feed', 'POST', request); }
  catch (error) {
    if (!(error instanceof TypeError)) throw error;
    await api('/feed', 'POST', request);
  }
  toast('「' + page.title + '」の版 ' + page.revision + ' を学習キューに追加しました');
  await poll();
}
function pageMenu(page) {
  const dialog = document.createElement('dialog'); dialog.className = 'page-dialog';
  dialog.innerHTML = '<h3></h3><p>本文だけを学習対象にします。</p>' +
    '<button class="primary" data-feed>このページを学習</button> ' +
    '<button class="secondary" data-delete>ページを削除</button> ' +
    '<button class="secondary" data-close>閉じる</button>';
  dialog.querySelector('h3').textContent = page.title;
  document.body.append(dialog);
  dialog.addEventListener('close', () => dialog.remove());
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  dialog.querySelector('[data-feed]').onclick = () => {dialog.close(); action(() => feed(page.id));};
  dialog.querySelector('[data-delete]').onclick = () => {
    dialog.close();
    if (!confirm('ページを一覧から削除します。過去の本文版・学習履歴は残り、モデルからの忘却は行いません。')) return;
    action(async () => {
      await api('/pages/' + page.id, 'DELETE');
      if (state.page?.id === page.id) {state.page = null; state.dirty = false;}
      await refreshPages(); setView('pages'); toast('ページを一覧から削除しました');
    });
  };
  dialog.showModal();
}
function drawList() {
  const term = $('search').value.toLowerCase(); $('pageList').replaceChildren();
  $('pagesCount').textContent = state.pages.length;
  state.pages.filter(p => (p.title + '\n' + p.body).toLowerCase().includes(term)).forEach(page => {
    const shell = document.createElement('div'); shell.className = 'page-shell';
    shell.innerHTML = '<div class="under"><span>学習へ →</span><span>← 学習へ</span></div>' +
      '<div class="page-row"><span class="picon">▤</span><div class="page-info">' +
      '<div class="page-name"></div><div class="page-meta"></div></div>' +
      '<button class="dots" aria-label="ページの操作">⋯</button></div>';
    const row = shell.querySelector('.page-row');
    row.classList.toggle('active', state.page?.id === page.id);
    row.tabIndex = 0; row.setAttribute('role','button'); row.setAttribute('aria-label',page.title);
    shell.querySelector('.page-name').textContent = page.title;
    shell.querySelector('.page-meta').textContent = '版 ' + page.revision + ' · ' + date(page.updated);
    row.querySelector('.dots').onclick = e => {e.stopPropagation(); if (!state.busy) pageMenu(page);};
    let start = null, drag = false, suppressClick = false;
    row.addEventListener('pointerdown', e => {
      if (e.target.closest('button') || state.busy || e.button !== 0) return;
      start = {x:e.clientX, y:e.clientY}; drag = false; suppressClick = false;
      row.setPointerCapture(e.pointerId);
    });
    row.addEventListener('pointermove', e => {
      if (!start) return;
      const dx = e.clientX-start.x, dy=e.clientY-start.y;
      if (!drag && Math.abs(dy)>12 && Math.abs(dy)>Math.abs(dx)) {start=null;return;}
      if (Math.abs(dx)>12 && Math.abs(dx)>Math.abs(dy)*1.3) drag=true;
      if (drag) {
        shell.classList.add('swiping');
        row.style.transform = `translateX(${Math.max(-110,Math.min(110,dx))}px)`;
      }
    });
    function reset() {start=null; row.style.transform=''; shell.classList.remove('swiping');}
    row.addEventListener('pointerup', e => {
      if (!start) return;
      const shouldFeed=drag && Math.abs(e.clientX-start.x)>=60;
      suppressClick=drag; reset();
      if (shouldFeed) action(() => feed(page.id));
    });
    row.addEventListener('pointercancel', () => {suppressClick=drag;reset();});
    row.addEventListener('click', e => {
      if (e.target.closest('button')) return;
      if (suppressClick) {suppressClick=false;return;}
      action(() => openPage(page.id));
    });
    row.addEventListener('keydown', e => {
      if (e.target !== row) return;
      if (e.key === 'Enter') {e.preventDefault();action(() => openPage(page.id));}
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault(); action(() => feed(page.id));
      }
    });
    $('pageList').append(shell);
  });
}
function drawAnswer() {
  const select=$('responseSelect'); select.replaceChildren();
  state.responses.forEach((response, index) => {
    const option=document.createElement('option'); option.value=index;
    option.textContent=date(response.created)+' · 本文版 '+response.revision;
    select.append(option);
  });
  select.hidden=!state.responses.length; select.value=String(state.response);
  $('maskToggle').hidden=!state.visible || !state.responses.length;
  const response=state.responses[state.response]; $('answerBox').replaceChildren();
  if (!response) {
    $('responseMeta').textContent='';
    const empty=document.createElement('div');empty.className='nothing';
    empty.textContent='本文を送信すると、その時点のモデルが出力します。';
    $('answerBox').append(empty);return;
  }
  $('responseMeta').textContent='モデル '+response.checkpoint_id.slice(0,8) +
    ' · 本文版 '+response.revision + (response.metadata.context_truncated?' · 末尾の文脈のみ使用':'') +
    (response.metadata.untrained?' · まだ未学習':'');
  if (!state.visible) {
    $('answerBox').innerHTML='<div class="masked"><div class="skeleton"></div>'+ 
      '<div class="skeleton"></div><div class="skeleton"></div>'+ 
      '<button class="mask-button" id="reveal">クリックして出力を表示</button></div>';
    $('reveal').onclick=()=>{state.visible=true;drawAnswer();};
  } else {
    const output=document.createElement('div');output.className='response-text';
    output.textContent=response.text.replace(/[\x00-\x08\x0b-\x1f\x7f]/g,'�') || '（EOS：出力が終了しました）';
    $('answerBox').append(output);
  }
}
async function respond() {
  await save(); if (!state.page) return;
  const pageId=state.page.id;
  $('responseMeta').textContent='生成中…';
  await api('/pages/'+pageId+'/responses','POST',{revision:state.page.revision});
  state.responses=await api('/pages/'+pageId+'/responses');state.response=0;state.visible=false;
  drawAnswer();toast('モデルの出力を保存しました。本文は学習していません。');
}
async function history() {
  const h=await api('/history');
  const status={queued:'学習待ち',running:'学習中',succeeded:'学習済み・能力は未評価',
    failed:'失敗（旧モデル維持）',cancelled:'取り消し'};
  $('historyContent').innerHTML='<div class="overline">TRAINING LOG</div><h1>学習履歴</h1>'+ 
    '<p class="muted">復元可能な状態は7日間保存。使用中のモデルは期限後も保持します。<br>'+ 
    '原文とログは別に保持します。復元してもページ本文は書き換えません。</p>'+ 
    '<div class="log-card"><h2>食べさせた履歴</h2>'+ (h.jobs.map(j=>{
      const m=j.metrics;
      return '<div class="logrow"><div><div class="logrow-title">'+esc(j.title)+
        '</div><div class="logrow-sub">'+date(j.created)+' · 本文版 '+j.revision+' · '+j.repeats+
        '周を指定<br>'+esc(status[j.status])+
        (j.status==='succeeded'?'<br>新規提示 '+m.new_targets+' / '+m.requested_new_targets+
          ' tokens · 復習 '+m.replay_targets+' tokens'+(m.capped?' · 部分処理（'+esc(m.stop_reason||'更新上限')+'）':'')+
          '<br>対象ページLoss '+m.loss_before.toFixed(3)+' → '+m.loss_after.toFixed(3)+
          ' nats/token（学習対象への適合）'+
          (m.prequential_loss!=null?'<br>初回投入前Loss '+m.prequential_loss.toFixed(3)+
            '（近似重複は未検査）':'')+
          (m.retention?.status==='measured'?'<br>過去の学習ページLoss '+
            m.retention.loss_before.toFixed(3)+' → '+m.retention.loss_after.toFixed(3)+
            '（保持評価・未学習評価ではありません）':'')+
          (m.elapsed_seconds!=null?'<br>評価＋更新 '+m.elapsed_seconds.toFixed(3)+
            '秒 · CPU '+m.process_cpu_seconds.toFixed(3)+'秒（保存時間を除く）':''):'')+
        '</div></div>'+(j.status==='queued'?'<button class="secondary" data-cancel="'+j.id+
          '">取り消し</button>':'')+'</div>';
    }).join('') || '<p class="muted">まだ学習していません。</p>')+'</div>'+ 
    '<div class="log-card"><h2>チェックポイント</h2>'+h.checkpoints.map(cp=>
      '<div class="logrow"><div><div class="logrow-title">'+cp.id.slice(0,8)+(cp.current?' · 使用中':'')+
      '</div><div class="logrow-sub">'+date(cp.created)+' · '+esc(cp.kind)+
      (cp.parent_id?' · 親 '+cp.parent_id.slice(0,8):'')+'</div></div><button class="secondary" '+
      'data-restore="'+cp.id+'" '+(!cp.restorable||cp.current?'disabled':'')+'>'+
      (cp.current?'現在':cp.restorable?'ここから復元':'期限切れ')+'</button></div>').join('')+'</div>';
  $('historyContent').querySelectorAll('[data-cancel]').forEach(button=>button.onclick=()=>action(async()=>{
    await api('/jobs/'+button.dataset.cancel+'/cancel','POST');await history();
  }));
  $('historyContent').querySelectorAll('[data-restore]').forEach(button=>button.onclick=()=>{
    if (!confirm('このモデルから分岐して復元します。現在のモデルとページ本文は残ります。')) return;
    action(async()=>{await api('/checkpoints/'+button.dataset.restore+'/restore','POST');
      await poll();toast('過去の状態から新しい分岐を作成しました');});
  });
}
async function poll() {
  const s=await api('/status');state.checkpoint=s.checkpoint_id;
  $('modelVersion').textContent='Model '+s.checkpoint_id.slice(0,8);
  $('queueStatus').textContent=(s.jobs.running?'学習中 · ':'')+'学習待ち '+(s.jobs.queued||0)+
    '件 · CPU / ローカル';
  if (state.view==='history') await history();
  if (state.view==='health') await TraceHealth.refresh();
}
$('newPage').onclick=$('firstPage').onclick=()=>action(addPage);
$('save').onclick=()=>action(async()=>{await save();toast('保存しました');});
$('respond').onclick=()=>action(respond);
$('pagesNav').onclick=()=>action(async()=>{await save();setView('pages');});
$('historyNav').onclick=()=>action(async()=>{await save();setView('history');await history();});
$('healthNav').onclick=()=>action(async()=>{await save();setView('health');await TraceHealth.refresh();});
TraceHealth.init({api, esc, date, history:()=>action(async()=>{setView('history');await history();})});
$('search').oninput=drawList;
$('closeSide').onclick=()=>$('sidebar').classList.add('hidden');
$('openSide').onclick=()=>$('sidebar').classList.toggle('hidden');
$('repeats').oninput=()=>$('repeatValue').textContent=$('repeats').value;
['title','body'].forEach(id=>$(id).oninput=()=>{state.dirty=true;$('saveStatus').textContent='未保存';});
$('maskToggle').onclick=()=>{state.visible=false;drawAnswer();};
$('responseSelect').onchange=()=>{state.response=Number($('responseSelect').value);state.visible=false;drawAnswer();};
addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
addEventListener('keydown',event=>{
  if ((event.ctrlKey||event.metaKey) && event.key==='Enter' && state.view==='pages') {
    event.preventDefault();action(respond);
  }
});
if (matchMedia('(max-width:720px)').matches) $('sidebar').classList.add('hidden');
action(async()=>{await refreshPages();await poll();if(state.pages.length)await openPage(state.pages[0].id);});
setInterval(()=>{if(!state.busy) poll().catch(()=>{$('queueStatus').textContent='接続が切れています';if(state.view==='health')TraceHealth.disconnected();});},2000);
