# PS-1 Build Spec (frozen contracts)

Written 2026-11-12. This is the technical plan the code is built from. PLAN.md explains why;
this file pins what: every model, code, endpoint, wire shape and seed value. If code and this file
disagree, this file wins until it is deliberately changed.

## 0. Ground rules

- Architecture follows a MACH layering: Client → Controller → Service → Entity → Adapter, with these
  conventions (file naming, `_Types` / `_Helper` / `[Name]EntityClass`, registries, imports through
  package `__init__`, no loose module-level functions, no class-level variables, `Final` constants
  on `self`, docstrings state contract only, no WHAT comments, sections separated by blank lines,
  `Literal` for fixed string sets, at most two words per variable name, no manual trailing commas).
- One-way imports, enforced by `scripts/check_imports.py`:
  - adapters import only `app.config` and third-party libraries;
  - entities import only `app.adapters` (through its `__init__`), `app.entities._types`,
    `app.entities.prompts`, `app.config`, the standard library, pydantic and sqlalchemy;
  - services import only `app.entities` (through its `__init__`) and their own router package;
  - controllers import only their own router's services, models and config.
- Every third-party library other than pydantic / sqlalchemy / fastapi is wrapped by one adapter.
- Python 3.14, uv, every dependency pinned with `==`. Do not edit `pyproject.toml` or `uv.lock`
  unless you own them (section 9).
- No files outside the project folder.

## 1. Layout

```
zamp-ps1/
  backend/
    pyproject.toml  .env.example  .gitignore
    app/
      main.py                        FastAPI app, lifespan, CORS, router mounting
      config/
        __init__.py                  exports ENVConfigs
        env_config.py                pydantic-settings groups: Llm, Rules, Storage
        llm.env  rules.env  storage.env      non-secret values (secret key comes from backend/.env)
      adapters/
        __init__.py                  AdapterRegistry + AdapterTypes
        db_adp.py                    DBAdapterClass: SQLite engine, Base, run(work) in a worker thread, create_tables()
        llm_adp.py                   AnthropicAdapterClass.extract()            (only importer of anthropic)
        openai_adp.py                OpenAIAdapterClass.extract()               (only importer of openai)
        match_adp.py                 MatchAdapterClass: fuzzy ratio            (only importer of rapidfuzz)
        pdf_adp.py                   PdfAdapterClass: inspect bytes            (only importer of pypdf)
        store_adp.py                 StoreAdapterClass: save / read uploaded files, read seed files (seeding only)
      entities/
        __init__.py                  EntitiesRegistry
        _types/
          __init__.py
          extraction_types.py        InvoiceRead (LLM wire) + InvoiceExtraction (normalised)
          finding_types.py           Finding, FindingCode, Severity
          stage_types.py             IntakeResult ... Decision, StepEvent, statuses
        prompts/
          system-extract.md  context-extract.md
        prompt_ent.py                loads the pair once, fills placeholders
        rules_ent.py                 AP policy shared by stages: tolerance(), gstin_shaped(), reject codes, bands,
                                     ordered() (reason order)
        vendor_ent.py                VendorDB + create(), records(), names(), tolerance_pct()
        order_ent.py                 OrderDB (PO header + lines) + create(), snapshots(), map_lines(), map_split(),
                                     consume_in()
        invoice_ent.py               InvoiceDB (register) + register_in(), approved(), briefs()
        stages/                      one module per stage; the logic of that check only
          intake_stage.py            0  accept / reject the file, store it
          extraction_stage.py        1  live: calls the LLM adapter; recorded: replays the seed entity's reading for
                                        the document's SHA-256; converts InvoiceRead -> InvoiceExtraction
          validation_stage.py        2  taxable value, arithmetic checks, priced lines
          vendor_stage.py            3  vendor matching (identity ladder, suspension, tax ID veto)
          bank_stage.py              4  payee account vs vendor master
          duplicate_stage.py         5  already approved under this number, or a re-send
          po_stage.py                6  PO matching by code (printed / several / inferred)
          po_jev.py                  6  part 2: Jev selects the POs when the code's match is vague
          amounts_stage.py           7  one PO or split, every line and balance
          decision_stage.py          8  outcome, summary, four gates, PO updates
          persist_stage.py           9  one transaction: close, register, consume
        run_ent.py                   RunDB, StepDB + start / record / finish / list / event bus
        seed_ent.py                  SeedDB.Samples (scenario + PDF bytes + digest + reading); seeds and reset()s
                                     samples, vendors and POs; sample(id) (id + PDF, one query), recording(document)
        pipeline_invoice.py          stage order 0-9; the only file that knows it (orchestration only)
      routers/
        __init__.py
        run/     controllers/run_c_v1.py     services/run_s_v1.py     run_models.py    config.py
        order/   controllers/order_c_v1.py   services/order_s_v1.py   order_models.py  config.py
        admin/   controllers/admin_c_v1.py   services/admin_s_v1.py   admin_models.py  config.py
    data/
      vendors.csv  purchase_orders.csv  po_lines.csv  samples.json  golden.json
      invoices/INV-NNN.pdf  invoices/INV-NNN.read.json    (seed only: copied into the samples table)
    scripts/
      make_invoices.py  run_golden.py  check_imports.py
    tests/
      test_rules.py  test_vendor.py  test_order.py  test_extraction.py  test_pipeline.py  test_schema.py
  frontend/                          Vite + React + TypeScript + Tailwind
```

"PO" is called `order` in code (router names must be one singular word).

## 2. Settings

`backend/.env` (secret, gitignored): `ANTHROPIC_API_KEY=` and / or `OPENAI_API_KEY=`.

