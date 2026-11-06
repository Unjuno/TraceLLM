# TraceLLM

**自分の言葉だけで、小さな言語モデルをゼロから育てるローカルノート。**

1ページは1つの編集可能なノートです。本文の保存や応答生成だけでは学習しません。
ページ一覧を左右にスワイプすると、その版の本文を学習キューに投入します。
同じページを何度でも投入でき、反復回数は1〜10周で指定できます。
モデル出力は初期状態で非表示。クリックすると生成時の出力を確認できます。

**v0.4は実験用の単一ユーザー・ローカルアプリです。** ダミー応答ではなく、
ランダム初期化したGPTの実際の推論と勾配更新を行います。学習データがない初期状態の
出力は不自然な文字列です。日本語の会話能力や「本人の思考の再現」は保証しません。

## v0.4 — 明示的な状態管理と原稿保護

v0.4では、ブラウザの編集中・保存中・競合・エラー・応答マスクを明示的な状態遷移で制御します。
学習ジョブはSQLite上で`queued → running → succeeded/failed`、または`queued → cancelled`に限定。
ページ削除と学習完了が重なった場合、公開直前に対象ページ・現用モデル・ジョブ状態を同一トランザクション内で再検証します。
DB公開に失敗した未登録チェックポイントは後始末します。保存競合でも原稿を失わず、別ページへの退避か明示的な再読込を選べます。

新規モデルの重みや学習データは追加していません。`SQLite version 1`および`checkpoint format 1`を維持。
状態遷移図、検証対象、残る制約は[`docs/state-machines.md`](docs/state-machines.md)を参照してください。

## 起動

Python 3.11〜3.13を使用してください。最初の依存関係インストールには通信が必要です。
実行時はモデルのダウンロードや外部APIへの送信を行いません。

```bash
git clone https://github.com/Unjuno/TraceLLM.git
cd TraceLLM
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
# Linux / WindowsでCPU専用PyTorchを先に入れる場合:
# python -m pip install 'torch>=2.10,<2.11' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e .
trace-llm
```

ブラウザで `http://127.0.0.1:8000` を開きます。
新しい保存先の既定値は、Windowsでは `%LOCALAPPDATA%\TraceLLM`、macOSでは
`~/Library/Application Support/TraceLLM`、Linuxでは `$XDG_DATA_HOME/trace-llm`
（未設定時 `~/.local/share/trace-llm`）です。ソースリポジトリとは別の場所です。
**旧版の `~/.local/share/trace-llm/trace.sqlite3` があれば、その保存先をそのまま使います。**
自動で移動・統合・作り直しはしません。`--data-dir`、`TRACE_DATA_DIR`、旧保存先、OS既定値の順に優先します。

```bash
trace-llm --data-dir /path/to/private/trace-data --port 8000 --threads 2
```

`TRACE_DATA_DIR` 環境変数でも保存先を指定できます。CLI引数が優先されます。
公開サーバーに配置しないでください。認証・複数ユーザー・複数workerは未対応です。
二重起動は保存先のファイルロックで拒否します。GPU利用・量子化・compileは未実装です。

## 学習の健全性（v0.3）

左の「学習の健全性」から、処理の失敗・部分処理、初回投入前のLoss、過去資料の保持、
実際の教師提示量を確認できます。「現在のモデルの系譜 / すべての分岐」「7日 / 30日 / 全期間」を切り替え可能。
グラフと表は最近120件、上部の集計は選択範囲全体です。欠損値はゼロにしません。
**処理成功は言語能力の向上ではありません。独立した検証データがないため汎化は未評価と表示します。**

同じ本文版を2つの保存済みモデルに入力し、マスクした出力を比較できます。
この操作は生成履歴を保存するだけで、モデルを学習・復元しません。
計測JSONはローカルに保存でき、本文・ページ名・生成文を含みません。
詳細は [指標とプラットフォーム](docs/health-ui.md) を参照してください。

`python -m trace_llm` でも起動できます。Node.js、デスクトップ実行基盤、CDNは実行時に不要です。

## 操作

1. 「新しいページ」で日記・メモを記入し「保存」。移動時にも保存します。
2. 「送信して応答を生成」でその時点のモデルが本文の続きを生成。生成だけでは学習しません。
3. 左一覧のページを左右にスワイプして学習。PCではドラッグ、ページの「⋯」メニュー、
   またはページ行にフォーカスして左右矢印キーでも同じ操作ができます。
