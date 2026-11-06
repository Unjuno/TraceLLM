/* Read-only observation view. No model score, no external assets, no automatic training. */
'use strict';
window.TraceHealth = (() => {
  let helpers, loading = false, signature = '';
  const el = id => document.getElementById(id);
  const number = n => typeof n === 'number' && Number.isFinite(n) ? n.toLocaleString('ja-JP') : '未計測';
  const loss = n => typeof n === 'number' && Number.isFinite(n) ? n.toFixed(3) : '未計測';
  const seconds = n => typeof n === 'number' && Number.isFinite(n) ? n.toFixed(3) + ' 秒' : '未計測';
  const when = n => typeof n === 'number' && Number.isFinite(n) ? helpers.date(n) : '時刻未計測';
  const label = {
    first_seen_page_exact_hash_only: '初回投入', repeated_body: '再投入', edited_page: '編集後',
    legacy_unknown: '旧記録・未分類'
  };
  const stop = {step_safety_cap:'更新上限', time_budget:'時間上限',
    requested_targets_completed:'指定量を完了', unknown:'停止理由は未計測'};
  function card(title, value, text, kind='neutral', extra='') {
    return `<section class="health-card ${kind}" ${extra}><h2>${title}</h2>` +
      `<div class="health-card-value">${value}</div><p>${text}</p></section>`;
  }
  function scatter(points) {
    if (!points.length) return '<div class="health-empty">まだ観測値がありません。<br>' +
      '新しいページを初めて学習すると、更新前の予測誤差を記録します。</div>';
    const values = points.map(p => p.prequential_loss), low = Math.min(...values), high = Math.max(...values);
    const margin = Math.max((high-low)*.15, .05), min = Math.max(0,low-margin), max = high+margin;
    const x = i => points.length === 1 ? 354 : 68 + i*558/(points.length-1);
    const y = v => 158 - (v-min)/(max-min)*122;
    let svg = '<svg class="health-chart" viewBox="0 0 660 210" role="img" ' +
      'aria-label="初回投入前の予測誤差。点はそれぞれ異なるページで、成長を示す線ではありません。">';
    [min,(min+max)/2,max].forEach(t => {
      svg += `<line class="health-gridline" x1="60" y1="${y(t)}" x2="636" y2="${y(t)}"/>` +
        `<text class="health-axis" x="49" y="${y(t)+4}" text-anchor="end">${t.toFixed(2)}</text>`;
    });
    points.forEach((p,i) => {svg += `<circle class="health-point" cx="${x(i)}" cy="${y(p.prequential_loss)}" r="4">` +
      `<title>${helpers.esc(when(p.observed_at))} · ${loss(p.prequential_loss)} nats/token</title></circle>`;});
    svg += `<text class="health-axis" x="68" y="177">1</text>`;
    if (points.length>1) svg += `<text class="health-axis" x="626" y="177" text-anchor="end">${points.length}</text>`;
    return svg + '<text class="health-axis" x="355" y="200" text-anchor="middle">観測順（点ごとに異なるページ）</text></svg>';
  }
  function render(h) {
    const e = helpers.esc, r = h.retention, c = h.execution.recent_counts, active = h.execution.active_counts;
    const states = {not_started:'まだ学習していません', ready:'直近のエラーなし', running:'学習中',
                    queued:'学習待ち', attention:'確認が必要です'};
    const executionText = !h.recovery.current_preserved ? '現用モデルの保存ファイルを確認できません。学習履歴を確認してください。' :
      !h.scope.lineage_complete ? 'モデルの親子関係を確認できません。保存先を確認してください。' :
      `全分岐の操作・直近7日：完了 ${number(c.succeeded||0)} 件 / 失敗 ${number(c.failed||0)} 件。` +
      `現在：待機 ${number(active.queued||0)} 件 / 実行 ${number(active.running||0)} 件。`;
    let retentionValue = 'まだ測定できません', retentionText = '学習済みの別ページがあると、その一部を更新前後で比較します。', retentionKind='neutral';
    if (r.status === 'measured') {
      retentionValue = r.delta > 0 ? '誤差の増加を観測' : r.delta < 0 ? '誤差の減少を観測' : '同じ値を観測';
      retentionKind = r.delta > 0 ? 'caution' : 'neutral';
      retentionText = `${loss(r.loss_before)} → ${loss(r.loss_after)} nats/token<br>` +
        `同じ学習済み ${number(r.sample_pages)} ページの一部。<br>` +
        `${e(when(r.observed_at))} の更新。汎化評価ではありません。`;
    } else if (r.status === 'invalidated_by_deletion') {
      retentionText = '計測中に対象ページが削除されたため、この比較は無効です。';
    }
    const t = h.latest_timing, totals = h.exposure;
    const period = totals.complete ? '現在の分岐の累積・反復を含む' : '計測済みの範囲のみ・旧版の過去分は不明';
    const partial = h.observations.filter(o=>o.capped).length;
    el('healthContent').innerHTML =
      `<div class="health-scope"><span class="health-chip">CPU · FP32 · ${number(h.runtime.threads)} threads</span>` +
      `<span>モデル <code>${e((h.checkpoint_id||'').slice(0,8))}</code> · 文脈 ${number(h.runtime.context)} byte tokens</span></div>` +
      '<div class="health-cards">' +
      card('学習処理', states[h.execution.state]||'不明', executionText,
           h.execution.state==='attention'?'caution':'neutral', 'data-testid="execution"') +
      card('過去の文章の保持', retentionValue, retentionText, retentionKind, 'data-testid="retention"') +
      card('未学習文への汎化', '未評価', '独立した検証用データがありません。<br>学習処理の完了だけでは、言語能力が改善したとは判断しません。',
           'neutral','data-testid="generalization"') + '</div>' +
      '<div class="health-metrics">' +
      `<div><h3>投入ページの提示</h3><strong>${number(totals.new_targets)}</strong><span>教師トークン · ${period}</span></div>` +
      `<div><h3>復習の提示</h3><strong>${number(totals.replay_targets)}</strong><span>教師トークン · 新しい文章量ではありません</span></div>` +
      `<div><h3>直近の処理時間</h3><strong>${seconds(t?.elapsed_seconds)}</strong><span>読込・評価・学習。保存時間を除く</span></div>` +
      `<div><h3>初回投入の観測</h3><strong>${number(h.first_seen.length)} 件</strong><span>現在の分岐・直近7日・完全一致のみ検査</span></div></div>` +
      '<section class="health-panel"><div class="health-panel-heading"><h2>初回投入前の予測</h2>' +
      '<span>低いほど、その文章の予測誤差が小さい</span></div>' +
      '<p class="health-description">学習する前の値だけを表示。ページごとに内容や難しさが違うため、点の上下だけで成長は判断できません。</p>' +
      '<div class="health-chart-unit">nats / target-token（UTF-8バイトとEOS）</div>' + scatter(h.first_seen) +
      (h.scope.truncated?`<p class="health-caution">対象 ${number(h.scope.total_observations)} 件のうち、最新 ${number(h.scope.limit)} 件だけを表示しています。</p>`:'') +
      '</section>' +
      '<section class="health-panel"><div class="health-panel-heading"><h2>更新ごとの観測値</h2>' +
      `<span>現在の分岐 · 直近7日 · 最新20件${partial?' · 部分処理 '+partial+' 件':''}</span></div>` +
      '<p class="health-description">対象ページへの適合と、過去の文章の保持は別の指標です。再投入・編集後の値は初回投入のグラフに含めません。</p>' +
      '<div class="health-table-wrap"><table class="health-table"><caption>保存済みの更新記録。損失はnats/target-token。未計測はゼロではありません。</caption>' +
      '<thead><tr><th scope="col">更新 / 本文版</th><th scope="col">対象への適合<br>更新前 → 更新後</th><th scope="col">過去の文章の保持<br>更新前 → 更新後</th><th scope="col">実提示量 / 結果</th></tr></thead><tbody>' +
      [...h.observations].reverse().slice(0,20).map(o=> {
        const retention = o.retention.status==='measured' ?
          `${loss(o.retention.loss_before)} → ${loss(o.retention.loss_after)}<small>${number(o.retention.sample_pages)} ページの一部</small>` : '未測定';
        return `<tr data-testid="health-observation"><td>${e(when(o.published_at))}<small>${e(label[o.evaluation_scope]||'未分類')} · 本文版 ${number(o.revision)} · ${e(o.checkpoint_id.slice(0,8))}</small></td>` +
          `<td>${loss(o.loss_before)} → ${loss(o.loss_after)}</td><td>${retention}</td>` +
          `<td>投入 ${number(o.new_targets)} / 復習 ${number(o.replay_targets)}<small class="${o.capped?'health-caution':''}">${o.capped?'途中まで：':'更新済み：'}${e(stop[o.stop_reason]||'未計測')}</small></td></tr>`;
      }).join('') + (h.observations.length?'':'<tr><td colspan="4" class="health-empty">この分岐の直近7日には、公開された学習更新がありません。</td></tr>') +
      '</tbody></table></div></section>' +
      '<details class="health-details"><summary>計測の範囲と限界</summary>' +
      '<p>処理状況はすべての操作の直近7日です。予測・保持・提示量は現在のモデルの親をたどった分岐だけで、復元で離れた分岐の値を混ぜません。</p>' +
      `<p>直近ジョブのプロセスCPU時間：${seconds(t?.process_cpu_seconds)}。複数スレッドや他の処理も含むため、PC全体のCPU使用率ではありません。` +
      (t?` 観測日時：${e(when(t.observed_at))}。`:'') + '</p>' +
      '<p>初回投入は本文ハッシュとページIDで区別しています。類似文章・部分重複の検出や、独立した未学習データの保証ではありません。</p>' +
      '<p>この画面は保存された記録を読みます。開くだけでは追加学習・生成・チェックポイントの書換えを行いません。</p></details>' +
      '<section class="health-recovery"><div><h2>以前のモデルに戻す</h2>' +
      `<p>7日以内の復元候補：${number(h.recovery.available_previous)} 件（保存記録上）。現用モデルは期限を過ぎても保持します。</p>` +
      '<p>復元時にファイルを検証します。ページ本文は巻き戻しません。</p></div><button class="secondary" id="healthHistory">学習履歴・復元を開く</button></section>';
    el('healthHistory').onclick = helpers.history;
  }
  function disconnected() {
    el('healthError').hidden = false;
    el('healthContent').classList.add('is-stale');
    el('healthUpdated').textContent = '更新停止中 · 表示がある場合は前回の観測値です';
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    el('healthRefresh').disabled = true;
    el('healthContent').setAttribute('aria-busy','true');
    try {
      const h = await helpers.api('/learning-health');
      const next = JSON.stringify({...h, as_of:0, scope:{...h.scope,since:0}});
      if (next !== signature) {render(h); signature=next;}
      el('healthUpdated').textContent = '最終取得 ' + helpers.date(h.as_of) + ' · 自動更新';
      el('healthError').hidden = true;
      el('healthContent').classList.remove('is-stale');
    } catch (_) {disconnected();}
    finally {loading=false;el('healthRefresh').disabled=false;el('healthContent').setAttribute('aria-busy','false');}
  }
  function init(options) {
    helpers=options;
    el('healthRefresh').onclick=refresh;
  }
  return {init, refresh, disconnected};
})();