| Group | Field | Default |
|---|---|---|
| Llm | `mode` | `auto` (`live` when a key is set, else `recorded`); may be forced to `live` / `recorded` |
| Llm | `provider` | `openai` in `llm.env` (default `auto`: Anthropic, unless only `OPENAI_API_KEY` is set); or `anthropic` |
| Llm | `model` | `claude-opus-5-5` (Anthropic) |
| Llm | `openai_model` | `gpt-6-luna` (OpenAI) |
| Llm | `effort` | `low` |
| Llm | `cache_ttl` | `5m` (`1h` on demo day) |
| Llm | `max_tokens` | `16000` (validated ≤ 20000; larger values need streaming) |
| Llm | `replay_delay` | `0.8` seconds (recorded mode only, so the live view is visible) |
| Rules | `tolerance_pct` | `2.0` |
| Rules | `tolerance_floors` | `{"INR": 500, "USD": 10, "EUR": 10, "GBP": 10, "JPY": 1000}` (by PO currency; unlisted = no floor) |
| Rules | `pass_score` | `80` (a vendor or PO is matched only when its weights add up to MORE than this; sums are capped at 100) |
| Rules | `vendor_weights` | `{"tax_id": 80, "bank_account": 60, "name": 30}` (every key required; a name alone must stay below `pass_score`) |
| Rules | `line_sure_above`, `line_candidate_at`, `po_pool_max` | `90`, `70`, `10` (PO matching by lines) |
| Rules | `max_pages` | `10` |
| Rules | `max_bytes` | `20971520` |
| Storage | `db_path` | `var/ps1.sqlite3` |
| Storage | `upload_dir` | `var/uploads` |
| Storage | `data_dir` | `data` |
| Storage | `cors_origin` | `http://localhost:5173` |

## 3. Models

All Pydantic v2. Shared models live in `entities/_types`.

### 3.1 LLM wire model: `InvoiceRead` (extraction_types.py)

The only schema the model sees. Every field is required; text fields are plain strings and the
model writes `""` when a value is absent; amounts are copied **exactly as printed** as strings.
No optional fields, no unions, no open objects. This keeps the schema safe under structured-output
limits, and moves number parsing into code. Every field has a `description` (TYP-3 allows it here).

```
InvoiceRead
  supplier_name, supplier_tax_id, supplier_address, supplier_email: str
  bank_account_name, bank_account_number, bank_code, bank_name: str     (supplier's bank details)
  buyer_name, buyer_tax_id: str
  invoice_number: str
  invoice_date, due_date: str          ISO YYYY-MM-DD, or ""
  currency: str                        ISO 4217 code, or ""
  po_reference_text: str               any order reference, verbatim, or ""
  prices_include_tax: bool             true when line amounts / total are tax-inclusive with no separate subtotal
  lines: list[LineRead]                LineRead: description, quantity, unit, unit_price, line_amount, tax_rate_pct (all str)
  subtotal: str
  tax_lines: list[TaxRead]             TaxRead: label, rate_pct, amount (all str)
  total: str
  sources: list[SourceRead]            SourceRead: field, text   (quoted source text for invoice_number, invoice_date, subtotal, total, po_reference_text)
```

### 3.2 Normalised model: `InvoiceExtraction` (extraction_types.py)

Produced in code by `stages/extraction_stage.py` from `InvoiceRead`. Everything nullable.

```
Party        name, tax_id, address, email: str | None
BankDetails  account_name, account_number, bank_code, bank_name: str | None
LineItem     description: str | None; quantity, unit_price, line_amount, tax_rate_pct: Decimal | None; unit: str | None
TaxLine      label: str | None; rate_pct, amount: Decimal | None
SourceQuote  field: str; text: str
InvoiceExtraction
  supplier: Party; supplier_bank: BankDetails; buyer: Party
  invoice_number: str | None; invoice_date, due_date: date | None; currency: str | None
  po_reference_text: str | None; prices_include_tax: bool
  lines: list[LineItem]; subtotal: Decimal | None; tax_lines: list[TaxLine]; total: Decimal | None
  sources: list[SourceQuote]
```

Conversion rules: `""` → `None`. Amounts are parsed in the document's style from
`decimal_separator`: with `","` dots and spaces group and the comma is the decimal point
(`1.249,50`); otherwise commas group (Indian or Western) and a European-looking form (`36.000,00`)
→ `None`, never a guess. Parentheses or a minus sign outside the digits mean negative.
Dates: the model's YYYY-MM-DD value; when the field's source quote prints the same date numerically,
code settles the order from `date_order` (or from a part above 12), and an order the document does
not show is read day first and listed in `ambiguous_dates` (`DATE_AMBIGUOUS`, warn). Month names
are accepted; anything else → `None`.

### 3.3 Findings (finding_types.py)

```
Severity    = Literal["info", "warn", "block"]
FindingCode = Literal[
  "FILE_REJECTED", "EXTRACTION_FAILED",
  "MISSING_INVOICE_NUMBER", "MISSING_TOTAL", "MISSING_DATE", "DATE_IN_FUTURE",
  "LINE_SUM_MISMATCH", "TAX_SUM_MISMATCH", "TAX_ID_FORMAT_INVALID", "TAXABLE_VALUE_DERIVED", "NON_POSITIVE_AMOUNT",
  "VENDOR_IDENTIFIED", "VENDOR_NOT_FOUND", "VENDOR_AMBIGUOUS", "VENDOR_NAME_ONLY", "VENDOR_SCORE_LOW", "VENDOR_SUSPENDED",
  "TAX_ID_MISMATCH", "BANK_ACCOUNT_MISMATCH",
  "PO_MATCHED", "PO_NOT_FOUND", "PO_VENDOR_MISMATCH", "PO_CANCELLED", "PO_INFERRED", "PO_AMBIGUOUS",
  "CURRENCY_MISMATCH", "AMOUNT_WITHIN_TOLERANCE", "PARTIAL_DELIVERY", "PO_FULLY_INVOICED",
  "OVER_CONSUMPTION", "QTY_EXCEEDS_ORDERED", "UNIT_PRICE_MISMATCH", "TAX_RATE_MISMATCH", "HEADER_LEVEL_ONLY",
  "NO_DUPLICATE", "DUPLICATE_EXACT", "DUPLICATE_SUSPECTED",
]
Finding  code: FindingCode; severity: Severity; message: str; data: dict[str, str]
```