4. 「学習履歴」で待機・実行・完了・失敗、提示トークン数、対象ページLossを確認。
5. 保存されたチェックポイントから復元。元の分岐は残り、ページ本文は巻き戻しません。

未保存でタブを閉じると警告します。ブラウザ内だけの自動保存ではないため、
意図的に警告を無視すると未保存編集は失われます。複数タブからの古い版の上書きは409で拒否します。

## 実装済み

- 本文の不変な版管理。投入後に編集しても学習ジョブの本文は変化しません。
- SQLiteの永続キュー。通信再試行は同じrequest keyで重複排除、明示的再投入は別イベント。
- nanoGPT型の独立実装: Decoder-only / RoPE / 2層 / 幅64 / 4 heads / **115,136 parameters**。
- UTF-8バイト256種類＋BOS/EOS。外部Tokenizer・学習済み重み・外部コーパス不使用。
- CPU FP32、AdamW、学習率0.0003、betas=(0.9,0.95)、行列のみweight decay 0.01、
  dropout 0.1、gradient norm clipping 1.0。これらは最適性未検証の初期設定です。
- 学習・推論コンテキストは256バイトトークン。長いページの推論には末尾のみを使用し明示します。
- ページごとの教師トークンを一巡する反復。短いページ、末尾の不足、パディングに対応。
- 過去データは現在のモデル分岐が学習した各ページの最新投入版だけから復習。
  新規ページ自身はReplayから除外し、未投入・削除済みページは追加しません。
- 復習は既定ではジョブ全体で最大25%相当の有効トークン予算。短いデータや更新上限により
  実際の割合は小さくなることがあり、実処理量を記録します。
- 1更新は新規の1系列と任意の復習系列。有効教師トークン数で正規化した勾配累積を使い、
  短い復習系列のPadding計算を省きます。指定周回が主予算、64更新と学習ループ5秒は安全上限です。
  上限で指定周回を完了しなかった場合は、実際のトークン数と停止理由を表示します。
- モデル、AdamW、torch RNG、サンプラーRNG、学習済み版、更新数をチェックポイントに保存。
- 一時ファイル→fsync→rename→DBトランザクションでモデル公開とジョブ完了を確定。
- 非有限Loss・勾配・パラメータは公開せず旧モデルを保持。
- 学習待ちは再起動後に継続。公開前に中断された実行中ジョブは失敗として記録し、自動再実行しません。
- 7日を過ぎた非現用チェックポイントは削除。現用モデルは常に保持します。
  長い休止後の学習でも直前へ戻せるよう、置き換え時に旧現用モデルの期限を7日延長します。
- 応答は上書きせず本文版・モデルID・生成条件・raw tokensとともに保存。直近100件をUI表示。

## 重要な区別

保存すること、生成すること、学習することは別です。モデル出力は学習データに自動追加しません。
ページ削除は一覧からの非表示と今後のReplay除外です。過去の本文版・ログは残り、
モデルからの忘却や個人情報の完全消去を意味しません。完全削除の専用フローは未実装です。
バックアップ・完全消去時は停止したアプリの保存先全体を対象にしてください。

対象ページの学習後Lossは適合の指標です。初回投入前Lossは別に記録し、反復・編集を除外します。
完全一致ハッシュによる判定なので、近似重複や文章の独立性は保証しません。汎化や成長の証明ではありません。
初期版には独立した検証データも自動品質昇格ゲートもありません。
`content_seen_before` は現在の分岐の本文ハッシュによる既出判定であり、
類似文・部分重複の検出ではありません。Byte-level出力は無効なUTF-8を含むことがあり、
表示には置換文字を使います。EOSで生成が即終了する場合もあります。

## 構成

```text
src/trace_llm/
  app.py         # localhost API + static UI + CLI
  engine.py      # page revisions, queue, training, generation, checkpoint publication
  store.py       # SQLite schema and short transactions
  model.py       # scratch causal GPT with RoPE
  data.py        # byte tokenizer and page-local supervised windows
  static/        # approved page-style UI, no frontend build step
 tests/          # model, persistence, API, optional real-browser smoke test
 docs/           # architecture, privacy, implementation plan
```

1プロセス内の学習ワーカーがジョブを直列実行します。推論・復元・学習はモデルロックで
排他実行するため、学習中の推論は待機する場合があります。ページの保存と読取は独立した
短いSQLiteトランザクションです。学習中の未確定重みは生成に使いません。

