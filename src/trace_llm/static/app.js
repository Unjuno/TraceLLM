'use strict';
const $ = id => document.getElementById(id);
const machine = window.TraceUIState;
const state = {pages: [], page: null, ui: machine.initial(),
  responses: [], response: 0, checkpoint: null};
const isBusy = () => state.ui.operation !== 'idle';
const hasDraft = () => machine.hasUnsavedContent(state.ui);
function emit(event) { state.ui = machine.transition(state.ui, event); syncUI(); }
function syncUI() {
  const working=isBusy();
  ['save','respond','newPage','firstPage'].forEach(id => $(id).disabled=working);
  $('title').readOnly=working; $('body').readOnly=working;
  $('saveStatus').textContent=machine.editorLabels[state.ui.editor];
  $('saveStatus').dataset.state=state.ui.editor;
  $('operationStatus').textContent=machine.operationLabels[state.ui.operation];
  $('operationStatus').hidden=!working;
  $('saveConflict').hidden=!['conflict','error'].includes(state.ui.editor);
  $('saveConflictTitle').textContent=state.ui.editor==='conflict'?
    'ほかの画面でこのページが更新されました。':'保存に失敗しました。';
  $('saveConflictText').textContent=state.ui.editor==='conflict'?
    '入力中の文章はこの画面に残っています。新しいページに退避するか、明示的に再読込してください。':
    '入力中の文章は残っています。接続を確認し、再度保存するか新しいページに退避できます。';
  $('connectionStatus').textContent=state.ui.connection==='offline'?'接続を確認してください':
    state.ui.connection==='online'?'ローカル接続中':'接続を確認中…';
  $('connectionStatus').dataset.state=state.ui.connection;
}

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
    const error = new Error(message); error.status = response.status; throw error;
  }
  return data;
}
async function action(work, operation='navigating') {
  if (isBusy()) return;
  emit({type:'OP_START',operation});
  try { await work(); }
  catch (error) { toast(error.message || '処理に失敗しました。'); }
  finally { emit({type:'OP_END',operation}); }
}
async function refreshPages() { state.pages = await api('/pages'); drawList(); }
async function save() {
  if (!state.page || !hasDraft()) return;
  emit({type:'SAVE_START'});
  let updated;
  try {
    updated = await api('/pages/' + state.page.id, 'PUT', {title:$('title').value.trim() ||
      '無題のページ', body:$('body').value, revision:state.page.revision});
  } catch (error) {
    emit({type:error.status===409?'SAVE_CONFLICT':'SAVE_FAILURE'});
    throw error;
  }
  state.page = updated; emit({type:'SAVE_SUCCESS'});
  $('revision').textContent = '本文の版 ' + updated.revision;
  $('updated').textContent = date(updated.updated); $('topTitle').textContent = updated.title;
  await refreshPages();
}
function setView(view) {
  emit({type:'NAVIGATE',view});
  $('welcome').hidden = view !== 'pages' || Boolean(state.page);
  $('pageView').hidden = view !== 'pages' || !state.page;
  $('historyView').hidden = view !== 'history';
  $('healthView').hidden = view !== 'health';
  $('healthNav').classList.toggle('active', view === 'health');
  ['pages','history','health'].forEach(name=>{
    const button=$(name+'Nav');
    if(name===view)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
  });
  document.querySelector('.breadcrumb').textContent = view==='pages'?'ページ /':view==='health'?'学習 /':'履歴 /';
  $('topTitle').textContent=view==='health'?'学習の健全性':view==='history'?'学習履歴':state.page?.title||'TraceLLM';
  if(view!=='health')healthView.hide();
  $('pagesNav').classList.toggle('active', view === 'pages');
  $('historyNav').classList.toggle('active', view === 'history');
}
async function openPage(id, {skipSave=false,discardDraft=false}={}) {
  if (!skipSave) await save();
  // Both reads must succeed before changing the selected page. A partial
  // navigation used to make the old editor silently save into the new page.
  const nextPage = await api('/pages/' + id);
  const nextResponses = await api('/pages/' + id + '/responses');
  if (discardDraft) emit({type:'DISCARD_DRAFT'});
  state.page = nextPage;
  state.responses = nextResponses;
  state.response = 0; emit({type:'PAGE_LOADED'});
  $('title').value = state.page.title; $('body').value = state.page.body;
  $('topTitle').textContent = state.page.title; $('updated').textContent = date(state.page.updated);
  $('revision').textContent = '本文の版 ' + state.page.revision;
  setView('pages'); drawList(); drawAnswer();
  if (matchMedia('(max-width:720px)').matches) $('sidebar').classList.add('hidden');
}
async function addPage() {
  await save();
  const page = await api('/pages', 'POST', {title:'無題のページ', body:''});
  await refreshPages(); await openPage(page.id,{skipSave:true}); $('body').focus();
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
  dialog.querySelector('[data-feed]').onclick = () => {dialog.close(); action(() => feed(page.id),'feeding');};
  dialog.querySelector('[data-delete]').onclick = () => {
    dialog.close();
    if (!confirm('ページを一覧から削除します。過去の本文版・学習履歴は残り、モデルからの忘却は行いません。')) return;
    action(async () => {
      // Keep the pending draft as an immutable revision before soft deletion.
      // If saving conflicts, abort deletion rather than losing local edits.
      await save();
      await api('/pages/' + page.id, 'DELETE');
      if (state.page?.id === page.id) {state.page = null; emit({type:'PAGE_CLOSED'});}
      await refreshPages(); setView('pages'); toast('ページを一覧から削除しました');
    },'deleting');
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
    row.querySelector('.dots').onclick = e => {e.stopPropagation(); if (!isBusy()) pageMenu(page);};
    let start = null, drag = false, suppressClick = false;
    row.addEventListener('pointerdown', e => {
      if (e.target.closest('button') || isBusy() || e.button !== 0) return;
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
      if (shouldFeed) action(() => feed(page.id),'feeding');
    });
    row.addEventListener('pointercancel', () => {suppressClick=drag;reset();});
    row.addEventListener('click', e => {
      if (e.target.closest('button')) return;
      if (suppressClick) {suppressClick=false;return;}
      action(() => openPage(page.id),'navigating');
    });
    row.addEventListener('keydown', e => {
      if (e.target !== row) return;
      if (e.key === 'Enter') {e.preventDefault();action(() => openPage(page.id),'navigating');}
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault(); action(() => feed(page.id),'feeding');
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
  $('maskToggle').hidden=state.ui.response!=='revealed' || !state.responses.length;
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
  if (state.ui.response!=='revealed') {
    $('answerBox').innerHTML='<div class="masked"><div class="skeleton"></div>'+
      '<div class="skeleton"></div><div class="skeleton"></div>'+
      '<button class="mask-button" id="reveal">クリックして出力を表示</button></div>';
    $('reveal').onclick=()=>{emit({type:'RESPONSE_REVEALED'});drawAnswer();};
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
  state.responses=await api('/pages/'+pageId+'/responses');state.response=0;emit({type:'RESPONSE_SELECTED'});
  drawAnswer();toast('モデルの出力を保存しました。本文は学習していません。');
}
async function history() {
  const h=await api('/history');
  const status={queued:'学習待ち',running:'学習中',succeeded:'学習済み・能力は未評価',
    failed:'失敗（旧モデル維持）',cancelled:'取り消し'};
  const valid = n => typeof n==='number' && Number.isFinite(n);
  const amount = n => Number.isSafeInteger(n) && n>=0 ? n.toLocaleString('ja-JP') : '—';
  const fixed = n => valid(n) ? n.toFixed(3) : '—';
  const jobRows=h.jobs.map(j=>{
    const m=j.metrics && typeof j.metrics==='object'?j.metrics:{};
    const hasMetrics=Number.isSafeInteger(m.new_targets) && valid(m.loss_before) && valid(m.loss_after);
    let summary='';
    if(j.status==='succeeded') {
      if(!hasMetrics) summary='<br>計測なし（旧記録・一部欠損）';
      else summary='<br>新規提示 '+amount(m.new_targets)+' / '+amount(m.requested_new_targets)+
        ' tokens · 復習 '+amount(m.replay_targets)+' tokens'+
        (m.capped?' · 部分処理（'+esc(m.stop_reason||'更新上限')+'）':'')+
        '<br>対象ページLoss '+fixed(m.loss_before)+' → '+fixed(m.loss_after)+
        ' nats/token（学習対象への適合）'+
        (valid(m.prequential_loss)?'<br>初回投入前Loss '+fixed(m.prequential_loss)+'（近似重複は未検査）':'')+
        (m.retention?.status==='measured' && valid(m.retention.loss_before) &&
         valid(m.retention.loss_after)?'<br>過去の学習ページLoss '+
          fixed(m.retention.loss_before)+' → '+fixed(m.retention.loss_after)+
          '（保持評価・未学習評価ではありません）':'')+
        (valid(m.elapsed_seconds)?'<br>評価＋更新 '+fixed(m.elapsed_seconds)+
          '秒 · CPU '+fixed(m.process_cpu_seconds)+'秒（保存時間を除く）':'');
    }
    return '<div class="logrow"><div><div class="logrow-title">'+esc(j.title)+
      '</div><div class="logrow-sub">'+date(j.created)+' · 本文版 '+esc(j.revision)+
      ' · '+esc(j.repeats)+'周を指定<br>'+esc(status[j.status]||'不明な状態')+summary+
      '</div></div>'+(j.status==='queued'?'<button class="secondary" data-cancel="'+
      esc(j.id)+'">取り消し</button>':'')+'</div>';
  });
  $('historyContent').innerHTML='<div class="overline">TRAINING LOG</div><h1>学習履歴</h1>'+
    '<p class="muted">復元可能な状態は7日間保存。使用中のモデルは期限後も保持します。<br>'+
    '原文とログは別に保持します。復元してもページ本文は書き換えません。</p>'+
    '<div class="log-card"><h2>食べさせた履歴</h2>'+
    (jobRows.join('')||'<p class="muted">まだ学習していません。</p>')+'</div>'+
    '<div class="log-card"><h2>チェックポイント</h2>'+h.checkpoints.map(cp=>
      '<div class="logrow"><div><div class="logrow-title">'+esc(cp.id.slice(0,8))+
      (cp.current?' · 使用中':'')+'</div><div class="logrow-sub">'+date(cp.created)+
      ' · '+esc(cp.kind)+(cp.parent_id?' · 親 '+esc(cp.parent_id.slice(0,8)):'')+
      '</div></div><button class="secondary" data-restore="'+esc(cp.id)+'" '+
      (!cp.restorable||cp.current?'disabled':'')+'>'+
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
  $('queueStatus').textContent=s.worker?.state==='failed'?
    '学習ワーカー停止（再起動が必要） · 学習待ち '+(s.jobs.queued||0)+'件':
    (s.jobs.running?'学習中 · ':'')+'学習待ち '+(s.jobs.queued||0)+
    '件 · CPU / ローカル';
  if (state.ui.view==='history') await history();
  if (state.ui.view==='health') await healthView.refresh();
  emit({type:'CONNECTED'});
}
const healthView = window.TraceHealth.create({api,toast,action});
$('healthNav').onclick=()=>action(async()=>{await save();setView('health');await healthView.show();},'navigating');
$('newPage').onclick=$('firstPage').onclick=()=>action(addPage,'navigating');
$('save').onclick=()=>action(async()=>{await save();toast('保存しました');},'saving');
$('respond').onclick=()=>action(respond,'generating');
$('pagesNav').onclick=()=>action(async()=>{await save();setView('pages');},'navigating');
$('historyNav').onclick=()=>action(async()=>{await save();setView('history');await history();},'navigating');
$('search').oninput=drawList;
$('closeSide').onclick=()=>$('sidebar').classList.add('hidden');
$('openSide').onclick=()=>$('sidebar').classList.toggle('hidden');
$('repeats').oninput=()=>$('repeatValue').textContent=$('repeats').value;
['title','body'].forEach(id=>$(id).oninput=()=>emit({type:'EDIT'}));
$('maskToggle').onclick=()=>{emit({type:'RESPONSE_HIDDEN'});drawAnswer();};
$('responseSelect').onchange=()=>{state.response=Number($('responseSelect').value);emit({type:'RESPONSE_SELECTED'});drawAnswer();};
addEventListener('beforeunload',event=>{if(hasDraft()){event.preventDefault();event.returnValue='';}});
addEventListener('keydown',event=>{
  if ((event.ctrlKey||event.metaKey) && event.key==='Enter' && state.ui.view==='pages') {
    event.preventDefault();action(respond,'generating');
  }
});
$('saveAsNew').onclick=()=>action(async()=>{
  // The original server page remains unchanged: preserve this unsaved draft separately.
  const title=($('title').value.trim()||'無題のページ').slice(0,185)+'（退避）';
  const body=$('body').value;
  const saved=await api('/pages','POST',{title,body});
  emit({type:'DRAFT_REHOMED'}); // Only after the replacement is safely persisted.
  state.page=null;emit({type:'PAGE_CLOSED'});
  await refreshPages();await openPage(saved.id,{skipSave:true});
  toast('編集中の文章を新しいページとして保存しました。');
},'saving');
$('reloadPage').onclick=()=>{
  if(!state.page || !confirm('未保存の変更を破棄し、サーバーに保存された版を読み込みますか？'))return;
  const id=state.page.id;
  action(async()=>{await openPage(id,{skipSave:true,discardDraft:true});toast('サーバーの最新版を読み込みました。');},'navigating');
};
if (matchMedia('(max-width:720px)').matches) $('sidebar').classList.add('hidden');
action(async()=>{await refreshPages();await poll();if(state.pages.length)await openPage(state.pages[0].id);},'navigating');
syncUI();
setInterval(()=>{if(!isBusy()&&!document.hidden) poll().catch(()=>{emit({type:'DISCONNECTED'});$('queueStatus').textContent='接続が切れています';});},2000);