`message` is a complete sentence an AP clerk could read, with the numbers in it. `data` holds the
same numbers as strings. Reject set: `FILE_REJECTED, VENDOR_SUSPENDED, PO_CANCELLED, DUPLICATE_EXACT`.

### 3.4 Stage results (stage_types.py)

```
StepName    = Literal["intake","extraction","validation","vendor","po","amounts","duplicate","decision","persist"]
StepState   = Literal["running","done","skipped","failed"]
RunStatus   = Literal["running","completed","error"]
Outcome     = Literal["approved","needs_attention","rejected"]
LlmMode     = Literal["live","recorded"]                 (how documents are read; GET /v1/admin/meta)
RunMode     = Literal["live","recorded","manual"]        (a run's mode; manual = a typed invoice)
Usage            input, cache_read, cache_write, output: int          (defined in adapters; re-exported)
IntakeResult     filename: str | None; media_type: str; size_bytes: int; page_count: int | None; encrypted: bool; text_layer: bool | None; accepted: bool; findings
ExtractionResult extraction: InvoiceExtraction | None; usage: Usage | None; mode: RunMode; model: str; seconds: float; findings
ValidationResult taxable_value: Decimal | None; tax_total: Decimal | None; tax_rate_pct: Decimal | None; derived: bool; findings
VendorCandidate  vendor_id, legal_name: str; score: float; basis: Literal["name","weights"] (name similarity, or the weighted score when identifiers point to two vendors)
VendorSignal     signal: Literal["tax_id","bank_account","name"]; state: Literal["matched","mismatched","absent"]; weight, points: float
VendorMatch      vendor_id: str | None; legal_name: str | None; method: Literal["tax_id","bank_account","name_fuzzy","none"]; score: float (sum of matched weights, capped at 100; 0 when no vendor is chosen); needed: float (pass_score); evidence: list[VendorSignal]; candidates: list[VendorCandidate]; findings
OrderCandidate   po_number: str; remaining: Decimal; signals: list[str]; score: float
OrderMatch       po_number: str | None; others: list[str] (further POs of an invoice that prints several; empty otherwise); method: Literal["explicit","inferred","none"]; signals: list[str]; score: float; needed: float; candidates: list[OrderCandidate]; findings
LineResult       description: str; po_number: str | None (set only on a split invoice); po_line: int | None; quantity, ordered, consumed_before, unit_price, po_price: Decimal | None; ok: bool
AmountPart       po_number: str; invoice_amount, remaining_before, variance, tolerance: Decimal; within: bool
AmountCheck      basis: Literal["taxable_value"]; invoice_amount, remaining_before, variance, tolerance: Decimal; within: bool; level: Literal["line","header"]; lines: list[LineResult]; findings; parts: list[AmountPart] (one per PO of a split invoice; empty otherwise; the amounts above are then totals over the POs)
DuplicateCheck   verdict: Literal["none","exact","suspected"]; prior_run: str | None; findings
OrderUpdate      po_number: str; amount: Decimal; remaining_after: Decimal; quantities: dict[int, Decimal]   (po line number -> qty)
Gate             name: Literal["vendor","po","amount"]; passed: bool; detail: str
Decision         outcome: Outcome; summary: str; reasons: list[Finding] (these three in memory only, excluded from the stored step data); gates: list[Gate]; update: OrderUpdate | None; others: list[OrderUpdate] (the further POs of a split invoice)
StepEvent        run_id: str; name: StepName; state: StepState; millis: int; data: dict; findings: list[Finding]
RunClosing       status: RunStatus; outcome: Outcome | None; summary: str; vendor_id: str | None; po_number: str | None; invoice_id: int | None; millis: int
```

`findings` is `list[Finding]` on every result, and `steps.findings` is the only place findings are
stored. `Decision.reasons` is every finding of the run, ordered by `RulesEntityClass.ordered`:
reject-set blocks, other blocks, warns, infos, stable by stage order within a group. The run detail's
`reasons` is rebuilt on read from the stored steps with the same method. `summary` is one sentence that does not repeat the outcome word for
needs-attention and rejected runs (the UI shows the outcome beside it), for example
"Approved as partial delivery; 24,000.00 remains open on PO-2024-0142." or "Invoice … was already
registered by an earlier run; it must not be paid twice."

## 4. Stage rules

Tolerance for a PO: `tol = max(pct / 100 * remaining, floor)`, `pct` = vendor override or setting.

**0 Intake** (`pipeline` via `PdfAdapter`, `StoreAdapter`). Accept `application/pdf`, `image/png`,
`image/jpeg`. Reject (finding `FILE_REJECTED`, run ends `rejected`, no later stage): unknown type,
over `max_bytes`, unreadable, encrypted, over `max_pages`. `text_layer` is true when any page has
extractable text (shown in the UI as "scanned, no text layer" when false).

**1 Extraction** (`stages/extraction_stage.py`). One call to the LLM adapter with `response_model=InvoiceRead`,
then convert to `InvoiceExtraction`. Any adapter failure → `EXTRACTION_FAILED` (block); stages 2-6
are marked `skipped`; decision is `needs_attention`.

**2 Validation** (`stages/validation_stage.py`).
- `MISSING_INVOICE_NUMBER` block; `MISSING_DATE` warn; `DATE_IN_FUTURE` warn.
- `NON_POSITIVE_AMOUNT` block when the taxable value is zero or negative, or any line quantity or
  line amount is negative (a credit note or a misread: never auto-processed, never consumed).
- Taxable value: `subtotal` when present; else `total - sum(tax amounts)` when tax lines carry
  amounts; else `total / (1 + rate/100)` when a rate is known. The last two set `derived` and add
  `TAXABLE_VALUE_DERIVED` (info). No total or no taxable value → `MISSING_TOTAL` block.
- `tax_rate_pct` = sum of tax line rates, else `tax_total / taxable * 100` rounded to 2 places.
- `LINE_SUM_MISMATCH` block when not derived and `|sum(line_amount) - taxable| > 1`.
- `TAX_SUM_MISMATCH` block when subtotal, tax and total are all present and `|subtotal + tax - total| > 1`.
- `TAX_ID_FORMAT_INVALID` warn when the supplier tax ID is 15 characters and fails
  `^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$`.

