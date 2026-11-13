# PS-1 Invoice Processing — 5-minute video script and interview deep dive

Written 2026-11-13 from the code, not from `README.md`. The README had drifted from the code in
eleven places; those were corrected on the same day, and §14 records what changed in case anyone
refers to an older copy.

- **Part A** — the spoken script, timestamped, with on-screen cues. 700 spoken words: 4:40 at
  150 wpm, 5:00 at 140 wpm, leaving the balance for clicks and page loads. Rehearse with a timer
  and use the trim list at the end of Part A.
- **Part B** — the interview deep dive: architecture, library decisions, stages, edge cases,
  standards, limits, and the questions you will be asked with one-line answers.

---

# PART A — The 5-minute script

Rules of the recording: the guide asks for the happy path **plus at least one edge case**. This
script does the happy path live and the edge cases as fast clicks on already-run samples, so no
time is lost waiting for stages.

**Before you hit record**

1. Backend up, frontend up, mode badge visible in the first frame.
2. Press **Reset demo data**.
   ⚠️ Reset deletes every run **and every vendor or PO you created by hand**. Do it before you
   stage anything you want to keep. Do **not** run `scripts/run_golden.py` — it resets too.
3. Pre-run exactly three: **INV-019, INV-034, INV-041**. These are independent of INV-001.
4. Leave **INV-001 unrun** (the live happy path) and **INV-021 unrun**.
   ⚠️ INV-021 *is* the re-send of INV-001, and duplicates are only caught against **approved**
   invoices. If you pre-run INV-021 first it gets approved, and your live INV-001 is then rejected
   as *its* duplicate — the demo inverts. INV-001 must run before INV-021, both on camera.
5. Dashboard open in a second tab.

---

### 0:00 – 0:25 — What it is

> **ON SCREEN:** Process page, mode badge visible.

"This is Invoice Desk. A supplier invoice goes in as a PDF or a photo, and a decision comes out:
approved, needs attention, or rejected. One model call reads the document. Everything after that
is plain Python — comparison and arithmetic — with one narrow exception I'll come back to. So the
same invoice gets the same answer every time. Let me run the happy path."

---

### 0:25 – 1:30 — Happy path, live

> **ON SCREEN:** Click **Run** on step 1, INV-001. Let the timeline fill. Scroll the stages.

"I'm clicking step one. Acme Supplies bills thirty of the fifty laptop stands on PO-2024-0142.

The backend answered 202 Accepted immediately with a run id, and the browser is now following a
server-sent-events stream — so what you're seeing is the real pipeline, stage by stage, not a
progress bar.

Ten stages. Intake checks the bytes. Extraction is the model call. Then validation, vendor, bank,
duplicate, PO matching, amounts, decision, and one write at the end.

Approved as a partial delivery. The PO balance drops from sixty thousand to twenty-four thousand,
and every check that fired is listed with its numbers."

---

### 1:30 – 2:05 — The two decisions that shape the backend

> **ON SCREEN:** Expand the **extraction** evidence panel (fields + source quotes + token block),
> then flash the folder tree `adapters / entities / routers`.

"Two decisions shape the whole backend.

First: the model reads, the rules decide. The model never sees a tolerance or a vendor list. It
fills one flat, all-text schema — every field required, empty string for 'not printed'. Code then
parses the money and the dates, in the document's own style, with tested functions. That's why
'1,70,000.00' and '14.994,00' both come out right.

Second: four layers — controller, service, entity, adapter — and each may only call the one below.
Every third-party library sits behind exactly one adapter, and a script parses the whole package
with Python's `ast` module to enforce that. It exits one. The rule is executable, not a review
comment."

---

### 2:05 – 3:25 — Edge cases

> **ON SCREEN:** One click per sample, straight to its decision banner. Don't scroll; the banner is
> the point.

"Now the edge cases.

**Bank account mismatch.** [INV-019] Everything on this Pixel Print invoice matches the vendor
master except the payee account. That's the classic invoice-interception fraud, so it blocks, and
the finding says: verify the change by phone.

**Duplicate.** [INV-021] Acme re-sends the invoice we just approved. Rejected — and notice it's
only checked against *approved* invoices, so an invoice held for a fixable reason can be corrected
and run again.

**Tolerance.** [INV-041] Zenith bills a thousand rupees over the PO balance. The tolerance is the
greater of two percent or a currency floor, so this passes, and the finding states the overage in
numbers.

**And the one I'd want to be asked about.** [INV-034] This invoice says 'Ergo footrest pad' where
the PO says 'Ergonomic footrest'. That scores seventy-four — below the ninety the code needs to be
sure. So the code hands that line, and the other reworded one, to Jev: an evaluation model that
returns a probability per question instead of text. Above 0.80 the line is taken, at or below 0.50
it's dropped, anything in between and the invoice is held. Jev only picks *which PO line*. Every
quantity, balance and outcome stays in code."

---

### 3:25 – 4:05 — Correctness

> **ON SCREEN:** Dashboard, PO balances table.

"Two correctness details.

Stages five to nine hold a lock **per vendor**, not one global lock. Those stages read a PO balance
and then write it. With the lock disabled I reproduced a PO at minus twelve thousand from two
concurrent invoices. The slow part — the model call — holds no lock, so extractions still run in
parallel.

And stage nine is one transaction: the register row, the PO consumption, the step, and the run's
closing values commit together. An approval can never consume a PO without the run recording it,
or the reverse."

---

### 4:05 – 4:40 — Tests and standards

> **ON SCREEN:** Terminal, `uv run pytest -q` output already on screen (379 passed in 9.14s).

"On standards. Three hundred and seventy-nine tests pass in nine seconds, with no network —
extraction replays a recorded reading matched by the file's SHA-256, never by its name. Forty-six
seeded invoices cover every finding code the pipeline raises, each with an expected outcome,
vendor, PO and balance in a golden file. Every defect found in review has its own regression test
that failed before the fix. Fourteen Ruff rule groups, exact-pinned dependencies, and closed
Pydantic schemas."

---

### 4:40 – 5:00 — Limits and close

> **ON SCREEN:** Back to the Process page, full scenario list visible.

"What it doesn't do: two-way match only, no goods receipt; no currency conversion; no auth; single
process. The limits are written down.

