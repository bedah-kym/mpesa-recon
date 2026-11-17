"""Match statement payments to the people who owe money.

Each payment goes through these checks in order, and the first one that
gives a single answer wins:

1. Account reference equals an expected ref         -> matched
2. An expected ref appears inside the reference     -> matched
3. Full phone number equals a known phone           -> matched
4. Reference is one edit away from exactly one ref  -> probable
5. Masked phone fits a known phone and name agrees  -> probable
6. Payer name agrees with exactly one person        -> probable
7. Nothing, or more than one candidate              -> unmatched

"Probable" means a human should confirm it. Nothing is guessed silently.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Iterable, Optional

from .models import Balance, Expected, Match, MatchStatus, Payment, Result


def normalize_ref(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _ref_variants(value: str) -> set[str]:
    n = normalize_ref(value)
    return {n, n.lstrip("0")} - {""}


def normalize_phone(value: str) -> str:
    v = re.sub(r"[^\d\*]", "", value or "")
    if v.startswith("0") and len(v) == 10:
        v = "254" + v[1:]
    elif len(v) == 9 and v[0] in "17":
        v = "254" + v
    return v


def edit_distance(a: str, b: str) -> int:
    """Damerau-Levenshtein (optimal string alignment) distance."""
    rows, cols = len(a) + 1, len(b) + 1
    d = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        d[i][0] = i
    for j in range(cols):
        d[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def _describe_edit(typed: str, actual: str) -> str:
    if len(typed) == len(actual):
        diffs = [i for i, (x, y) in enumerate(zip(typed, actual)) if x != y]
        if len(diffs) == 2 and typed[diffs[0]] == actual[diffs[1]] and typed[diffs[1]] == actual[diffs[0]]:
            return "two characters swapped"
        return "one character different"
    return "one character missing" if len(typed) < len(actual) else "one extra character"


def phone_fits(masked: str, known: str) -> bool:
    """True when a statement phone (possibly masked with *) fits a known phone."""
    m, k = normalize_phone(masked), normalize_phone(known)
    if not m or not k:
        return False
    if "*" not in m:
        return m == k
    prefix = m[: m.index("*")]
    suffix = m[m.rindex("*") + 1:]
    if len(suffix) < 3:
        return False
    return k.startswith(prefix) and k.endswith(suffix)


def _name_tokens(value: str) -> set[str]:
    return {t for t in re.split(r"[^a-z]+", (value or "").lower()) if len(t) > 1}


def names_agree(a: str, b: str) -> bool:
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    shared = ta & tb
    return len(shared) >= 2 or (len(shared) == 1 and (len(ta) == 1 or len(tb) == 1))


class Reconciler:
    def __init__(self, expected: Iterable[Expected], max_edit: int = 1, min_fuzzy_len: int = 3):
        self.expected = list(expected)
        self.max_edit = max_edit
        self.min_fuzzy_len = min_fuzzy_len
        self._by_variant: dict[str, list[Expected]] = {}
        for e in self.expected:
            for v in _ref_variants(e.ref):
                self._by_variant.setdefault(v, []).append(e)

    # -- individual checks -------------------------------------------------

    def _exact(self, p: Payment) -> list[Expected]:
        hits: dict[str, Expected] = {}
        for v in _ref_variants(p.account_ref):
            for e in self._by_variant.get(v, []):
                hits[e.ref] = e
        return list(hits.values())

    def _embedded(self, p: Payment) -> list[Expected]:
        tokens = {t for t in re.split(r"[^A-Z0-9]+", (p.account_ref or "").upper()) if t}
        hits: dict[str, Expected] = {}
        for t in tokens:
            for v in {t, t.lstrip("0")} - {""}:
                for e in self._by_variant.get(v, []):
                    hits[e.ref] = e
        return list(hits.values())

    def _full_phone(self, p: Payment) -> list[Expected]:
        if not p.payer_phone or "*" in p.payer_phone:
            return []
        return [e for e in self.expected if any(phone_fits(p.payer_phone, k) for k in e.phones)]

    def _fuzzy(self, p: Payment) -> list[tuple[Expected, str]]:
        typed = normalize_ref(p.account_ref)
        if len(typed) < self.min_fuzzy_len:
            return []
        out = []
        for e in self.expected:
            actual = normalize_ref(e.ref)
            if len(actual) >= self.min_fuzzy_len and edit_distance(typed, actual) <= self.max_edit:
                out.append((e, _describe_edit(typed, actual)))
        return out

    def _names_for(self, e: Expected) -> list[str]:
        return [n for n in [e.name, *e.payer_names] if n]

    def _masked_phone_and_name(self, p: Payment) -> list[Expected]:
        if not p.payer_phone or "*" not in p.payer_phone:
            return []
        return [
            e for e in self.expected
            if any(phone_fits(p.payer_phone, k) for k in e.phones)
            and any(names_agree(p.payer_name, n) for n in self._names_for(e))
        ]

    def _name_only(self, p: Payment) -> list[Expected]:
        if not p.payer_name:
            return []
        return [e for e in self.expected if any(names_agree(p.payer_name, n) for n in self._names_for(e))]

    # -- orchestration -----------------------------------------------------

    def match_one(self, p: Payment) -> Match:
        hits = self._exact(p)
        if len(hits) == 1:
            return Match(p, MatchStatus.MATCHED, hits[0].ref, "account reference matches")
        if len(hits) > 1:
            return self._ambiguous(p, hits, "reference matches more than one record")

        hits = self._embedded(p)
        if len(hits) == 1:
            return Match(p, MatchStatus.MATCHED, hits[0].ref, f"reference found in '{p.account_ref}'")
        if len(hits) > 1:
            return self._ambiguous(p, hits, f"'{p.account_ref}' contains more than one reference")

        hits = self._full_phone(p)
        if len(hits) == 1:
            return Match(p, MatchStatus.MATCHED, hits[0].ref, "payer phone matches")

        fuzzy = self._fuzzy(p)
        if len(fuzzy) == 1:
            e, how = fuzzy[0]
            return Match(p, MatchStatus.PROBABLE, e.ref, f"typed '{p.account_ref}', closest is '{e.ref}' ({how})")
        if len(fuzzy) > 1:
            return self._ambiguous(p, [e for e, _ in fuzzy], f"'{p.account_ref}' is close to several references")

        hits = self._masked_phone_and_name(p)
        if len(hits) == 1:
            return Match(p, MatchStatus.PROBABLE, hits[0].ref, "masked phone and payer name both fit")

        hits = self._name_only(p)
        if len(hits) == 1:
            return Match(p, MatchStatus.PROBABLE, hits[0].ref, f"payer name '{p.payer_name}' fits")
        if len(hits) > 1:
            return self._ambiguous(p, hits, f"payer name '{p.payer_name}' fits several records")

        return Match(p, MatchStatus.UNMATCHED, None, "no reference, phone or name match")

    @staticmethod
    def _ambiguous(p: Payment, hits: list[Expected], reason: str) -> Match:
        return Match(p, MatchStatus.UNMATCHED, None, reason, candidates=sorted(e.ref for e in hits))

    def run(self, payments: Iterable[Payment], skipped: int = 0) -> Result:
        seen: set[str] = set()
        unique: list[Payment] = []
        duplicates: list[Payment] = []
        for p in payments:
            if p.receipt in seen:
                duplicates.append(p)
                continue
            seen.add(p.receipt)
            unique.append(p)

        matches = [self.match_one(p) for p in unique]
        return Result(matches=matches, balances=self._balances(matches), duplicates=duplicates, skipped=skipped)

    def _balances(self, matches: list[Match]) -> list[Balance]:
        paid: dict[str, Decimal] = {}
        review: dict[str, Decimal] = {}
        for m in matches:
            if m.expected_ref is None:
                continue
            bucket = paid if m.status == MatchStatus.MATCHED else review
            bucket[m.expected_ref] = bucket.get(m.expected_ref, Decimal("0")) + m.payment.amount
        return [
            Balance(
                ref=e.ref,
                name=e.name,
                amount_due=e.amount_due,
                paid=paid.get(e.ref, Decimal("0")),
                pending_review=review.get(e.ref, Decimal("0")),
            )
            for e in self.expected
        ]


def reconcile(payments: Iterable[Payment], expected: Iterable[Expected], skipped: int = 0,
              max_edit: int = 1) -> Result:
    return Reconciler(expected, max_edit=max_edit).run(payments, skipped=skipped)


def find(result: Result, receipt: str) -> Optional[Match]:
    return next((m for m in result.matches if m.payment.receipt == receipt), None)