**3 Vendor** (`stages/vendor_stage.py`). Normalise tax ID and account numbers (uppercase, strip
non-alphanumerics).
Every master vendor is scored on three printed fields; a field that equals the master earns its
weight from `vendor_weights` (tax ID 80, bank account 60, name 30), a field that differs or is not
printed earns 0, and the sum is capped at 100. The name counts as equal at a fuzzy ratio ≥ 90. The
bank account counts as equal only when the bank code also agrees (one signal, not two).
1. A vendor is a contender when its tax ID or bank account matched. No contender → step 3.
2. Top contender, by score: score > `pass_score` → `VENDOR_IDENTIFIED` info, method `tax_id` when
   the tax ID matched, else `bank_account`. Score ≤ `pass_score` (one identifier with nothing to
   back it: tax ID alone is 80, bank account alone is 60) → the vendor id is kept and
   `VENDOR_SCORE_LOW` blocks. A second contender that ties with the first or also clears
   `pass_score` → `VENDOR_AMBIGUOUS` block, no id.
3. Name only (token-set ratio on names with suffixes stripped: pvt, ltd, limited, private, inc,
   llc, llp, co, corp, technologies, india): best ≥ 90 and no other ≥ 85 → `name_fuzzy`, score 30,
   plus `VENDOR_NAME_ONLY` block (a name alone is never enough to pay); else any ≥ 70 →
   `VENDOR_AMBIGUOUS` block (candidates listed, no id); else `VENDOR_NOT_FOUND` block.
4. Vetoes on the chosen vendor, at any score: status `suspended` → `VENDOR_SUSPENDED` block; the
   invoice prints a tax ID that differs from the master → `TAX_ID_MISMATCH` block. (The payee
   account is checked in its own stage, below.)

**3b Bank** (`stages/bank_stage.py`, step `bank`). Skipped when no vendor was matched. Compares
the account the invoice asks to be paid to with the vendor master's: different → `BANK_ACCOUNT_MISMATCH`
block at any vendor score (message names both accounts, last 4 digits visible); equal →
`BANK_ACCOUNT_VERIFIED` info; none printed → `BANK_DETAILS_ABSENT` info (payment goes to the master
account). Returns `BankCheck` (`state` matched / mismatched / absent, both accounts masked).

**4 PO, matched by lines** (`stages/po_stage.py`, Jev part `stages/po_jev.py`). One rule for any number of POs
(design: docs/superpowers/specs/2026-11-14-po-matching-by-lines-design.md).
- **Pool.** Find `(?<![A-Za-z0-9])PO[-\s/]?(\d{4})[-\s/]?(\d{4})(?!\d)` (case-insensitive) in the reference text, then in
  line descriptions; also a bare `(?<![\dA-Za-z-])(\d{4})-(\d{4})(?![\d-])` in the reference text only; canonical
  `PO-YYYY-NNNN`. Printed numbers (one or several): each must exist (else `PO_NOT_FOUND` cause `refused`), belong to
  the vendor (else `PO_VENDOR_MISMATCH`) and not be cancelled (else `PO_CANCELLED`); any refusal blocks with no
  pool. Pool = the printed POs. Nothing printed: a PO-like mistyped reference → `PO_NOT_FOUND` cause
  `bad_reference`; unknown vendor → cause `no_vendor`; no lines → cause `no_lines`; else pool = the vendor's `open` /
  `partially_invoiced` POs dated on or before the invoice with something left, newest first, at most
  `RULES_PO_POOL_MAX` (10); empty → cause `no_open_po`.
- **Lines.** Every invoice line is scored against every line of every pool PO by description (token-set ratio). The
  code is sure of a line when its best score is above `RULES_LINE_SURE_ABOVE` (90) and the match also covers most of the PO description (token-sort ratio at or above `RULES_LINE_COVERAGE_AT`, 80), so a short word inside a different item ("Monitor" in "Monitor arm") is never matched without Jev and no other pool line scores at
  or above `RULES_LINE_CANDIDATE_AT` (70). Price, quantity and tax never choose a line.
- **Jev** decides every line the code is not sure about, in one request with the whole invoice (no bank details or
  tax IDs), every pool PO in full and the code's conclusions: one yes/no per not-sure line and pool line with
  something left. Lines above 0.80 are chosen (one or several); all at or below 0.50 means the line is on no PO line;
  anything in between leaves it unmatched. Asked only when no earlier stage blocked. No answer → unmatched.
- **Allocations.** A settled line becomes (PO, PO line, quantity); several PO lines fill the oldest PO first, each
  up to its quantity left, the newest takes any rest. A line may draw on several PO lines only when they order the same item (near-identical descriptions, plain similarity ≥ 95, and exactly the same numbers); Jev choosing several different items holds the line for a person (decided by the user, 2026-11-14). The POs used are the POs the lines land on (`po_number` the
  first printed PO when printed, else the first used PO by date; `others` the rest).
- **Findings.** All lines settled → `PO_MATCHED` (info). Jev settled some → `LINES_MATCHED_BY_JEV` (info). Any line
  unmatched or dropped → `LINES_NOT_MATCHED` (block) and stage 5 is skipped. A printed PO no line landed on →
  `PO_UNUSED` (block). Amounts never choose a PO; stage 5 checks them afterwards.

**5 Amounts** (`stages/amounts_stage.py`). Skipped when there is no matched PO or no taxable value.
- Invoice currency present and different from the PO currency → `CURRENCY_MISMATCH` block.
- `variance = taxable - remaining`. `variance > tol` → `OVER_CONSUMPTION` block. Otherwise
  `AMOUNT_WITHIN_TOLERANCE` info, plus `PO_FULLY_INVOICED` info when `taxable >= remaining`,
  else `PARTIAL_DELIVERY` info stating what stays open. One-sided: invoicing less than the balance is never an error.