The point of the design is the split — approve what's certain, reject what's clearly wrong, and
hand a person everything else with the work already done."

---

This is **700 spoken words**: 4:40 at 150 wpm, 5:00 at 140 wpm. The gap is your click-and-load
time. **If you still run long**, cut in this order:
1. INV-041 tolerance (−18 s) — INV-019 and INV-021 already prove "blocks".
2. The last sentence of beat 5 (−10 s).
3. The first sentence of beat 7 (−8 s).

**Do not cut** the Jev beat or the per-vendor lock. Those two are the only things in the video an
average candidate would not have built.

---
---

# PART B — Interview deep dive

## 1. The thesis, in 30 seconds

> "An LLM reads the document. Deterministic code makes every decision. A second, non-generative
> model is asked exactly one narrow question — which PO line does this invoice line bill — and only
> for the lines the code isn't sure of. It sees prices so it can tell two otherwise identical PO
> lines apart, but it does no arithmetic and decides no outcome. If it isn't confident, the invoice
> is held for a person. Every check is a coded finding carrying its numbers, so an auditor can see
> which rule fired on which value."

Three outcomes: `approved`, `needs_attention`, `rejected`.

---

## 2. Architecture

### Four layers, enforced

```
Browser → Controller → Service → Entity → Adapter → (SQLite, LLM API, PDF, fuzzy match, disk)
```

| Layer | Folder | Job | May import |
|---|---|---|---|
| Controller | `app/routers/<name>/controllers/` | route, status code, response model. Nothing else. | own router's services/models/config, FastAPI, Pydantic |
| Service | `app/routers/<name>/services/` | entity results → wire models; raises 404/409/422/429 | `app.entities` (the package), own router |
| Entity | `app/entities/` | the business: rules, tables, matching, the pipeline | `app.adapters` (the package), `app.entities._types`, `app.config`, stdlib, Pydantic, SQLAlchemy |
| Adapter | `app/adapters/` | the outside world | `app.config`, any third-party library |

**Why it's worth mentioning:** `scripts/check_imports.py` parses every file under `app/` with
Python's `ast` module, records each import and whether it sits inside `if TYPE_CHECKING:`, and
exits 1 on a violation. Relative imports always fail. A sibling entity may only be imported for
type hints — stages receive what they need by constructor injection.

> **Line:** "The layering isn't a convention in a README, it's a script that exits 1. That's the
> difference between a rule and a hope."

⚠️ There is **no CI pipeline** in this repo (no `.github/`). Say "a script that exits 1, run with
the lint and the tests" — not "it fails the build". If you have ten minutes before the interview,
adding a three-line GitHub Actions workflow would make the stronger claim true.

### Two registries, built once at startup

`AdapterRegistry` (DB, LLM, Jev, Match, Pdf, Store) → `EntitiesRegistry.initialize(adapters)`
constructs entities in dependency order: Prompt, Rules, Vendor, Order, Invoice, Run, Seed,
Extraction, Pipeline. No entity builds its own dependency. Then, concurrently via `asyncio.gather`:
prompts loaded, orphaned `running` rows from a dead process marked `error`, seed data loaded if
tables are empty.

Services are `functools.cache`d singletons, injected by FastAPI through a type alias
(`RunServiceRegistry.V1`).

### The request path, concretely

```
POST /v1/runs (file)
  → RunService.upload  (reads max_bytes+1 bytes, so intake can still report "too big")
  → Pipeline.submit    (insert run row, status=running, spawn asyncio task)
  → 202 Accepted {"id": "..."}        ← returned before any stage runs
  → browser: GET /v1/runs/{id} + EventSource /v1/runs/{id}/events
```

### SSE with replay — the subtle bit

`RunEntityClass.follow(run_id)`:
1. **Subscribe first** (add a queue for this follower).
2. *Then* read the run and its stored steps.
3. Send every stored step; remember each name as finished.
4. If the run is no longer `running`, send the closing event and stop.
5. Otherwise stream the queue, **dropping events for already-finished steps**.

Subscribing before reading means nothing published between the two is lost. Dropping finished
steps means a step never visibly goes `done → running`. Result: you can open or refresh the run
page at any moment, during or after, and get full history then the live tail.

> **Line:** "Subscribe first, then replay from storage, then de-duplicate on the step name. If you
> read storage first you lose anything published in the gap, and there's no way to detect it."

---

## 3. Library decisions

The rejected alternatives below are the ones actually weighed in `DECISIONS.md` §9, §10 and §25 —
use these rather than inventing a comparison on the spot.

| Chosen | Why | Rejected, and why |
|---|---|---|
| **FastAPI** 0.162.4 | async streaming of stages over SSE in ~10 lines; Pydantic built in, so the LLM schema, the DB models and the API responses share one class; free `/docs` as a demo fallback | **n8n / Make** — rules become a tangle of IF nodes and explanations get hard to make rich. **Streamlit** — reruns the whole script on every interaction, so a live stage view is awkward and it reads as a data app, and the UI is explicitly graded. **Next.js full-stack** — viable, but Python is better for PDF and rules ergonomics |
| **Pydantic** 2.14.3 | one type system for the model's schema, the entities and the wire; `extra="forbid"` closes every schema | — |
| **SQLAlchemy** 2.1.8 + **SQLite** | data is relational (invoice→vendor, invoice→PO, balance as a running sum) and approve-and-consume must be one transaction; zero infrastructure on demo day; tables match how finance people think. WAL mode so readers never block on the writer | **Postgres** — overkill for ~50 rows a table and one user, and a service that can be down on demo day. **MongoDB** — Atlas free clusters sleep and take ~30 s to wake, and transactions need a replica set. **SQLModel** — passed over for debuggability. **Alembic** — skipped deliberately; tables are created at startup |
| **rapidfuzz** 3.14.6 | C++ `token_set_ratio` *and* `token_sort_ratio`. Two ratios on purpose: token-**set** for "do these mention the same thing", token-**sort** for coverage — which is the guard that stops "Monitor" matching "Monitor arm" | — |
| **pypdf** 7.0.1 | intake only: does it open, is it encrypted, page count, text-layer present. **Never used to read content** | **Library text extraction in the data path** — a scanned image inside a PDF returns empty text, so the most ordinary scan fails. **Hybrid (text for all pages + images only for scanned pages)** — discussed in detail and rejected: "has image" is true on nearly every page (logos, signatures, QR codes) so the saving mostly disappears, text extraction flattens tables, and it adds a second code path and a threshold to defend |
| **openai** 3.49.0 (live) / **anthropic** 2.8.0 | same `extract` contract behind two adapters; `LLM_PROVIDER` picks, defaulting to whichever key is present | **LangChain / agent frameworks** — a fixed stage sequence with one model call doesn't need one, and it makes "why did it do that" harder to answer |
| **typesafe-sdk** 0.7.0 | Jev, the evaluation model (§6) | a second LLM call returning JSON — see §6 |
| **uv** | lockfile, fast resolution, `uv run` needs no venv activation | pip + requirements.txt |
| **ruff** 0.17.4 | lint *and* format in one tool, so no black/isort/flake8 disagreement | black + isort + flake8 + pyupgrade |
| **Vite + React + TS + Tailwind** | SSE is a browser primitive; no SSR need; Tailwind avoids a CSS-naming debate on a one-week build | (see Streamlit / Next.js above) |

