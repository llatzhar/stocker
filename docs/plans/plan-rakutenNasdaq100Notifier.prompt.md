## Plan: 楽天NASDAQ100ファンドを通知対象に追加

`stock_notifier/notifier.py` を「ファンド単位の処理」に分割するリファクタを行い、既存の MUFG CSV ファンド (`253425`) と新規の楽天証券スクレイピング版 (`NASDAQ100`) を同一スクリプト内で順次処理する。ファンドごとに独立 try/except で、片方が失敗しても他方の通知は継続。

**Steps**

1. **フェーズ1（リファクタ）** — [stock_notifier/notifier.py](stock_notifier/notifier.py) の `build_message` を一般化。
   - `FUND_CODE` グローバル参照を `label: str` 引数化
   - `peak_override: float | None` を追加（外部から最高値を差し替え可能に）
   - `allow_peak_update_note: bool` を追加。**表示文言だけでなく計算分岐も切り替える**: `False` のときは「最高値更新分岐」に入らず、`effective_peak = max(peak_override, last_price)` として常に最高値比（≤ 0%）を計算する。`True`（MUFG 既定）のときは既存の前日比＋🎉 表記ロジックを完全維持。
   - `download_csv` のリトライ部を `download_page(url) -> bytes` に抽出（**戻り値は生 bytes**、デコードは呼び出し側責務）。MUFG 側は従来通り `cp932` でデコード。
2. **フェーズ2（楽天ハンドラ実装）** — *フェーズ1に依存*
   - 定数: `RAKUTEN_FUND_LABEL = "NASDAQ100"`, `RAKUTEN_URL = "https://www.rakuten-sec.co.jp/web/fund/detail/?ID=JP90C000QF22"`
   - **デコード方針**: `download_page` は bytes に加えて HTTP `Content-Type` の charset も返せる形に拡張する（例: `(bytes, charset: str | None)` のタプル、または楽天用に薄いラッパで `urlopen` を直接叩く）。charset 未指定時は `utf-8`。`UnicodeDecodeError` は `ValueError` に変換して上に伝播（`errors="replace"` でのフォールバックはしない）。
   - `parse_rakuten(html: str) -> tuple[list[tuple[str, float]], float]`:
     - 基準価額ヘッダから「現在値＋M/D」を抽出（**これを正とする**）
     - 設定来高値（最高値）を抽出
     - 基準価額の推移表から直近5日を抽出 → `(YYYY/MM/DD, price)` 昇順
     - **ヘッダの (M/D, 現在値) を rows の最終行として必ず採用**。推移表の最新行と日付が一致すればその行で重複させず、不一致ならヘッダ行を推移表の末尾に追加して最終行にする（`build_message` は `rows[-1]` を現在値として扱うため）。ヘッダの価格と推移表同日行の価格が乖離していたら `ValueError`。
     - 戻り値は `(rows, peak)`。`peak` を `build_message` の `peak_override` に渡す。
   - M/D の年は実行日 JST から近接推定（±180日に収まる年）
   - 失敗は `ValueError` を上に伝播
   - 楽天版は `allow_peak_update_note=False` で `build_message` を呼ぶ。変化率は常に最高値比（≤ 0%）、3% 以上の下落時のみ `⚠️ N%ダウン中` を付与。
3. **フェーズ3（統合）** — *フェーズ1,2に依存*
   - `main()` を「ハンドラのリストをループして各々通知」に変更。各ハンドラは独立して try/except し、片方が失敗してもループを継続して他方の通知を送る。
   - **取得/パース失敗**: `【{label}】ページの取得に失敗しました` 通知（既存 `【253425】CSVの取得に失敗しました` メッセージは維持）
   - **本日のデータなし**: 楽天側は `【NASDAQ100】本日のデータなし`（MUFG と同じ書式）
   - **Discord 送信失敗**: そのファンドは失敗扱いで exit code に反映するが、ループは継続して次ファンドへ進む（既存の「通知失敗で即終了」から挙動変更）。失敗詳細は stderr。
   - 終了コードはいずれか失敗で 1、すべて成功で 0。