- Line mapping: an invoice line maps to the PO line with the best description ratio when that
  ratio ≥ 70; otherwise, when the PO has exactly one line and the unit price is within 0.5 percent,
  to that line. Any unmapped invoice line (or no lines) → `HEADER_LEVEL_ONLY` block, level `header`,
  no line checks; the message says unit price and quantity could not be verified.
- Per mapped line: `consumed_qty + quantity > ordered` → `QTY_EXCEEDS_ORDERED` block; unit price
  differs from the PO price by more than `pct` percent → `UNIT_PRICE_MISMATCH` block; the line's tax
  rate (or, only when the line has none, the invoice rate from validation) differs from the PO line rate by more than 0.1 →
  `TAX_RATE_MISMATCH` block.

**6 Duplicate** (`stages/duplicate_stage.py`). Runs right after the bank stage, before PO matching (it needs no PO, and an
invoice already registered must never reach Jev). Skipped when the vendor is unknown. Register = stored
invoices whose outcome is `approved` (a held or rejected invoice that is corrected and re-run
must not be blocked by its own earlier attempt). Number normalised: uppercase, strip spaces, `-`, `/`.
- Same vendor and same normalised number → `DUPLICATE_EXACT` block, `prior_run` set.
- Else same vendor, same taxable value, and same invoice date or same line fingerprint (sorted
  tuples of normalised description, quantity, unit price) → `DUPLICATE_SUSPECTED` block.
- Otherwise `NO_DUPLICATE` info.

**7 Decision** (`stages/decision_stage.py`). Any block finding in the reject set → `rejected`; any other
block → `needs_attention`; none → `approved`. `update` is set only when approved and a PO matched.
`gates` restates the result as the three checks an approval needs (vendor identified, PO matched,
amount cleared): a gate passes when its stage ran and raised no block finding. The gates are a
summary for the reader; the findings alone decide the outcome.

**5 Amounts on the allocations** (`stages/amounts_stage.py`). Stage 4 decided every line's PO line(s); each
allocation becomes one portion line (the invoice line's description, unit price and tax rate, the allocation's
quantity). One PO → `amount_check`; several → `split_check`, each PO's share checked against that PO, `within` only
when every share is. Stage 7 returns one `OrderUpdate` per PO and stage 8 consumes them all in one transaction. The
run row and the invoice register keep the first PO number only.

**8 Persist** (pipeline, one transaction). Register row for every run that produced an extraction
(vendor, PO, normalised number, date, taxable value, fingerprint, outcome, run id). When approved:
`order_ent.consume` adds the amount and line quantities, recomputes status (`open` →
`partially_invoiced` → `fully_invoiced`; `cancelled` never changes). Run closed with outcome,
summary, duration and three references: `vendor_id` (FK `vendors`), `po_number` (FK `orders`)
and `invoice_id` (FK `invoices`, the register row just written, whose id `register_in` returns). The
run copies no vendor name, invoice number, amount, reasons or token usage; the decision step's data
holds only `update`, so the run row is the one home of the outcome and summary.

**Pipeline behaviour.** Runs proceed in parallel; stages 4 to 8 hold one `asyncio.Lock` per matched vendor. Each stage emits a `running` event,
then `done` / `skipped` / `failed`, and stores the step row before emitting. Report-only mode: a
block finding never stops later stages; a stage is skipped only when its input does not exist.
An unexpected exception marks the step `failed`, the run `error`, and never approves.

## 5. LLM adapter and recorded mode

One adapter, built only in live mode (`AdapterRegistry.LLM` is `None` otherwise). It is the chosen
provider's: `AnthropicAdapterClass` or `OpenAIAdapterClass`. Both have this contract:

```
async extract[T: BaseModel](document: bytes, media_type: Literal["application/pdf","image/png","image/jpeg"],
    system_prompt: str, context_prompt: str, response_model: type[T]) -> tuple[T, Usage]
```

`AnthropicAdapterClass` (live), using `AsyncAnthropic().messages.parse(...)`:
- `model`, `max_tokens` from settings; `output_config={"effort": <setting>}`; `output_format=response_model`.
- `system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral", "ttl": <setting>}}]`.
- `messages=[{"role": "user", "content": [<document or image block, base64>, {"type": "text", "text": context_prompt}]}]`.
  PDF: `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": ...}}`;
  images: `{"type": "image", "source": {...}}`.
- No `thinking`, `temperature`, `tool_choice` parameters (rejected or unnecessary on this model).
- Result: `response.parsed_output`; `stop_reason` of `refusal` or `max_tokens`, or a missing parsed
  output, raises `ExtractionError`. `RateLimitError` / `APIConnectionError`: one retry after 2 s
  (client constructed with `max_retries=0` so this is the only retry). `InternalServerError` (5xx, including
  overloaded) gets the same single retry. Any other `APIStatusError` → `ExtractionError` carrying
  the status. A response that fails schema validation → `ExtractionError` naming the stop reason. Never catch bare `Exception`.
- `Usage` from `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`.

`OpenAIAdapterClass` (live, `openai_adp.py`, the only importer of `openai`), using
`AsyncOpenAI().responses.parse(...)`:
- `model`, `max_output_tokens` from settings; `reasoning={"effort": <setting>}`; `text_format=response_model`;
  `store=False` (invoices carry bank details, so nothing is kept by OpenAI).
- `input` is ordered from what never changes to what always changes, because a prompt cache matches a
  request from its first byte up to a breakpoint:
  1. `{"role": "developer", "content": [{"type": "input_text", "text": system_prompt, "prompt_cache_breakpoint": {"mode": "explicit"}}]}`
     (the shared prefix; its breakpoint is the only one);
  2. `{"role": "user", "content": [<file or image block>, {"type": "input_text", "text": context_prompt}]}`.
- `prompt_cache_key="invoice-extract"` (one cache for every call) and `prompt_cache_options={"mode": "explicit"}`
  (no implicit breakpoint, so the prefix is written once and no invoice is ever written to the cache).
  PDF: `{"type": "input_file", "filename": "invoice.pdf", "file_data": "data:application/pdf;base64,..."}`;
  images: `{"type": "input_image", "image_url": "data:image/png;base64,...", "detail": "auto"}`.