**Every dependency is pinned to an exact version** (`==`, not `>=`), with `uv.lock` committed. A
case study that still runs in three weeks beats one on the latest patch.

**Python 3.14** is a hard requirement (`requires-python = ">=3.14"`), and the code uses its
features — `openai_adp.py` has unparenthesised `except RateLimitError, InternalServerError,
APIConnectionError:`, which is PEP 758 and a syntax error on 3.13. Worth knowing in case anyone
tries to run it on an older interpreter.

> **Line on adapters:** "Adding OpenAI as a second provider was one new file and one config line.
> Nothing in the entities layer knows a provider exists."

---

## 4. The ten stages

Stage order in code (`pipeline_invoice.py`).

| # | Stage | Input | Output | Skipped when |
|---|---|---|---|---|
| 0 | intake | filename, raw bytes | type, size, pages, encrypted, text_layer, accepted | never (manual run: skipped, no file) |
| 1 | extraction | bytes, media type, filename, source hint | `InvoiceExtraction`, token usage, mode, model, seconds | intake rejected the file |
| 2 | validation | extraction + today's date | taxable_value, tax_total, tax_rate_pct, derived | no extraction |
| 3 | vendor | supplier party + bank details + vendor master | vendor_id, method, score, evidence, candidates | no extraction |
| 4 | bank | payee account/bank code vs master | verified / mismatch / absent | no extraction, or vendor unknown |
| 5 | duplicate | vendor id, extraction, taxable value | verdict: none / exact / suspected | no extraction, or vendor unknown |
| 6 | **po** (Jev inside) | extraction lines, printed references, vendor's POs | PO set, per-line allocations, pool, Jev transcript | no extraction |
| 7 | amounts | matched PO(s), taxable value, currency, priced lines | variance, tolerance, within, level, per-line checks | no extraction / no PO / no taxable value |
| 8 | decision | every finding so far | outcome, summary, reasons, **4** gates (vendor, bank, po, amount), PO update | never |
| 9 | persist | the decision | registered, po_updated, remaining_after | never |

**Lock boundary:** stages 0–4 hold no lock. Stages **5–9** hold the matched vendor's lock.

### Report-only

A blocking finding **never** stops later stages. A stage is skipped only when its *input* does not
exist. A reviewer handed "vendor unknown" would otherwise fix it, re-run, and only then discover the
PO is also over-consumed. One pass, every problem.

### The decision rule

```
sort all findings: reject-set blocks → other blocks → warnings → info   (stage order within a group)
any block?
  no  → approved, summary names the PO and what remains, update = amount + per-line quantities
  yes → first block in the reject set?  yes → rejected    no → needs_attention
         summary = first block's message + " N more issue(s) to review."
```

The four **gates** (vendor, bank, po, amount) shown in the UI are a restatement of the blocks per
stage — they are for the reviewer to read, not an input: the findings alone decide the outcome.

Reject set: `FILE_REJECTED`, `VENDOR_SUSPENDED`, `PO_CANCELLED`, `DUPLICATE_EXACT`. Because the
findings are sorted first, "the first block is in the reject set" is equivalent to "any block is".
`RulesEntityClass.ordered` is the single implementation of that order — `decide` uses it, and so
does the run service when it rebuilds reasons on read, so the stored run and a re-read run can't
disagree.

**Findings are stored once**, in the steps' `findings` JSON column. The run row holds only the
outcome and summary; reasons are *derived* on read. There is no `reasons` column, and a test
asserts that.

---

## 5. Extraction: the two-schema design

This is the AI-engineering core. **Two Pydantic models, deliberately.**

**`InvoiceRead`** — the only schema the model ever sees:
- Every field **required**. No optionals, no unions, no free-form objects.
- Every value is **text**, except one boolean and three lists.
- `""` means "not printed". Amounts are copied **exactly as printed** — `"1,70,000.00"`.
- `extra="forbid"` on every nested object, so the generated JSON schema is fully closed.
- Carries two meta-fields the model is better placed to judge than code: `decimal_separator`
  (`"."` / `","` / `""`) and `date_order` (`"DMY"` / `"MDY"` / `"YMD"` / `""`).
- Carries `sources`: the printed text quoted verbatim for the six fields most worth auditing.

**`InvoiceExtraction`** — what every later stage reads: real `Decimal`, real `date`, or `None`.

**Why split them:**
1. Structured output is most reliable on a simple closed schema. Optionals, unions and free-form
   objects are where refusals and schema-limit errors come from.
2. It moves every numeric judgement out of the model and into small tested functions.
   `"1,70,000.00"` parses identically every time; a form the code can't be sure of becomes `None`,
   not a guess.
3. The model reports *style*, the code applies it. That's the key move: the model is good at "this
   document is German", bad at "therefore 14.994,00 is 14994.00". Code does the second part.

