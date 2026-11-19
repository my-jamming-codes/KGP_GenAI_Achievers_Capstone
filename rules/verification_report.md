# Rule verification report

Read on 12 November 2026 from rbi.org.in. Records are in `rules/rules.yaml`.

**Result: 30 records written, 29 verified, 1 unverified.** The only unverified record is the bank guarantor disclosure rule, which was not found.

## Update after team review, 12 November 2026

- The team saved the current PDFs of the two bank documents and HFC Annex V into `data/raw/manual/` (since moved to `data/raw/pdf/`; the 154MD copy read here is kept as `154MD_verified_2026-11-12.pdf`). Paragraphs 111 and 113 (Credit Facilities, updated 1 October 2026) and paragraph 55 with Table 12 (Capital Adequacy, updated 7 October 2026) match the versions read earlier, so BANK-LTV-CAP, BANK-LTV-COMPUTE and BANK-RW-CURRENT are now verified.
- Annex V was read and HFC-VALUATION-ANNEX-V is verified. It adds real limits: two valuation reports for loans of ₹50 lakh to below ₹75 lakh, two independent reports from ₹75 lakh, and the lower valuation is used.
- Bank guarantor disclosure stays unverified. The checker reports that check as `unknown` for banks.
- Corpus: the 500 document floor is assumed to apply, so the corpus is widened with RBI notifications beyond the 383 Master Direction pages.
- Gold set frozen after this review: 80 queries, 20 development and 60 test, 25 application based.

The table rows marked unverified below were written before this update and are kept as the record of the first pass.

## Findings that change the spec

| Spec assumption | What the RBI text says |
| --- | --- |
| Bank rules are in the housing finance master circular (taxguru link) | Bank housing rules now sit in the Commercial Banks – Credit Facilities Directions, 2025, Chapter VIII. Paragraph 451 repeals the earlier credit facility instructions. I did not read the repeal list itself, so the circular's own entry is unconfirmed |
| HFC and bank LTV caps may differ | They are the same numbers in both rulebooks: 90% up to ₹30 lakh, 80% above ₹30 lakh to ₹75 lakh, 75% above ₹75 lakh. The paragraph cited differs (HFC 58, bank 111) |
| The 64 lakh on 80 lakh example | LTV is 80%, the loan is in the ₹30 to ₹75 lakh band, the cap is 80%, so it is met for an HFC, and for a bank once item 1 is cleared |
| Blog: risk weights from 20% to 40%, 60% for a third loan | Those figures match the bank Directions effective 1 April 2027 (Tables 9 and 10), not the rules in force today. Today both lender types use 35%, 50% and 75% bands |
| Stamp duty relaxation up to ₹10 lakh is an HFC rule | Both rulebooks have it (HFC 58 Note 1, bank 113) |
| Guarantor rules exist for both | Found for HFCs only |
| Prepayment differs by rate and borrower type | HFCs carry two rules: paragraph 163 (housing loans, floating from any source, fixed from own sources) and the NBFC rule applied by paragraph 164. Banks have paragraphs 352 and 353 |

## Themes, one by one

| Theme | HFC | Bank |
| --- | --- | --- |
| LTV cap | Verified. HFC Directions para 58 | Text read, **unverified** pending item 1. Credit Facilities para 111 |
| LTV calculation and stamp duty | Verified. Para 58 Note (1) | Text read, **unverified** pending item 1. Para 113 |
| Risk weight by LTV | Verified. Para 22, item (3)(b) | Current: text read, **unverified** pending item 1 (Capital Adequacy para 55). From 1 April 2027: verified (Standardised Approach Directions para 16(5)(ii)) |
| Valuation of property | Para 215 verified (Board approved policy). Annex V detail **unverified**: PDF only | Verified. Credit Risk Management para 70. No rule on when a fresh valuation is needed was found |
| Prepayment charges | Verified. Para 163, and NBFC Responsible Business Conduct paras 33 and 34(1) | Verified. Responsible Business Conduct paras 352 and 353 |
| Interest rate reset communication | Verified. NBFC Responsible Business Conduct para 31, applied by HFC para 176(3) | Verified. Responsible Business Conduct para 349 |
| Key facts statement and borrower disclosure | Verified. NBFC para 29 (via HFC para 175), HFC paras 131 and 165 | Verified. Paras 348 and 327 |
| Guarantor disclosure | Verified. Paras 158 and 159 | **Unverified**: not found |
| KYC and customer due diligence | Verified. NBFC KYC para 23 | Verified. Commercial Banks KYC para 23 |
| Related party lending | Verified. NBFC Credit Risk Management paras 13G to 13J | Verified. Credit Risk Management paras 14, 42I to 42L |
| Grievance redressal | Verified. Paras 142 to 144 | Para 346 verified, but it sets up the mechanism only and does not list what the borrower is given |

