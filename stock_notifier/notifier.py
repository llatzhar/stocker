import csv
from html import unescape
import json
import os
import re
import sys
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FUND_CODE = "253425"
CSV_URL = f"https://www.am.mufg.jp/fund_file/setteirai/{FUND_CODE}.csv"
RAKUTEN_FUND_LABEL = "NASDAQ100"
RAKUTEN_URL = "https://www.rakuten-sec.co.jp/web/fund/detail/?ID=JP90C000QF22"
JST = timezone(timedelta(hours=9))
TODAY_DATA_MAX_AGE_HOURS = 24


def download_page(
    url: str,
    *,
    max_retries: int = 3,
    retry_delay: float = 10.0,
) -> tuple[bytes, str | None]:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with urlopen(req, timeout=30) as response:
                return response.read(), response.headers.get_content_charset()
        except HTTPError as error:
            last_error = error
            if error.code in (403, 429, 500, 502, 503, 504) and attempt < max_retries - 1:
                print(f"HTTP {error.code} — {retry_delay}秒後にリトライします ({attempt + 1}/{max_retries})",
                      file=sys.stderr)
                time.sleep(retry_delay)
                continue
            break
        except URLError as error:
            last_error = error
            if attempt < max_retries - 1:
                print(f"接続エラー — {retry_delay}秒後にリトライします ({attempt + 1}/{max_retries})",
                      file=sys.stderr)
                time.sleep(retry_delay)
                continue
            break
    raise RuntimeError(f"ページの取得に失敗しました: {last_error}") from last_error


def download_csv(*, max_retries: int = 3, retry_delay: float = 10.0) -> bytes:
    try:
        data, _charset = download_page(CSV_URL, max_retries=max_retries, retry_delay=retry_delay)
    except RuntimeError as error:
        raise RuntimeError(f"CSVの取得に失敗しました: {error}") from error
    return data


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
    """CSV最終行の日付がJSTで一定時間以内なら本日分と判断する。"""
    now_jst = datetime.now(JST)
    try:
        last_date = datetime.strptime(last_date_str, "%Y/%m/%d").date()
    except ValueError:
        return False
    # datetime_time(0, 0) = その日の 00:00
    last_datetime_jst = datetime.combine(last_date, datetime_time(0, 0), tzinfo=JST)
    age = now_jst - last_datetime_jst
    max_age = timedelta(hours=TODAY_DATA_MAX_AGE_HOURS)
    return timedelta(0) <= age <= max_age


def format_price(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def build_message(
    rows: list[tuple[str, float]],
    *,
    label: str = FUND_CODE,
    peak_override: float | None = None,
    allow_peak_update_note: bool = True,
) -> str:
    if not rows:
        raise ValueError("通知対象データがありません")

    last_date, last_price = rows[-1]
    prev_price = rows[-2][1] if len(rows) >= 2 else last_price

    # 最終行を除いた過去の最高値
    peak = peak_override if peak_override is not None else (
        max(price for _, price in rows[:-1]) if len(rows) >= 2 else last_price
    )

    if allow_peak_update_note and last_price > peak:
        # 最高値更新: 変化率は前日比
        change_pct = (last_price - prev_price) / prev_price * 100.0
        sign = "+" if change_pct >= 0 else ""
        note = "🎉 最高値更新"
        peak_display = last_price
    else:
        # 最高値未更新: 変化率は最高値比
        effective_peak = peak if allow_peak_update_note else max(peak, last_price)
        change_pct = (last_price - effective_peak) / effective_peak * 100.0
        sign = ""  # 常に 0 以下
        drawdown_pct = -change_pct
        level = int(drawdown_pct / 3) * 3
        note = f"⚠️ {level}%ダウン中" if level >= 3 else ""
        peak_display = effective_peak

    change_str = f"{sign}{change_pct:.1f}%"
    msg = (
        f"【{last_date}】【{label}】"
        f"最高値:{format_price(peak_display)} 現在値:{format_price(last_price)} ({change_str})"
    )
    if note:
        msg += f" {note}"
    return msg


def parse_price(price_str: str) -> float:
    normalized = price_str.replace(",", "").replace("\u00a0", "").strip()
    return float(normalized)


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"[\s\u00a0]+", " ", text).strip()


