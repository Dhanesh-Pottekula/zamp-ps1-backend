# Jev PO Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a run ends in needs attention because the code could not choose the PO(s), ask Jev which of the vendor's open POs the invoice bills, then rerun the existing line mapping, amount checks and decision on Jev's POs.

**Architecture:** `TypeSafeAdapterClass` (only importer of `typesafe_sdk`) and `AssistEntityClass` (`app/entities/assist_ent.py`) decide whether Jev runs, build one yes/no question per candidate PO, ask Jev live or replay a recording, and select the POs. `PipelineEntityClass._finish` computes the first decision in memory; when Jev triggers it records a `po_assist` step, reruns the amount stage with the same branches `_check` uses, and records only the final decision. The run detail leaves out findings of superseded steps.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, pydantic, rapidfuzz, `typesafe-sdk==0.7.0`, pytest (asyncio auto); React + TypeScript; reportlab for sample PDFs.

**Spec:** `docs/superpowers/specs/2026-11-13-jev-po-fallback-design.md`

## Global Constraints

- Jev only selects POs. Line mapping, amounts and decision are the existing code paths.
- Trigger: first outcome `needs_attention`; vendor identified; taxable value present; `order.method in {"inferred", "none"}`; the `po` stage raised `PO_AMBIGUOUS`, `PO_NOT_FOUND` with `cause == "no_reference"`, or block `PO_INFERRED`; no block outside the `po` and `amounts` stages.
- Candidates: vendor POs `open` / `partially_invoiced`, `po_date <= invoice_date`, at most **10**, most recent first.
- Selected p(yes) > **0.80**; unsure 0.50 < p ≤ 0.80; rejected p ≤ **0.50**; confident = no candidate unsure and ≥ 1 selected (user's thresholds).
- Jev: model `jev-1.13.0`, timeout **10 s**, no retries. Error / timeout / missing recording → `po_assist` skipped, first decision kept.
- Recorded mode without `TYPESAFE_API_KEY`: SHA-256 of `json.dumps({"state": …, "questions": …}, sort_keys=True, default=str)` → `backend/data/jev_recordings.json`. `JEV_RECORD=true` + key writes answers.
- Runs where Jev does not trigger record exactly the steps they record today.
- Golden rows other than INV-006, 023, 024 (rewritten), INV-014, 021 (removed) and INV-025–034 (new) must not change.
- Never run `scripts/run_golden.py` on the demo DB without asking; use a temp `STORAGE_DB_PATH`.
- `uv run ruff check . && uv run ruff format --check .` clean; `uv run pytest -q` green after every task.

## Review Focus

1. Jev's answer omits a candidate or names an unknown question (stale recording, SDK drift) → skipped, first decision kept. Test in Task 3.
2. Jev selects one PO of two that the lines actually span → the code's own `HEADER_LEVEL_ONLY` / unmapped-line block holds the run; never approved on a subset. Test in Task 4.
3. A Jev-approved run's detail lists no superseded `PO_INFERRED` / `PO_AMBIGUOUS` / `PO_NOT_FOUND` reasons; a skipped `po_assist` supersedes nothing. Test in Task 4.
4. A vendor with no open PO left (all fully invoiced) → no Jev request, skipped with a reason, outcome unchanged. Test in Task 3.
5. An inferred PO whose amount check raised `OVER_CONSUMPTION` still triggers Jev (amounts are recomputed), but a bank mismatch on the same run does not. Test in Task 2.

---

### Task 1: Config, dependency, adapter

**Files:**
- Modify: `backend/pyproject.toml` (`uv add typesafe-sdk==0.7.0`)
- Modify: `backend/app/config/env_config.py` (+ `_JevConfigClass`, `ENVConfigs.Jev`); Create `backend/app/config/jev.env`
- Create: `backend/app/adapters/typesafe_adp.py`; Modify `backend/app/adapters/__init__.py`
- Modify: `backend/tests/conftest.py` (pop `TYPESAFE_API_KEY`, set `JEV_RECORD=false`)
- Test: `backend/tests/test_assist.py`

**Interfaces:**
- Produces: `ENVConfigs.Jev`: `api_key: SecretStr | None` (alias `TYPESAFE_API_KEY`, from `backend/.env`), `model="jev-1.13.0"`, `timeout=10.0`, `select_above=0.80`, `reject_at_or_below=0.50`, `max_candidates=10`, `record=False`, `recordings=Path("data/jev_recordings.json")` (resolved against backend dir), property `active -> Literal["live","recorded"]`. Prefix `JEV_`.
- Produces: `TypeSafeAdapterClass(api_key: str, model: str)`: `async evaluate(state, questions, *, timeout: float) -> Response`, `async close()` (`RetryPolicy(max_retries=0)`). `AdapterTypes.Jev`, `AdapterTypes.JevNoul = typesafe_sdk.Noul`, `AdapterTypes.JevError = typesafe_sdk.TypeSafeError`. `AdapterRegistry.Jev: TypeSafeAdapterClass | None` (None when recorded).

- [ ] **Step 1:** `uv add typesafe-sdk==0.7.0`; read the installed SDK for `Noul`, `system_one`, and the response's `nouls[name]` fields (probability of true) so Task 3 uses real names.
- [ ] **Step 2:** Failing test `test_jev_is_recorded_without_a_key`: `ENVConfigs.Jev.active == "recorded"` and `adapter.Jev is None`.
- [ ] **Step 3:** Run → FAIL. Implement. Run → PASS; full suite → PASS.

### Task 2: Trigger and PO_NOT_FOUND cause

**Files:**
- Modify: `backend/app/entities/order_ent.py` (`cause` on every `PO_NOT_FOUND`: `bad_reference` ~L420, `no_vendor` ~L432, `refused` in `_refusal`, `no_reference` in `_infer`)
- Create: `backend/app/entities/assist_ent.py`
- Test: `backend/tests/test_assist.py`, `backend/tests/test_order.py`

**Interfaces:**
- Produces: `AssistEntityClass.triggers(decision: StageTypes.Decision, *, order: StageTypes.OrderMatch | None, vendor_id: str | None, taxable: Decimal | None, outside: list[FindingTypes.Finding]) -> bool`, where `outside` = findings of every stage except `po` and `amounts`.

- [ ] **Step 1: Failing tests:**
  - `test_each_po_selection_block_triggers_jev` (parametrized: `PO_AMBIGUOUS`; `PO_NOT_FOUND` cause `no_reference`; `PO_INFERRED` block) → True.
  - `test_other_po_not_found_causes_do_not_trigger` (`bad_reference`, `no_vendor`, `refused`) → False.
  - `test_printed_po_never_triggers` (`order.method == "explicit"` with `HEADER_LEVEL_ONLY`) → False.
  - `test_amount_blocks_on_a_guessed_po_do_not_stop_jev_but_others_do` (Review Focus 5): `PO_INFERRED` + `OVER_CONSUMPTION` → True; + `BANK_ACCOUNT_MISMATCH` in `outside` → False.
  - `test_approved_rejected_no_vendor_or_no_taxable_never_trigger`.
  - `test_order_findings_carry_their_cause` in `test_order.py` for all four causes.
- [ ] **Step 2:** Run → FAIL. Implement. Run → PASS; full suite → PASS.

### Task 3: Candidates, questions, recordings, selection

**Files:**
- Modify: `backend/app/entities/assist_ent.py`; `backend/app/entities/_types/stage_types.py` (`OrderMethod` gains `"jev"`; new models)
- Create: `backend/data/jev_recordings.json` (`{}`)
- Test: `backend/tests/test_assist.py`

**Interfaces:**
- Produces (on `StageTypes`): `AssistCandidate(po_number: str, remaining: Decimal, probability: float | None, selected: bool)`; `AssistResult(state: Literal["answered","skipped"], reason: str | None, mode: Literal["live","recorded"], digest: str | None, candidates: list[AssistCandidate], selected: list[str], confident: bool, supersedes: list[str], order: OrderMatch | None, amounts: AmountCheck | None, findings: list[Finding])`.
- Produces on `AssistEntityClass`: `__init__(self, jev_adapter, order_entity: OrderEntityClass, *, recordings: Path, record: bool, select_above: float, reject_at_or_below: float, max_candidates: int, timeout: float, pass_score: float)`;
  - `candidates(vendor_id: str, invoice_date: date | None) -> list[OrderSnapshot]` (async; via `order_entity.snapshots(vendor_id)`).
  - `request(reading, vendor, lines: list[PricedLine], taxable: Decimal, candidates) -> tuple[dict, dict[str, Noul]]` (state, questions); PO line text exactly `"line 3: Monitor (24 inch IPS), 20 ordered, 10 already invoiced, 10 left, 11,000.00 each, GST 18%"`; lines with nothing left omitted.
  - `async ask(state, questions) -> tuple[dict[str, float] | None, str | None]` (p(yes) per question or a reason).
  - `select(candidates, probabilities) -> tuple[list[AssistCandidate], list[str], bool]`; raises `ValueError` if a candidate's question is missing or an unknown question is answered.
- Question wording (exact): instructions `"Does this invoice bill items ordered on {po}? Answer yes when at least one invoice line is an item this PO ordered and still has left, even when the invoice takes only part of it or also bills items from other POs."`; criteria true `"At least one invoice line is an item {po} ordered and still has left."`, false `"No invoice line is an item {po} ordered and still has left."`.

- [ ] **Step 1: Failing tests:**
  - `test_candidates_are_open_dated_and_capped`: 12 open + 1 fully invoiced + 1 cancelled + 1 dated after the invoice → 10, most recent first, none of the excluded.
  - `test_po_line_text_states_what_is_left` (exact string above; exhausted line omitted).
  - `test_recorded_answer_is_found_by_digest`; `test_missing_recording_is_skipped`; `test_record_mode_writes_the_answer` (fake adapter); `test_jev_error_is_a_skip`.
  - `test_selection_bands`: `{0.97, 0.30}` → confident, one selected; `{0.81, 0.50}` → confident, one selected (0.50 is rejected); `{0.97, 0.80}` → not confident (0.80 is unsure); `{0.97, 0.51}` → not confident; `{0.40, 0.20}` → not confident (none selected); `{0.85, 0.92}` → confident, both selected.
  - `test_an_answer_for_an_unknown_po_is_an_error` (Review Focus 1).
  - `test_no_open_po_left_skips_without_a_request` (Review Focus 4): fake adapter asserts not called.
- [ ] **Step 2:** Run → FAIL. Implement (fake adapter in tests returns an object with `.nouls[name].probability`-style fields matching the real SDK found in Task 1). Run → PASS; full suite → PASS.

### Task 4: Pipeline integration and reasons

**Files:**
- Modify: `backend/app/entities/assist_ent.py` (`resolve`); `backend/app/entities/pipeline_invoice.py`; `backend/app/entities/_types/stage_types.py` (`StepName` gains `"po_assist"` between `"duplicate"` and `"decision"`); `backend/app/entities/rules_ent.py` (`_gates`: po detail `"… selected by Jev"` when `order.method == "jev"`); `backend/app/entities/__init__.py` (`cls.Assist`); `backend/app/routers/run/services/run_s_v1.py` (`detail` skips findings of steps in any `po_assist` step's `data["supersedes"]`)
- Test: `backend/tests/test_assist.py`, `backend/tests/test_pipeline.py`

**Interfaces:**
- Produces: `async AssistEntityClass.resolve(*, reading, vendor: VendorMatch, priced: list[PricedLine], taxable: Decimal, pct: Decimal, invoice_rate: Decimal | None, rules: RulesEntityClass) -> AssistResult`. When confident: `OrderMatch(method="jev", po_number=selected[0], others=selected[1:], signals=["jev"], score=100.0, needed=pass_score, candidates=[], findings=[PO_SELECTED_BY_JEV info])`, selected ordered by PO date then number; amounts = `rules.amount_check(snapshot, …, mapping=order_entity.map_lines(…))` for one PO, `rules.split_check(snapshots, …, mapping=order_entity.map_split(…))` for several — the same arguments `_check` passes today. Not confident → `PO_SELECTED_BY_JEV` **block** listing every candidate and probability, `order`/`amounts` None, `supersedes=["po","amounts"]`. Message format: `"Jev selected PO-2024-0162 (0.97) and PO-2024-0163 (0.95) from 3 open POs of this vendor."` / `"Jev could not select the PO with confidence: PO-2024-0162 (0.62), PO-2024-0163 (0.08)."`.
- `_finish`: `first = decide(...)` in memory; if `assist.triggers(...)` → `result = await self._step(run_id, "po_assist", …)` (or `_skip` with the reason when skipped) → when answered, `trace.order = result.order or trace.order`, `trace.amounts = result.amounts`, and decide from `trace` findings with `po`/`amounts` findings replaced by `result.findings` (+ new amount findings) → record as `decision`. Not triggered → record `first` as today. `_check` must keep `pct`, `priced`, `taxable` on the trace for reuse.

- [ ] **Step 1: Failing tests** (manual entry via `Pipeline.enter(reading(...))`, fake Jev injected on `EntitiesRegistry.Assist`):
  - `test_jev_selects_two_pos_and_the_code_splits` → approved, both POs consumed, `po_assist` step answered, decision gate po passed.
  - `test_jev_selecting_one_of_two_needed_pos_is_held` (Review Focus 2) → needs attention with the code's unmapped-line block.
  - `test_jev_unsure_keeps_needs_attention_with_probabilities`.
  - `test_jev_failure_keeps_the_first_decision` → `po_assist` skipped, outcome and reasons identical to a run without Jev.
  - `test_jev_approved_run_shows_no_superseded_blocks` (Review Focus 3).
  - `test_runs_without_jev_record_the_same_steps` (INV-001 step names unchanged).
- [ ] **Step 2:** Run → FAIL. Implement. Run `uv run pytest -q` → all pass, including `test_reasons_are_derived_from_the_steps` and `test_derived_reasons_match_the_decision` (no recording in tests for INV-004 so it is unaffected; INV-006/023 runs get a skipped `po_assist`).

### Task 5: Frontend

**Files:**
- Modify: `frontend/src/lib/api.ts` (`StepName`, `STEP_ORDER` gain `"po_assist"` before `"decision"`; type of its data)
- Modify: the timeline component that renders `STEP_ORDER` (label "PO selection (Jev)"; steps in `supersedes` marked "replaced by Jev")
- Modify: `frontend/src/components/evidence.tsx` (candidate POs with probability and selected badge)

- [ ] **Step 1:** Implement. **Step 2:** `npm run build` (+ lint if defined) clean. **Step 3:** After Task 6, open a Jev sample run in the browser preview and screenshot the timeline and evidence panel.

### Task 6: Samples, master data, recordings, golden

**Files:**
- Modify: `backend/data/vendors.csv` (+V011), `purchase_orders.csv` (+0162–0164), `po_lines.csv`
- Modify: `backend/scripts/make_invoices.py` (remove INV-014, INV-021 specs; add INV-025–034 per the spec table)
- Delete: `backend/data/invoices/INV-014.*`, `INV-021.*`; Create the new PDFs/readings (generated)
- Modify: `backend/data/samples.json`, `backend/data/golden.json`, `backend/data/jev_recordings.json`, `BUILD_SPEC.md`, `README.md`/`PLAN.md` sample lists if they enumerate samples
- Modify: any test that references INV-014 or INV-021 (`grep -rn "INV-014\|INV-021" backend/tests frontend/src`)

- [ ] **Step 1:** `shasum backend/data/invoices/*` before; edit master data and specs; `uv run python scripts/make_invoices.py`; `shasum` after → every pre-existing file except the removed two is byte-identical.
- [ ] **Step 2:** For each new sample, compute expected code-only outcome and confirm the trigger fires for INV-025–031 (dry run in recorded mode with no recordings: all show a skipped `po_assist`), and INV-032–034 reject without `po_assist`.
- [ ] **Step 3:** Check `TYPESAFE_API_KEY` is set in `backend/.env` (ask the user if not). Record: `STORAGE_DB_PATH=$(mktemp -d)/rec.sqlite3 JEV_RECORD=true uv run python scripts/run_golden.py`.
- [ ] **Step 4:** Inspect every Jev run against the spec table. If the real model disagrees, stop and report; do not edit recordings.
- [ ] **Step 5:** Write golden rows, `samples.json` text and BUILD_SPEC rows from the results; rerun golden in recorded mode (key unset) on a temp DB → all pass.
- [ ] **Step 6:** `uv run pytest -q` and ruff → clean.
