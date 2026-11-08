---
doc_id: deposit-ops-001
domain: deposit
title: Early Withdrawal Operations Procedure
version: "1.4"
status: active
effective_from: 2026-02-01
effective_to: null
classification: internal
acl_groups:
  - role:branch_teller
  - role:retail_business
owner: Retail Banking Operations
source_url: https://intranet.example.com/deposit/ops-001
doc_type: policy
---

# Early Withdrawal Operations Procedure

## 1 Processing Steps

The teller processes an early withdrawal in the following order. Steps must not be
reordered, because the fee calculation depends on the verified certificate data.

1. Verify the CD certificate and the depositor's identity document.
2. Confirm that the seven-day cooling-off period has expired.
3. Check that the remaining balance stays at or above USD 100,000.
4. Calculate the early withdrawal fee using the fee schedule in `deposit-cd-001`.
5. Collect the signed withdrawal application form (Form CD-WD-01).
6. Scan every document into the case management system within 2 hours.
7. Obtain supervisor approval for amounts above USD 1,000,000.
8. Release the funds to the original funding account only.

## 2 Document Checklist by Scenario

| Scenario | Required documents | Additional approval |
| --- | --- | --- |
| Standard early withdrawal | CD certificate, identity document, Form CD-WD-01 | None |
| Authorized representative | CD certificate, representative identity document and copy, notarized power of attorney, Form CD-WD-01 | Branch manager |
| Foreign customer | CD certificate, passport, proof of address, tax residency declaration | Compliance review |
| Partial withdrawal | CD certificate, identity document, Form CD-WD-01 | None |
| Estate claim | Grant of probate, beneficiary identity document, Form CD-WD-01 | Legal review |

## 3 Foreign Customer Requirements

A foreign customer must provide a valid passport, proof of residential address dated
within three months, and a tax residency declaration. Where the customer is a
non-resident and the withdrawal exceeds USD 500,000, the branch must complete an FX
reporting check under Notice 2023-12 before releasing funds.

> Compliance note: this section is a summary only. AML/KYC and FX reporting rules are
> owned by the respective policies and must be applied in their original form.

## 4 Common Rejection Reasons

The most frequent reasons an early withdrawal request is rejected at the counter are:

- A missing or expired identity document;
- A request made before the seven-day cooling-off period ends;
- A partial withdrawal that would leave less than USD 100,000;
- A power of attorney that does not name the specific CD;
- An application form signed by one party only;
- A certificate that is currently pledged as loan collateral.

## 5 Escalation Matrix

| Amount or scenario | Approver | Turnaround |
| --- | --- | --- |
| Up to USD 500,000 | Branch teller | Same day |
| USD 500,000 to USD 1,000,000 | Branch supervisor | Same day |
| Above USD 1,000,000 | Branch manager | 1 business day |
| Fee waiver up to 50% | Branch manager | 2 business days |
| Fee waiver above 50% | Head of Retail Banking | 5 business days |
| Estate claim | Legal and Compliance | 10 business days |

## 6 Record Keeping

The scanned case file must be retained for seven years. The case management system
records the teller, the approver, the fee charged and the interest adjustment. Any
manual override requires a written justification attached to the case file.
