---
doc_id: deposit-int-001
domain: deposit
title: Deposit Interest and Tier Rules
version: "2.1"
status: active
effective_from: 2026-01-15
effective_to: null
classification: internal
acl_groups:
  - role:retail_business
  - role:branch_teller
owner: Retail Banking Treasury
source_url: https://intranet.example.com/deposit/int-001
doc_type: policy
---

# Deposit Interest and Tier Rules

## 1 Accrual Basis

Interest accrues daily on an actual/365 basis and is rounded to two decimal places.
Payment frequency depends on the product: at maturity for standard CDs, quarterly for
premier CDs and monthly for institutional CDs.

The value date, not the booking date, determines when accrual starts. A back-dated
booking therefore accrues from the value date and must be reported to Treasury.

## 2 Tiered Interest Rates

Tiered interest is applied on the full balance, not on the marginal amount above each
threshold. The tier is determined by the face value at the value date and does not
change if the balance later falls after a partial withdrawal.

| Balance tier | 6-month rate | 12-month rate | 24-month rate |
| --- | --- | --- | --- |
| USD 100,000 to USD 499,999 | 2.80% | 3.10% | 3.25% |
| USD 500,000 to USD 999,999 | 2.95% | 3.25% | 3.40% |
| USD 1,000,000 to USD 4,999,999 | 3.10% | 3.40% | 3.55% |
| USD 5,000,000 and above | 3.25% | 3.55% | 3.70% |

## 3 Early Withdrawal Interest Rules

If a CD is withdrawn before maturity, interest is recalculated using the reduced rules
below. The reduction replaces the contractual rate for the whole holding period, not
only for the days after the request.

- Withdrawal after 12 months: the six-month tier rate applies pro rata to the days held.
- Withdrawal between 3 and 12 months: half of the applicable tier rate applies.
- Withdrawal within 3 months: no interest is paid, and the early withdrawal fee applies.

## 4 Rate Change Notice

Rate changes take effect on the first day of the following month. Under Notice 2023-12
the bank must notify affected depositors at least 30 days before a rate decrease takes
effect, and must publish the new rates on the intranet rate page on the same day.

Notice 2023-12 does not require notice for rate increases. An existing CD keeps its
contractual rate until maturity regardless of later rate changes.

## 5 Worked Examples

Example 1 — full early withdrawal of USD 100,000 after 14 months (425 days). The
holding period is longer than 12 months, so the six-month tier rate of 2.80% applies
pro rata: USD 100,000 × 2.80% × 425 / 365 = USD 3,260.27. The early withdrawal fee is
0.75% of the withdrawn amount, that is USD 750.00, so the depositor receives
USD 102,510.27.

Example 2 — partial withdrawal of USD 200,000 from a USD 1,000,000 CD after 8 months
(240 days). The holding period is between 3 and 12 months, so half of the applicable
tier rate applies. The 12-month tier rate for this balance is 3.40%, so the recalculated
rate is 1.70%: USD 200,000 × 1.70% × 240 / 365 = USD 223.56. A flat partial withdrawal
fee of USD 50.00 applies.

Example 3 — withdrawal of USD 300,000 within 3 months. No interest is paid. The fee is
1.50% of the withdrawn amount, that is USD 4,500.00.

## 6 Rate Publication and Exceptions

Rates are published on the intranet rate page and in the branch rate binder. The
following exceptions must be escalated to Treasury before quotation:

- Any request for a rate different from the published tier;
- Any CD above USD 20 million, which requires a treasury desk quote;
- Any back-dated booking older than five business days.
