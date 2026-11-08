---
doc_id: deposit-prd-001
domain: deposit
title: Deposit Product Terms 2026
version: "2026.1"
status: active
effective_from: 2026-01-01
effective_to: null
classification: internal
acl_groups:
  - role:retail_business
  - role:branch_teller
owner: Retail Banking Product
source_url: https://intranet.example.com/deposit/prd-001
doc_type: product
---

# Deposit Product Terms 2026

## 1 Product Catalogue

| Product code | Product name | Minimum amount | Tenor | Nominal rate |
| --- | --- | --- | --- | --- |
| CD-2026-001 | Standard CD 6M | USD 100,000 | 6 months | 2.80% |
| CD-2026-002 | Standard CD 12M | USD 100,000 | 12 months | 3.10% |
| CD-2026-003 | Premier CD 24M | USD 500,000 | 24 months | 3.40% |
| CD-2026-004 | Institutional CD 12M | USD 5,000,000 | 12 months | 3.55% |

Quoted rates are nominal annual rates and assume the balance stays within the same
tier for the whole tenor. Early withdrawal changes the applicable rate, as described
in `deposit-int-001`.

## 2 Eligibility and Required Documents

| Customer type | Eligibility | Required documents |
| --- | --- | --- |
| Individual resident | Age 18 or above, valid national ID | National ID, tax residency declaration |
| Individual non-resident | Valid passport, proof of address | Passport, proof of address, tax residency declaration |
| Corporate | Registered entity, KYC completed | Certificate of incorporation, board resolution, authorised signatory list |
| Trust | Registered trust deed | Trust deed, trustee identity document, authorised signatory list |

A foreign customer opening a CD must additionally complete the checks in
`deposit-ops-001`, including the FX reporting check for withdrawals above USD 500,000.

## 3 Early Redemption versus Early Withdrawal

| Feature | Early withdrawal | Early redemption |
| --- | --- | --- |
| Trigger | Depositor asks to release funds before maturity | Bank calls the CD under the redemption clause |
| Who initiates | Depositor | Issuing bank |
| Fee | 0.75% or 1.50% of the withdrawn amount | No fee |
| Interest | Recalculated at the reduced tier rate | Paid at the contractual rate up to the redemption date |
| Notice | None below USD 1,000,000; five business days above | 30 days from the bank |

In short, early redemption is bank-initiated and fee-free, whereas early withdrawal is
depositor-initiated and always attracts a fee. A depositor who asks for "redemption"
at the counter is making an early withdrawal request and must be treated accordingly.

## 4 Reference Formats

- Product codes use the pattern CD-YYYY-NNN, for example CD-2026-002.
- Document references follow Circular [YYYY] No. N or Notice YYYY-NN.
- Monetary amounts are quoted with ISO currency codes, for example USD 100,000.
- Percentages carry two decimal places, for example 3.25%.

## 5 Sales Restrictions

Institutional CDs are restricted to corporate and trust customers that have completed
KYC. Private banking clients are served under `deposit-pb-001` instead of this
catalogue. Staff must not quote a rate for a product code that does not appear in the
catalogue table above.