def infer_nearest_jst_date(month: int, day: int, *, today: date | None = None) -> date:
    base_date = today if today is not None else datetime.now(JST).date()
    candidates: list[date] = []
    for year in (base_date.year - 1, base_date.year, base_date.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        raise ValueError(f"日付を補完できません: {month}/{day}")

    nearest = min(candidates, key=lambda candidate: abs((candidate - base_date).days))
    if abs((nearest - base_date).days) > 180:
        raise ValueError(f"日付が実行日から離れすぎています: {month}/{day}")
    return nearest


def format_date(value: date) -> str:
    return value.strftime("%Y/%m/%d")


def parse_rakuten(html: str) -> tuple[list[tuple[str, float]], float]:
    text = html_to_text(html)

    header_match = re.search(
        r"基準価額\s+([0-9,]+)\s*円?\s*[（(]\s*(\d{1,2})/(\d{1,2})\s*[）)]",
        text,
    )
    if not header_match:
        raise ValueError("楽天ページから基準価額ヘッダを抽出できません")
    header_price = parse_price(header_match.group(1))
    header_date = infer_nearest_jst_date(int(header_match.group(2)), int(header_match.group(3)))
    header_date_str = format_date(header_date)

    peak_match = re.search(
        r"設定来高値\s+([0-9,]+)\s*円\s*[（(]\s*"
        r"\d{4}\s*[./年]\s*\d{1,2}\s*[./月]\s*\d{1,2}\s*日?\s*[）)]",
        text,
    )
    if not peak_match:
        raise ValueError("楽天ページから設定来高値を抽出できません")
    peak = parse_price(peak_match.group(1))

    row_pattern = r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s+([0-9,]+)\s*円"
    section_matches = re.finditer(
        r"基準価額の推移\s+(.*?)(?=\s+分配金[（(]|\s+ファンドスコア|\s+リスクリターン|$)",
        text,
    )
    row_matches: list[tuple[str, str, str, str]] = []
    for section_match in section_matches:
        current_matches = re.findall(row_pattern, section_match.group(1))
        if len(current_matches) > len(row_matches):
            row_matches = current_matches
    if not row_matches:
        raise ValueError("楽天ページから基準価額の推移行を抽出できません")

    parsed_rows = sorted(
        (
            (date(int(year), int(month), int(day)), parse_price(price_str))
            for year, month, day, price_str in row_matches
        ),
        key=lambda row: row[0],
    )
    rows = [(format_date(row_date), price) for row_date, price in parsed_rows[-5:]]

    normalized_rows: list[tuple[str, float]] = []
    for row_date, price in rows:
        if row_date > header_date_str:
            raise ValueError("楽天ページの基準価額ヘッダより新しい推移表行があります")
        if row_date == header_date_str:
            if price != header_price:
                raise ValueError("楽天ページの基準価額ヘッダと推移表の価格が一致しません")
            continue
        normalized_rows.append((row_date, price))

    normalized_rows.append((header_date_str, header_price))
    return normalized_rows, peak


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
    except HTTPError as error:
        detail = ""
        try:
            body = error.read().decode("utf-8", errors="replace")
            if body:
                detail = f" body={body}"
        except Exception:
            pass
        raise RuntimeError(f"Discord通知失敗: HTTP {error.code}{detail}") from error
    except URLError as error:
        raise RuntimeError(f"Discord通知失敗: {error}") from error


def build_mufg_message() -> str:
    data = download_csv()
    rows = parse_csv(data)
    if not rows:
        raise ValueError("CSVにデータがありません")

    last_date = rows[-1][0]
    if not is_today_data(last_date):
        return f"【{FUND_CODE}】本日のデータなし"
    return build_message(rows)


def build_rakuten_message() -> str:
    data, charset = download_page(RAKUTEN_URL)
    encoding = charset or "utf-8"
    try:
        html = data.decode(encoding)
    except (LookupError, UnicodeDecodeError) as error:
        raise ValueError(f"楽天ページのデコードに失敗しました: {error}") from error

    rows, peak = parse_rakuten(html)
    if not is_today_data(rows[-1][0]):
        return f"【{RAKUTEN_FUND_LABEL}】本日のデータなし"
    return build_message(
        rows,
        label=RAKUTEN_FUND_LABEL,
        peak_override=peak,
        allow_peak_update_note=False,
    )


def run_fund(
    *,
    label: str,
    webhook_url: str,
    build_message_func,
    fetch_failure_message: str,
) -> bool:
    try:
        message = build_message_func()
    except (RuntimeError, ValueError) as error:
        try:
            notify_discord(webhook_url, fetch_failure_message)
        except RuntimeError as notify_error:
            print(notify_error, file=sys.stderr)
        print(error, file=sys.stderr)
        return False

    try:
        notify_discord(webhook_url, message)
    except RuntimeError as error:
        print(f"【{label}】{error}", file=sys.stderr)
        return False

    print(message)
    return True


def main() -> int:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL が設定されていません", file=sys.stderr)
        return 1

    handlers = [
        {
            "label": FUND_CODE,
            "build_message_func": build_mufg_message,
            "fetch_failure_message": f"【{FUND_CODE}】CSVの取得に失敗しました",
        },
        {
            "label": RAKUTEN_FUND_LABEL,
            "build_message_func": build_rakuten_message,
            "fetch_failure_message": f"【{RAKUTEN_FUND_LABEL}】ページの取得に失敗しました",
        },
    ]

    has_failure = False
    for handler in handlers:
        succeeded = run_fund(webhook_url=webhook_url, **handler)
        if not succeeded:
            has_failure = True
    return 1 if has_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
