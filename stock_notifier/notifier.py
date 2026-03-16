import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FUND_CODE = "253425"
CSV_URL = f"https://www.am.mufg.jp/fund_file/setteirai/{FUND_CODE}.csv"
JST = timezone(timedelta(hours=9))


def download_csv() -> bytes:
    req = Request(CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=30) as response:
            return response.read()
    except URLError as e:
        raise RuntimeError(f"CSVの取得に失敗しました: {e}") from e


def parse_csv(data: bytes) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    text = data.decode("cp932")
    reader = csv.reader(text.splitlines())
    next(reader, None)
    next(reader, None)
    for row in reader:
        if len(row) < 2:
            continue
        date_str = row[0].strip()
        price_str = row[1].strip()
        if not date_str or not price_str:
            continue
        try:
            price = float(price_str)
        except ValueError:
            continue
        rows.append((date_str, price))
    return rows


def is_today_data(last_date_str: str) -> bool:
    """CSV最終行の日付が実行日（JST）と0〜1日差以内であれば本日分と判断する。"""
    today_jst = datetime.now(JST).date()
    try:
        last_date = datetime.strptime(last_date_str, "%Y/%m/%d").date()
    except ValueError:
        return False
    diff = (today_jst - last_date).days
    return 0 <= diff <= 1


def format_price(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def build_message(rows: list[tuple[str, float]]) -> str:
    last_date, last_price = rows[-1]
    prev_price = rows[-2][1] if len(rows) >= 2 else last_price

    # 最終行を除いた過去の最高値
    peak = max(price for _, price in rows[:-1]) if len(rows) >= 2 else last_price

    if last_price > peak:
        # 最高値更新: 変化率は前日比
        change_pct = (last_price - prev_price) / prev_price * 100.0
        sign = "+" if change_pct >= 0 else ""
        note = "🎉 最高値更新"
        peak_display = last_price
    else:
        # 最高値未更新: 変化率は最高値比
        change_pct = (last_price - peak) / peak * 100.0
        sign = ""  # 常に 0 以下
        drawdown_pct = -change_pct
        level = int(drawdown_pct / 3) * 3
        note = f"⚠️ {level}%ダウン中" if level >= 3 else ""
        peak_display = peak

    change_str = f"{sign}{change_pct:.1f}%"
    msg = (
        f"【{last_date}】【{FUND_CODE}】"
        f"最高値:{format_price(peak_display)} 現在値:{format_price(last_price)} ({change_str})"
    )
    if note:
        msg += f" {note}"
    return msg


def notify_discord(webhook_url: str, message: str) -> None:
    payload = json.dumps({"content": message}).encode("utf-8")
    post_url = webhook_url if "?" in webhook_url else f"{webhook_url}?wait=true"
    req = Request(
        post_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "stock-notifier/1.0",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f"Discord通知失敗: HTTP {response.status}")
    except HTTPError as e:
        detail = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
            if body:
                detail = f" body={body}"
        except Exception:
            pass
        raise RuntimeError(f"Discord通知失敗: HTTP {e.code}{detail}") from e
    except URLError as e:
        raise RuntimeError(f"Discord通知失敗: {e}") from e


def main() -> int:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL が設定されていません", file=sys.stderr)
        return 1

    try:
        data = download_csv()
    except RuntimeError as e:
        try:
            notify_discord(webhook_url, f"【{FUND_CODE}】CSVの取得に失敗しました")
        except RuntimeError:
            pass
        print(e, file=sys.stderr)
        return 1

    rows = parse_csv(data)
    if not rows:
        print("CSVにデータがありません", file=sys.stderr)
        return 1

    last_date = rows[-1][0]
    if not is_today_data(last_date):
        message = f"【{FUND_CODE}】本日のデータなし"
    else:
        message = build_message(rows)

    try:
        notify_discord(webhook_url, message)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1

    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
