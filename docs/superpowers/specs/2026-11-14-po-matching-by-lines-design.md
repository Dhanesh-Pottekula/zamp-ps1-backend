# PO matching by lines: design

Date: 2026-11-14. Status: approved by the user with the changes of 2026-11-14 (Jev decides every not-sure line; oldest PO first). Replaces stage 6 of 2026-11-13-jev-po-fallback-design.md and
supersedes the draft 2026-11-14-jev-line-confirmation-design.md (its line rules and "what Jev sees" are folded in).

## Intent

Today stage 6 has four rule sets that change with the number of POs (one printed PO, several printed POs, inference
by weighted signals, Jev picking POs), and they contradict each other: the inference "lines" signal works only when
every item is on one PO, the "amount" signal only when the invoice uses up a PO's whole balance, the price fallback
exists for one PO but not several, and a printed PO's lines are never used to confirm it.

The user's rule: **one way to match, no matter how many POs.** The invoice's lines decide which POs it bills; the
code proposes, and when it is not sure Jev confirms or picks; only a confident answer moves ahead.

## Decided by the user

1. The same matching rule for any number of POs.
2. **Line first:** invoice lines are matched to PO lines; the PO set is the POs the lines land on.
3. **Printed PO numbers restrict the search** to those POs only (after they are checked to exist, belong to the
   vendor and not be cancelled). A line that fits none of the printed POs is held; the code never pays against a PO
   the vendor did not name. No PO printed: the search covers the vendor's open POs.
4. **Amount is only a check** (stage 7), never a selector. The amount/lines/only-open-PO weights go away.
5. A line match is **sure only above 90** (text), only when it also **covers most of the PO description**
   (token-sort ratio ≥ 80, added after real-world testing: "Monitor" scored 100 against "Monitor arm"), and only when
   no other PO line in the pool looks like the same item.
6. **When the code is not sure, its match is only a suggestion and Jev decides the line:** keep the suggestion, pick
   another PO line (also on another PO of the pool), pick several PO lines, or drop it (the item is on no PO line).
   How many times Jev is called does not matter.
9. **One invoice line on several PO lines: the quantity fills the oldest PO first** (by PO date, then number), each
   portion up to that PO line's quantity left. Jev picks the PO lines; the code only splits the quantity.
10. **Only the same item is split** (decided after review): the PO lines Jev chooses for one invoice line must order
    the same item (near-identical descriptions, plain similarity ≥ 95, and the same numbers such as sizes); Jev
    choosing several different items (e.g. Mumbai and Delhi freight) holds the line for a person.
7. Jev's confidence bands: **above 0.80** used; anything else holds.
8. Jev gets **the full invoice, every candidate PO in full, and what the code concluded** (below).

## Stages

0 intake, 1 extraction, 2 validation, 3 vendor, 4 bank, 5 duplicate, **6 PO matching (by lines, Jev inside)**,
7 amounts, 8 decision, 9 persist. Same ten stages; stage 6 is rewritten.

## Stage 6, step by step (the same for any number of POs)

### 6.1 Candidate pool

- **PO numbers printed** (one or several, parsed as today): each must exist, belong to the vendor and not be
  cancelled; a refused number blocks as today (`PO_NOT_FOUND` cause `refused` / `PO_VENDOR_MISMATCH` /
  `PO_CANCELLED`). Pool = the printed POs. A printed PO that is fully invoiced stays in the pool (its lines show
  nothing left, so stage 7 reports over-consumption, as today).
- **Nothing printed:** pool = the vendor's `open` / `partially_invoiced` POs dated on or before the invoice with
  something left (at most 10, newest first, as today). A mistyped PO-like reference still blocks
  (`PO_NOT_FOUND` cause `bad_reference`); an unknown vendor blocks (`no_vendor`).
- Empty pool → `PO_NOT_FOUND` (cause `no_open_po`), stage 7 skipped.

### 6.2 Code proposal, per invoice line

- Score the description against **every line of every PO in the pool** (token-set ratio, 0 to 100).
- **Candidates** = PO lines scoring 70 or more.
- **Proposal** = the best-scoring PO line (any score; none when the pool has no lines).
- No separate price fallback: the proposal is the best line at any score, so a single-line pool always proposes its
  one line (not sure unless it scores above 90).
- An invoice with no lines and no printed PO blocks (`PO_NOT_FOUND`, cause `no_lines`).

### 6.3 Sure or not, per line

| Class | When | Then |
|---|---|---|
| **sure** | best score > 90 and no other PO line in the pool scores ≥ 70 | accepted by the code; Jev is not asked about it |
| **not sure** | anything else: best ≤ 90, a second similar line (same PO or another PO), only the price fallback, or no line ≥ 70 | Jev decides the line (6.4); the code's best line is shown to Jev as its suggestion |

Price, quantity and tax never choose a line; stage 7 checks them on the result.

### 6.4 Jev decides every line the code is not sure about

Asked when at least one line is not sure and no earlier stage (validation, vendor, bank, duplicate) blocked. One
request per invoice; every not-sure line gets one yes/no question per candidate PO line:

- `line_<n>__<PO>/<line>` (yes/no): "Does invoice line n bill items from PO-… line k (description)?"
- **Candidates** for line n: every line of every PO in the pool that still has something left (decided by the user:
  no cap; the pool holds at most 10 POs). Jev sees the whole invoice and every pool PO in full in the same call. So Jev can keep the suggestion, choose a different
  line, choose a line on a different PO, choose several, or choose none.

Reading the answers for line n:

| Jev's answers | Result | Method recorded |
|---|---|---|
| one or more candidates > 0.80, none between 0.50 and 0.80 | those PO lines; several → quantity split, oldest PO first | `jev_kept` (only the suggestion), `jev_changed` (any other choice) |
| every candidate ≤ 0.50 | the line is on no PO line (Jev dropped it) | `jev_dropped` (unmatched) |
| any candidate between 0.50 and 0.80 | Jev unsure | `unmatched` |