**Amount parsing** — the cases that matter in an interview:
- `decimal_separator == ","` → `"14.994,00 €"` → 14994.00; more than one comma → `None`.
- Otherwise → `"1,70,000.00"` (Indian grouping) → 170000.00.
- Ambiguity is refused, not guessed: `"36.000,00"` with no separator reported → `None`.
  A dot-grouped whole number with no decimal character reported (`"36.000"`) → `None`, because it
  could be 36000 or 36.
- Swiss apostrophes (`1'234.50`) handled. Parens and trailing/leading minus → negative.

**Date disambiguation** — 11/04/2024 is 4 November in the US and 11 April in India/UK:
1. Take the model's YYYY-MM-DD value.
2. Find the field's **source quote**. If it has no numeric date, keep the model's value.
3. If the quote can't be read to give the model's value, the quote is about a different date — keep.
4. Use `date_order` if reported; else, if one number > 12 that's the day; else read day-first **and**
   flag the field in `ambiguous_dates` → validation raises `DATE_AMBIGUOUS` (warn).

> **Line:** "I never let the model do arithmetic or date logic. I let it tell me which convention
> the document uses, and I apply the convention in tested code. When even that is ambiguous, the
> invoice carries a warning rather than a wrong number."

**Why send the whole PDF, not extracted text:** a scanned PDF has no text layer — the most ordinary
real case returns nothing. Text extraction also flattens tables. So the PDF goes as a base64 data
URL (`input_file`) and a PNG/JPEG as `input_image`: one path for clean PDFs, scans and photos.
Intake only tells the model, in a one-line hint, whether a text layer exists.

### The live adapter — know which one runs

`LLM_PROVIDER=openai`, so **`openai_adp.py` is what executes**: `gpt-6-luna`, effort `low`, through
the **Responses API** (`responses.parse`, `text_format=InvoiceRead`). `llm_adp.py` (Anthropic,
`messages.parse`, `cache_control: ephemeral`) is a complete second implementation of the same
`extract` contract and is what you'd get by setting the provider the other way. Don't describe the
Anthropic one as if it's live.

Three choices inside the OpenAI adapter worth naming:

- **`store=False`.** Invoices carry bank details, and OpenAI retains responses for 30 days by
  default. This is a one-line privacy decision and a good thing to be asked about.
- **Explicit prompt caching.** The request is ordered from what never changes to what always
  changes: the system prompt as a `developer` message carrying a
  `prompt_cache_breakpoint: {mode: "explicit"}` that closes the shared prefix, *then* the document
  and its filename. A fixed `prompt_cache_key` routes every call to the same cache. Explicit mode
  matters: the automatic breakpoint would write **each invoice** into the cache, where nothing
  could ever read it again.
  ✅ **Measured, not aspirational** — `DECISIONS.md` §25, 2026-11-13: the first call wrote 3,535
  tokens to the cache and the second read 3,535 back.
- **Token normalisation.** OpenAI counts cached tokens *inside* `input_tokens`; the adapter
  subtracts them so `input / cache_read / cache_write / output` means the same thing whichever
  provider ran. Without that, a dashboard comparing providers would silently lie.

**Retry policy** (identical in both adapters): the SDK client is built with `max_retries=0`, so the
adapter's own retry is the only one. Rate limit / 5xx / connection error → wait 2.0 s, one retry,
then `ExtractionError`. Any other API status → immediate `ExtractionError` carrying the status.
A `ValidationError` on the parsed answer gets its own message naming truncation or refusal as the
usual cause. An `ExtractionError` is **not** a crash: it becomes an `EXTRACTION_FAILED` finding and
the run completes as `needs_attention`.

**Recorded mode** (no key): the document's SHA-256 is looked up in the `samples` table; on a match
the stored reading is replayed after a 0.8 s pause so the live view has something to show. Matched
**by bytes, never by filename** — a file named `INV-001.pdf` whose bytes differ gets
`EXTRACTION_FAILED`, and there's a test for exactly that.

---

## 6. Jev: the second model, and why it is not an LLM

**The problem.** Stage 6 matches invoice lines to PO lines by description. Text similarity settles
most of them, but not: `"Ergo footrest pad"` vs `"Ergonomic footrest"` (74), or the same chair on
two POs at two prices, or `"Monitor"` which scores **100** against `"Monitor arm"` by token-set
ratio while being a different product.

**The rule the code applies first** — a line is *sure* only when all three hold:
1. best description score **> 90** (`RULES_LINE_SURE_ABOVE`), and
2. token-**sort** ratio ≥ **80** (`RULES_LINE_COVERAGE_AT`) — the coverage guard that kills the
   "Monitor" / "Monitor arm" false positive, added after real-world testing, and
3. no other PO line in the pool scores ≥ **70** (`RULES_LINE_CANDIDATE_AT`).

**Everything else goes to Jev.** One request per invoice containing: the whole invoice as read
(**bank details and tax IDs stripped**), every candidate PO in full with quantities left, and the
code's own conclusions including its score. Then **one independent yes/no question per
(not-sure invoice line × candidate PO line)**.

**Jev returns a probability per question, not text.** Bands:

| Answers for one line | Result |
|---|---|
| one or more > **0.80**, none in 0.50–0.80 | those PO lines taken (several → quantity split) |
| every candidate ≤ **0.50** | the line is on no PO line — dropped |
| anything in 0.50–0.80 | Jev is unsure → line stays **unmatched** → invoice held |

**Why an evaluator rather than a second LLM call:**
- A probability is **thresholdable**. "Held because 0.63 fell in the unsure band" is auditable;
  "the model said maybe" is not.
- Questions are **independent**, so there's no ordering bias and no way for one answer to drag
  another.
- The output can't be malformed. No JSON parsing, no retry-on-bad-shape.
- It does one narrow thing: **it picks PO lines.** Be precise about what it does and doesn't see —
  the request *does* carry unit prices and quantities left, and the instruction tells Jev to use
  price as a tie-break when several PO lines order the same item at different prices (that's
  INV-040). What it never does is arithmetic, allocation or a decision. Quantity splitting,
  balance checks, tolerance and the outcome are all code. Bank details and tax IDs are stripped
  from the request entirely.

**Failure is safe by construction.** Error, timeout, no recorded answer, or any earlier blocking
finding → the not-sure lines stay unmatched → `LINES_NOT_MATCHED` (block) → stage 7 skipped →
invoice held. There is no path where a Jev failure approves something.

