from decimal import Decimal
from pathlib import Path

import pytest

from mpesa_recon import Expected, MatchStatus, Payment, read_expected, read_statement, reconcile
from mpesa_recon.cli import main
from mpesa_recon.matcher import edit_distance, find, names_agree, normalize_phone, phone_fits
from mpesa_recon.parsers import parse_amount, parse_details

DATA = Path(__file__).parent / "data"


@pytest.fixture
def result():
    payments, skipped = read_statement(DATA / "statement.csv")
    expected = read_expected(DATA / "expected.csv")
    return reconcile(payments, expected, skipped=skipped)


# -- end to end on the sample statement -----------------------------------

@pytest.mark.parametrize("receipt, status, ref", [
    ("SKA1000001", MatchStatus.MATCHED, "4521"),    # exact reference
    ("SKA1000002", MatchStatus.MATCHED, "4537"),    # "ADM 4537"
    ("SKA1000003", MatchStatus.PROBABLE, "4521"),   # typed 4512, digits swapped
    ("SKA1000004", MatchStatus.PROBABLE, "4608"),   # typed 460, digit missing
    ("SKA1000005", MatchStatus.MATCHED, "4615"),    # reference "FEES", phone matches
    ("SKA1000006", MatchStatus.PROBABLE, "4700"),   # masked phone + parent name
    ("SKA1000007", MatchStatus.UNMATCHED, None),    # nothing fits
])
def test_sample_statement(result, receipt, status, ref):
    m = find(result, receipt)
    assert m is not None
    assert m.status == status
    assert m.expected_ref == ref


def test_duplicates_and_skips(result):
    assert [p.receipt for p in result.duplicates] == ["SKA1000001"]
    assert result.skipped == 2  # one withdrawal, one failed payment
    assert len(result.matches) == 7


def test_balances(result):
    by_ref = {b.ref: b for b in result.balances}
    assert by_ref["4521"].state == "paid"
    assert by_ref["4521"].pending_review == Decimal("5000.00")
    assert by_ref["4537"].state == "partial"
    assert by_ref["4537"].remaining == Decimal("15000.00")
    assert by_ref["4608"].state == "unpaid"
    assert by_ref["4608"].pending_review == Decimal("18000.00")


def test_cli_writes_reports(tmp_path, capsys):
    code = main([str(DATA / "statement.csv"), str(DATA / "expected.csv"), "--out", str(tmp_path)])
    assert code == 0
    assert {p.name for p in tmp_path.iterdir()} == {"payments.csv", "review.csv", "balances.csv", "duplicates.csv"}
    out = capsys.readouterr().out
    assert "matched" in out and "probable" in out


def test_cli_reports_bad_file(tmp_path, capsys):
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n")
    assert main([str(bad), str(DATA / "expected.csv")]) == 2
    assert "header row" in capsys.readouterr().err


# -- matcher rules ----------------------------------------------------------

def _pay(receipt="R1", ref="", name="", phone="", amount="100"):
    return Payment(receipt=receipt, amount=Decimal(amount), account_ref=ref, payer_name=name, payer_phone=phone)


def test_ambiguous_fuzzy_goes_to_review():
    expected = [Expected("4520"), Expected("4521")]
    m = reconcile([_pay(ref="4522")], expected).matches[0]
    assert m.status == MatchStatus.UNMATCHED
    assert m.candidates == ["4520", "4521"]


def test_leading_zeros_and_punctuation():
    m = reconcile([_pay(ref="00-45 21")], [Expected("4521")]).matches[0]
    assert m.status == MatchStatus.MATCHED


def test_fuzzy_can_be_turned_off():
    m = reconcile([_pay(ref="4512")], [Expected("4521")], max_edit=0).matches[0]
    assert m.status == MatchStatus.UNMATCHED


def test_short_refs_are_not_fuzzy_matched():
    m = reconcile([_pay(ref="12")], [Expected("13")]).matches[0]
    assert m.status == MatchStatus.UNMATCHED


def test_name_only_is_probable():
    expected = [Expected("A1", name="Grace Achieng"), Expected("A2", name="Tom Mboya")]
    m = reconcile([_pay(name="GRACE ACHIENG")], expected).matches[0]
    assert m.status == MatchStatus.PROBABLE
    assert m.expected_ref == "A1"


def test_exact_ref_beats_name():
    expected = [Expected("A1", name="Grace Achieng"), Expected("A2")]
    m = reconcile([_pay(ref="A2", name="GRACE ACHIENG")], expected).matches[0]
    assert m.expected_ref == "A2"
    assert m.status == MatchStatus.MATCHED


# -- helpers ----------------------------------------------------------------

def test_edit_distance():
    assert edit_distance("4521", "4521") == 0
    assert edit_distance("4512", "4521") == 1   # transposition
    assert edit_distance("460", "4608") == 1    # missing
    assert edit_distance("4529", "4521") == 1   # substitution
    assert edit_distance("1234", "4321") > 1


def test_phones():
    assert normalize_phone("0712 345 678") == "254712345678"
    assert normalize_phone("+254712345678") == "254712345678"
    assert phone_fits("2547****5678", "0712345678")
    assert not phone_fits("2547****5679", "0712345678")
    assert not phone_fits("2547******78", "0712345678")  # too little visible


def test_names():
    assert names_agree("MARY WANJIRU", "Mary Wanjiru")
    assert not names_agree("MARY WANJIRU", "Daniel Wanjiru")
    assert not names_agree("", "Mary")


def test_parse_amount():
    assert parse_amount("25,000.00") == Decimal("25000.00")
    assert parse_amount("KES 1,500") == Decimal("1500")
    assert parse_amount("") == Decimal("0")


def test_parse_details():
    acc, phone, name = parse_details("Pay Bill from 2547****1521 - JANE OTIENO Acc. 4521")
    assert (acc, phone, name) == ("4521", "2547****1521", "JANE OTIENO")


def test_daraja_style_columns():
    csv_text = (
        "TransID,TransTime,TransAmount,BillRefNumber,MSISDN,FirstName,LastName\n"
        "QWE123,20261201081402,1500.00,4521,254722111521,JANE,OTIENO\n"
    )
    payments, skipped = read_statement(csv_text)
    assert skipped == 0
    p = payments[0]
    assert p.account_ref == "4521" and p.payer_name == "JANE OTIENO" and p.amount == Decimal("1500.00")
    assert p.time is not None and p.time.year == 2026