- Result: `response.output_parsed`; a `status` other than `completed`, or a missing parsed output, raises
  `ExtractionError`. Retries and status errors as for Anthropic.
- `Usage`: OpenAI counts cached tokens inside `input_tokens`, so `input` is `input_tokens` minus
  `cached_tokens` and `cache_write_tokens`, which become `cache_read` and `cache_write`.

Recorded mode (no adapter): `ExtractionStageClass` asks `SeedEntityClass.recording(document)` for the
stored reading of the sample whose PDF has the same SHA-256 (`samples.digest`), sleeps `replay_delay`,
and returns zero usage. The file name plays no part. No match → `EXTRACTION_FAILED` (block) with
"The invoice could not be read: no recorded reading matches this document; set ANTHROPIC_API_KEY or
OPENAI_API_KEY to read new documents, or enter the invoice by hand." Nothing under `data/` is read at run time.

## 6. HTTP API (JSON keys are exactly these; use field aliases)

```
POST /v1/runs                      multipart "file"                     -> {"id": "..."}            202
POST /v1/runs/samples/{sample}     sample = "INV-001" ...               -> {"id": "..."}            202 at any time, in any order; 404 when not in the samples table
POST /v1/runs/manual               JSON ManualInvoice                   -> {"id": "..."}            202; 422 for unknown keys, > 50 lines, > 10 tax lines, a string > 500 chars
GET  /v1/runs?outcome=             optional filter                      -> {"data": [RunRow]}
GET  /v1/runs/{id}                                                      -> RunDetail                 404 when unknown
GET  /v1/runs/{id}/events          text/event-stream                    events below
GET  /v1/runs/{id}/document        the stored PDF or image, shown inline -> 404 when none is stored (typed or rejected invoice)
GET  /v1/orders                                                         -> {"data": [OrderRow]}
POST /v1/orders                    JSON NewOrder                        -> OrderRow                  201; 404 unknown vendor; 409 suspended vendor; 422 bad lines
GET  /v1/vendors                                                        -> {"data": [VendorRow]}     ordered by id
POST /v1/vendors                   JSON NewVendor                       -> VendorRow                 201; 422 blank or malformed field; 409 tax ID or account taken
GET  /v1/admin/samples                                                  -> {"data": [SampleRow]}
GET  /v1/admin/samples/{id}/document  the sample's PDF, shown inline     -> 404 unknown sample
GET  /v1/admin/meta                                                     -> {"mode": "live"|"recorded", "model": "..."}
POST /v1/admin/reset               at most one per 10 s (429 otherwise) -> {"ok": true}             restores samples, vendors and POs to the seed files
```

`GET /v1/admin/samples` and the sample-run route read the `samples` table, which is seeded from
`samples.json` and each sample's `invoices/<id>.pdf` and `.read.json` when empty and restored from
them on every reset. A sample run takes its PDF from the table; an upload in recorded mode replays a
reading only when its bytes equal a sample PDF's (SHA-256), whatever its name. Listings never load
the `pdf` or `reading` columns. The 404, 409 and 422 refusals of the
two create routes carry one sentence in `detail`; a missing required key or a wrong type, and every
422 of the manual route, is FastAPI's request-validation list.

```
RunRow     {"id","file","status","outcome"|null,"vendor_id"|null,"vendor"|null,"po"|null,"invoice_id"|null,
            "number"|null,"amount"|null (string),"summary"|null,"tokens": {"in","read","write","out"}|null
            (the extraction step's usage),
            "mode","at" (ISO 8601),"ms"|null}
            vendor = the vendor's legal name, number and amount = the register row's number and taxable
            value, all read through vendor_id and invoice_id by the service; history reads names, invoices
            and extraction usage in three queries under one gather, never one per run
RunDetail  RunRow + {"reasons": [FindingRow] (the steps' findings in reason order, derived on read; empty
            while running), "steps": [StepRow]}
StepRow    {"name","state","ms","data": {...stage result without findings...},"findings": [FindingRow]}
FindingRow {"code","level" (info|warn|block),"text","data": {...}}
OrderRow   {"po","vendor","currency","status","total","used","left","lines": [{"no","text","qty","used","price"}]}   amounts as strings
SampleRow  {"step","id","title","expect","outcome","ran": bool,"result": outcome|null}
VendorRow  {"id","name","country","tax_id","account","bank_code","bank","currency","status": "approved"|"suspended",
            "tolerance": percent as text (e.g. "2.5")|null}

NewVendor  {"name", "country" = "IN", "tax_id", "account", "bank_code", "bank", "currency" = "INR",
            "tolerance"?: number, 0 < x <= 20}
            id = next "V" + 3 digits; status approved; pan = GSTIN[2:12] for IN, else ""; email_domain and
            payment_terms ""; tax ID and account unique after normalising (uppercase, letters and digits)
NewOrder   {"vendor_id", "date"?: ISO date = today, "lines": 1..20 of
            {"text", "qty" > 0, "unit" = "pcs", "price" > 0, "tax" = 18, 0..100}}
            po = "PO-" + year of date + "-" + (highest sequence across all POs + 1, 4 digits); currency =
            the vendor's; total = sum qty x price; status open; nothing consumed
ManualInvoice  the InvoiceRead fields without "sources", all optional ("" / false / []): supplier_name,
            supplier_tax_id, supplier_address, supplier_email, bank_account_name, bank_account_number,
            bank_code, bank_name, buyer_name, buyer_tax_id, invoice_number, invoice_date, due_date, currency,
            po_reference_text, prices_include_tax, lines: [{description, quantity, unit, unit_price,
            line_amount, tax_rate_pct}], subtotal, tax_lines: [{label, rate_pct, amount}], total
            The run: mode "manual", file null, sample null; intake stored skipped ("Entered
            manually; there is no file."); extraction done with the converted reading, no usage (tokens
            null), no model call; stages 2 to 8 unchanged.
```

