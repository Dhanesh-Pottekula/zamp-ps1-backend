# PO Matching by Lines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace stage 6's four PO rule sets with one line-first rule: invoice lines are matched to the PO lines of a candidate pool, the code accepts a line only when sure, Jev decides every other line (keep, change, split, drop), and the PO set is the POs the lines land on.

**Architecture:** `OrderEntityClass.score_line` is the one scorer. `PoStageClass.run` builds the pool (printed POs, else the vendor's open POs), classifies each line sure / not sure, asks `PoJevClass.decide` about the not-sure lines in one request with full context, turns the answers into `LineAllocation`s (oldest PO first for a split line) and returns an `OrderMatch` carrying them. `AmountsStageClass` checks money on those allocations and no longer maps lines.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, pydantic, rapidfuzz, typesafe-sdk 0.7.0 (Noul questions), pytest; React + TypeScript.

**Spec:** `docs/superpowers/specs/2026-11-14-po-matching-by-lines-design.md`

## Global Constraints

- One rule for any number of POs. Printed PO numbers restrict the pool to those POs (each must exist, belong to the vendor, not be cancelled); nothing printed → the vendor's `open` / `partially_invoiced` POs dated on or before the invoice with something left, at most 10, newest first.
- Amount never selects a PO. `RULES_PO_WEIGHTS` and PO inference are removed.
- A line is **sure** when its best text score is **> 90** and no other PO line in the pool scores **≥ 70**; otherwise **not sure** and Jev decides it. Thresholds in `rules.env`: `RULES_LINE_SURE_ABOVE=90`, `RULES_LINE_CANDIDATE_AT=70`.
- Jev: one request per invoice with the whole invoice and every pool PO in full; one yes/no per (not-sure line, pool PO line with something left), no cap; > 0.80 selected, ≤ 0.50 rejected, between → unsure → line unmatched.
- Several PO lines for one invoice line: quantity fills the **oldest PO first** (PO date, then number), each up to that line's quantity left; the newest portion takes any rest.
- Jev is asked only when some line is not sure **and** no earlier stage (validation, vendor, bank, duplicate) blocked. Otherwise not-sure lines are unmatched.
- Any unmatched line → `LINES_NOT_MATCHED` (block) and stage 7 skipped. Printed PO with no allocation → `PO_UNUSED` (block).
- Jev state: full invoice (no bank details, no tax IDs), every pool PO in full (every line, nothing-left lines marked), the code's per-line conclusions. Recording key = that state without balance fields, plus the questions.
- Finding codes kept in the `FindingCode` literal for stored runs: `PO_INFERRED`, `PO_AMBIGUOUS`, `PO_SELECTED_BY_JEV` (no longer produced).
- New samples must not change existing samples' outcomes: their items must not appear on another PO of an existing sample's vendor.
- Every task: `uv run ruff check . && uv run ruff format --check . && uv run python scripts/check_imports.py && uv run pytest -q` green. Never run `run_golden.py` on the demo DB (temp `STORAGE_DB_PATH` + `STORAGE_UPLOAD_DIR`, `LLM_MODE=recorded JEV_MODE=recorded`).

## Revisions after plan review (binding; they override the tasks below where they differ)

1. **No lines, nothing printed** → `PO_NOT_FOUND` block, `cause="no_lines"` ("No PO number is printed and the invoice
   has no lines to match."). Printed PO and no lines → `PO_UNUSED`. Test in Task 3:
   `test_an_invoice_without_lines_is_never_approved`.
2. **Every task stays green: add new code beside the old, remove the old in the task that rewires its callers.**
   Task 1 only adds (types, `score_line`, `line_sure_above` / `line_candidate_at` as keyword arguments with defaults on
   `RulesEntityClass`, config fields); it removes nothing. Task 2 adds `PoJevClass.request_lines` / `decide` beside the
   old PO-selection methods. Task 3 rewires the PO stage and removes inference, `po_weights` (field, validator at
   env_config.py, rules.env, registry), the old Jev methods and their tests. Task 4 rewires amounts and removes
   `map_lines` / `map_split` / `_split_line` / `_map_line` and `price_close` (the single-line price fallback is dropped:
   "proposal = best line at any score" covers it).
3. **Split portions:** zero portions are dropped (never an `OrderUpdate` of 0); an invoice line with no quantity that
   Jev puts on several PO lines is unmatched; a single chosen line keeps its quantity, even None. Test in Task 4:
   `test_a_split_whose_oldest_line_covers_everything_has_one_portion`.
4. **`LinePlan` has two lists:** `rivals` = every other pool line scoring ≥ 70 (decides "sure"), and `ask` = the lines
   Jev is asked about (see the open item on the limit below). Lines with nothing left are never in `ask`; a not-sure
   line with an empty `ask` is unmatched; no questions at all → no Jev call. The code's suggestion with nothing left
   is shown in the state but not asked.
5. **The pool has one owner:** `PoStageClass.pool(...)` holds the status / date / something-left filter and the cap
   (setting moves from `JEV_MAX_CANDIDATES` to `RULES_PO_POOL_MAX=10`); the filter test moves from test_assist to
   test_order (`test_pool_is_open_dated_capped_and_newest_first`). `PoJevClass` drops the constructor arguments it no
   longer uses (`order_entity`, `max_candidates`, `pass_score`).
6. **Tests to update as well:** test_regressions.py:72, :95 (call `PoStage.run`), :104 (`"inferred"` → `"lines"`);
   test_master.py:62 (34 → 38 samples, and next vendor / PO ids); test_assist.py:362 (gate wording).
7. **Recording key:** leaves out `used`, `remaining`, `already_invoiced`, `left`, `status` and `nothing_left`. The
   question set still depends on which lines have something left, so INV-035–037 must not use up a V012 PO line that
   INV-038 needs; documented in run_golden.py.
8. **Frontend:** keep `JevSelection` for stored runs whose `assist` has `candidates` (old PO-selection runs); add
   `lines` to `PO_METHOD`; hide the per-PO score column when the method is `lines` / `jev`.
9. **INV-034:** golden `po` / `left` change too (PO-2024-0162 / 149400.00); BUILD_SPEC's "rejected before any PO is
   chosen" is reworded.
10. **Decided by the user: no cap.** Jev sees the whole invoice and every pool PO with all its lines in one call, and
    `ask` = every pool line with something left (pool ≤ 10 POs). No `JEV_LINE_ASK_MAX` setting.

## Review Focus

1. Printed PO, one line fits only another open PO of the vendor → that PO is never used; the line is not sure → Jev may only choose lines of the printed PO(s); held if none fits. Test in Task 3.
2. Jev puts one line on two PO lines whose quantity left is less than the invoice quantity → oldest filled, newest takes the rest, stage 7 reports `QTY_EXCEEDS_ORDERED`, nothing consumed. Test in Task 4.
3. Two invoice lines settled on the same PO line → stage 7 checks their combined quantity (existing `drawn` logic) through the allocations. Test in Task 4.
4. An earlier block (bank mismatch) on an invoice with one sure and one not-sure line → no Jev call; the sure line is allocated, the other unmatched; `LINES_NOT_MATCHED`. Test in Task 3.
5. Jev's answer misses a question or names one not asked → treated as no answer (lines unmatched), never a crash. Test in Task 2.

---

### Task 1: Types, findings, thresholds, one scorer

**Files:**
- Modify: `backend/app/entities/_types/stage_types.py`, `backend/app/entities/_types/finding_types.py`
- Modify: `backend/app/config/env_config.py`, `backend/app/config/rules.env` (remove `po_weights` / `RULES_PO_WEIGHTS`; add `line_sure_above: float = 90.0`, `line_candidate_at: float = 70.0`)
- Modify: `backend/app/entities/rules_ent.py` (accept and expose `line_sure_above`, `line_candidate_at`)
- Modify: `backend/app/entities/order_ent.py` (replace `map_lines`, `map_split`, `_split_line`, `_map_line` with `score_line`; keep `price_close`)
- Test: `backend/tests/test_order.py`

**Interfaces (produced):**
- `StageTypes.LineCandidate(po_number: str, po_line: int, po_date: date, description: str, score: float, left: Decimal)`
- `StageTypes.LineAllocation(line: int, description: str, po_number: str | None, po_line: int | None, quantity: Decimal | None, method: Literal["code", "jev_kept", "jev_changed", "jev_dropped", "unmatched"], score: float | None, suggestion: str | None, probabilities: dict[str, float] = {})` — `line` is 1-based; `suggestion` is `"PO-…/k"`; probabilities keyed `"PO-…/k"`.
- `StageTypes.AssistResult` reshaped: `state: Literal["answered", "skipped"]`, `reason: str | None`, `mode`, `digest: str | None`, `asked: list[int]` (invoice lines asked about), `findings: list[Finding]`.
- `OrderMatch` gains `allocations: list[LineAllocation] = []`, `pool: list[str] = []`; `OrderMethod` gains `"lines"`.
- Finding codes added: `PO_UNUSED`, `LINES_NOT_MATCHED`, `LINES_MATCHED_BY_JEV`.
- `OrderEntityClass.score_line(pool: list[OrderSnapshot], line: PricedLine) -> list[LineCandidate]`: every line of every pool PO scored by `match_adapter.ratio(line.description, po_line.description)`, sorted by score desc, then PO date asc, then PO number, then line number. Empty description → every score 0.

- [ ] **Step 1: Failing tests** in `test_order.py`: `test_score_line_ranks_every_pool_line` (pool PO-2024-0159 + 0160, "Monitor (24 inch IPS)" → first candidate `("PO-2024-0159", 3, score 100)`, length = all 6 lines); `test_score_line_with_no_description_scores_zero`. Delete the inference / map_* tests (`test_sole_open_po_is_inferred_from_several_signals`, `test_single_signal_inference_blocks`, `test_two_qualifying_pos_are_ambiguous`, `test_lines_map_by_description_even_at_a_wrong_price`, `test_each_line_of_a_split_invoice_maps_to_one_po`); their behaviour moves to Task 3 tests.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run the two tests → PASS (other suites may fail until Task 3–5; note which).

### Task 2: Jev decides not-sure lines

**Files:**
- Modify: `backend/app/entities/stages/po_jev.py` (remove PO-level selection: `triggers`, `candidates`, `select`, `assisted`, `_verdict`, `_selection_block`, `_kept`, `_answered`; keep `ask`, recordings, `_Helper.digest/load/save`)
- Test: `backend/tests/test_assist.py` (rewrite the unit tests for the new shape; keep the recordings and failure tests)

**Interfaces:**
- Consumes: Task 1 types.
- Produces: `StageTypes.LinePlan` (add to stage_types in this task): `line: int, description: str, quantity: Decimal | None, sure: bool, suggestion: LineCandidate | None, candidates: list[LineCandidate]`.
- `PoJevClass.request(reading: InvoiceExtraction, vendor_name: str | None, taxable: Decimal | None, pool: list[OrderSnapshot], printed: list[str], plans: list[LinePlan]) -> tuple[dict, dict, dict]` (state, questions, key). Questions: for each not-sure plan, for each of its ≤ 15 candidates with `left > 0`: name `line_<n>__<PO>/<k>`, `{"type": "noul", "instructions": "Does invoice line <n> (<description>) bill items from <PO> line <k> (<po description>)?", "criteria": {"true": "Invoice line <n> is this item, in whole or in part.", "false": "Invoice line <n> is a different item."}}`. Key = state with every `used`, `remaining`, `already_invoiced`, `left` field removed, plus questions.
- `PoJevClass.decide(...same args as request) -> tuple[dict[int, list[tuple[str, int]] | None], AssistResult]`: per asked line, the selected `(PO, line)` list (empty list = Jev dropped it; `None` = unsure / unanswered). Uses `ask()`; any missing or foreign question in the answer → every asked line `None`, `AssistResult.state="skipped"` with the reason.
- State shape (exact keys): `situation`, `invoice` {supplier, invoice_number, invoice_date, due_date, currency, po_reference, subtotal, tax_lines[{label, rate_pct, amount}], total, taxable_value, lines[{line, description, quantity, unit, unit_price, line_amount, tax_rate_pct}]}, `printed_po_numbers`, `purchase_orders` [{po_number, po_date, currency, status, total, used, remaining, lines[{line, description, ordered, already_invoiced, left, unit_price, tax_rate_pct, nothing_left}]}], `code_conclusions` [{line, sure, suggestion, suggestion_score, candidates[{po_line, score}]}].

- [ ] **Step 1: Failing tests:** `test_only_not_sure_lines_get_questions` (two plans, one sure → questions only for the other); `test_candidates_skip_lines_with_nothing_left_and_cap_at_15`; `test_state_holds_the_full_invoice_and_pos_but_no_bank_or_tax_id` (assert `"bank" not in json.dumps(state)` and `"tax_id" not in json.dumps(state)`, invoice_number present, a nothing-left PO line present with `nothing_left: true`); `test_key_leaves_out_balances`; `test_decide_reads_selected_dropped_and_unsure` (answers 0.95 / 0.10 / 0.65 → `[(po,k)]`, `[]`, `None`); `test_a_foreign_or_missing_answer_skips_every_line` (Review Focus 5); keep `test_recorded_answers_replay_by_digest_and_misses_are_reported`, `test_a_corrupt_recordings_file_is_a_skip_not_a_crash`, `test_a_recording_that_cannot_be_written_never_fails_a_live_answer`, `test_a_jev_error_is_a_skip` adapted.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.

### Task 3: PO stage by lines

**Files:**
- Modify: `backend/app/entities/stages/po_stage.py` (remove `_infer`, `_total`, `_lines_fit`, weights; keep `references`, `reference`, `_refusal`)
- Modify: `backend/app/entities/__init__.py` (PoStage without weights), `backend/app/entities/pipeline_invoice.py` (`self._po.run(reading, vendor, priced, taxable=taxable, earlier=earlier)`)
- Test: `backend/tests/test_order.py`, `backend/tests/test_assist.py` (pipeline tests)

**Interfaces:**
- Consumes: Task 1 `score_line`, Task 2 `PoJevClass.decide`, `LinePlan`.
- Produces: `PoStageClass.run(reading, vendor, priced, *, taxable: Decimal | None, earlier: list[Finding]) -> OrderMatch`; `PoStageClass.plan(pool, priced) -> list[LinePlan]` (public for tests); `PoStageClass.allocate(plan: LinePlan, chosen: list[tuple[str, int]], pool: list[OrderSnapshot], method) -> list[LineAllocation]`.
- Pool and refusals exactly as the spec 6.1 (printed: one or several; a refused number blocks with today's findings and an empty pool; bad reference `cause="bad_reference"`; unknown vendor `cause="no_vendor"`; empty open pool `PO_NOT_FOUND` `cause="no_open_po"`, message "No PO number is printed and this vendor has no open PO with anything left to invoice.").
- Jev only when some plan is not sure, `vendor.vendor_id` is set and no block in `earlier`.
- Allocation: one chosen line → one allocation, full quantity; several → oldest first (PO date, number), each `min(left, rest)`, last takes the rest. Methods: sure → `code`; Jev chose exactly the suggestion → `jev_kept`; any other non-empty choice → `jev_changed`; empty → `jev_dropped` (po fields None); otherwise `unmatched`.
- Findings: code-only success → `PO_MATCHED` (info) "Matched PO-… by the reference printed on the invoice." (printed) or "Matched PO-… (and PO-…) by the invoice lines." (nothing printed), data `po_number`, `others`; Jev helped → `LINES_MATCHED_BY_JEV` (info) listing "line n → PO-…/k (0.97)" per Jev-settled line; unmatched lines → `LINES_NOT_MATCHED` (block) "Invoice line n (<desc>) could not be matched to a PO line with confidence: …" ; printed PO with no allocation → `PO_UNUSED` (block).
- `OrderMatch`: `po_number` = first printed PO when printed, else first PO with an allocation (PO date order), else None; `others` = the remaining POs with allocations; `method` = `explicit` (printed) / `jev` (Jev settled ≥ 1 line) / `lines`; `signals=[]`, `score=100.0` when every line is allocated else `0.0`, `needed=pass_score`; `candidates` = pool as `OrderCandidate(po_number, remaining, signals=[], score=0)`; `pool` = pool numbers; `allocations`; `assist`.

- [ ] **Step 1: Failing tests** (manual entry via `/v1/runs/manual` like existing `test_assist` helpers, fake Jev on `EntitiesRegistry.Jev._jev_adapter`):
  - `test_exact_lines_on_two_pos_match_by_code_without_jev` (INV-024 reading, reference blank → approved, `method == "lines"`, PO set 0159 + 0160, fake Jev not called).
  - `test_printed_pos_restrict_the_pool` (Review Focus 1: Brightline invoice printing PO-2024-0160 that bills "Monitor (24 inch IPS)" (only on 0159) → not sure; fake Jev offered only 0160 lines (assert question names), answers all ≤ 0.50 → `LINES_NOT_MATCHED` + `PO_UNUSED`, needs attention, nothing consumed).
  - `test_jev_keeps_changes_drops_and_splits` (four parametrized cases on crafted readings; assert methods and allocations).
  - `test_an_earlier_block_keeps_jev_out_but_sure_lines_match` (Review Focus 4).
  - `test_unknown_vendor_bad_reference_refused_and_empty_pool` (causes `no_vendor`, `bad_reference`, `refused`, `no_open_po`).
  - Keep and adapt `test_explicit_reference_matches`, `test_reference_in_line_description_and_bare_number`, `test_unknown_reference_is_not_found`, `test_other_vendors_po_is_refused`, `test_cancelled_po_is_refused`, `test_fully_invoiced_po_still_matches`, `test_two_printed_pos_of_the_vendor_both_match`, `test_one_refused_po_refuses_them_all`, `test_no_reference_and_unknown_vendor_is_not_found` to call `PoStage.run`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.

### Task 4: Amounts on allocations; decision gate wording

**Files:**
- Modify: `backend/app/entities/stages/amounts_stage.py` (`skip_reason(order, snapshots, taxable)`; `run(order, snapshots, taxable, *, reading, priced, pct, invoice_rate)`; drop `map_lines` / `map_split` use)
- Modify: `backend/app/entities/pipeline_invoice.py` (new `skip_reason` / `run` arguments)
- Modify: `backend/app/entities/stages/decision_stage.py` (po gate: `lines` → "PO-… (and PO-…) matched by the invoice lines.", `jev` → "… matched by the invoice lines, with Jev settling the lines the code was not sure about.")
- Test: `backend/tests/test_rules.py`, `backend/tests/test_assist.py`

**Interfaces:**
- Consumes: Task 3 `OrderMatch.allocations`.
- `skip_reason` returns, in order: "Some invoice lines are not matched to a PO line, so the amounts cannot be checked line by line." when any allocation is `unmatched`/`jev_dropped`; "No PO was matched." when no snapshot; "There is no taxable value to compare." when taxable is None; else None.
- `run` builds one `PricedLine` per allocation (the invoice line's description and unit price/tax rate, the allocation's quantity) and the mapping (`int` line numbers for one PO, `(po, line)` tuples for several), then calls the unchanged `amount_check` / `split_check`.

- [ ] **Step 1: Failing tests:** `test_a_split_line_is_checked_as_portions` (100 chairs, PO lines with 50 + 50 left → approved, both lines fully used); `test_split_overflow_reports_quantity_exceeded` (Review Focus 2: 120 on 50 + 50); `test_two_lines_on_one_po_line_are_checked_together` (Review Focus 3); `test_unmatched_line_skips_amounts`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Full suite → PASS (except golden, fixed in Task 6).

### Task 5: Frontend

**Files:**
- Modify: `frontend/src/components/evidence.tsx` (PO evidence: pool list; allocations table: invoice line, PO line, quantity, method badge — Code / Jev kept / Jev changed / Jev dropped / Not matched —, score, Jev probabilities; Jev section: mode, reason, lines asked; remove the PO-selection candidates table), `frontend/src/lib/api.ts` types, `frontend/scripts/mock-api.mjs` (PO step shape)
- [ ] **Step 1:** Implement. **Step 2:** `npm run build` clean, `node --check scripts/mock-api.mjs`. **Step 3:** Browser check after Task 6 on a Jev sample and a code-only split; screenshot.

### Task 6: Samples, recordings, golden, docs

**Files:**
- Modify: `backend/data/vendors.csv`, `purchase_orders.csv`, `po_lines.csv` (new vendor V012 with PO-2024-0165 and PO-2024-0166 that both order one item, e.g. "Stackable visitor chair", plus distinct items), `backend/scripts/make_invoices.py` (INV-035 … INV-038), `backend/data/samples.json`, `backend/data/golden.json`, `backend/data/jev_recordings.json` (provisional, regenerated), tests that expect next vendor / PO ids (shift by one vendor and two POs), `BUILD_SPEC.md`, `README.md`, the earlier specs (mark stage 6 superseded).
- New samples: INV-035 printed PO, one line reworded to score 70–90 → Jev keeps → approved; INV-036 printed PO whose reworded line scores best on the wrong PO line → Jev changes → approved; INV-037 nothing printed, the shared item → Jev assigns to one PO → approved; INV-038 nothing printed, the shared item at a quantity needing both POs → split oldest first → approved.

- [ ] **Step 1:** Generate PDFs (`uv run python scripts/make_invoices.py`), confirm pre-existing files byte-identical (`shasum` before/after).
- [ ] **Step 2:** Regenerate provisional recordings with the scratchpad script (temp DB/uploads, `LLM_MODE=recorded`), answers chosen so each sample lands on its expected outcome; every entry `"source": "provisional"`.
- [ ] **Step 3:** Run golden on a temp DB; update rows whose codes change (INV-005, 006, 023, 025–032, 034 expected; outcomes unchanged except as the spec lists) and add 035–038; rerun → 34+4 pass.
- [ ] **Step 4:** Run the scratchpad simulation and race scripts (updated for the new Jev questions) → no invariant problems.
- [ ] **Step 5:** Full checks green; docs updated.
