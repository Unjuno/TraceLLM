# TraceLLM CPU-first redesign: audit and measured results

Date: 2026-11-06. Baseline: `b9897befa205531d84565f3aa67dd1d6c9f52387`.
Source archive tree verified equal to remote `40e120365ccf206bd549350e98146eb1618a89d9`.
Scope: artificial-data engineering experiments, NOT validated personal language acquisition.

## 1. 現行実装の監査

既存39テスト成功。v0.1は実推論・実勾配更新・原子的チェックポイント公開を持つ。
115,136 parameter / 2 layers / width64 / heads4 / RoPE / SDPA / byte vocab258 / context256。
CPU FP32、AdamW、SQLite WAL。ページ版・明示的feed・再試行排除・生成履歴・7日復元は有効。
**学習前Lossは既に存在した**。不足していたのは初回・再投入・編集の分離と成長評価の観測口。
Replayは選ばれた窓の先頭だけを切り出すため偏りがある。短いReplayも長い新規系列へPaddingする。
RoPEのsin/cosを毎forward計算。未知DB版の検査前にCREATE TABLEを実行していた点も修正対象。

## 2. 第一原理からの制約

反復は最適化機会と重みづけを増やすが独立情報を増やさない。日記の次トークン予測は主に続きを
学習するので対話能力や本人の内面の完全な再現を保証しない。CPU負荷・情報量・容量は別の制約。
バイト列の長さは文字数と違う。3 byte文字主体なら256 byteは約85文字、512は約170文字。
Loss低下はUTF-8や定型句の学習だけでも起こるため、訓練適合、過去の保持、初回投入前を分ける。

## 3. 候補とトレードオフ

|案|計算・複雑さ|データ効率|長期運用・互換性|
|---|---|---|---|
|A:115K byte GPT + 学習制御|最低、既存構造|実測で監視する|既存重み・DBを維持。採用|
|B:357K/821K GPT|計算と状態保存量が増える|人工データでは同提示量のLoss低下。ただし計算量が違う|新規独立保存先で比較。自動移行なし|
|C:文字/BPE/階層型byte|語彙拡大・移行か追加ネットワークが必要|日本語の系列短縮の仮説。未測定|BPEは投入済み自作文章のみで語彙を作る必要があり、ID変更はcheckpoint互換性を壊す|

固定Unicode文字語彙は大きく、観測文字だけの動的語彙はembedding/optimizer拡張が必要。
バイト方式は未知文字と語彙移行を避ける。階層型は研究候補であって、少量日記での有効性は未検証。

## 4. 採用構成

既定の115K/context256を維持。FP32、SDPA、AdamW、固定Tokenizer、1 writer、排他推論を維持。
変更は計測・トークン予算・CPU実行方法に集中。RoPE cacheはnonpersistentでstate_dict互換。
context512/1024とモデルpresetは新規保存先での比較用。勝手に既存モデルを拡大しない。

## 5. 学習設定と更新方法

learning_rate=0.0003, betas=(0.9,0.95), matrix weight_decay=0.01, dropout=0.1,
clip_grad_norm=1.0。反復既定2、Replay予算25%（実績は別計測）、安全上限64更新/学習ループ5秒。
AdamWの状態と学習率を再開時に復元し、ジョブごとにwarmupやdecayをリセットしない。定数学習率を維持。
`duty_cycle` は既定1.0、休止を挟むオプションで厳密なCPU上限ではない。

手順: 不変版をfeed → 初回/再投入/編集を分類して更新前Loss保存 → 過去投入資料のみから保持probe →
本文の教師トークンを指定周回 → 各更新へ許可されたReplayを混合 → 有効トークン数で勾配正規化 →
有限値検査 → 学習後/保持評価 → fsync/rename/DBトランザクションで公開。
Paddingと文脈用prefixは損失から除外。実際のページ先頭/末尾だけにBOS/EOS。
Replayはページ内のランダムな教師位置から抽出。ページ一様・開始位置一様であり、全コーパスのトークン一様ではない。
削除済みページは次の更新のサンプル選択から除外。既に始まった更新は取り消せない。
時間上限は更新間で確認し、最低1更新は行う。評価・保存・推論の時間はこの上限の対象外。
品質による自動早期停止は未実装。独立評価資料なしで訓練Lossに基づく「成長判定」はしない。

### 正規化の検算

|記号|意味|SI単位|定義・範囲|型|
|---|---|---|---|---|
|i|microbatchの番号|1|1からm|整数スカラー|
|m|microbatch数|1|正整数|スカラー|
|n_i|有効な教師トークン数|1|padding/prefixマスクを除く正整数|スカラー|
|L_i|microbatchの平均負対数尤度|1|nats/target、非負|スカラー|
|L|更新全体の損失|1|以下の加重平均|スカラー|