## テスト

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
# UI状態遷移のテスト（開発時のみ。Node.js 18以降）
node --test tests/ui_machine.test.cjs
# ローカルAPI連携の画面テスト（Playwright Chromiumを利用）
python tests/browser_state.py --browser chromium
# 任意の実ブラウザ検証（毎回、一時保存先と人工文章だけを使用）:
python -m playwright install chromium
python tests/browser_smoke.py
# システムChromiumを使う場合:
# TRACE_BROWSER_EXECUTABLE=/usr/bin/chromium python tests/browser_smoke.py
```

ブラウザテストは自動で一時サーバーを立て、ページ作成、マスク解除、左右スワイプ学習、
分岐復元、再読込、モバイル幅を検証します。制限された実行環境ではブラウザ起動・接続が
拒否される場合があります。その場合、APIテスト通過を画面検証通過と読み替えないでください。

## 次の段階

実際の自作文を使った時系列評価、N-gram基準との比較、サイズ・学習率・Replay割合の比較、
長文対応、バックアップ/エクスポート/完全削除、GPU実測が未実施です。
スループットや会話品質の保証値はありません。

詳細は [設計](docs/architecture.md) と [安全上の制約](SECURITY.md) を参照してください。
このリポジトリの利用許諾ライセンスはまだ設定していません。

学習ワーカー自体が停止した場合、サイドバーに「学習ワーカー停止（再起動が必要）」と表示します。
この表示は学習キューの失敗履歴とは別です。原因を確認した後、安全にアプリを再起動してください。


## v0.2: CPU継続学習の計測と制御

既存保存先はそのまま開けます。SQLite version 1、checkpoint format 1を維持し、
training/tokenizer/exposure_totalsを追加しました。旧モデルの重みとAdamW状態はリセットしません。
未計測の過去の累積トークン数は「不明」として扱い、0から学んだことにはしません。
異なるモデル・学習設定を既存保存先へ指定すると拒否します。モデル大型化・文脈長変更の自動移行は未実装です。

通常の起動は従来どおり `trace-llm`。以下は**新規の独立した保存先**用の例です。

```bash
trace-llm --data-dir ./private-experiment --model-preset seed --context 256 \
  --replay-ratio 0.25 --learning-rate 0.0003 --max-steps 64 \
  --max-train-seconds 5 --duty-cycle 0.5 --threads 1
```

`--duty-cycle` は更新間に休止を入れる協調制御で、OSのCPU使用率の厳密な上限ではありません。
初期化・評価・保存には適用しません。5秒も更新間で確認するソフト上限であり、ジョブ全体の締切ではありません。
学習と推論の排他制御は維持しています。GPU・KV cache・compileは必須ではなく、現行アプリでは未使用です。
`--model-preset medium` は357,216、`small` は820,608パラメータ。`--context 512` / `1024` も比較できます。
`--context-prefix` は長文窓に損失マスク付き左文脈を追加します。既定0で旧データ処理を維持します。
`--batch-mode padded` で従来型のPadding一括計算と比較できます。

- `GET /api/evaluations`: 初回投入前Loss、反復・編集の区別、最大4件の過去投入ページの保持評価、
  実提示トークン数、処理時間。直近200件・複数分岐を含むため、全件を一つの学習曲線として集計しないでください。
- `POST /api/pages/{id}/responses`: 任意の `checkpoint_id` を指定すると、保存期間内のモデルで
  同じページ本文を生成し直せます。現在のモデル参照や以前の出力は変更しません。
- 過去ページの保持評価は学習済み資料の抜き取り評価です。未学習データ評価ではありません。
- `ByteNgram` は明示的に呼ぶ診断用です。アプリの生成には混ぜません。
- 学習品質による自動採否判定は未実装。非有限値の更新だけは従来どおり公開しません。

[監査・実測結果](docs/cpu-study.md) と [設計](docs/superpowers/specs/2026-11-06-cpu-learning.md) を参照してください。
比較実験は人工文章だけを一時保存先へ学習させます。本番モデルや日記保存先を指定する引数はありません。

```bash
PYTHONPATH=src python benchmarks/cpu_study.py --section speed --output speed.json
PYTHONPATH=src python benchmarks/cpu_study.py --section quality --output quality.json
PYTHONPATH=src python benchmarks/cpu_study.py --section e2e --output e2e.json
PYTHONPATH=src python benchmarks/cpu_study.py --section cold --output cold.json
PYTHONPATH=src python benchmarks/cpu_study.py --section compile --output compile.json
```

ベンチマークスクリプトはLinux用です。`quality` は3シードの探索実験であり、個人の日記の検証を代替しません。