Jev error, timeout, no recording, or an earlier block: sure lines stay accepted, every other line is unmatched.
An invoice whose lines are all sure makes no Jev call.

### 6.5 Allocations and the PO set

- Each accepted line becomes one or more **allocations** `(PO, PO line, quantity)`. A line on one PO line is one
  allocation with its full quantity. A line Jev put on several PO lines is split oldest PO first (by PO date, then
  number), each portion up to that line's quantity left; if they together have less left than the invoice quantity,
  the last (newest) portion takes the rest and stage 7 reports `QTY_EXCEEDS_ORDERED`.
- **PO set** = the POs that received an allocation, ordered by PO date then number.
- Printed POs that received no allocation → `PO_UNUSED` (block): "The invoice names PO-…, but none of its lines is
  on it."
- Any unmatched line → `LINES_NOT_MATCHED` (block) listing each line with the code's proposal, its score and Jev's
  probabilities.
- Jev helped → `LINES_MATCHED_BY_JEV` (info) listing what Jev confirmed, picked or assigned.
- Code-only → `PO_MATCHED` (info), as today, naming the PO(s) and "by the lines" or "by the reference printed on the
  invoice".

### 6.6 Stage result (`OrderMatch`)

My calls (reviewer's question on the hand-off): when any line is unmatched, stage 6 blocks with
`LINES_NOT_MATCHED` and **stage 7 is skipped** ("Some invoice lines are not matched to a PO line, so the amounts
cannot be checked line by line"): comparing the whole invoice against only the POs some lines landed on would add
misleading findings. `po_number` (and so the run row) is the first **printed** PO when numbers were printed, else
the first PO that received an allocation, else none.

`po_number` = first PO (as above), `others` = the rest, `method` = `explicit` (printed) / `lines` (code, nothing printed) /
`jev` (Jev settled at least one line), `allocations` (per invoice line: PO, PO line, quantity, method, score, Jev
probabilities), `pool` (the candidate POs), `assist` (Jev's request digest, mode and answers), `findings`. The old
`signals`, `score`, `needed`, `candidates` fields are kept, filled from the pool, so the UI and stored runs stay
readable.

## Stage 7, amounts

Unchanged money rules, but it **no longer maps lines**: it takes stage 6's allocations. One PO → `amount_check`; several
→ `split_check` (each allocation is a portion line with its quantity at the invoice line's unit price, so a split line
needs no new money logic). An invoice with an unmatched line → `HEADER_LEVEL_ONLY`, as today.

## What Jev sees (one request, full context)

- **The full invoice:** supplier name, invoice number, invoice and due dates, currency, printed PO reference, every
  line (description, quantity, unit, unit price, line amount, tax rate), subtotal, tax lines, total, the taxable
  value. Left out on purpose: bank details and tax IDs (who is paid, not what is billed; kept out of an outside
  service's request).
- **Every PO in the pool, in full:** number, date, currency, status, total, used and remaining amount, every line
  (description, ordered, already invoiced, left, unit price, tax rate), lines with nothing left marked as such.
- **What the code concluded:** whether PO numbers were printed and which; for every invoice line whether the code is
  sure, its suggested PO line with its score ("the code suggests PO-2024-0162 line 3, score 84; not sure"), and the
  other candidates with their scores. Lines the code is sure about are shown as decided, for context.
- Recording key: this state without balances (used / remaining / already invoiced / left) plus the questions.

## What goes away

- The inference weights `RULES_PO_WEIGHTS` (amount 50, lines 50, sole open PO 50) and the separate
  "Jev picks POs" step (`po_jev.py` PO questions, `PO_SELECTED_BY_JEV`, `PO_INFERRED`, `PO_AMBIGUOUS`).
- `map_lines` / `map_split` as two rules; one scorer serves every case.
- Finding codes retired: `PO_INFERRED`, `PO_AMBIGUOUS`, `PO_SELECTED_BY_JEV`. New: `PO_UNUSED`, `LINES_NOT_MATCHED`,
  `LINES_MATCHED_BY_JEV`. Stored old runs keep their findings and still render.

## Effect on the samples (to be confirmed by running them)

- Printed-PO samples (INV-001 to 004, 007 to 019, 021, 022, 024, 033): unchanged; their lines score 100.
- Nothing printed, lines exact (INV-005, 023, 025 to 029, 031): matched **by the code, no Jev**. INV-023 and the
  Saffron samples now approve or hold on code alone; their golden rows change (codes, not outcomes, expected).
- INV-006 (freight, best score 61): unsure → Jev confirm/pick → held unless confident.
- INV-030 (services on no PO): no line ≥ 70 → Jev pick → `none` → held.
- New samples to show Jev at line level: a reworded line Jev confirms; a wrong best line Jev replaces; the same item
  on two POs Jev assigns; one line split across two POs (100 = 50 + 50).

## Recordings

Every new or reworked Jev answer used by the samples is a provisional, hand-written recording (marked
`"source": "provisional"`), as for the current ones, until the TypeSafe key arrives and real answers are recorded. The
new request shape invalidates the 9 current recordings; they are rewritten.

## Approaches considered

- **Line first, one rule (chosen):** the PO set falls out of the lines; works the same for 1 or 10 POs; Jev only where
  a line is unclear.
- Keep weighted PO inference and add line rules on top: keeps two selectors that can disagree.
- Jev picks POs, then lines: two Jev calls and the PO choice made before the evidence that decides it (the lines).

## Left to the builder

Class and file names, exact Jev wording, the evidence UI layout, sample quantities, migration of the frontend labels.
