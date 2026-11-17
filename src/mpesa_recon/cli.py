"""Command line: mpesa-recon STATEMENT.csv EXPECTED.csv [--out DIR]"""
from __future__ import annotations

import argparse
import sys

from .matcher import reconcile
from .parsers import read_expected, read_statement
from .report import summary, write_reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mpesa-recon",
        description="Match M-Pesa statement payments to the people who owe you money.",
    )
    parser.add_argument("statement", help="M-Pesa statement exported as CSV")
    parser.add_argument("expected", help="CSV of who owes what (ref, name, amount_due, ...)")
    parser.add_argument("--out", default="recon-output", help="folder for the result CSVs (default: recon-output)")
    parser.add_argument("--max-edit", type=int, default=1,
                        help="how many typos to tolerate in a reference (default: 1, use 0 to turn off)")
    args = parser.parse_args(argv)

    try:
        payments, skipped = read_statement(args.statement)
        expected = read_expected(args.expected)
    except (OSError, ValueError) as exc:
        print(f"mpesa-recon: {exc}", file=sys.stderr)
        return 2

    result = reconcile(payments, expected, skipped=skipped, max_edit=args.max_edit)
    sys.stdout.write(summary(result) + "\n\n")
    for path in write_reports(result, args.out):
        sys.stdout.write(f"wrote {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
