"""Write results as CSV files and a plain-text summary."""
from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .models import MatchStatus, Result


def summary(result: Result) -> str:
    def total(status: MatchStatus) -> Decimal:
        return sum((m.payment.amount for m in result.by_status(status)), Decimal("0"))

    lines = []
    for status in MatchStatus:
        items = result.by_status(status)
        lines.append(f"{status.value:<10} {len(items):>5} payments   KES {total(status):>14,.2f}")
    lines.append(f"{'duplicates':<10} {len(result.duplicates):>5} ignored (same receipt twice)")
    lines.append(f"{'skipped':<10} {result.skipped:>5} lines (money out, failed, or blank)")

    states: dict[str, int] = {}
    for b in result.balances:
        states[b.state] = states.get(b.state, 0) + 1
    lines.append("")
    lines.append("accounts: " + ", ".join(f"{n} {s}" for s, n in sorted(states.items())))
    return "\n".join(lines)


def write_reports(result: Result, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []

    path = out / "payments.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["receipt", "time", "amount", "account_ref", "payer_name", "payer_phone",
                    "status", "matched_ref", "reason", "candidates"])
        for m in result.matches:
            p = m.payment
            w.writerow([p.receipt, p.time.isoformat(sep=" ") if p.time else "", f"{p.amount:.2f}",
                        p.account_ref, p.payer_name, p.payer_phone, m.status.value,
                        m.expected_ref or "", m.reason, ";".join(m.candidates)])
    written.append(path)

    path = out / "review.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["receipt", "amount", "account_ref", "payer_name", "status", "suggested_ref",
                    "reason", "candidates"])
        for m in result.matches:
            if m.status == MatchStatus.MATCHED:
                continue
            p = m.payment
            w.writerow([p.receipt, f"{p.amount:.2f}", p.account_ref, p.payer_name, m.status.value,
                        m.expected_ref or "", m.reason, ";".join(m.candidates)])
    written.append(path)

    path = out / "balances.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ref", "name", "amount_due", "paid", "remaining", "pending_review", "state"])
        for b in result.balances:
            w.writerow([b.ref, b.name, f"{b.amount_due:.2f}", f"{b.paid:.2f}", f"{b.remaining:.2f}",
                        f"{b.pending_review:.2f}", b.state])
    written.append(path)

    if result.duplicates:
        path = out / "duplicates.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["receipt", "amount", "account_ref"])
            for p in result.duplicates:
                w.writerow([p.receipt, f"{p.amount:.2f}", p.account_ref])
        written.append(path)

    return written
