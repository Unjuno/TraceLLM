# 学習の健全性 / デスクトップ対応

## 方針

FastAPI + SQLite + HTML/CSS/JavaScript/SVGを継続。既存のノートと学習エンジンを
書き換えず、読み取り専用の観測機能を追加しました。Electron/TauriやReactへの全面移行は
今回の機能に不要な配布・ビルドの負担を追加するため採用していません。新しい実行時依存なし。
ブラウザとPythonを使うローカルWebアプリであり、OSネイティブのインストーラーではありません。

## 読み方

|表示|データ・単位|制約|
|---|---|---|
|学習処理|ジョブの公開・失敗・待機|学習や生成の品質ではない|
|初回投入前の予測誤差|初回分類の公開ジョブの prequential_loss、nats/教師トークン|完全一致ハッシュ・ページIDのみ。近似重複、難しさ、話題は未調整|
|対象ページLoss|同じジョブの学習前後Loss|学習済み文章への適合。汎化の証明ではない|
|保持の変化|同じ過去資料での学習後Lossから学習前Lossを引く|正なら誤差増加。資料はジョブ間で変わり得る。統計的な忘却判定ではない|
|提示量|新規側とReplay側の実際の教師トークン|バイトとEOS、Paddingと文脈専用prefixを除く。独立した新しい情報量ではない|
|初出本文版の量（詳細）|first_seen_body_bytes|編集では全文を数える。別分岐も含める場合は分岐ごとに数える|
|処理時間|モデル読込・評価・学習にかかった秒|checkpoint公開の時間は除く|
|CPU時間（詳細）|プロセスのCPU秒|OSのCPU使用率ではない。複数スレッドでは実時間を超え得る|
|復元候補|7日以内の保存台帳＋使用中の状態|読み込み時にファイルの整合性を検証。ポーリングでは重みを読まない|

**合成した健全性スコア、恣意的な能力レベル、自動の成功判定はありません。**
初回投入も独立したテスト集合ではないので、汎化・会話能力は「未評価」です。

## 範囲・系譜・更新

- 期間は投入時刻を基準とする、直近7日・30日・全期間。秒はUTCで保存し、UIでは端末のローカル時刻を表示。
- 現在の系譜はcheckpointのparent_idを辿る。復元時のprevious_id側の子孫は含めない。
- すべての分岐は実験・失敗を含む観測ログであり、一つのモデルの成長曲線ではない。
- 完了して公開された更新だけを品質のグラフ・提示量集計へ使用。失敗・待機は表に残す。
- 割り当て前の待機/取消ジョブは分岐未確定。待機件数は全体の件数を示す。
- 上部の集計は全選択範囲。グラフと表は最近120件。対象範囲の違いを画面で明示する。
- 比較モデルの選択肢は現用モデルを含む最新200件の台帳から作る。集計の復元候補数とは上限が異なる。
- 全期間表示は該当ジョブのメタデータを走査する。非常に長い履歴での遅延は未測定。
- 欠損・NaN・無効な指標はnull、画面では「—/未計測」。旧記録の不足を推測で埋めない。
- 表示中は約2秒ごとに読み取る。バックグラウンドタブはポーリングを休止。
  一回のSQLite snapshotで読み、モデルロックを取らない。重みをロードしない。
- 接続エラー時は古い表示であると警告し、JSON保存を無効化する。
- `/api/learning-health?scope=current&days=7&limit=120`。limitは1〜500。

## 出力比較

ページとモデルA/Bを明示的に選ぶ。比較開始時の同じ本文版を指定し、温度0・seed42・
最大64トークンで生成する。本文が途中で編集された場合は409で停止し、一部だけ成功した
状態を完了と呼ばない。出力は既存の生成履歴に保存され、初期状態では非表示。
学習、重みの切り替え、本文の更新はしない。checkpoint期限切れは明示的なエラー。
並列推論は追加せず、既存の学習ロックで順次実行する。

## プラットフォームと起動

