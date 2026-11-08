---
doc_id: deposit-cd-001
domain: deposit
title: Large-Denomination CD Policy
version: "3.2"
status: active
effective_from: 2026-01-01
effective_to: null
supersedes: deposit-cd-001@3.1
classification: internal
acl_groups:
  - role:retail_business
  - role:branch_teller
owner: Retail Banking
source_url: https://intranet.example.com/deposit/cd-001
doc_type: policy
---

# Large-Denomination CD Policy

## 1 Purpose and Scope

This policy sets out the rules for large-denomination certificates of deposit (CDs)
issued by Retail Banking. It applies to every CD with a face value of USD 100,000 or
more, and supersedes the 2024 legacy terms (document `deposit-legacy-001@2.0`).

Branches must apply this policy together with the product terms in `deposit-prd-001`
and the interest rules in `deposit-int-001`. Where the documents disagree, this policy
prevails for early withdrawal, and the product terms prevail for rate quotation.

## 2 Definitions

| Term | Definition |
| --- | --- |
| Large-denomination CD | A certificate of deposit with a face value of USD 100,000 or more. |
| Value date | The date on which the deposit is booked and interest starts to accrue. |
| Cooling-off period | Seven calendar days after the value date during which no withdrawal is accepted. |
| Early withdrawal | A depositor-initiated release of funds before the maturity date. |
| Early redemption | A bank-initiated call of the CD before the maturity date under the redemption clause. |
| Authorized representative | A person holding a notarized power of attorney signed by the depositor. |

## 3 Early Withdrawal

Early withdrawal is available only after the cooling-off period has expired. The
branch must confirm the certificate, the identity of the person in front of it, and
the remaining balance before releasing any funds.

### 3.1 General Requirements

Early withdrawal of a large-denomination CD is subject to the rules in
Circular [2022] No. 5 and the product terms. Requests submitted before 15:00 on a
business day are processed the same day; later requests are processed on the next
business day.

The depositor must provide:

- Original CD certificate or account statement;
- Depositor's identity document (passport or national ID);
- Completed withdrawal application form (Form CD-WD-01);
- Tax residency declaration if the account was opened after 2024-01-01.

### 3.2 Authorized Representative

If an authorized representative handles the withdrawal, the following documents are
required:

- Original and copy of the representative's identity document;
- Power of attorney signed by the depositor and notarized;
- Original CD certificate or account statement;
- Withdrawal application form (Form CD-WD-01) signed by both parties.

The power of attorney must name the specific CD and must remain valid for no more
than 90 days from the date of notarization.

### 3.3 Partial Withdrawal and Minimum Balance

A partial early withdrawal may not reduce the remaining balance below USD 100,000.
Where the requested amount would breach that floor, the branch must process a full
early withdrawal instead.

For CDs with a face value above USD 1,000,000, the depositor must give five business
days' written notice before the withdrawal date. The notice period may be waived by
the branch manager where the funds are required for a documented emergency.

### 3.4 Fees and Charges

| Scenario | Fee | Notes |
| --- | --- | --- |
| Early withdrawal within 3 months of the value date | 1.50% of the withdrawn amount | Minimum USD 500 |
| Early withdrawal after 3 months and before maturity | 0.75% of the withdrawn amount | Minimum USD 250 |
| Partial withdrawal | USD 50 flat | Waived for private banking clients |
| Reissuance of certificate | USD 25 | Per certificate |
| Statement of interest paid | USD 10 | Per statement |

Fees are deducted from the withdrawal proceeds. A branch manager may waive up to 50%
of the fee; any larger waiver requires the Head of Retail Banking.

## 4 Transfer and Pledge

A CD may be transferred to another person with a notarized transfer form signed by
both parties and branch manager approval. The transferee must pass the standard KYC
checks before the transfer is booked.

A CD may be pledged as collateral for a retail loan. A pledged CD cannot be
withdrawn or transferred until the pledge is released by the lending unit.

## 5 Interest on Early Withdrawal

Interest is recalculated at the reduced tier rate described in `deposit-int-001`.
The applicable rate depends on how long the funds were held:

- Withdrawal after 12 months: the six-month tier rate applies pro rata.
- Withdrawal between 3 and 12 months: half of the applicable tier rate applies.
- Withdrawal within 3 months: no interest is paid and the 1.50% fee applies.

Interest already credited in a previous quarter is not clawed back; the recalculation
applies only to the current interest period.
