---
doc_id: deposit-rate-2027-001
domain: deposit
title: CD Rate Schedule 2027
version: "1.0"
status: scheduled
effective_from: 2027-01-01
effective_to: null
classification: internal
acl_groups:
  - role:retail_business
  - role:branch_teller
owner: Retail Banking Treasury
source_url: https://intranet.example.com/deposit/rate-2027
doc_type: notice
---

# CD Rate Schedule 2027

## 1 Effective Date

This schedule takes effect on 2027-01-01 and applies to CDs booked on or after that
date. Bookings made in 2026 keep the rates in `deposit-int-001` until maturity.

## 2 Published Rates

Rates below are indicative and subject to Treasury confirmation before the schedule is
activated. They are published 30 days ahead of the effective date under Notice 2023-12.

| Balance tier | 6-month rate | 12-month rate |
| --- | --- | --- |
| USD 100,000 to USD 499,999 | 2.60% | 2.90% |
| USD 500,000 to USD 999,999 | 2.75% | 3.05% |
| USD 1,000,000 and above | 2.90% | 3.20% |

## 3 Transition Rules

A CD booked in 2026 that matures in 2027 may be renewed at the 2027 rate on the
maturity date. The renewal is a new booking, so the depositor must be given the 2027
terms and must sign a new application form.

## 4 Withdrawal Rules for 2027 Bookings

The early withdrawal fee schedule and the interest recalculation rules in
`deposit-cd-001` and `deposit-int-001` continue to apply to 2027 bookings. No separate
fee schedule is introduced by this notice.