`L = sum(n_i * L_i) / sum(n_i)`。
各平均に対象数を掛けて損失和へ戻し、全対象数で割る。分子と分母の個数が相殺され、
損失は無次元。勾配の線形性により、固定forward結果なら分割backwardと一括計算は同じ正規化。
Dropoutありでは乱数の消費順が異なるため軌跡一致は主張しない。dropout=0で勾配一致を回帰テスト。

## 6. 変更ファイル

model.py: 設定検証・RoPE cache。data.py: 効率的な窓・任意の損失マスク付き左文脈・Replay位置。
training.py: 保存可能policy・tokenizer契約・正規化backward・presets。
evaluation.py: 固定probe採点・診断用Ngram（生成には不使用）。
engine.py: policy/metadata・学習量・初回評価・保持評価・時間計測・過去checkpointでの生成。
app.py: 新規保存先用CLI・評価API。static/app.js: 既存履歴へ計測欄。
store.py: 未対応DBを変更前に拒否。tests: 回帰テスト。benchmarks/cpu_study.py: 一時保存先のみの実験。

## 7. 検証と実測

ローカルPythonテスト70件成功。JS構文/compileall・wheelビルド/同梱内容検査成功。元のv0.1コードで作った人工データストアを
新版で読み、同じcheckpointの生成一致・AdamW継続・新版の追加metadataを旧コードが読めることを確認。
旧checkpointの欠落した累積提示量はcomplete=falseとし、過去の値を捏造しない。
ブラウザ試験はローカルURLへの `ERR_BLOCKED_BY_ADMINISTRATOR` で停止。UI成功とは主張しない。

測定: AMD EPYC 9V74 (KVM, 5 visible vCPUs), Python 3.13.5, PyTorch 2.10.0+cpu, FP32。
クロック固定・CPU affinity固定なし。速度は3 warmup後9回。以下は2 threads、AdamW、
新規長T + Replay長floor(T/3)、separate mode。ランダムtoken列の更新速度であり自然言語能力ではない。
RSSはPython/PyTorchを含むfresh workerのピーク。モデルだけのメモリではない。

|preset|parameters|context|更新ms 中央値（範囲）|peak RSS MB|
|---|---:|---:|---:|---:|
|seed|115,136|256|18.03 (16.30–23.95)|345.8|
|seed|115,136|512|60.08 (50.21–63.47)|380.4|
|seed|115,136|1024|166.87 (144.01–177.38)|499.8|
|medium|357,216|256|40.53 (38.52–43.97)|357.0|
|medium|357,216|512|83.71 (71.98–102.28)|407.9|
|medium|357,216|1024|277.11 (254.61–320.42)|604.5|
|small|820,608|256|61.51 (49.36–62.67)|373.0|
|small|820,608|512|150.34 (113.58–158.56)|440.7|
|small|820,608|1024|387.06 (352.82–413.50)|682.6|

同じseed/context256のpaired測定は、2 threadsでpadded 22.32ms、separate 22.05ms。
この単一環境で更新時間の大きな改善は断定できない。1 threadと4 threadsではseparateの中央値が低かった。
別の同条件runの変動も存在し、既定スレッド数の普遍的最適性は未確定。

### ジョブ全体（各7回）

初期化した一時モデルで人工ページAを学習し、Bを2周投入。load/evaluate/train/fsync/DBを含む。
同一新版のbatch modeだけを比較。**v0.1とのend-to-end高速化率ではない**。

|mode|job ms 中央値（範囲）|process CPU ms 中央値|
|---|---:|---:|
|padded|82.53 (78.26–95.01)|149.68|
|separate|71.49 (68.93–87.49)|127.94|

64新規tokenの実生成はseparate中央値146.23ms。
EOSで短縮された測定はなく、全試行64tokens。ただし出力の流暢さを示すものではない。
Python/PyTorch importを含む新プロセス起動＋Engine初期化＋初回学習は3回で
中央値3.094秒（3.059–3.241秒）。OS/ライブラリキャッシュは未制御。
compileは独立workerが45秒予算内に完了しなかった。成功したcompile時間・定常速度は未測定。
SGD(momentum0.9)の更新速度も測定したが品質比較をしていないのでAdamWから変更しない。
KV cacheは未実装。短い生成の実測と、rolling contextでの互換性を考え今回は見送る。

### 小規模学習比較

