"""Data types shared across the library."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


@dataclass(frozen=True)
class Payment:
    """One money-in line from an M-Pesa statement."""

    receipt: str
    amount: Decimal
    time: Optional[datetime] = None
    account_ref: str = ""
    payer_name: str = ""
    payer_phone: str = ""
    details: str = ""


@dataclass
class Expected:
    """Someone who owes money: a student, a customer, an invoice."""

    ref: str
    name: str = ""
    amount_due: Decimal = Decimal("0")
    phones: list[str] = field(default_factory=list)
    payer_names: list[str] = field(default_factory=list)


class MatchStatus(str, Enum):
    MATCHED = "matched"
    PROBABLE = "probable"
    UNMATCHED = "unmatched"


@dataclass
class Match:
    payment: Payment
    status: MatchStatus
    expected_ref: Optional[str] = None
    reason: str = ""
    candidates: list[str] = field(default_factory=list)


@dataclass
class Balance:
    ref: str
    name: str
    amount_due: Decimal
    paid: Decimal
    pending_review: Decimal

    @property
    def remaining(self) -> Decimal:
        return self.amount_due - self.paid

    @property
    def state(self) -> str:
        if self.paid == 0:
            return "unpaid"
        if self.remaining > 0:
            return "partial"
        if self.remaining < 0:
            return "overpaid"
        return "paid"


@dataclass
class Result:
    matches: list[Match]
    balances: list[Balance]
    duplicates: list[Payment]
    skipped: int

    def by_status(self, status: MatchStatus) -> list[Match]:
        return [m for m in self.matches if m.status == status]