**Allocation, once Jev has spoken:** a line on several PO lines fills the **oldest PO first** (by
date, then number), each portion up to that line's quantity left, the newest takes the rest. And a
guard: a line may only split across PO lines that order the **same item** (plain similarity ≥ 95 and
identical numbers). Jev picking several *different* items — Mumbai freight and Delhi freight — holds
the line for a person instead.

> **Line:** "The LLM reads, the evaluator picks a line, the code decides. Each model does the one
> thing it's actually reliable at, and neither of them does arithmetic or owns an outcome."

---

## 7. Edge cases, by what they prove

The seed covers **46 invoices** against **13 vendors** and **27 POs**, each with an expected
outcome, vendor, PO and balance in `golden.json`. Every finding code the pipeline raises is
exercised by at least one sample, **except `EXTRACTION_FAILED`** — that one is a model failure, so
it's covered by the test suite rather than by a sample.

### The five to have ready

| Sample | Case | What fires | Why it's non-trivial |
|---|---|---|---|
| **INV-019** | Bank account mismatch | `BANK_ACCOUNT_MISMATCH` (block) | The classic invoice-interception fraud. Everything else matches. Both accounts shown masked; the finding says "verify by phone". |
| **INV-021** | Exact duplicate | `DUPLICATE_EXACT` (reject) | Checked only against **approved** invoices — otherwise an invoice held for a fixable reason could never be corrected and re-run. |
| **INV-034** | Reworded lines | `LINES_MATCHED_BY_JEV` | Scores 74/72, below the sure threshold. Jev confirms. Shows the code→model→code handoff. |
| **INV-035** | Jev not confident | `LINES_NOT_MATCHED`, `PO_UNUSED` | Jev lands in the unsure band, so the line is held. Proves the system refuses rather than guesses. |
| **INV-041** | Inside tolerance | `AMOUNT_WITHIN_TOLERANCE`, `PO_FULLY_INVOICED` | 1,000 over a 98,000 balance, inside the 1,960 tolerance. PO closes at **−1,000** and the finding states the overage. |

### The full map, by stage

