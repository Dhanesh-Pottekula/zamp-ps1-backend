# Demo samples: full coverage, no duplicates

Date: 2026-11-13. Status: approved and built (renumbered by stage; bad sums now block).

As built, two merges were reversed after checking the code paths: INV-010 (taxable value derived from the rate,
no tax rows) is a different branch from INV-014 (total minus the VAT shown), and INV-029 is the only sample where a
line quantity exceeds what is left while the money fits, on a split invoice. So 8 samples were removed, not 10, and
the final set is **46** (30 kept + 16 new): `HEADER_LEVEL_ONLY` turned out to be live (a split invoice with a line that has no unit price), so INV-046 shows it. The three codes that really are dead (`PO_INFERRED`, `PO_AMBIGUOUS`, `PO_SELECTED_BY_JEV`) were left in place, not deleted.

Goal: one sample PDF set that shows every decision the pipeline can make, for the demo. Similar cases are merged
into one PDF; duplicates are removed. First test run uses the saved readings (`.read.json`, no OpenAI) with live Jev;
a second run later sends the PDFs to OpenAI and compares.

## What the 38 samples cover today

45 finding codes exist. 25 are produced by the samples, 20 are not:

- **Never raised by the code any more** (left from the old design): `PO_AMBIGUOUS`, `PO_SELECTED_BY_JEV`, and
  `HEADER_LEVEL_ONLY` (amounts are skipped when a line is unmatched, so it can no longer happen). `PO_INFERRED` is
  only read, never raised. Proposal: delete these four codes.
- **Cannot be shown with a sample PDF:** `EXTRACTION_FAILED` (offline it only means "no saved reading"; live it
  means the model call failed). Stays covered by tests, like a Jev timeout.
- **Missing, need new samples:** `FILE_REJECTED`, `MISSING_TOTAL`, `MISSING_DATE`, `DATE_AMBIGUOUS`,
  `DATE_IN_FUTURE`, `LINE_SUM_MISMATCH`, `TAX_SUM_MISMATCH`, `TAX_ID_FORMAT_INVALID`, `NON_POSITIVE_AMOUNT`,
  `VENDOR_AMBIGUOUS`, `VENDOR_NAME_ONLY`, `VENDOR_SCORE_LOW`, `TAX_ID_MISMATCH`, `CURRENCY_MISMATCH`,
  `CURRENCY_NOT_READ`.