## Unverified records

| Record | Why | How to clear it |
| --- | --- | --- |
| BANK-LTV-CAP, BANK-LTV-COMPUTE | Read in the 15 July 2026 version; the 1 October 2026 version is PDF only | Check paras 111 and 113 in [154MD.PDF](https://rbidocs.rbi.org.in/rdocs/notification/PDFs/154MD.PDF) |
| BANK-RW-CURRENT | Read in the 1 July 2026 version; the 7 October 2026 version is PDF only | Check para 55, Table 12 in [151MD.PDF](https://rbidocs.rbi.org.in/rdocs/notification/PDFs/151MD.PDF) |
| HFC-VALUATION-ANNEX-V | Annex V is PDF only; not read at all, no values filled | Save [Annex V](https://rbidocs.rbi.org.in/rdocs/content/pdfs/365MD06022026_AN5.pdf) into `data/raw/manual/` |
| BANK-GUARANTOR-DISCLOSURE | No paragraph found in the four bank documents read; no values filled | Name the document if you know it, or accept "unknown for banks" |

For item 1, the evidence that the paragraphs are probably unchanged is indirect: neither paragraph carries an amendment footnote in the version read, and the 1 October 2026 footnotes in the other bank documents all trace to the Governance Amendment Directions of 14 July 2026. That is not a read of the current text, so the records stay unverified.

## Amendments in force

| Rule | Change | Date |
| --- | --- | --- |
| Bank risk weights | Standardised Approach Directions replace the size and LTV bands with LTV only bands, and add a separate table for a third housing loan | 1 April 2027 |
| Prepayment, both lender types | New rule for loans sanctioned or renewed from this date; older floating rate loans to individuals are covered by a separate paragraph | 1 January 2026 |
| Related party lending, both lender types | Materiality thresholds and recusal inserted | 1 April 2026 |
| Key facts statement | Applies to loans sanctioned from this date | 1 October 2024 |
| Rate reset communication | Applies to existing and new loans from this date | 1 January 2024 |

I did not find a rule saying whether the bank LTV caps in paragraph 111 change on 1 April 2027. The 2027 Directions give risk weights up to an LTV of 90% with no loan size bands, so this is worth a question to the TA.

## Spot check

Five records were picked at random (seed 20261112) and their source paragraphs re-read: BANK-RW-2027, BANK-RELATED-PARTY-THRESHOLD, HFC-BORROWER-DISCLOSURE, HFC-RATE-RESET, HFC-GRIEVANCE. Quoted wording and numbers matched in all five.

Separately, `rules/check_quotes.py` checks every quote in all 30 records against the parsed source, ignoring only whitespace. All matched.

## Documents read

All from rbi.org.in, saved in `data/raw` and parsed to `data/parsed`.

| Short name | Document | Version read |
| --- | --- | --- |
| HFC-MD | Housing Finance Companies Directions, 2025 | 1 October 2026 (current) |
| NBFC-RBC | NBFC Responsible Business Conduct Directions, 2025 | 1 July 2026 (current) |
| NBFC-KYC | NBFC Know Your Customer Directions, 2025 | 29 December 2025 (current) |
| NBFC-CRM | NBFC Credit Risk Management Directions, 2025 | 1 July 2026 (current) |
| CB-CF | Commercial Banks Credit Facilities Directions, 2025 | 15 July 2026 (previous) |
| CB-CAP | Commercial Banks Capital Adequacy Directions, 2025 | 1 July 2026 (previous) |
| CB-SA-2027 | Commercial Banks Capital Charge for Credit Risk, Standardised Approach Directions, 2026 | Effective 1 April 2027 |
| CB-RBC | Commercial Banks Responsible Business Conduct Directions, 2025 | 1 October 2026 (current) |
| CB-KYC | Commercial Banks Know Your Customer Directions, 2025 | 1 October 2026 (current) |
| CB-CRM | Commercial Banks Credit Risk Management Directions, 2025 | 1 October 2026 (current) |

"Bank" here means a commercial bank as these Directions define it, which excludes small finance banks, payments banks and local area banks. Regional rural banks have their own Directions, which I did not read.