4. **フェーズ4（ドキュメント）** — *並行可（フェーズ2の決定後）*
   - [stock_notifier/README.md](stock_notifier/README.md) に楽天版の仕様セクションを追加（URL、出力フォーマット、最高値更新表記なし、Discord 失敗時もループ継続）

**Relevant files**
- [stock_notifier/notifier.py](stock_notifier/notifier.py) — 共通化 + 楽天ハンドラ追加（既存 `build_message`, `notify_discord`, `is_today_data`, `format_price` を流用）
- [stock_notifier/README.md](stock_notifier/README.md) — 新ファンド仕様の追記

**Verification**
1. ローカル実行 `python stock_notifier/notifier.py` で MUFG・楽天両方の通知が Discord に届く
2. 楽天ページのHTMLを保存し、`parse_rakuten` を単体で呼んで「現在値18,584／設定来高値18,821／推移5日分」が取れることを確認
3. **年跨ぎ推定の2ケース**:
   - 実行日 `2026/01/03`、表示日 `12/31` → `2025/12/31` に補完
   - 実行日 `2025/12/31`、表示日 `1/2` → `2026/01/02` に補完
4. MUFG 側通知（成功・失敗・本日データなしの全メッセージ）が既存仕様と**バイト単位で完全一致**（リファクタの非破壊性）
5. 楽天ページの一部要素を欠落させた HTML で `【NASDAQ100】ページの取得に失敗しました` が送られ exit 1
6. 楽天版で「現在値 == 設定来高値」のケースでも `🎉 最高値更新` が出力されないこと（変化率は 0.0%、ダウン中表記もなし、`peak_display` は設定来高値）
7. 楽天版でヘッダ価格と推移表同日行の価格が乖離する HTML で `ValueError` → `【NASDAQ100】ページの取得に失敗しました`
8. 片方のファンド処理が失敗してももう片方の通知が正常に送られ、終了コードは 1 になること
9. 楽天 Discord 送信が失敗（Webhook 不正など）しても MUFG 側通知は実行され、終了コードは 1 になること

**Decisions**
- 構成: A（同一スクリプト内で順次処理、1回の cron で両方通知）
- 識別子: `NASDAQ100`
- 変化率: 楽天版は常に最高値比（≤ 0%）を表示。3% 以上の下落時のみ `⚠️ N%ダウン中` を付与。「🎉 最高値更新」表記は出さず、`allow_peak_update_note=False` で計算分岐自体も無効化（前日比へ切り替わらない）。
- 「本日のデータなし」判定: 基準価額ヘッダ横の M/D（年は JST 実行日から近接推定）。メッセージは `【NASDAQ100】本日のデータなし`。
- rows 最終行の正規化: 基準価額ヘッダの (M/D, 現在値) を正として最終行に採用。推移表との価格乖離は `ValueError`。
- `download_page` は生 `bytes`（＋楽天用に charset）を返し、デコードは呼び出し側責務。楽天は `Content-Type` charset → 未指定なら `utf-8`、デコード失敗は `ValueError`。
- 失敗時: 取得失敗・パース失敗・デコード失敗いずれも `【NASDAQ100】ページの取得に失敗しました`／exit 1
- ファンドごとに独立してエラーハンドリング: 取得・パース・Discord 送信のいずれが失敗しても次ファンドへ継続。終了コードはいずれか失敗で 1。
- HTML パースは標準ライブラリ縛り維持のため正規表現で実装

**Further Considerations**
1. **HTML 構造変化への耐性**: 正規表現で各セルを個別に抽出するため、1セルでも欠落したら ValueError → 失敗通知。多少の HTML 揺れにも対応できるよう、空白・全角括弧 `（）`／半角括弧 `()` 両対応で書く。
2. **`build_message` 後方互換**: `label` / `peak_override` / `allow_peak_update_note` は既定値を持たせ、MUFG 既存呼び出しを変更なしで動かせるシグネチャにする（Verification 4 の非破壊性を担保）。
