# TraceLLM

**自分の言葉だけで、小さな言語モデルをゼロから育てるローカルノート。**

1ページは1つの編集可能なノートです。本文の保存や応答生成だけでは学習しません。
ページ一覧を左右にスワイプすると、その版の本文を学習キューに投入します。
同じページを何度でも投入でき、反復回数は1〜10周で指定できます。
モデル出力は初期状態で非表示。クリックすると生成時の出力を確認できます。

**v0.1は実験用の単一ユーザー・ローカルアプリです。** ダミー応答ではなく、
ランダム初期化したGPTの実際の推論と勾配更新を行います。学習データがない初期状態の
出力は不自然な文字列です。日本語の会話能力や「本人の思考の再現」は保証しません。

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
保存先の既定値は `~/.local/share/trace-llm/` です。ソースリポジトリとは別の場所です。

```bash
trace-llm --data-dir /path/to/private/trace-data --port 8000 --threads 2
```

`TRACE_DATA_DIR` 環境変数でも保存先を指定できます。CLI引数が優先されます。
公開サーバーに配置しないでください。認証・複数ユーザー・複数workerは未対応です。
二重起動は保存先のファイルロックで拒否します。GPU利用・量子化・compileは未実装です。

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
- 復習はジョブ全体で最大25%相当の有効トークン予算。短いデータや更新上限により
  実際の割合は小さくなることがあり、実処理量を記録します。
- 1更新は新規の1系列と任意の復習系列。1ジョブ最大64更新。
  上限で指定周回を完了しなかった場合は履歴に部分処理として表示します。
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

対象ページの学習前後Lossは適合の指標です。未知の日記への汎化や成長の証明ではありません。
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