| Stage | Sample | Case |
|---|---|---|
| 0 intake | INV-003 | password-protected PDF → `FILE_REJECTED` (reject), nothing else runs, file never saved, model never called |
| 1 extraction | INV-002 | scanned PDF, no text layer, read by vision |
| 2 validation | INV-004 | no invoice number → block (can't dedupe what has no key) |
| | INV-005 | no date *and* no total → warn + block |
| | INV-006 | future date + ambiguous date + no currency → three warnings, still **approved** |
| | INV-007 | line sum and tax sum don't add up → block |
| | INV-008 | credit note, negative amounts → `NON_POSITIVE_AMOUNT`, never auto-processed |
| | INV-015 | GSTIN misprinted → `TAX_ID_FORMAT_INVALID` + `TAX_ID_MISMATCH` |
| i18n | INV-009 | GST-inclusive prices, taxable value derived from the rate |
| | INV-010 | US invoice, 11/04/2024 read **month-first** |
| | INV-011 | German, `14.994,00`, MwSt. 19%, IBAN |
| | INV-012 | UK, VAT-inclusive, no net total printed |
| 3 vendor | INV-013 | not in the master → `VENDOR_NOT_FOUND` (never auto-reject: could be a legitimate new vendor) |
| | INV-014 | suspended → **rejected**, Jev never asked |
| | INV-016 | bank account only, 60 points < 81 → `VENDOR_SCORE_LOW` |
| | INV-017 | name only → `VENDOR_NAME_ONLY` (block) but the vendor id is **kept**, so later checks still run |
| | INV-018 | name close to two vendors, one suspended → `VENDOR_AMBIGUOUS`, refuses to choose |
| 4 bank | INV-020 | no bank details printed, score 100 on GSTIN+name → approved, pays the master account |
| 5 duplicate | INV-023 | re-issued under a new number, same lines/date/amount → `DUPLICATE_SUSPECTED` |
| 6 PO | INV-029 | PO belongs to another vendor → `PO_VENDOR_MISMATCH` |
| | INV-030 | cancelled PO → **rejected**; a printed PO is never handed to Jev |
| | INV-031 | PO number doesn't exist → held; a printed number is **never** replaced by a guess |
| | INV-032 | invoice dated before any PO existed → no pool |
| | INV-028 | one invoice split across **two** printed POs, both balances drop in one step |
| | INV-033 | **no** PO number, lines land on two POs, split without asking Jev |
| | INV-036 | wrong PO printed; only that PO is searched; line unmatched → held |
| | INV-038/039 | same item on two POs → oldest PO filled first, one line split across both |
| | INV-040 | same item, two prices → Jev picks by the invoice's unit price |
| 7 amounts | INV-025 | over-consumption + quantity exceeds order |
| | INV-042 | price raised 10% but fits the balance — only the **line** check catches it |
| | INV-043 | 18% GST on a 12% PO line → `TAX_RATE_MISMATCH` (band 0.1pp) |
| | INV-044 | quantity exceeds what's left after an earlier run — state-dependent |
| | INV-045 | invoice in USD, PO in INR → `CURRENCY_MISMATCH` |
| | INV-046 | a lump-sum line with no unit price → `HEADER_LEVEL_ONLY`, can't split, held |

### The numeric bands, in one place

| Comparison | Band |
|---|---|
| taxable value vs PO balance | `max(2% of remaining, currency floor)` — INR 500, USD/EUR/GBP 10, JPY 1000 |
| unit price vs PO line | 2%, **no floor** |
| tax rate vs PO line | 0.1 percentage points |
| line-sum / tax-sum arithmetic | 1.00 |
| description match (candidate) | token-set ratio 70 |
| description match (sure) | > 90 **and** token-sort coverage ≥ 80 |
| Jev select / reject | > 0.80 / ≤ 0.50 |
| vendor pass score | > 80 (tax_id 80, bank 60, name 30; capped at 100) |

**Why a percentage with a floor:** 2% of 1,000 is 20 — a pure percentage blocks small invoices on
rounding. A flat 500 is huge on 1,000 and nothing on 10,00,000. The greater of the two works at
both ends. And the floor must be **per currency**: a flat 500 is ₹500 or $500 depending on the PO.

**Why the tolerance is one-sided:** invoicing *less* than the balance is a partial delivery, which
is normal. Only going over can mean paying for something not ordered.

**Why compare the taxable value, not the total:** POs are agreed before tax, and the tax on an
invoice depends on things the PO doesn't fix (CGST+SGST intra-state vs IGST inter-state). Comparing
totals would make a correct ₹60,000 + 18% invoice look ₹10,800 over. Tax is checked separately:
rate per line against the PO line, arithmetic in validation.

---

## 8. Concurrency and transactional correctness

### One lock per vendor

Stages 0–4 touch only the run's own document and the (read-only) vendor master, so every run does
them concurrently — **including the model call**, which is the slow part. Stages 5–9 read *and
write* data shared with other runs **of the same vendor**: that vendor's POs and its approved
invoices.

```
A (V001) ─ 0..4 ─► lock[V001] free  ─► 5..9 ─► release ─┐
B (V001) ─ 0..4 ─► lock[V001] taken ─► waits ───────────┴─► 5..9: sees A's approval and PO usage
C (V002) ─ 0..4 ─► lock[V002] free  ─► 5..9, alongside A
```

**Both races were reproduced with the lock disabled:** two copies of one invoice both approved, and
two invoices each fitting a balance that together overdrew it — PO-2024-0142 reached **−12,000.00**.

A run with no matched vendor takes **no** lock: `VENDOR_NOT_FOUND` blocks approval, so it consumes
no PO and never counts in a duplicate check.

> **Line:** "Lock granularity follows data ownership. Every PO belongs to exactly one vendor and
> duplicates are only checked within a vendor, so the vendor id *is* the partition key."

### Stage 9 is one transaction

Four writes commit together: the invoice register row (for any run that produced an extraction,
whatever the outcome), the PO consumption (only if approved), the persist step row, and the run's
closing values. Only **after** the commit are the step and the closing event published. If anything
fails, all four roll back — an approval can never consume a PO without the run recording it, nor the
reverse.

`consume_in` **raises** on a non-positive amount or a negative quantity, so a credit note can never
hand a PO its balance back.

### Reset never corrupts a run in flight

Not by waiting — by construction:
- `record` inserts a step with `INSERT ... SELECT ... WHERE EXISTS (the run)` — one statement, so a
  step of a deleted run is never written.
- The persist transaction's **first write** is the `UPDATE` of the run row, so it takes SQLite's
  write lock before anything else. If it matches no row, the run was reset away and the register
  row, PO usage and persist step are all skipped.

A reset therefore lands entirely before or entirely after a persist. Open SSE streams get a closing
event: "The demo data was reset; this run no longer exists."

### Crash and restart matrix

| Event | Result |
|---|---|
| a stage raises | step stored `failed` with the error type, run closed `error`, summary names the stage |
| storing the failure also fails | the failed step and closing event are still **published**, so the browser stops waiting |
| the model call fails in a known way | not a crash: `EXTRACTION_FAILED` finding, run completes `needs_attention` |
| server stopped mid-run | cancellation is not an `Exception`, so the row stays `running`; the **next startup** sweeps every `running` row to `error` |

**Run `status` and decision `outcome` are two separate columns.** "The process crashed"
(`error`, no outcome) and "the invoice was rejected" (`completed`, `rejected`) are different facts
and must not be conflated.

### Money never touches a float

A custom SQLAlchemy column type, `DecimalText`, writes a `Decimal` as its exact text. SQLite's
dynamic typing never promotes it to a float. All arithmetic is `Decimal`.

The DB work runs in a worker thread via `asyncio.to_thread` — SQLAlchemy's async engine needs
`greenlet`, which isn't a dependency, so plain sessions run off the event loop instead. Honest
trade-off, worth stating as one.

---

## 9. Coding standards

| Standard | How it's enforced |
|---|---|
| Layer boundaries | `scripts/check_imports.py` — AST walk, exits 1, understands `TYPE_CHECKING` (run by hand; no CI in the repo) |
| One adapter per third-party library | same script; `anthropic`, `openai`, `pypdf`, `rapidfuzz`, `typesafe_sdk` each have exactly one importer |
| Lint + format | `ruff`, **14 rule groups**: `E,F,I,B,UP,N,SIM,RUF,ASYNC,PL,C4,PTH,RET,TC`, line length 120, `py314` target |
| Exact dependency pins | `==` on every runtime and dev dependency; `uv.lock` committed |
| Closed schemas | `extra="forbid"` on every wire model, including nested objects |
| Naming convention | file suffixes `_ent` / `_adp` / `_stage` / `_c_v1` / `_s_v1` / `_types`; classes end in `Class` |
| No relative imports | always fails the import checker |
| Exact money | `Decimal` end to end, `DecimalText` column type |
| API versioning | `/v1` in the path, `_v1` in controller/service filenames, `Models.V1` namespaces |
| Wire/internal separation | responses use short `serialization_alias` keys (`po`, `left`, `ms`); internal field names never leak except inside a step's raw `data` |
| One regression test per defect | `test_regressions.py` — ~20 tests, each written to fail before its fix |

---

## 10. Testing strategy

All four verified green on 2026-11-13, immediately before this document was written:

```
uv run ruff check .                     # All checks passed!
uv run ruff format --check .            # 93 files already formatted
uv run python scripts/check_imports.py  # Import rules: OK
uv run pytest -q                        # 379 passed in 9.14s
uv run python scripts/run_golden.py     # ⚠️ RESETS THE DB — never during a demo
```

- **379 tests from 213 test functions** (the rest are parametrized cases), **9 seconds**, **no
  network**. `conftest.py` forces a temp DB, a temp upload dir, `LLM_MODE=recorded` and zero replay
  delay.
- The live adapter is tested with a **stub client** in place of the SDK: request shape (document vs
  image block, cache marker), refusal, truncation, unparseable output, one retry on rate limit, a
  second connection failure raising, status errors carrying the status.
- The Jev adapter is tested by running the **real SDK against a fake server** through an injected
  `httpx2` transport — so the SDK's own serialization is exercised, not mocked away.
- `test_pipeline.py` runs **all 46 samples** end to end against `golden.json` and asserts
  structural invariants: no `reasons` column, no `usage` column, the decision step stores only
  `update`, derived reasons equal what `decide` returned, a run stores only ids and history
  resolves names through them.
- `test_recorded.py` asserts on the **emitted SQL** that listing samples never selects the deferred
  `pdf` / `reading` columns, and that the sample-run route issues exactly one query.
- `run_golden.py` is the acceptance test: reset, run all 46 in step order through the real
  pipeline, compare outcome + vendor + PO + balance + required codes, print a PASS/FAIL table. With
  a key it becomes the live extraction test.

> **Line:** "The golden file is a contract. If a rule change moves a balance by a rupee, 46 rows
> tell me which one and why."

---

## 11. Known limits — state these before you're asked

**Verification**
- Live extraction was verified on 2026-11-13 (`gpt-6-luna`, low effort) against the samples that
  existed **then** — `DECISIONS.md` §25 says 14, an older README said 24. There are now **46**, so
  the current set has **not** all been run live. Either re-run `run_golden.py` with a key before the
  interview, or say plainly which subset was verified.
- Two of those were compared field by field with the answer key at 6–8 s each. Everything matched
  except the letter case of one supplier name and a `"PO Reference: "` label kept in front of the
  PO number — neither changes a result.
- Performance on real, messy, photographed invoices is **unverified**. This is the honest headline
  limit.
- The recorded readings are generated from the same spec as the PDFs. They show what a *correct*
  reading looks like, not what a model produces. Don't claim them as model output. (The **Jev**
  recordings are different — those are real live answers captured with `JEV_RECORD`.)

