# stock_notifier

## 概要

Debian サーバの cron で毎朝自動実行し、ファンドの基準価額の最新情報を Discord に通知するツール。
状態ファイルは保持せず、CSV最終行の日付と実行日（JST）を比較して新規データの有無を判断する。

## ファイル構成

```
stock_notifier/
  notifier.py         # メイン処理
```

## 使い方（ローカル実行）

```bash
python stock_notifier/notifier.py
```

環境変数 `DISCORD_WEBHOOK_URL` に Discord の Webhook URL を設定してから実行する。

```bash
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
python stock_notifier/notifier.py
```

---

## 動作仕様

### 1. CSVの取得

以下のURLから基準価額CSVを毎回ダウンロードする。

- `https://www.am.mufg.jp/fund_file/setteirai/253425.csv`

### 2. 更新行の検出

- CSV最終行の日付を取得する。
- 実行日（JST）との差分日数を判定する。
- 判定式: `実行日 - 最終行日付 <= 1日` なら **更新あり**
- 判定式: `実行日 - 最終行日付 >= 2日` なら **更新なし**
- 未来日付（`実行日 - 最終行日付 < 0日`）は異常値として **更新なし** 扱いにする。

### 3. 更新なし時の通知

```
【253425】本日のデータなし
```

更新なし通知には年月日を付与しない。

### 4. 更新あり時の通知

CSVの全行を走査して過去最高値を算出し（drawdown.py と同じロジック）、最終行のデータと比較して通知を送信する。

#### 通知フォーマット

```
【YYYY/MM/DD】【253425】最高値:XXXXX 現在値:YYYYY (±Z.Z%) 所感
```

- `YYYY/MM/DD` は通知対象であるCSV最終行の日付を使用する。
- `±Z.Z%` の定義:
  - **最高値更新時:** 前日比（CSV最終行の価格 ÷ 前行の価格 − 1）
  - **それ以外:** 最高値からの変化率（常に 0% 以下）

#### 所感の判定ルール

| 優先順位 | 条件 | 所感 |
|---|---|---|
| 1 | 最終行の価格が過去最高値を**厳密に上回る** | 🎉 最高値更新 |
| 2 | 最高値からの下落率が 3% 以上 | ⚠️ N%ダウン中 |
| 3 | 上記のいずれでもない（最高値と同値 or 下落率 3% 未満） | （所感なし） |

> N は `floor(下落率 ÷ 3) × 3` で求める 3 の倍数。
> 例: 下落率 5.2% → `floor(5.2/3)×3 = 3` → `⚠️ 3%ダウン中`
> 例: 下落率 6.0% → `floor(6.0/3)×3 = 6` → `⚠️ 6%ダウン中`
>
> 状態ファイルは保持しないため「初めてその閾値に到達」の検出はしない。
> 同じ下落レベルが続く日も同じ所感を繰り返し通知する（重複は許容）。

#### 通知例

```
# 最高値更新（変化率は前日比）
【2026/03/13】【253425】最高値:35123 現在値:35123 (+5.2%) 🎉 最高値更新

# 3%台ダウン中（変化率は最高値比）
【2026/03/13】【253425】最高値:35123 現在値:34069 (-3.0%) ⚠️ 3%ダウン中

# 6%台ダウン中
【2026/03/13】【253425】最高値:35123 現在値:32815 (-6.6%) ⚠️ 6%ダウン中

# 通常（下落率 3% 未満、所感なし）
【2026/03/13】【253425】最高値:35123 現在値:34500 (-1.8%)
```

---

## サーバ設定（Debian）

### 前提

- Python 3.10 以上がインストールされていること
- 外部ライブラリは不要（標準ライブラリのみ使用）

### セットアップ

```bash
# リポジトリを配置
git clone <リポジトリURL> /opt/stocker
```

### 環境変数の設定

Webhook URL を記載したファイルを作成する。

```bash
sudo mkdir -p /etc/stocker
sudo tee /etc/stocker/env > /dev/null <<'EOF'
export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
EOF
sudo chmod 600 /etc/stocker/env
```

### cron の登録

毎朝 9:00 JST に実行する例（サーバのタイムゾーンが JST の場合）:

```bash
sudo crontab -e
```

```cron
0 9 * * * . /etc/stocker/env && /usr/bin/python3 /opt/stocker/stock_notifier/notifier.py >> /var/log/stocker.log 2>&1
```

> サーバが UTC の場合は `0 0 * * *`（UTC 00:00 = JST 09:00）に変更する。

### 動作確認

```bash
. /etc/stocker/env && /usr/bin/python3 /opt/stocker/stock_notifier/notifier.py
```

### Discord Webhook URL の取得方法

1. 通知を送りたい Discord チャンネルの設定を開く
2. 「連携サービス」→「ウェブフック」→「新しいウェブフック」
3. 作成後に「ウェブフック URL をコピー」

---

## 入力CSVの仕様

`allcountrycsv/README.md` 参照。

---

## 制約・前提

- CSV は Shift_JIS エンコーディング
- 日付は昇順（古い→新しい）で格納されている前提
- タイムゾーンは JST 固定で判定する
- 状態ファイルは保持しないため、手動再実行時の重複通知は許容する
- ダウンロード失敗時は Discord に `【253425】CSVの取得に失敗しました` と通知して終了コード1