検証対象: Windows x64、macOS Apple Silicon、Linux x64、Python 3.11〜3.13。
現行デスクトップブラウザで利用。スマートフォン、32bit、Windows ARM、Intel Mac native、
旧OSの全バージョンは今回の動作保証に含めない。特にtorch 2.10の公式Mac wheelはarm64向け。
古いPyTorchへの安易なダウングレードや、未検証wheelの利用を案内しない。
Linux VM等の別実行環境は追加検証対象。UIがWeb技術でも、学習ライブラリの対応条件は別。

### Windows PowerShell（仮想環境のActivateは不要）

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install "torch>=2.10,<2.11" --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m trace_llm
```

### macOS（Apple Silicon）

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m trace_llm
```

### Linux（x64）

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install 'torch>=2.10,<2.11' --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -e .
.venv/bin/python -m trace_llm
```

ブラウザで `http://127.0.0.1:8000`。バインド先はlocalhost固定。外部公開サーバーにはしない。
依存インストール時だけ通信が必要。実行時の本文・学習・表示・JSON出力はローカルのみ。
明示的に保存先を変えると別モデルになるため、旧保存先から移行する際はバックアップする。
既存DBが旧既定パスにあるときはその場所を優先し、勝手に移動しない。

## 検証の種類

`pytest` はメトリクス集計、系譜、期限、旧データ欠損、NaN/破損JSON、読み取り専用性、
学習ロックを取らないこと、パス解決を検証する。
`tests/browser_health.py` は実HTTP/実ブラウザで操作し、人工データだけで実際の学習と生成を行う。
3OSのChromiumとmacOSのWebKitをCI対象にした。結果は該当commitのActionsで確認する。
`tests/render_health_preview.py` はナビゲーション制限下のプレビュー用。set_contentで配布DOM/CSS/JSを
読み、fetchを実FastAPI TestClientへ橋渡しする。**HTTP/CSPのE2E成功を代替するものではない。**

原理的にこの画面だけでは、長期汎化や会話能力を証明できない。異なる新規文章の難しさを制御した
評価と、時間を分離した実データの観測が必要。今回モデルの学習アルゴリズムや保存形式は変更しない。

## 参照（公式）

- FastAPI StaticFiles: https://fastapi.tiangolo.com/tutorial/static-files/
- FastAPI lifespan: https://fastapi.tiangolo.com/advanced/events/
- Playwright OS/browser support: https://playwright.dev/python/docs/intro
- GitHub runner architectures: https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- PyTorch wheels: https://download.pytorch.org/whl/torch

## この成果物の実施結果（2026-11-06）

- GitHub PR #1 の `b887d1792434f6735ff7c56f7df5c2d49e70729b` を基点にしたローカル変更。
  元のソースツリーと同梱アーカイブの一致を確認し、別worktreeで作業した。mainは変更していない。
- 最終Pythonテスト: 99 passed。JavaScript構文、Python compileallを確認。
- wheelをビルドし、ソースとは別の場所へ依存なしでインストール。モジュール起動ヘルプ、静的配布物、
  人工ページの実学習、健全性API、旧モデル指定生成、分岐復元を確認した。
- Chromiumでオフラインコンポーネント描画＋実FastAPI TestClientによる操作試験が成功。
  取得フィルター・グラフのキー操作とフォーカス維持・詳細・実生成比較・初期マスク・1024/1440幅を確認。
- localhostへの実ブラウザ接続は環境ポリシー `ERR_BLOCKED_BY_ADMINISTRATOR` で停止。
  HTTP/CSPエンドツーエンド成功、Windows/macOSでの実行成功を主張しない。
- 現在のGitHubコネクターは読み取り専用。新しいpush・PR・今回のCI実行は行っていない。
  3OS向けCI設定は追加済みだが未実行。ソース、wheel、適用用patch、証跡を引き渡す。
- 最終レビューは作者による独立した読み直しであり、第三者レビューではない。
  未評価値を0へ変換しないこと、過去の学習済みモデルの期間外表示、壊れたJSON分類への耐性を確認。