人工8ページ2,140 bytes + 未投入4ページ1,064 bytes、3 seeds (11,29,47)。
完全一致ページの重複なし。ただし共有テンプレートがあるため、独立した自然日記とは異なる。
heldoutは学習に未使用。以下はpage-macro平均nats/target（EOSを含む）。
同じ反復回数は同じ新規教師量だが、同じ計算時間ではない。探索結果で既定値を最適化していない。

|preset/context|Replay予算|LR|反復|heldout中央値（範囲）|8 job合計秒中央値|
|---|---:|---:|---:|---:|---:|
|seed / 256|25%|0.0003|2|4.7495 (4.7125–4.7728)|0.731|
|medium / 256|25%|0.0003|2|4.3621 (4.3095–4.4352)|1.181|
|small / 256|25%|0.0003|2|3.9901 (3.9620–4.0711)|1.777|
|seed / 256|0%|0.0003|2|4.7576 (4.7169–4.7756)|0.707|
|seed / 256|50%|0.0003|2|4.7282 (4.7034–4.7635)|0.788|
|seed / 256|25%|0.001|2|3.9216 (3.8921–3.9229)|0.731|
|seed / 256|25%|0.0003|6|3.8991 (3.8667–3.9037)|1.452|
|seed / 512|25%|0.0003|2|4.8492 (4.8381–4.9201)|0.836|
|seed / 1024|25%|0.0003|2|4.8492 (4.8381–4.9201)|0.812|

N-gram baseline heldout = 1.9473。定型句の共有と極少量データを強く反映する結果。
今回Replayなしでも旧Aのlossは改善しており、忘却を再現していない。25%が最適という結論は出せない。
512と1024は全ページが512より短いため同じ教師処理となり、差がない。
256との比較では窓分割・更新回数・利用文脈も変わるため、Loss差を文脈長だけの因果効果とは呼べない。

## 8. 不確実性・未実装

実日記、長期学習、普通の個人PC、macOS/Windows/GPUは未検証。3 seedsの範囲は信頼区間ではない。
保持probeは最大4ページ・各最大256targetsの学習済みデータで、near-duplicate検査もない。
独立検証が足りない場合はAPIでunavailableとする。自動quality gate/early stoppingは未実装。
OSの厳密なCPU quota、同時推論、KV cache、適応的Replay、BPE/階層型の実測、完全忘却も未実装。
チェックポイント互換性は保存形式の互換性で、旧版と新版の学習軌跡の一致ではない。
複数分岐のログを混ぜた成長曲線、同じ検証集合で何度も設定を選んだ結果は汎化の証拠としない。

## 9. 次の実験

実際に投入された自作文を時系列でtest-then-train評価。学習・holdoutをページ族/編集版単位で分け、
近似重複も記録する。115K/357K/821Kを同提示量と同CPU時間の両条件で比較。
変化前後で異なる話題の期間を用いて忘却を測り、Replay0/25/50%を検証。
最低30ページは探索開始の目安で検出力保証ではない。判定時点を事前固定し、日単位の相関を扱う。

H: Replayで過去予測の悪化を減らし、次ページの学習前予測を悪化させない。
T: 同一初期値対・時系列・3+ seeds・複数日。初期の人工データは別枠。
D: 事前登録した実用差と区間推定で判定。標本不足はUNCERTAIN。恣意的な成長スコアは作らない。
C: 定型句、UTF-8頻度、近似重複、話題の偏りが改善を説明し得る。
U: 合成標準不確かさは未推定。3 seedsのrangeを包含係数2の区間へ読み替えない。

関連分野: 機械学習（予測と汎化）、制御工学（予算/停止/観測）、DB/分散システム（原子的公開）、
HCI（育成体験と正直な表示）。

## 一次資料

- Muennighoff et al. Scaling Data-Constrained Language Models, JMLR 26(53), 2025. https://jmlr.org/papers/v26/24-1000.html
- Abbes et al. Revisiting Replay and Gradient Alignment for Continual Pre-Training, PMLR330, 2026. https://proceedings.mlr.press/v330/abbes26a.html
- Xue et al. ByT5, TACL2022. DOI:10.1162/tacl_a_00461 https://aclanthology.org/2022.tacl-1.17/
- Yu et al. MEGABYTE, arXiv:2305.07185 https://arxiv.org/abs/2305.07185
- PyTorch 2.10 SDPA https://docs.pytorch.org/docs/2.10/generated/torch.nn.functional.scaled_dot_product_attention.html
- PyTorch 2.10 threads https://docs.pytorch.org/docs/2.10/generated/torch.set_num_threads.html
- Compile caching https://docs.pytorch.org/tutorials/recipes/torch_compile_caching_tutorial.html

文献は設計の根拠であり、数千バイトの個人日記で性能を保証するものではない。