**Matching**
- Two-way match only (invoice vs PO). No goods receipt, so no three-way match.
- No currency conversion — a currency difference is held, not converted.
- PO numbers must be `PO-YYYY-NNNN` (or bare `NNNN-NNNN`). An SAP-style `4500012` gets
  `PO_NOT_FOUND`.
- Descriptions are compared as text, so a PO in one language and an invoice in another match badly.
- GSTIN is a **shape** check only — the check digit isn't computed and nothing is looked up in the
  GST registry. Other countries' tax IDs are matched against the master but not format-checked.
- An approval can take a PO negative within tolerance (INV-041 → −1,000) and it's then
  `fully_invoiced` with a negative balance. Deliberate, and visible on the dashboard.
- Indian `1,200/-` parses as −1200, because a trailing minus marks a negative. Known, documented.

**Operations**
- No authentication. Anyone who can reach the backend can upload, read every run and reset.
- The vendor locks, the event bus and the task set live in **one process**. Running with multiple
  workers would give each its own locks — two workers could approve the same invoice twice. Scaling
  out means moving locks to the DB (`SELECT ... FOR UPDATE` on the vendor row) or to Redis.
- No DB migrations — `create_all` only adds missing tables, never columns.
- No paging on run history; the dashboard refetches everything every 4 s.
- Deployment config exists for Railway (backend, Docker, healthcheck on `/v1/admin/meta`) and
  Vercel (frontend, rewriting `/v1` to the Railway host). ⚠️ **Open the deployed URL and confirm
  it's actually live before you claim it** — config files are not a running service.

---

## 12. Questions you will be asked, with one-line answers

**"Why not let the LLM make the decision?"**
> Same input must give the same answer, a finance user must change the tolerance in a config file
> rather than a prompt, and an auditor needs "this rule fired on these numbers". The model reads,
> the rules decide, a person handles what the rules can't settle.

**"Then why is there a second model?"**
> Because one judgement genuinely isn't rule-shaped: is "Ergo footrest pad" the same item as
> "Ergonomic footrest"? That's semantics. But I don't ask it in free text — Jev returns a
> probability per independent yes/no question, which I threshold. It picks PO lines and nothing
> else. It does see unit prices, because two PO lines can order the same chair at two prices and
> the invoice's price is the only way to tell them apart — but it does no arithmetic and decides
> no outcome.

**"What happens when the model is wrong?"**
> Three containments. Extraction failure is a finding, not a crash, so the run completes as
> needs_attention. A Jev failure or unsure answer leaves the line unmatched, which blocks. And every
> extracted value is re-checked by arithmetic — line sums, tax sums, taxable vs total — so a misread
> number usually surfaces as a mismatch rather than a payment.

**"Why one model call per invoice?"**
> Reading a document of arbitrary layout, including a scan, is the one job that needs a model.
> Everything after is comparison on known values, which code does exactly and for free. One call
> also means one cost, one failure point, one retry.

**"How do you handle hallucination?"**
> The schema is closed and all-required, so there's nowhere to invent a field. Empty string means
> "not printed" — the prompt forbids guessing. Six fields carry a verbatim source quote, shown in
> the UI, and the date logic actually cross-checks the model's value against its own quote. And no
> extracted number is ever trusted on its own: it has to survive validation arithmetic.

**"Why does a name-only vendor match block?"**
> A name is not an identity. With my own matcher, "Acme Supplies Trading Co" scores 100 against
> "Acme Supplies Pvt Ltd" — suffixes are dropped and token-set treats a superset as a full match.
> The tax ID and bank account were verified at onboarding; a name wasn't.

**"Why is a changed bank account a block and not a warning?"**
> It's the single highest-value fraud in AP. Everything else on the invoice is correct by design —
> that's what makes it work. The finding tells the reviewer to confirm by phone, not by email,
> because the email is what's compromised.

**"Why check duplicates only against approved invoices?"**
> Only an approved invoice can be paid twice. If held invoices counted, an invoice held for a
> fixable reason could never be corrected and re-run — it'd be a duplicate of its own earlier
> attempt.

**"Why do later stages run after something already blocked?"**
> So the reviewer sees every problem in one pass. Otherwise they fix the vendor, re-run, and only
> then discover the PO is over-consumed. The block still decides the outcome; nothing is approved
> because later checks passed.

**"Why a lock per vendor and not a global lock?"**
> The contended data — PO balances and approved invoices — is always one vendor's. And the slow
> part, the model call, touches no shared data, so it stays outside the lock entirely. Global would
> serialize every extraction for no benefit.

