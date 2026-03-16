import csv
import sys
from pathlib import Path


def format_price(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def resolve_input_path(arg: str) -> Path:
    provided = Path(arg)
    if provided.exists():
        return provided

    script_dir = Path(__file__).resolve().parent
    candidate = script_dir / arg
    if candidate.exists():
        return candidate

    return provided


def process_csv(csv_path: Path) -> None:
    peak_price = None
    next_threshold = 3.0

    with csv_path.open("r", encoding="cp932", newline="") as f:
        reader = csv.reader(f)

        # Skip 2 header rows as defined in the specification.
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
                current_price = float(price_str)
            except ValueError:
                continue

            if peak_price is None or current_price > peak_price:
                peak_price = current_price
                next_threshold = 3.0
                continue

            if peak_price <= 0:
                continue

            drawdown_pct = (peak_price - current_price) / peak_price * 100.0

            while drawdown_pct >= next_threshold:
                print(
                    f"{date_str}: 最高値 {format_price(peak_price)} → 現在値 {format_price(current_price)} "
                    f"(-{drawdown_pct:.1f}%) [{int(next_threshold)}%ドローダウン突破]"
                )
                next_threshold += 3.0


def main() -> int:
    if len(sys.argv) != 2:
        print("使い方: python drawdown.py <csvファイル名>")
        return 1

    csv_path = resolve_input_path(sys.argv[1])
    if not csv_path.exists():
        print(f"ファイルが見つかりません: {csv_path}")
        return 1

    process_csv(csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