SSE: on connect, replay every stored step of the run as `event: step` (data = StepRow), then live
events. A `running` step is sent as a StepRow with state `running` and empty data. When the run
closes: `event: done`, data = `{"status","outcome","summary"}`, then the stream ends. Connecting to
a finished run replays the steps and sends `done` immediately.

## 7. Seed data (exact)

Buyer on every invoice: Meridian Retail Pvt Ltd, GSTIN 29AAGCM4567H1Z9, Bengaluru.
`vendors.csv`, `purchase_orders.csv`, `po_lines.csv`, `samples.json`, `golden.json` are already
written in `backend/data/`; they are the source of truth for values.

| File | Vendor | Invoice no. | Date | PO ref printed | Lines | Taxable | Tax | Total | Notes |
|---|---|---|---|---|---|---|---|---|---|
| INV-001 | V001 Acme | ACM/24-25/0187 | 2024-11-04 | PO-2024-0142 | 30 × Laptop stand (adjustable aluminium) @ 1,200.00 | 36,000.00 | CGST 9% 3,240.00 + SGST 9% 3,240.00 | 42,480.00 | Happy path: clean PDF |
| INV-002 | V002 Zenith | ZT-2024-1105 | 2024-11-05 | PO-2024-0143 | 20 × Ergonomic office chair @ 10,030.00 (incl. tax) | 1,70,000.00 (derived) | IGST 18% 30,600.00 (included) | 2,00,600.00 | Scanned PDF, with tax inside the prices |
| INV-003 | Acme Supplies Pvt Ltd | ACM/24-25/0244 | 2024-11-26 | PO-2024-0158 | 10 × Cable management tray (steel) @ 650.00 | 6,500.00 | CGST 9% 585.00 + SGST 9% 585.00 | 7,670.00 | Password-protected PDF |
| INV-004 | V001 Acme | (none) | 2024-11-21 | PO-2024-0153 | 25 × Wireless keyboard and mouse combo @ 1,800.00 | 45,500.00 printed (lines total 45,000.00) | CGST 9% 4,050.00 + SGST 9% 4,050.00 | 54,000.00 printed (not 53,600.00) | No invoice number, and sums that do not add up |
| INV-006 | V011 Saffron | SFS/30-31/0004 | 2030-04-03 | PO-2024-0163 | 8 × Paper shredder (cross-cut) @ 9,500.00 | 76,000.00 | CGST 9% 6,840.00 + SGST 9% 6,840.00 | 89,680.00 | Warnings only: future and unclear date, no currency |
| INV-008 | V001 Acme | ACM/CN/24-25/009 | (none) | PO-2024-0158 | -5 × Cable management tray (steel) @ 650.00 | -3,250.00 | CGST 9% -292.50 + SGST 9% -292.50 | (none) | Credit note: negative amounts, no date, no total |
| INV-013 | Lotus Workspace Interiors Pvt Ltd | LWS/2024/117 | 2024-11-26 | none | 12 × Workstation partition panel @ 4,800.00 | 57,600.00 | CGST 9% 5,184.00 + SGST 9% 5,184.00 | 67,968.00 | Supplier not in the vendor master |
| INV-014 | V006 Nova | NCS/24-25/0312 | 2024-12-10 | none | 50 × Proximity sensor M12 (PNP) @ 640.00 | 32,000.00 | IGST 18% 5,760.00 | 37,760.00 | Rejected: suspended vendor |
| INV-015 | V001 Acme | ACM/24-25/0240 | 2024-11-24 | PO-2024-0153 | 10 × Wireless keyboard and mouse combo @ 1,800.00 | 18,000.00 | CGST 9% 1,620.00 + SGST 9% 1,620.00 | 21,240.00 | GSTIN misprinted |
| INV-016 | V002 Zenith | ZOS/24-25/118 | 2024-11-16 | PO-2024-0156 | 5 × Ergonomic office chair @ 8,500.00 | 42,500.00 | IGST 18% 7,650.00 | 50,150.00 | Bank account matches, name does not |
| INV-017 | V004 Orion | OL-INV-7840 | 2024-11-18 | PO-2024-0145 | 1 × Freight - Bengaluru to Mumbai (October) @ 80,000.00 | 80,000.00 | IGST 18% 14,400.00 | 94,400.00 | Vendor found by name only |
| INV-018 | Nova Component Pvt Ltd | NC/1124/077 | 2024-11-20 | none | 20 × Proximity sensor M12 (PNP) @ 640.00 | 12,800.00 | IGST 18% 2,304.00 | 15,104.00 | Name close to two vendors |
| INV-019 | V005 Pixel | PP/2024/0456 | 2024-11-11 | PO-2024-0147 | 5000 × Product brochure (A4 colour) @ 5.00 | 25,000.00 | CGST 9% 2,250.00 + SGST 9% 2,250.00 | 29,500.00 | Bank account mismatch |
| INV-021 | V001 Acme | ACM/24-25/0187 | 2024-11-04 | PO-2024-0142 | 30 × Laptop stand (adjustable aluminium) @ 1,200.00 | 36,000.00 | CGST 9% 3,240.00 + SGST 9% 3,240.00 | 42,480.00 | Duplicate invoice |
| INV-023 | V001 Acme | ACM/24-25/0236 | 2024-11-04 | PO-2024-0142 | 30 × Laptop stand (adjustable aluminium) @ 1,200.00 | 36,000.00 | CGST 9% 3,240.00 + SGST 9% 3,240.00 | 42,480.00 | Re-issued under a new number (INV-001's details) |
| INV-028 | V010 Brightline | BLI/24-25/0431 | 2024-11-30 | PO-2024-0159 / PO-2024-0160 | 20 × Wireless headset @ 3,200.00; 15 × Height-adjustable office desk @ 9,000.00 | 1,99,000.00 | CGST 9% 17,910.00 + SGST 9% 17,910.00 | 2,34,820.00 | One invoice split between two POs |
| INV-029 | V004 Orion | OL-INV-7802 | 2024-11-23 | PO-2024-0153 | 1 × Freight - Bengaluru to Chennai (November) @ 45,000.00 | 45,000.00 | IGST 18% 8,100.00 | 53,100.00 | PO number of another vendor |
| INV-030 | V005 Pixel | PPS/24-25/0398 | 2024-12-11 | PO-2024-0149 | 2000 × Business cards (box of 100) @ 2.50 | 5,000.00 | CGST 9% 450.00 + SGST 9% 450.00 | 5,900.00 | Rejected: cancelled PO |
| INV-034 | V012 Kaveri | KOI/24-25/0210 | 2024-12-12 | PO-2024-0165 | 10 × Ergo footrest pad @ 1,400.00; 2 × Glass marker board 6ft x 4ft @ 12,500.00 | 39,000.00 | CGST 9% 3,510.00 + SGST 9% 3,510.00 | 46,020.00 | Jev keeps reworded lines |
| INV-035 | V012 Kaveri | KOI/24-25/0214 | 2024-12-13 | PO-2024-0166 | 12 × Meeting table wire tray @ 900.00 | 10,800.00 | CGST 9% 972.00 + SGST 9% 972.00 | 12,744.00 | An ambiguous line: Jev is not confident, so a person decides |
| INV-043 | V005 Pixel | PP/2024/0502 | 2024-11-24 | PO-2024-0157 | 400 × Spiral notebook (A5, ruled) @ 66.00 | 26,400.00 | CGST 9% 2,376.00 + SGST 9% 2,376.00 | 31,152.00 | Price raised and the wrong GST rate |
| INV-045 | V011 Saffron | SFS/24-25/0097 | 2024-12-12 | PO-2024-0164 | 2 × Water dispenser (hot and cold) @ 14,000.00 | 28,000.00 | CGST 9% 2,520.00 + SGST 9% 2,520.00 | 33,040.00 | Invoice in the wrong currency |
| INV-046 | V011 Saffron | SFS/24-25/0102 | 2024-12-13 | PO-2024-0162 / PO-2024-0164 | 4 × Acoustic partition panel @ (no unit price); 2 × Water dispenser (hot and cold) @ 14,000.00 | 52,800.00 | CGST 9% 4,752.00 + SGST 9% 4,752.00 | 62,304.00 | Split invoice with a line that has no unit price |

Each PDF prints the bank details from `vendors.csv`, except INV-019 (another account) and INV-017 and INV-018
(none printed). The samples are ordered by the stage that decides them (intake, validation, vendor, bank,
duplicate, PO, Jev, amounts); INV-001 runs first because later samples re-use its invoice and its PO. Some invoices
carry more than one defect so that one sample proves more than one rule (INV-004, INV-008, INV-043). The vendor
master and the PO register are deliberately larger than the sample set. V007, V008 and V009 have no sample now;
the reading rules they showed (month-first dates, decimal commas, gross prices) are still tested in
`backend/tests/test_extraction.py` and `test_rules.py`. V013 and PO-2024-0167 / 0168 are unused seed data: the rule
they showed, Jev picking between two POs that order the same item at different prices, has neither a sample nor a
test since the scenario was cut. INV-003 is password-protected
(generated with that password, so it is refused at intake). Each PDF has a sibling `INV-NNN.read.json`: the `InvoiceRead` a careful reader would
produce, with amounts as printed strings (for example `"36,000.00"`). It must validate against `InvoiceRead`.

Expected results are in `golden.json` (outcome, codes that must be present, vendor, PO, PO balance after the run).
The samples run in order after a reset.

## 8. Frontend

Vite + React + TypeScript + Tailwind, small local components in shadcn style, lucide icons,
react-router. Dev server on 5173 proxies `/v1` to `http://localhost:8000`.

- `/` Process: mode badge from `/v1/admin/meta` ("Live model" or "Recorded extraction"); a drop
  zone (PDF / PNG / JPG); the ordered sample scenario with a "Reset demo data" button, each step
  showing title, expectation and its last result; every step can be run at any time (its button is
  disabled only while a run is starting). Starting a run navigates to `/runs/:id`.
- `/runs/:id` Run: header (file, outcome badge, summary); vertical timeline of the nine steps fed
  by `EventSource`, each with a state icon, elapsed time, findings (block / warn / info styling)
  and an expandable evidence panel rendering `data` readably (extraction: fields and source
  quotes; amounts: a numbers table and per-line results; vendor / PO: method, signals,
  candidates); extraction shows token usage (input, cache read, cache write, output) and the mode;
  decision shows the ordered reasons. Works on refresh (replay) and for finished runs.
- `/dashboard` Dashboard: counts by outcome; run history table (time, file, vendor, PO, invoice
  no., amount, outcome, summary, tokens) with an outcome filter and row click to the run; PO
  balances card (total, used, left, status, progress bar).

Outcome colours: approved green, needs attention amber, rejected red. Clean, dense, finance-tool
look; no marketing chrome.

## 9. Ownership

| Owner | Files |
|---|---|
| Coordinator | this spec, `pyproject.toml`, `uv.lock`, `data/*.csv`, `data/samples.json`, `data/golden.json`, PLAN.md, DECISIONS.md, HANDOFF.md |
| Data builder | `backend/scripts/make_invoices.py`, `backend/data/invoices/*` |
| Backend builder | `backend/app/**`, `backend/tests/**`, `backend/scripts/run_golden.py`, `backend/scripts/check_imports.py`, `backend/.env.example`, `backend/.gitignore` |
| Frontend builder | `frontend/**` |

## 10. Done means

- `uv run ruff check . && uv run ruff format --check .` clean.
- `uv run python scripts/check_imports.py` passes.
- `uv run pytest -q` passes; includes the eleven samples in order through the pipeline in recorded
  mode matching `golden.json`, and a test that the `InvoiceRead` JSON schema has no optional
  properties, no `anyOf`, and `additionalProperties: false` on every object.
- `uv run python scripts/run_golden.py` prints a pass table (recorded mode without a key, live with).
- The demo script in PLAN.md A1 runs from the browser.
