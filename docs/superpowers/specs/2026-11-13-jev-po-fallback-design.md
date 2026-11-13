# Jev PO selection fallback: design

Date: 2026-11-13. Status: SUPERSEDED for stage 6 by 2026-11-14-po-matching-by-lines-design.md. Was: approved by the user (revised: Jev selects POs only).

## Intent

When the code cannot decide which PO(s) an invoice draws on, the run goes to **needs attention** and a
person picks the PO by hand. We add **Jev** (TypeSafe AI's evaluation model, called through
`app/adapters/typesafe_adp.py`) to **select the POs on the
code's behalf**: one PO or a combination. Jev's job ends there. With Jev's POs, the existing code runs
exactly as it does for an invoice that printed those PO numbers: line mapping (`map_lines` /
`map_split`), amount checks (`amount_check` / `split_check`), the decision and persistence. If the code
then approves, the invoice is approved.

## Many-to-many

- **One PO, many invoices** already works (`consumed_qty` per PO line, `consumed` and status per PO,
  stages 4–8 under the vendor's lock). Jev is shown what is left on each PO so it does not pick an
  exhausted one, and the code still refuses a line taking more than is left.
- **One invoice, many POs** works today when the numbers are printed (`split_check`). With Jev it also
  works when none are printed.

## Stages (revised by the user)

Each check is its own stage, and Jev lives inside the PO stage:

0 intake, 1 extraction, 2 validation, 3 vendor matching, 4 **bank validation**, 5 duplicate check, 6 **PO
matching (Jev inside)**, 7 amount validation, 8 decision, 9 persist. The duplicate check needs no PO, so it
runs before PO matching: an invoice already registered never reaches Jev.

- **Bank validation** (`stages/bank_stage.py`, step `bank`) is split out of vendor identification: the
  payee account against the vendor master. Different → `BANK_ACCOUNT_MISMATCH` (block); equal →
  `BANK_ACCOUNT_VERIFIED` (info); not printed → `BANK_DETAILS_ABSENT` (info). Skipped when no vendor.
  It sits right after vendor matching because it needs only the vendor, not the PO.
- **Jev runs inside the PO stage**, so amount validation runs once, on whatever POs that stage settled on.

## When Jev runs

Inside the PO stage, right after the code's own match, only if all hold:

- the vendor is identified, a taxable value exists, **no PO number was printed** (`OrderMatch.method`
  is `inferred` or `none`, never `explicit`);
- the code's match raised one of: `PO_AMBIGUOUS`; `PO_NOT_FOUND` with data `cause == "no_reference"`;
  `PO_INFERRED` as a block;
- no earlier stage (validation, vendor, bank, duplicate) raised a block: such an invoice is held
  whatever PO it bills.

`PO_NOT_FOUND` gains a data key `cause`: `no_reference` (`_infer` found nothing), `bad_reference`
(mistyped PO-like text), `no_vendor`, `refused` (printed number unknown). Only `no_reference` triggers.

Never triggers: printed POs of any kind (mistyped, unknown, cancelled, another vendor's, fully
invoiced), bank mismatch, validation problems, vendor problems.

## The Jev request

- **Candidates**: the vendor's POs with status `open` or `partially_invoiced`, dated on or before the
  invoice date, at most **10** (most recent first). Fewer than one → no request (skipped).
- **State**: vendor name; invoice date; invoice lines (description, quantity, unit price, tax rate);
  taxable value; each candidate PO with its date, remaining amount and every line with something left:
  `line 3: Monitor (24 inch IPS), 20 ordered, 10 already invoiced, 10 left, 11,000.00 each, GST 18%`.
- **Questions**: one yes/no (`Noul`) per candidate, named `po_<PO number>`: "Does this invoice bill
  items ordered on <PO>? Answer yes when at least one invoice line is an item this PO ordered and still
  has left, even when the invoice takes only part of it or also bills items from other POs."
- **Answer**: probability of yes per PO.

### Confidence and selection

- Set by the user: **selected** when p(yes) > **0.80**; **unsure** when 0.50 < p(yes) ≤ 0.80;
  **rejected** when p(yes) ≤ **0.50**. Env: `JEV_SELECT_ABOVE=0.80`, `JEV_REJECT_AT_OR_BELOW=0.50`.
- **Confident** = no candidate is unsure and at least one is selected.
- Not confident, or nothing selected → the run stays in needs attention; a `PO_SELECTED_BY_JEV` block
  finding lists every candidate with its probability so the reviewer sees Jev's view. The code's own PO
  finding (e.g. `PO_NOT_FOUND`) stays beside it.

## After Jev selects

1. Confident → the PO stage returns `OrderMatch(method="jev", po_number=first, others=rest,
   signals=["jev"], score=100)` with one `PO_SELECTED_BY_JEV` info finding ("Jev selected PO-… (0.97)
   and PO-… (0.95) from 3 open POs"); POs ordered by PO date then number. The code's own findings are
   kept in `assist.code_findings`.
2. Not confident → the code's match stands, with a `PO_SELECTED_BY_JEV` block beside its own finding.
3. Amount validation runs once, as for printed numbers: one PO → `map_lines` + `amount_check`; several
   → `map_split` + `split_check`. Decision and persist are unchanged (one `OrderUpdate` per PO, one
   transaction).

Jev error, timeout (10 s, no retries) or missing recording → `assist.state = "skipped"` with the reason;
the code's match stands.

## Pipeline and data changes

- `OrderMatch.assist` (an `AssistResult`): state (answered/skipped), reason, mode, digest, candidates with
  probability and verdict, selected POs, confident, the code's method and findings, Jev's finding. It is
  part of the `po` step's data; no separate step and no "replaced" findings.
- Run row `po_number` = Jev's first PO when used.
- New adapter `backend/app/adapters/typesafe_adp.py` (only importer of `typesafe_sdk==0.7.0`, model
  `jev-1.13.0`), key `TYPESAFE_API_KEY` in `backend/.env`; `JEV_MODE` (auto/live/recorded) forces a mode.
- **Recorded mode** when no key: answers looked up by SHA-256 of a key built from the invoice and the
  candidate POs' ordered lines (not their balances) in `backend/data/jev_recordings.json`, so a recording
  replays whenever the same POs are open. With a key and `JEV_RECORD=true`, live answers are written there
  (atomically; a failed write never fails the run).
- Frontend: the timeline shows the bank stage; the PO stage's evidence shows Jev's candidates with
  probability, verdict, the selected set and what the code found before asking.

## Samples (demo and golden)

The user asked for more Jev and rejected cases, fewer near-duplicates.

**Removed:** none. On inspection INV-014 (the only GBP invoice, also a rules-test fixture) and INV-021 (the only
invoice without bank details) each cover a path no other sample does, so both were kept.

**Rewritten from real Jev answers:** INV-006 (Orion, ambiguous), INV-023 (Brightline, monitors, now
expected to approve on PO-2024-0159), INV-024 (balances shift).

**New master data:** vendor V011 *Saffron Facility Supplies Pvt Ltd* (not "Meridian": that is the buyer's name) (IN, approved) with three open
POs: PO-2024-0162 (3 lines), PO-2024-0163 (3 lines), PO-2024-0164 (2 lines). No existing sample uses
V011, so no existing row moves.

**New samples** (no PO printed unless stated):

| Id | Case | Expected |
|---|---|---|
| INV-025 | V011: 2 items of 0162 (one partial) + 1 item of 0163 (partial) | Jev selects 0162 + 0163; split; approved |
| INV-026 | V011: part of one item of 0164 only | Jev selects 0164; approved, partial |
| INV-027 | V011: rest of 0163 | Jev selects 0163; approved; 0163 fully invoiced (one PO, two invoices) |
| INV-028 | V011: an item of 0162 at a raised unit price | Jev selects 0162; code holds `UNIT_PRICE_MISMATCH` |
| INV-029 | V011: items of 0164 and 0162, one quantity above what is left | Jev selects both; code holds `QTY_EXCEEDS_ORDERED` |
| INV-030 | V011: catering / AMC services on no PO | Jev selects none; stays needs attention |
| INV-031 | Brightline: 5 monitors (0159) + 4 filing cabinets (0160) | Jev selects both; split; approved |
| INV-032 | V006 Nova Component Systems (suspended) | rejected `VENDOR_SUSPENDED` |
| INV-033 | Pixel prints cancelled PO-2024-0149 | rejected `PO_CANCELLED` |
| INV-034 | Exact re-submission of INV-025 | rejected `DUPLICATE_EXACT` (Jev does not run) |

Exact quantities and prices are the builder's, chosen so each row lands on its expected outcome with
the code's own checks; the final expectations are written from the real recorded Jev answers. If the
real model disagrees with a row, stop and report rather than editing recordings.

Running `run_golden.py` resets the configured DB: record and check against a temporary
`STORAGE_DB_PATH`; ask before touching the demo DB.

## Approaches considered

- **Jev selects POs, code does the rest (chosen by the user).** Smallest Jev surface; all money logic
  stays in code.
- Jev maps every invoice line to a PO line (earlier draft): resolves wording mismatches too, but puts
  line matching in the model; the user wants Jev limited to PO selection.
- Jev on every invoice: rejected (cost, determinism, regressions).

## Decided by the user

- Jev's only job is selecting POs (one or several) when the code could not; the code then matches and
  approves as today.
- A confident Jev selection may lead to automatic approval if the code's checks pass.
- INV-023 may now approve; golden rows 006, 023, 024 rewritten. Near-duplicate samples may be removed;
  more Jev and rejected samples added.
- A real TypeSafe key is available.

## My calls (correct any)

- At most 10 candidate POs; 10 s budget.
- No sample removed (see Samples). New vendor V011 for Jev samples.
- Until the TypeSafe key is added, `jev_recordings.json` holds provisional answers (marked `"source":
  "provisional"`) written from the expected probabilities, so the demo and golden run end to end; recording with
  the real key replaces them.
- Recorded mode mirrors extraction.

## Left to the builder

Class and file names, question wording details, line quantities/prices of new samples, frontend layout.
