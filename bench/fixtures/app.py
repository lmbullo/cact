import sys

from lib import parse_row

# TODO: stream instead of loading the whole file.


def main(path: str) -> int:
    total = 0.0
    with open(path, "r", encoding="utf-8") as fh:
        header = fh.readline()
        for raw in fh:
            row = parse_row(raw.strip())
            total += row["amount"]
    print(f"{total:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