**"How would you scale this?"**
> Three changes. Move locks from `asyncio.Lock` to a row lock in Postgres, so workers share them.
> Move the pipeline from `asyncio.create_task` to a real queue so a crash doesn't lose in-flight
> runs. Replace the in-process event bus with Redis pub/sub so any worker can serve any SSE stream.
> The layering means none of that touches the entities.

**"Where would it break on real invoices?"**
> Non-`PO-YYYY-NNNN` numbering, which is most ERPs. Cross-language line descriptions. Multi-page
> invoices with continuation tables. And the tolerance/threshold constants are tuned to my seed
> data — on a real tenant I'd want them per-vendor and backed by a sample of historical decisions.

**"How do you know it works?"**
> 46 invoices with expected outcome, vendor, PO and balance, replayed through the real pipeline by
> a golden script. 379 tests in 9 seconds with no network. And every defect found in review has a
> regression test that failed before its fix.

---

## 13. Numbers cheat sheet

| | |
|---|---|
| Stages | **10** (0–9); vendor lock around **5–9** |
| Finding codes | **45** defined, **42** raised (`PO_INFERRED`, `PO_AMBIGUOUS`, `PO_SELECTED_BY_JEV` are vestigial) |
| Reject set | 4: `FILE_REJECTED`, `VENDOR_SUSPENDED`, `PO_CANCELLED`, `DUPLICATE_EXACT` |
| Seed invoices | **46**, all in `golden.json` |
| Vendors / POs | **13** / **27** |
| Tests | **379 passed in 9.14 s**, 213 test functions, no network |
| Backend | 7,318 LOC `app/`, 4,369 LOC `tests/` |
| Frontend | 4,105 LOC `src/` |
| Runtime deps | 11, all exact-pinned |
| Live extraction | OpenAI Responses API, `gpt-6-luna`, effort `low` (Anthropic adapter also present, `LLM_PROVIDER` picks) |
| Jev | `jev-1.13.0`, 10 s timeout, no retry |
| Vendor weights | tax_id 80, bank 60, name 30; pass **> 80** |
| Tolerance | `max(2%, floor)` — INR 500 / USD·EUR·GBP 10 / JPY 1000 |
| Line thresholds | sure > 90 **and** coverage ≥ 80 **and** no rival ≥ 70 |
| Jev bands | select > 0.80, drop ≤ 0.50, else held |
| Intake limits | 20 MB, 10 pages, PDF/PNG/JPEG by magic bytes |

---

## 14. What was corrected in the README (2026-11-13)

`README.md` had drifted from the code as the PO matching was rewritten and samples were added. It
has been **fixed** — the table below is the record of what changed, so you can recognise an older
copy and answer confidently if an interviewer quotes one.

| The old README said | The code says, and the README now says |
|---|---|
| "nine-stage pipeline" (§1) | ten stages, 0–9 |
| detailed headers "Stage 4: PO matching", "Stage 6: duplicate" | 4 is bank, 5 is duplicate, 6 is PO |
| "35 finding codes" | 45 defined, 42 raised |
| "fourteen sample PDFs", "24 samples", "twenty samples" | 46 |
| "One model call" (§11) | two models: extraction LLM + Jev evaluator |
| "inferred PO needs two signals", "`PO_INFERRED`" | superseded by line-based matching on 2026-11-14 |
| "One invoice maps to one PO… not supported" (§13) | INV-028/033/039 split across POs |
| "Where: `RulesEntityClass.validate` / `.decide`" | `rules_ent.py` is 67 lines of policy; those live in `validation_stage.py` and `decision_stage.py` |
| "Not hosted. It runs on a local machine only." | `railway.json` + `vercel.json` exist (confirm the URL is live) |
| `LLM_MODEL=claude-opus-5-5` implies Anthropic | `LLM_PROVIDER=openai` → `gpt-6-luna` actually runs |
| `BANK_ACCOUNT_MISMATCH` listed under stage 3 vendor | it is raised by stage 4 bank; `BANK_ACCOUNT_VERIFIED` was missing from the code table entirely |
| 10 vendors, 20 POs in the seed tables | 13 vendors, 27 POs |
| the decision returns "three gates: vendor, po, amount" | **four**: vendor, **bank**, po, amount (`decision_stage.py::_gates`) |
| extraction is "the only stage that uses a model" | stage 6 uses one too; extraction is the only *generative* call |
| the lock covers "stages 4 to 8" | stages **5 to 9** (`_check` + `_finish`) |
| the adapter registry, adapter table and file map had no Jev | `typesafe_adp.py`, `jev.env` and `jev_recordings.json` now listed |
| "The live adapter (`AnthropicAdapterClass`)" | both adapters documented; the OpenAI one is the configured default |
| five test files undocumented | `test_assist`, `test_jev_triggers`, `test_jev_real_world`, `test_openai`, `test_typesafe` now in §12 |

Beyond that list, the fix also added a **Stage 4: bank validation** section (the stage had none at
all), moved the duplicate section ahead of PO matching so the document reads in execution order,
rebuilt the finding-code table with the right stage numbers and the three vestigial codes marked,
and extended the seed tables to all 13 vendors and 27 POs.

If an interviewer asks why it drifted: the PO matching was redesigned on 2026-11-14 from weighted
inference to line-by-line matching, and the prose describing the old design outlived it. That is a
fair thing to own — the code, the tests and the golden file were all updated together; only the
narrative lagged.

---

## 15. Recording checklist

- [ ] `uv run pytest -q` run in a terminal tab, output visible for beat 6
- [ ] Reset demo data **before** staging anything (it deletes hand-made vendors and POs)
- [ ] Pre-run **only** INV-019, INV-034, INV-041 so those clicks are instant
- [ ] Leave INV-001 **and** INV-021 unrun — INV-001 must run before INV-021 or the duplicate demo
      inverts (see the staging warning at the top of Part A)
- [ ] Dashboard open in a second tab for beat 5
- [ ] Mode badge visible in the first frame (say "recorded" or "live" — don't let them wonder)
- [ ] Do **not** run `scripts/run_golden.py` on camera — it resets the database
- [ ] Rehearse twice with a timer; the trim list is at the end of Part A
