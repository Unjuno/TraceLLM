/* Local-only observability. No external scripts, score, generated trend or telemetry. */
'use strict';
window.TraceHealth = (() => {
  const node = id => document.getElementById(id);
  const num = (x, digits=3) => typeof x === 'number' && Number.isFinite(x) ? x.toLocaleString('ja-JP', {minimumFractionDigits:digits, maximumFractionDigits:digits}) : '—';
  const count = x => num(x, 0);
  const signed = x => typeof x === 'number' && Number.isFinite(x) ? (x > 0 ? '+' : '') + num(x) : '—';
  const when = x => new Date(x*1000).toLocaleString('ja-JP', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
  const scopeName = x => ({first_seen_page_exact_hash_only:'初回（完全一致のみ確認）',repeated_body:'同じ本文の再投入',edited_page:'編集したページ',legacy_unknown:'旧記録・分類なし'}[x] || '分類なし');
  const stateName = x => ({succeeded:'公開済み',failed:'失敗・旧モデル維持',running:'実行中',queued:'待機中',cancelled:'取消済み'}[x] || '不明');
  const reason = x => ({requested_targets_completed:'指定の教師量を処理',step_safety_cap:'更新数の安全上限',time_budget:'学習時間の上限'}[x] || x || '記録なし');
  function el(tag, cls, text) { const e=document.createElement(tag); if(cls)e.className=cls; if(text!==undefined)e.textContent=text; return e; }
  function empty(container, title, detail) {
    const box=el('div','health-empty');box.append(el('span','empty-mark','·'),el('strong','',title),el('span','',detail));container.replaceChildren(box);
  }
  function scatter(container, rows, field, delta, allRows) {
    if(!rows.length){empty(container,delta?'保持の評価はまだありません':'初回投入の記録はまだありません',delta?'過去に学習した別のページがあると、更新前後の比較が記録されます。':'ページを学習すると、更新する直前の予測誤差を記録します。');return;}
    const ns='http://www.w3.org/2000/svg', svg=document.createElementNS(ns,'svg');
    svg.setAttribute('viewBox','0 0 440 205');svg.setAttribute('role','group');
    svg.setAttribute('aria-label',delta?'学習前後の保持Loss差。ゼロより上は誤差増加':'初回投入前Loss。線を結ばない散布図');
    const readout=el('div','chart-readout','点にカーソルを合わせるか、Tabキーで選択すると詳細を表示します。');
    function mark(tag,attrs,text){const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,String(v)));if(text!==undefined)e.textContent=text;svg.append(e);return e;}
    const values=rows.map(r=>r[field]); let min=0, max=Math.max(...values,0);
    if(delta){const extent=Math.max(...values.map(Math.abs),0.001)*1.15;min=-extent;max=extent;}else{max=Math.max(max*1.1,0.01);}
    const left=53,right=418,top=24,bottom=166;
    const y=v=>bottom-(v-min)/(max-min)*(bottom-top);
    const index=new Map(allRows.map((r,i)=>[r.job_id,i]));
    const x=r=>allRows.length===1?(left+right)/2:left+index.get(r.job_id)/(allRows.length-1)*(right-left);
    mark('text',{x:left,y:12},delta?'Loss差（nats / 教師トークン）':'Loss（nats / 教師トークン）');
    for(let i=0;i<5;i++){const v=min+(max-min)*i/4;mark('line',{x1:left,y1:y(v),x2:right,y2:y(v),class:delta&&i===2?'plot-zero':'plot-grid'});mark('text',{x:left-7,y:y(v)+4,'text-anchor':'end'},num(v,Math.abs(max)<0.1?3:2));}
    mark('line',{x1:left,y1:top,x2:left,y2:bottom,class:'plot-axis'});mark('line',{x1:left,y1:bottom,x2:right,y2:bottom,class:'plot-axis'});
    if(allRows.length===1){mark('text',{x:(left+right)/2,y:186,'text-anchor':'middle'},'1');}else{mark('text',{x:left,y:186},'1');mark('text',{x:right,y:186,'text-anchor':'end'},String(allRows.length));}mark('text',{x:(left+right)/2,y:201,'text-anchor':'middle'},'表示中のジョブ順（古い → 新しい）');
    rows.forEach(r=>{const value=r[field];const label=`${when(r.created)} · ${r.job_id.slice(0,8)} · ${delta?'保持差 '+signed(value):'初回Loss '+num(value)}${delta?' · '+count(r.retention_targets)+'教師トークン':''}`;
      const point=mark('circle',{cx:x(r),cy:y(value),r:5.2,class:'plot-point'+(delta?(value>0?' positive':' negative'):''),tabindex:0,role:'img','aria-label':label});
      const reveal=()=>{readout.textContent=label;};point.addEventListener('mouseenter',reveal);point.addEventListener('focus',reveal);point.addEventListener('click',reveal);
    });
    container.replaceChildren(svg,readout);
  }
  function stat(label,value,detail,warn=false,status=false){const card=el('div','health-stat');card.append(el('div','health-stat-label',label),el('div','health-stat-value'+(warn?' warn':'')+(status?' status':''),value));const d=el('div','health-stat-detail');detail.forEach(t=>d.append(el('span','',t)));card.append(d);return card;}
  function create({api,toast,action}) {
    let active=false, snapshot=null, pages=[], sequence=0, inFlight=false, comparing=false, lastKey='';
    const settings=()=>({scope:node('healthScope').value,days:Number(node('healthDays').value)});
    const key=()=>JSON.stringify(settings());
    function choices(select, items, desired){const current=select.value;select.replaceChildren();items.forEach(([value,label])=>{const option=el('option','',label);option.value=value;select.append(option);});const values=items.map(i=>i[0]);select.value=values.includes(current)?current:values.includes(desired)?desired:(values[0]||'');}
    function compareReady(){node('healthCompare').disabled=comparing||!node('healthPage').value||!node('healthModelA').value||!node('healthModelB').value||node('healthModelA').value===node('healthModelB').value;}
    function compareChoices(){if(!snapshot||comparing)return;
      choices(node('healthPage'),pages.map(p=>[p.id,p.title]),pages[0]?.id);
      const cps=snapshot.checkpoint_options.filter(c=>c.restorable);
      const opts=cps.map(c=>[c.id,`${c.id.slice(0,8)} · ${c.current?'現在':when(c.created)}`]);
      choices(node('healthModelA'),opts,cps.at(-1)?.id);choices(node('healthModelB'),opts,snapshot.current_checkpoint);
      node('healthCompareHint').textContent=!pages.length?'比較するには、本文のあるページを作成してください。':cps.length<2?'2つの保存済みモデルが必要です。学習すると比較できる状態が増えます。':'異なるモデルを選んでください。モデルの状態はこの操作では変更しません。';
      if(cps.length > 1 && node('healthModelA').value === node('healthModelB').value){
        // An initially untrained model is the only choice. After training,
        // keep distinct comparison defaults rather than leaving comparison disabled.
        const current=snapshot.current_checkpoint;
        if(node('healthModelA').value === current){
          node('healthModelA').value=cps.find(c=>c.id !== current)?.id || '';
        }else{
          node('healthModelB').value=cps.some(c=>c.id === current)?current:(cps.find(c=>c.id !== node('healthModelA').value)?.id || '');
        }
      }
      compareReady();
    }
    function detail(row){const dialog=el('dialog','health-detail');dialog.append(el('h2','',`学習ジョブ ${row.job_id.slice(0,8)}`));const list=el('dl');
      const fields=[['投入時刻',new Date(row.created*1000).toLocaleString('ja-JP')],['状態',stateName(row.status)],['分類',scopeName(row.scope)],['本文版',String(row.revision)],['更新前モデル',row.before_id||'未確定'],['更新後モデル',row.after_id||'未公開'],['対象ページLoss',num(row.loss_before)+' → '+num(row.loss_after)],['初回投入前Loss',num(row.prequential_loss)],['保持Loss',num(row.retention_before)+' → '+num(row.retention_after)],['保持評価の対象',count(row.retention_pages)+'ページ / '+count(row.retention_targets)+' 教師トークン'],['保持評価の状態',row.retention_status],['新規 / 復習の提示',count(row.new_targets)+' / '+count(row.replay_targets)+' tokens'],['希望した新規提示',count(row.requested_new_targets)+' tokens'],['反復指定 / 更新回数',row.repeats+'周 / '+count(row.updates)+'更新'],['復習の実績割合',row.replay_fraction===null?'未計測':num(row.replay_fraction*100,1)+'%'],['初出本文版のバイト数',count(row.first_seen_body_bytes)+' bytes（編集全文を含む）'],['停止理由',reason(row.stop_reason)],['処理時間',num(row.elapsed_seconds)+'秒（保存を除く）'],['プロセスCPU時間',num(row.process_cpu_seconds)+'秒（使用率ではありません）'],['CPUスレッド',count(row.cpu_threads)],['学習率',row.learning_rate===null?'未計測':String(row.learning_rate)],['評価プロトコル',row.evaluation_protocol],['単位','nats / 教師トークン（バイト・EOS）']];
      fields.forEach(([name,value])=>list.append(el('dt','',name),el('dd','',value)));dialog.append(list);
      const close=el('button','secondary','閉じる');close.onclick=()=>dialog.close();dialog.append(close);dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();close.focus();
    }
    function table(rows){const body=node('healthRows');body.replaceChildren();const names=new Map(pages.map(p=>[p.id,p.title]));
      if(!rows.length){const tr=el('tr'),td=el('td','table-empty','まだ記録がありません。ページ一覧から左右にスワイプして学習できます。');td.colSpan=7;tr.append(td);body.append(tr);return;}
      [...rows].reverse().forEach(r=>{const tr=el('tr');
        const title=el('td');title.append(el('div','page-label',names.get(r.page_id)||`ページ ${r.page_id.slice(0,8)}`),el('small','',when(r.created)+' · 版 '+r.revision),el('small','',scopeName(r.scope)));
        const loss=el('td','',num(r.loss_before)+' → '+num(r.loss_after));
        const retention=el('td',r.retention_delta>0?'delta-up':'',signed(r.retention_delta));
        if(r.retention_status==='invalidated_by_deletion')retention.append(el('small','','削除で評価無効'));
        const targets=el('td','',count(r.new_targets)+' / '+count(r.replay_targets));
        const elapsed=el('td','',r.elapsed_seconds===null?'未計測':num(r.elapsed_seconds)+' s');
        const status=el('td');status.append(el('span','health-badge'+(r.status==='failed'?' fail':r.partial?' warn':''),r.partial?'部分処理':stateName(r.status)));
        if(r.status==='succeeded'&&!r.metrics_complete)status.append(el('small','','計測が一部欠損'));
        const extra=el('td');const button=el('button','quiet-link','詳細');button.setAttribute('aria-label',`ジョブ ${r.job_id.slice(0,8)} の詳細`);button.onclick=()=>detail(r);extra.append(button);
        tr.append(title,loss,retention,targets,elapsed,status,extra);body.append(tr);
      });
    }
    function render(){const h=snapshot,s=h.summary,o=h.operation;
      const label={attention:'失敗を確認',incomplete_metrics:'記録が一部欠損',running:'学習中',queued:'学習待ち',published:'更新を公開済み',idle:'期間内の更新なし',not_started:'まだ未学習'}[o.state];
      node('healthSummary').replaceChildren(
        stat('学習処理',label,[`公開 ${count(s.succeeded)}件 · 失敗 ${count(s.failed)}件`,`全体の待機 ${count(o.queued)}件 / 実行中 ${count(o.running)}件`],o.state==='attention'||o.state==='incomplete_metrics',true),
        stat('初回投入の記録',count(s.first_seen)+' 件',[`再投入 ${count(s.repeated)}件 · 編集 ${count(s.edited)}件`,'独立した検証データではありません']),
        stat('実際の教師提示量',count(s.new_targets+s.replay_targets),[`新規 ${count(s.new_targets)} / 復習 ${count(s.replay_targets)}`,s.exposure_complete?'選択した範囲の合計（tokens）':'旧ログ等に欠損：計測できた分だけ']),
        stat('復元候補の保存状態',count(h.checkpoints.restorable)+' 個',['保存期間 7日 · 現用モデルは保持','読込時に整合性を確認 · 学習履歴から復元'])
      );
      const selection=h.selection;node('healthSelection').textContent=`集計：${selection.scope==='current'?'現在のモデルの系譜':'すべての分岐'} · ${selection.days?selection.days+'日間':'全期間'} · ${count(selection.total)}件。グラフ・表：${selection.truncated?'最近の':''}${count(selection.returned)}件。`+(selection.scope==='all'?' 別分岐の計測を含みます。':'')+(s.retention_increased?` 保持評価のうち${count(s.retention_increased)}件で誤差が増加（単回の観測）。`:'');
      node('healthRowCount').textContent=count(selection.returned)+'件を表示';
      scatter(node('healthFirstChart'),h.series.first_seen,'prequential_loss',false,h.rows);
      scatter(node('healthRetentionChart'),h.series.retention,'retention_delta',true,h.rows);
      table(h.rows);compareChoices();node('healthExport').disabled=false;
      node('healthUpdated').textContent='取得 '+new Date(h.as_of*1000).toLocaleTimeString('ja-JP');
      node('healthUpdated').dateTime=new Date(h.as_of*1000).toISOString();
    }
    async function refresh(force=false){if(!active||(!force&&inFlight))return;
      const request=++sequence, selectedKey=key();inFlight=true;
      if(force)node('healthConnection').textContent='更新中…';
      node('healthExport').disabled=true;
      try{const q=new URLSearchParams({...settings(),limit:120});const result=await api('/learning-health?'+q);
        if(request!==sequence||!active||selectedKey!==key())return;
        snapshot=result;node('healthError').hidden=true;node('healthConnection').textContent='接続中 · CPU / ローカル';
        // Preserve focused chart markers, open details and selected filters between unchanged polls.
        const dataKey=JSON.stringify([result.current_checkpoint,[result.selection.scope,result.selection.days,result.selection.total,result.selection.returned,result.selection.truncated],result.summary,result.rows,result.operation,result.checkpoint_options.map(p=>[p.id,p.restorable])]);
        if(dataKey!==lastKey||force){render();lastKey=dataKey;}
        else{node('healthExport').disabled=false;node('healthUpdated').textContent='取得 '+new Date(result.as_of*1000).toLocaleTimeString('ja-JP');}
      }catch(error){if(request===sequence&&active){node('healthConnection').textContent='更新できません';node('healthError').hidden=false;node('healthError').textContent='サーバーに接続できないか、計測を取得できませんでした。表示が残っている場合は前回取得した記録です。'+(error.message?' '+error.message:'');}}
      finally{if(request===sequence)inFlight=false;}
    }
    function output(response,label){const card=el('article','health-output');card.append(el('h3','',label+' · '+response.checkpoint_id.slice(0,8)),el('div','health-output-meta',`本文版 ${response.revision} · 温度0 · seed 42${response.metadata.context_truncated?' · ページ末尾の文脈のみ':''}`));
      const content=el('div');const button=el('button','secondary','出力を表示');let revealed=false;
      const toggle=()=>{revealed=!revealed;button.textContent=revealed?'出力を隠す':'出力を表示';content.replaceChildren();if(revealed)content.append(el('pre','',response.text.replace(/[\x00-\x08\x0b-\x1f\x7f]/g,'�')||'（EOS：生成が終了しました）'));else content.append(el('p','health-caption','モデルの出力は非表示です。'));};
      button.onclick=toggle;content.append(el('p','health-caption','モデルの出力は非表示です。'));card.append(content,button);return card;
    }
    async function compare(){comparing=true;compareReady();node('healthOutputs').replaceChildren();
      const pageId=node('healthPage').value,a=node('healthModelA').value,b=node('healthModelB').value;
      node('healthCompareHint').textContent='同じ本文版で生成しています。学習中は順番を待つ場合があります。';
      try{const page=await api('/pages/'+pageId);const data={revision:page.revision,max_new_tokens:64,temperature:0,seed:42};
        const first=await api('/pages/'+pageId+'/responses','POST',{...data,checkpoint_id:a});
        node('healthOutputs').append(output(first,'モデルA'));
        const second=await api('/pages/'+pageId+'/responses','POST',{...data,checkpoint_id:b});
        node('healthOutputs').append(output(second,'モデルB'));node('healthCompareHint').textContent='同じ本文版 '+page.revision+' の生成を保存しました。学習はしていません。';
      }catch(error){node('healthCompareHint').textContent='比較を完了できませんでした。保存期限・本文の変更・接続を確認してください。'+error.message;throw error;}
      finally{comparing=false;compareReady();}
    }
    node('healthRefresh').onclick=()=>refresh(true);
    [node('healthScope'),node('healthDays')].forEach(s=>s.addEventListener('change',()=>{snapshot=null;lastKey='';node('healthSummary').replaceChildren();node('healthRows').replaceChildren();node('healthExport').disabled=true;empty(node('healthFirstChart'),'読み込み中','');empty(node('healthRetentionChart'),'読み込み中','');refresh(true);}));
    ['healthPage','healthModelA','healthModelB'].forEach(id=>node(id).addEventListener('change',compareReady));
    node('healthCompare').onclick=()=>action(compare);
    node('healthExport').onclick=()=>{if(!snapshot)return;
      const blob=new Blob([JSON.stringify(snapshot,null,2)],{type:'application/json;charset=utf-8'});
      const url=URL.createObjectURL(blob), link=el('a');link.href=url;link.download='trace-learning-health-'+new Date(snapshot.as_of*1000).toISOString().slice(0,10)+'.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('計測値を保存しました。本文・ページ名・生成文は含みません。');};
    return {async show(){active=true;try{pages=await api('/pages');}catch{pages=[];}await refresh(true);},refresh,
      hide(){active=false;++sequence;inFlight=false;},};
  }
  return {create};
})();
