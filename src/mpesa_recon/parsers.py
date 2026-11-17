"""Read M-Pesa statements and expected-payment lists from CSV.

Statement exports differ between the M-Pesa business portal, personal
statements converted to CSV, and Daraja C2B callback logs. Rather than
hard-coding one layout, we find the header row and map columns by alias.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional, Union

from .models import Expected, Payment

PathOrText = Union[str, Path, io.TextIOBase]


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.strip().lower())


STATEMENT_ALIASES = {
    "receipt": ["receiptno", "receipt", "transactionid", "transid", "mpesareceipt", "mpesareceiptnumber"],
    "time": ["completiontime", "transactiontime", "transtime", "date", "datetime", "initiationtime"],
    "details": ["details", "description", "narration", "particulars"],
    "status": ["transactionstatus", "status"],
    "paid_in": ["paidin", "moneyin", "credit", "amountin", "transamount"],
    "amount": ["amount"],
    "account": ["acno", "accountno", "accountnumber", "billrefnumber", "accountreference", "accref", "reference"],
    "other_party": ["otherpartyinfo", "otherparty", "customer", "sender"],
    "phone": ["msisdn", "phone", "phonenumber"],
    "first_name": ["firstname"],
    "middle_name": ["middlename"],
    "last_name": ["lastname"],
}

EXPECTED_ALIASES = {
    "ref": ["ref", "reference", "admission", "admno", "admissionno", "admissionnumber",
            "account", "accountno", "invoice", "invoiceno", "id", "studentid", "customerid"],
    "name": ["name", "studentname", "student", "customername", "customer", "fullname"],
    "amount_due": ["amountdue", "due", "amount", "balance", "feebalance", "owed", "total"],
    "phones": ["phone", "phones", "phonenumber", "parentphone", "guardianphone", "msisdn"],
    "payer_names": ["payer", "payers", "payername", "parent", "parentname", "guardian", "guardianname"],
}

_OK_STATUSES = {"", "completed", "complete", "success", "successful"}

_TIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d.%m.%Y %H:%M:%S",
    "%Y%m%d%H%M%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
]

# Best-effort patterns for the free-text "Details" column. Formats vary, so
# explicit account / other-party columns always win when present.
_ACC_RE = re.compile(r"\bacc(?:ount)?\.?\s*(?:no\.?|number)?\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/]*)", re.I)
_PARTY_RE = re.compile(r"((?:\+?254|0)?[17]\d{0,2}[\d\*]{4,9}\d{2,3})\s*-\s*([A-Za-z][A-Za-z .'\-]*?)\s*(?=\bacc\b|\bacc\.|$)", re.I)


def parse_amount(value: str) -> Decimal:
    cleaned = re.sub(r"(?i)kes|ksh|sh|[,\s]", "", value or "")
    if cleaned in ("", "-"):
        return Decimal("0")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


def parse_time(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_details(details: str) -> tuple[str, str, str]:
    """Pull (account_ref, payer_phone, payer_name) out of a Details string."""
    account = phone = name = ""
    m = _ACC_RE.search(details or "")
    if m:
        account = m.group(1)
    p = _PARTY_RE.search(details or "")
    if p:
        phone, name = p.group(1), p.group(2).strip()
    return account, phone, name


def _split_party(value: str) -> tuple[str, str]:
    """'2547****1234 - JANE DOE' -> ('2547****1234', 'JANE DOE')."""
    if " - " in value:
        left, right = value.split(" - ", 1)
        return left.strip(), right.strip()
    if re.fullmatch(r"[\d\*\+ ]+", value.strip()):
        return value.strip(), ""
    return "", value.strip()


def _read_rows(source: PathOrText) -> list[list[str]]:
    if isinstance(source, io.TextIOBase):
        text = source.read()
    else:
        path = Path(source)
        if path.exists():
            text = path.read_text(encoding="utf-8-sig")
        else:
            text = str(source)
    return list(csv.reader(io.StringIO(text)))


def _locate_header(rows: list[list[str]], aliases: dict[str, list[str]], required: Iterable[str]) -> tuple[int, dict[str, int]]:
    required = list(required)
    for i, row in enumerate(rows[:50]):
        keys = [_key(c) for c in row]
        mapping: dict[str, int] = {}
        for field_name, names in aliases.items():
            for name in names:
                if name in keys:
                    mapping[field_name] = keys.index(name)
                    break
        if all(any(r in mapping for r in group.split("|")) for group in required):
            return i, mapping
    raise ValueError(
        "Could not find a header row. Expected columns like "
        + ", ".join(g.replace("|", " or ") for g in required)
    )


def read_statement(source: PathOrText) -> tuple[list[Payment], int]:
    """Return (payments, skipped_count). Only completed money-in lines are kept."""
    rows = _read_rows(source)
    start, col = _locate_header(rows, STATEMENT_ALIASES, ["receipt", "paid_in|amount"])
    amount_col = col.get("paid_in", col.get("amount"))

    def get(row: list[str], name: str) -> str:
        idx = col.get(name)
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    payments: list[Payment] = []
    skipped = 0
    for row in rows[start + 1:]:
        if not any(c.strip() for c in row):
            continue
        receipt = get(row, "receipt")
        amount = parse_amount(row[amount_col] if amount_col < len(row) else "")
        status = get(row, "status").lower()
        if not receipt or amount <= 0 or status not in _OK_STATUSES:
            skipped += 1
            continue

        details = get(row, "details")
        d_acc, d_phone, d_name = parse_details(details)
        o_phone, o_name = _split_party(get(row, "other_party")) if get(row, "other_party") else ("", "")
        cb_name = " ".join(x for x in (get(row, "first_name"), get(row, "middle_name"), get(row, "last_name")) if x)

        payments.append(Payment(
            receipt=receipt,
            amount=amount,
            time=parse_time(get(row, "time")),
            account_ref=get(row, "account") or d_acc,
            payer_name=o_name or cb_name or d_name,
            payer_phone=get(row, "phone") or o_phone or d_phone,
            details=details,
        ))
    return payments, skipped


def read_expected(source: PathOrText) -> list[Expected]:
    rows = _read_rows(source)
    start, col = _locate_header(rows, EXPECTED_ALIASES, ["ref"])

    def get(row: list[str], name: str) -> str:
        idx = col.get(name)
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    def split(value: str) -> list[str]:
        return [v.strip() for v in re.split(r"[;|]", value) if v.strip()]

    out: list[Expected] = []
    for row in rows[start + 1:]:
        ref = get(row, "ref")
        if not ref:
            continue
        out.append(Expected(
            ref=ref,
            name=get(row, "name"),
            amount_due=parse_amount(get(row, "amount_due")),
            phones=split(get(row, "phones")),
            payer_names=split(get(row, "payer_names")),
        ))
    return out