Jev cases with no sample today: same item on two POs at **different prices** (Jev picks by price), a **wrong PO
printed** (item is on the vendor's other PO), the **"Monitor" vs "Monitor arm" trap**, a **freight line** next to
goods, **several reworded lines** on one invoice. Other gaps: a printed PO number that **does not exist**, an invoice
**dated before the PO**.

Warnings never block: a sample whose only findings are warnings is approved with those warnings shown.

## Duplicates to merge (10 removed)

| Remove | Same as (kept) | Why |
|---|---|---|
| INV-005 no PO printed, matched by lines | INV-023 | both: no PO printed, line matches one PO exactly |
| INV-026 Saffron, one PO partly | INV-023 | same as above |
| INV-031 Brightline, two POs, no PO printed | INV-025 | both: lines on two POs, nothing printed, split |
| INV-027 PO used up across two invoices | INV-003 + INV-025 | fully invoiced (003) and split (025) already shown |
| INV-028 price held after line match | INV-015 | both: unit price above the PO price |
| INV-029 quantity held | INV-004 | both: quantity more than what is left |
| INV-034 Jev-approved invoice sent again | INV-008 | both: exact duplicate, rejected |
| INV-010 Indian GST-inclusive prices | INV-014 | both: prices include tax, taxable value derived |
| INV-006 freight, Jev rejects both POs | new "freight + trap" sample | both: Jev finds no PO line |
| INV-030 services on no PO | new "freight + trap" sample | same as above |

## New samples (15) and one changed

| Case | What the PDF shows | Expected |
|---|---|---|
| File rejected | password-protected PDF | **rejected** `FILE_REJECTED`; nothing else runs |
| Incomplete invoice | no invoice date, no total printed | held `MISSING_DATE` (warn) + `MISSING_TOTAL` |
| Warnings only | dated in the future and printed "03/04/2027" (day or month first?), no currency, no GSTIN (vendor found by bank + name) | **approved** with `DATE_IN_FUTURE`, `DATE_AMBIGUOUS`, `CURRENCY_NOT_READ` |
| Arithmetic errors | lines do not add up to the subtotal; subtotal + tax ≠ total | `LINE_SUM_MISMATCH` + `TAX_SUM_MISMATCH` (warnings: approved unless you want these to block, see questions) |
| Credit note | negative quantity and amounts | held `NON_POSITIVE_AMOUNT` |
| Wrong GSTIN | malformed GSTIN, differs from the master | held `TAX_ID_FORMAT_INVALID` + `TAX_ID_MISMATCH` |
| Bank only, name differs | bank account matches, trade name differs, no GSTIN | held `VENDOR_SCORE_LOW` (60 of 80) |
| Name only | no GSTIN, no bank details, name matches | held `VENDOR_NAME_ONLY` |
| Name close to two vendors | needs one look-alike vendor in the master | held `VENDOR_AMBIGUOUS` |
| PO number does not exist | prints PO-2024-0999 | held `PO_NOT_FOUND` |
| Invoice dated before the PO | the only PO was raised after the invoice | held `PO_NOT_FOUND` (no open PO) |
| Wrong PO printed | Brightline prints PO-0160, bills monitors (only on PO-0159) | Jev offered PO-0160 lines only, says no → held `LINES_NOT_MATCHED` + `PO_UNUSED`; PO-0159 never touched |
| Same item, two prices | new vendor: chair at 2,600 (older PO), 2,800 (newer); invoice at 2,800, nothing printed | Jev picks the 2,800 PO (`jev_changed`) → **approved** |
| Freight + trap | headset (exact), "Monitor" (PO has only "Monitor arm", same price), "Freight & handling" | headset `code`; Jev drops the other two → held `LINES_NOT_MATCHED` |
| Currency mismatch | invoice in USD against an INR PO | held `CURRENCY_MISMATCH` |
| INV-035 (changed) | three reworded lines: "Ergo footrest pad", an abbreviation, a model code | Jev keeps all three → **approved** |

The new vendor (for "two prices" and "freight + trap") and the look-alike vendor shift the next vendor / PO ids the
master-data tests expect, as before.

## Final set: 43 samples (28 kept + 15 new), in demo order by stage

Numbered 1..43 in the order the pipeline checks things, so the demo walks stage by stage. Run-order chains stay in
order (INV-001 → 003 → 004 → duplicate; re-issue original → re-issue; Brightline 022 → 023 → 024; Kaveri 037 → 038).

| Stage | Samples |
|---|---|
| Intake | file rejected; scanned PDF (INV-002) |
| Validation | missing invoice number (011); incomplete invoice; warnings only; arithmetic errors; credit note |
| International reading | US month-first (012); German decimal comma (013); UK VAT-inclusive (014) |
| Vendor | not in master (020); suspended (032); wrong GSTIN; bank only; name only; close to two vendors |
| Bank | account differs (007); no bank details (021) |
| Duplicate | exact (008); re-issued under a new number (018 → 019) |
| PO printed | happy path (001 → 003 → 004); some lines of a multi-line PO (022); two POs printed (024); another vendor's PO (016); cancelled PO (033); PO does not exist; dated before the PO |
| No PO printed | matched by lines (023); lines on two POs (025) |
| Jev | reworded lines kept (035); ambiguous line held (036); wrong PO printed; freight + trap; twins oldest first (037); split across two POs (038); two prices |
| Amounts | within tolerance (009); price raised (015); wrong GST rate (017); currency mismatch |

## Questions for you

1. **Renumber** all samples in this stage order (clean demo, every test reference updated), or keep the current
   numbers and add the new ones as INV-039+ (gaps where samples were removed)?
2. **Arithmetic errors**: the code only warns when lines or taxes do not add up, so such an invoice can be approved.
   Keep that, or make it block?

## How it will be tested

1. Generate PDFs and saved readings with `scripts/make_invoices.py` (new switches: encrypted, no date, no total,
   future/ambiguous date, no currency, wrong sums, negative amounts, printed GSTIN / name / bank overrides, currency).
2. Golden rows for every sample; re-record Jev answers (live) on a temp database.
3. Full test suite and golden check with `LLM_MODE=recorded` (no OpenAI), Jev live.
4. Later, with your go-ahead: the same run with OpenAI reading the PDFs, compared sample by sample.
