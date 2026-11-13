# Jev line confirmation: design

Date: 2026-11-14. Status: SUPERSEDED for stage 6 by 2026-11-14-po-matching-by-lines-design.md. Was: draft, awaiting approval. Builds on 2026-11-13-jev-po-fallback-design.md.

## Intent

Today an invoice line maps to a PO line when its description scores 70 or more (token-set ratio), and nobody
double-checks a 70 to 90 match ("USB-C hub" can pair with "USB-C docking station"). An item ordered on two of the
invoice's POs is never guessed: the invoice is always held.

The user's rule: **the code proposes; when it is not sure, Jev confirms; only a confident answer moves ahead.**

## Decided by the user

- A line match is sure only when its description scores **above 90**. At 90 or below the code still proposes its
  best PO line, and Jev is asked to confirm it.
- When Jev does not confirm (says no, or is unsure), Jev is asked to **pick the right line** among the POs' lines (or
  none); its pick is used only when confident, else the line is held.
- When the **same item sits on two (or more) of the invoice's POs**, **Jev decides** which PO line it bills.
- Confidence bands are the existing ones: yes / top choice **above 0.80** is used; anything else holds.

## Stages (my call: a new stage, so it stays visible)

0 intake, 1 extraction, 2 validation, 3 vendor, 4 bank, 5 duplicate, 6 PO, **7 lines (new)**, 8 amounts,
9 decision, 10 persist.

Stage 7 `lines` (`stages/lines_stage.py`, Jev part `stages/lines_jev.py`) settles which PO line each invoice line
bills, over the POs stage 6 settled on. Stage 8 (amounts) checks quantities, prices and balances on that mapping and
no longer maps lines itself.

## Stage 7, per invoice line

1. **Code proposes.** Score the line's description against every line of every stage-6 PO; the best one is the
   proposal.
2. **Sure:** best score > 90 **and** no line of another PO scores 70 or more. Accept (method `code`).
3. **Same item on two POs:** lines on two or more POs score 70 or more. Ask Jev to **choose** among those candidate
   lines (+ `none`). Top option > 0.80 → accept (method `jev_po`); else unmatched.
4. **Not sure:** best score 90 or below (including the single-line-PO price fallback). Ask Jev to **confirm** the
   proposal (yes/no). Yes > 0.80 → accept (method `jev_confirmed`). Otherwise use Jev's **pick** among all open lines
   of the stage-6 POs (+ `none`): top option > 0.80 and not `none` → accept (method `jev_picked`); else unmatched.
5. A line with no proposal at all (no PO line) goes straight to the pick question.

One Jev request per invoice carries every question it needs: for each unsure line both the confirm question and the
pick question (the pick is read only when the confirm is not a confident yes), for each same-item line the choose
question. Lines that are sure ask nothing; an invoice whose lines are all sure makes no Jev call.

**When Jev is not asked or cannot answer** (an earlier stage already blocks, Jev error/timeout, no recording): sure
lines stay mapped; unsure and same-item lines are unmatched, so stage 8 holds the invoice (`HEADER_LEVEL_ONLY`). An
uncertain match never pays without confirmation.

## Findings

- `LINES_CONFIRMED_BY_JEV` (info): which lines Jev confirmed, picked or assigned to a PO, with probabilities.
- `LINES_NOT_CONFIRMED` (block): which lines could not be settled, with the code's proposal, its score and Jev's
  probabilities. Stage 8 then reports `HEADER_LEVEL_ONLY` as today.

## What Jev sees (both Jev calls: stage 6 PO selection and stage 7 line confirmation)

Set by the user: Jev gets the full invoice, all the candidate POs in full, and what the code concluded, not just
lines. The same state shape is used by both calls.

- **The full invoice** as read: supplier name, invoice number, invoice and due dates, currency, the printed PO
  reference text, every line (description, quantity, unit, unit price, line amount, tax rate), subtotal, tax lines,
  total, the taxable value validation derived. Left out on purpose (my call): bank details and tax IDs, which say who
  is paid, not what is billed, and stay out of an outside service's request.
- **Every candidate PO in full:** number, date, currency, status, total, used and remaining amount, and **every line**
  (description, ordered, already invoiced, left, unit price, tax rate), including lines with nothing left (marked as
  such, so Jev sees the whole order).
  - Stage 6: all of the vendor's open POs dated on or before the invoice (at most 10, as now).
  - Stage 7: the POs stage 6 settled on.
- **What the code concluded:**
  - Stage 6: the code's PO result (method, the finding it raised) and each candidate PO's signals and score
    ("PO-2024-0162: lines 50, needs more than 80"), so Jev sees what the code tried and why it was not enough.
  - Stage 7: the PO(s) selected and how (printed / inferred / selected by Jev), and for every invoice line the code's
    proposed PO line with its score and whether it is sure (> 90), unsure, or found on two POs.

The recording key covers this whole state except the balances (used / remaining / already invoiced / left), so a
recording still replays whatever ran before, as long as the same POs are open. The stage-6 request changes shape,
so its provisional recordings are regenerated.

## Jev questions (exact wording to the builder)

- confirm `confirm_<n>` (yes/no): "Is invoice line n the same item as PO-… line k (description)?"
- pick `pick_<n>` (choice): "Which PO line does invoice line n bill?" options `<PO>/<line>` for every open line of
  the stage-6 POs, plus `none`.
- choose `choose_<n>` (choice): same as pick, options limited to the lines of the POs that order this item, plus
  `none`.

Recordings work as for PO selection (same file, SHA-256 key over the invoice lines, the candidate PO lines and the
questions, without balances).

## What stays

- PO selection by Jev (stage 6) is unchanged; PO inference keeps its own 70 cut-off for the "lines" signal.
- Every money check stays in code (stage 8). Jev never changes a number.
- Thresholds: `JEV_LINE_SURE_ABOVE=90` (env), the 70 candidate cut-off, the 0.80 / 0.50 bands.

## Samples (new; existing rows unchanged)

- **INV-035** printed PO, a line reworded so its score is between 70 and 90 → Jev confirms → approved.
- **INV-036** printed PO, the code's best line is the wrong item → Jev says no and picks the right line → approved.
- **INV-037** prints two POs that both order the same item → Jev assigns it to one PO → approved.
- New master data: one more Saffron PO (PO-2024-0165) that also orders an item of PO-2024-0162.
  (Master-data tests that expect the next ids shift by one, as before.)

## Approaches considered

- **New stage 7 lines (chosen):** visible in the timeline, amounts stage keeps only money checks.
- Inside the amounts stage: no new step, but mixes "which line" with "is the money right".
- Inside the PO stage: one stage does PO choice and line choice; harder to read.
