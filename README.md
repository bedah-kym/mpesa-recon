# mpesa-recon

Match M-Pesa payments to the people who owe you money.

You have a paybill statement and a list of who owes what: students and fee
balances, customers and invoices. `mpesa-recon` tells you which payment belongs
to whom, which ones need a human to check, and what each person still owes.

Plain statement converters already exist. This library does the step after
conversion: matching payments to people when the account number is missing,
mistyped or vague.

## Install

```bash
pip install git+https://github.com/bedah-kym/mpesa-recon
```

Python 3.10 or newer. No other dependencies.

## Use it from the command line

```bash
mpesa-recon statement.csv expected.csv --out results/
```

```
matched        3 payments   KES      44,000.00
probable       3 payments   KES      53,000.00
unmatched      1 payments   KES       2,500.00
duplicates     1 ignored (same receipt twice)
skipped        2 lines (money out, failed, or blank)

accounts: 1 paid, 2 partial, 2 unpaid
```

It writes four files:

| File | What's in it |
|---|---|
| `payments.csv` | Every payment, its status, and why |
| `review.csv` | Only the payments a person should check, with a suggested match |
| `balances.csv` | Each person's amount due, paid, remaining, and pending review |
| `duplicates.csv` | Receipts that appeared more than once and were counted once |

Try it on the synthetic sample in `tests/data/`.

## Use it from Python

```python
from mpesa_recon import read_statement, read_expected, reconcile

payments, skipped = read_statement("statement.csv")
expected = read_expected("expected.csv")
result = reconcile(payments, expected, skipped=skipped)

for m in result.matches:
    print(m.payment.receipt, m.status.value, m.expected_ref, m.reason)
```

## How matching works

Each payment goes through these checks in order. The first one that gives a
single answer wins.

| Check | Example | Result |
|---|---|---|
| Account reference equals a ref | `4521` → 4521 | matched |
| A ref appears inside the reference | `ADM 4537` → 4537 | matched |
| Full phone number is known | `254712000615` → parent of 4615 | matched |
| Reference is one typo from a single ref | `4512` → 4521 (swapped) | probable |
| Masked phone fits and payer name agrees | `2547******700 MARY WANJIRU` → 4700 | probable |
| Payer name agrees with one person | `GRACE ACHIENG` → A1 | probable |
| Nothing fits, or several things fit | `SCHOOL` | unmatched |

**Probable** means "probably right, please confirm." The library never guesses
silently. When a typo is close to two refs, the payment goes to review with
both candidates listed.

Only *matched* payments count towards `paid`. *Probable* payments are shown
separately as `pending_review` until someone confirms them.

## Input files

### Statement

A CSV with a header row. Extra lines above the header (organisation name,
date range) are skipped. Columns are found by name, so these all work:

- receipt: `Receipt No.`, `TransID`, `Transaction ID`
- amount in: `Paid In`, `TransAmount`, `Credit`, `Amount`
- reference: `A/C No.`, `BillRefNumber`, `Account Number`
- payer: `Other Party Info`, `MSISDN`, `FirstName` / `LastName`
- also used when present: `Completion Time`, `Details`, `Transaction Status`

Money out, failed transactions and blank lines are skipped. If there is no
reference column, the library tries to read it from the `Details` text.

> The column names are based on common export formats and Daraja C2B fields.
> They have not yet been tested against every real statement layout. If yours
> doesn't parse, please open an issue with the header row (no personal data).

### Expected list

A CSV with at least a reference column. Other columns are optional.

| Column (any of these names) | Meaning |
|---|---|
| `ref`, `admission`, `adm no`, `invoice`, `account` | The reference people should type |
| `name`, `student name`, `customer` | Who it is |
| `amount due`, `fee balance`, `balance` | What they owe |
| `phone`, `parent phone` | Known payer phones, separated by `;` |
| `payer`, `parent`, `guardian` | Known payer names, separated by `;` |

## PDF statements

Not yet. For now, convert the PDF to CSV with a tool such as
[mpesa2csv](https://mpesa2csv.com/), then run `mpesa-recon` on the CSV. Native
PDF support is planned once it can be tested on real (redacted) statements.

## Privacy

Everything runs locally. The library makes no network calls. Statements hold
other people's phone numbers and names, so treat the output files with the
same care as the statement.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## License

MIT
