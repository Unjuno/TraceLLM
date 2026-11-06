# デスクトップで起動する

## 構成の選択
FastAPI + UvicornをローカルのAPIサーバー、既存のHTML/CSS/JavaScriptを画面として使います。
PyTorchはCPU/FP32。React、Electron、Node、GPUを実行時の必須依存にしません。
同じコードをPCのブラウザで開く方式です。署名済みネイティブインストーラーではありません。

## 対応範囲

|OS/CPU|起動方式|CIの対象|
|---|---|---|
|Windows x64|Python 3.11–3.13 / Edge・Chrome等|Windows 2022 runner・Python3.12・Chromium|
|macOS Apple Silicon|ネイティブarm64 Python / Safari・Chrome等|macOS15 ARM64 runner・Python3.12・WebKit|
|Linux x64|Python 3.11–3.13 / Chrome・Firefox等|Ubuntu24.04・Python3.11/3.13・Chromium/Firefox|

上の表は試験対象です。成功・未実行・失敗は実際のGitHub Actions結果で確認してください。
CIのWebKit/Chromiumはブラウザエンジンの試験であり、全世代のSafari/Edge製品の保証ではありません。
Linux ARM64はtorchの適合する配布が必要で、今回の主要CI対象外です。32bit・Windows ARM・
Intel macOSはこの標準セットアップでは対象外です。スマホは今回の検証対象に含めません。

**Intel Macで古いPyTorchへ自動ダウングレードはしません。** PyTorchは2.3以降の
macOS x86_64公式配布を終了しており、2.10を前提とするこのアプリの標準インストールはできません。
Apple SiliconでもRosetta版Pythonではなくarm64 Pythonを使ってください。

一次情報：
- https://dev-discuss.pytorch.org/t/pytorch-macos-x86-builds-deprecation-starting-january-2024/1690
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- https://fastapi.tiangolo.com/deployment/manually/

## 初回セットアップ
コードを取得したディレクトリで実行します。Python3.12を推奨します。
インストール時だけ、Pythonパッケージの配布サーバーへの通信が発生します。
個人の日記やチェックポイントを送信する処理ではありません。

Windows:
```powershell
py -3.12 scripts\install.py
.\start.cmd
```

macOS / Linux（python3が3.11〜3.13であること）:
```bash
python3 scripts/install.py
./start.sh
```

macOSでは実行権限のある `start.command` をFinderから開く方法もあります。
アーカイブ展開で権限を失った場合は `sh start.command`、Linuxは `sh start.sh` を使えます。
仮想環境有効化やPowerShellのExecutionPolicy変更は必要ありません。

インストール先はソース内の `.venv` です。ユーザーが実行した場合だけ依存関係を導入し、
システムPythonのパッケージを直接変更しません。前提だけ確認するには `--check` を指定します。

## 手動起動
仮想環境のPythonから `-m trace_llm` または `trace-llm` が使えます。
```bash
python -m trace_llm --open-browser --threads 2 --port 8000
```
ブラウザを開くのは、このサーバー自身の起動成功を確認してからです。ポート使用中なら
他のアプリのタブを誤って開きません。URLは `http://127.0.0.1:8000` です。
終了は端末のCtrl+C。現在の実装では学習中は終了まで少し待つ場合があります。

## 保存先
優先順は `--data-dir` → `TRACE_DATA_DIR` → 旧保存先に既存DB → OS標準保存先です。
既存データを自動的に移動・削除・再初期化しません。

- Windows新規: `%LOCALAPPDATA%\TraceLLM`（未設定ならユーザーホームのAppData/Local）
- macOS新規: `~/Library/Application Support/TraceLLM`
- Linux新規: `$XDG_DATA_HOME/trace-llm`、未設定/相対パスなら `~/.local/share/trace-llm`
- 旧版の `~/.local/share/trace-llm/trace.sqlite3` があればOSに関わらずそちらを優先

旧先と新先の両方にDBがある場合も旧先を優先します。明示的に `--data-dir` で切り替えてください。
バックアップはアプリ停止後に保存先全体をコピーします。ファイルの同期・移動は自動では行いません。
Windowsの保護はユーザーディレクトリのACLを前提とし、POSIX permission値と同等だとは主張しません。

## 配布の境界
モデル処理はブラウザ内ではなく、そのPCのPythonで動作します。Python不要のexe/dmg/AppImage、
アプリ署名、OSの自動更新、LAN公開、複数ユーザー認証は今回の実装に含みません。
