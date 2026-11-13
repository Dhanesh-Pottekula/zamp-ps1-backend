# Invoice processing, from PDF to decision

A supplier invoice comes in as a PDF or a photo. One model call reads it into structured fields.
Plain code then checks those fields against a vendor list and a list of purchase orders, and
decides. One judgement inside that is not plain code: when an invoice line's wording is too far
from the purchase order's to be sure, a second model is asked which order line it bills. Every check it makes, and every reason for the decision, is stored and shown.

This file is the project's front page and a full description of how it works. Everything in it
is taken from the code in this folder. When a planning document says something different, this
file follows the code.

## Contents

1. [What it does](#1-what-it-does)
2. [How to run it](#2-how-to-run-it)
3. [Architecture](#3-architecture)
4. [The life of one run](#4-the-life-of-one-run)
5. [The ten stages](#5-the-ten-stages)
6. [Data model](#6-data-model)
7. [Finding codes and the decision rule](#7-finding-codes-and-the-decision-rule)
8. [HTTP API](#8-http-api)
9. [Frontend](#9-frontend)
10. [The sample scenario](#10-the-sample-scenario)
11. [Design decisions](#11-design-decisions)
12. [Tests and checks](#12-tests-and-checks)
13. [Known limits](#13-known-limits)

---

## 1. What it does

An accounts payable (AP) team must decide which supplier invoices to pay, and this project makes
that first decision. You upload an invoice, and a model reads it once into a fixed set of fields.
A ten-stage pipeline then checks the arithmetic, finds the approved vendor who sent it, finds the
purchase order (PO) it bills against line by line, compares the amounts and looks for duplicates.
Eight of those ten stages are plain code. Two use a model, and for different jobs: stage 1 reads
the document, and stage 6 asks an evaluation model which PO line a reworded invoice line bills when
the text alone cannot settle it, holding the invoice when the answer is not confident.
Each stage produces **findings**: short coded sentences such as "the bank account differs from the
vendor master". The outcome is computed from those findings, and you can watch every stage live in
the browser.

Two terms used throughout:

- **Vendor master**: the buyer's table of suppliers it may pay, with each one's tax ID and bank
  account, checked when the supplier was taken on.
- **Purchase order (PO)**: the buyer's own record of what it agreed to buy, from whom, at what unit
  price, before tax. A PO has lines and a balance that goes down as invoices against it are approved.

Every run ends in one of three outcomes:

| Outcome | Meaning |
|---|---|
| `approved` | No finding blocks payment. The amount is taken off the PO balance. |
| `needs_attention` | At least one blocking finding, none from the reject set. A person must look at it. |
| `rejected` | At least one blocking finding from the reject set: an unusable file, a suspended vendor, a cancelled PO, or an exact duplicate. |

---

## 2. How to run it

### Prerequisites

- Python 3.14 (`requires-python = ">=3.14"` in `backend/pyproject.toml`) and
  [uv](https://docs.astral.sh/uv/).
- Node.js and npm for the frontend.
- No API key is needed. Without one the backend runs in **recorded mode** (explained below).

### Start the backend (terminal 1)

```bash
cd backend && uv run uvicorn app.main:app --port 8000
```

On the first start the backend creates `backend/var/ps1.sqlite3` and loads the seed data: vendors,
POs and the sample scenario. It seeds everything only when the vendor table is empty, and the
samples alone when only the `samples` table is empty. After that, the seed files are reloaded only
by a reset.

### Start the frontend (terminal 2)

```bash
cd frontend && npm install && npm run dev
```

Open **http://localhost:5173**. The Vite dev server forwards every `/v1` request to
`http://localhost:8000`. Set `VITE_API_TARGET` to point it somewhere else.

### The demo

1. On the Process page, press **Reset demo data**.
2. Run the samples in step order. Some steps build on an earlier one (step 3 follows step 1,
   step 4 follows step 3, step 8 duplicates step 1). The order is not enforced: any sample can run
   at any time, and it is checked against whatever the data holds at that moment.
3. Each run opens its live view. The Dashboard lists every run and the PO balances.

Order matters because runs change state. An approval uses up a PO balance, and an approved
invoice becomes the original that a later duplicate is caught against. The full scenario is in
[section 10](#10-the-sample-scenario).

### Processing a new invoice without a key

Without an API key only the 33 sample PDFs can be read from a file (recorded mode recognises a
document by its exact bytes). A new invoice can still be processed by typing it in: `POST /v1/runs/manual` takes the same
fields the model would fill, as text ([section 8](#8-http-api)). The typed values go through the
same conversion as a model reading and then through stages 2 to 9 unchanged; no rule is relaxed.
New master data can be added the same way: `POST /v1/vendors` adds a vendor and `POST /v1/orders`
raises a PO for it. So the whole path can be exercised without a key: create a vendor, raise a PO
for it, then enter an invoice that names the vendor's tax ID and the PO number.

### Reset

Use the **Reset demo data** button, or call `POST /v1/admin/reset`. A reset deletes every run, step,
registered invoice and stored upload. It then restores the `samples` table from `samples.json` and
vendors and POs from the CSV files, all in the same transaction. Vendors and POs created through the
API disappear. The backend allows one reset every 10 seconds. A reset waits until any run in
progress has finished.

The seed files are only the seed: every reader uses the tables. They are read at startup when the
tables are empty, and on every reset. So after editing a seed file, reset to load it.

### Recorded mode and live mode

The mode decides how stage 1 (extraction) reads a document.

| Mode | When | What happens |
|---|---|---|
| `recorded` | No `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` set (the default) | The document's SHA-256 is looked up in the `samples` table. When it equals a sample PDF's, that sample's stored reading is replayed after a 0.8 second pause, whatever the file is called. Any other document ends with `EXTRACTION_FAILED`. No network call is made, no tokens are used, and nothing is read from `backend/data/` at run time. |
| `live` | `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` set to a non-empty value | One call per document: to the Anthropic Messages API, or to the OpenAI Responses API when only the OpenAI key is set (or `LLM_PROVIDER=openai`). |

To switch to live mode, put the key in `backend/.env` (the file is git-ignored; `backend/.env.example`
shows the line) and restart the backend:

```
ANTHROPIC_API_KEY=sk-ant-...
```

or, to read documents with OpenAI:

```
OPENAI_API_KEY=sk-...
```

With both keys set Anthropic is used, unless `LLM_PROVIDER=openai` is set in `llm.env`. **It is
set**, so a live run reads documents with OpenAI (`LLM_OPENAI_MODEL`, `gpt-6-luna`) unless that
line is changed; `LLM_MODEL` (`claude-opus-5-5`) then applies only if the provider is switched
back. The effort setting applies to both providers.

The setting `LLM_MODE` in `backend/app/config/llm.env` is `auto` by default: live when a key is
present, recorded otherwise. It can be set to `live` or `recorded` to force a mode. The mode in
use is shown as a badge on the Process page and on every run.

---

## 3. Architecture

### Four layers

The backend has four layers. A request always moves down through them in the same order, and
each layer may only call the one below it.

```
Browser
   |
   v
Controller   declares the route, its status code and its response model; nothing else
   |
   v
Service      shapes entity results into wire models; raises HTTP errors (404, 409, 422, 429)
   |
   v
Entity       the business: rules, tables, matching, the pipeline
   |
   v
Adapter      the outside world: SQLite, the model API, PDF reading, fuzzy text matching, files
```

| Layer | Folder | May import |
|---|---|---|
| Controller | `backend/app/routers/<name>/controllers/` | its own router's services, models and config; FastAPI, Pydantic |
| Service | `backend/app/routers/<name>/services/` | `app.entities` (the package, not its modules) and its own router package; FastAPI, Pydantic |
| Router models and config | `backend/app/routers/<name>/<name>_models.py` and `config.py` | `app.entities`, `app.routers.config`, its own router; FastAPI, Pydantic |
| Entity | `backend/app/entities/` (including `entities/stages/`) | `app.adapters` (the package), `app.entities._types`, `app.config`, the standard library, Pydantic, SQLAlchemy. A sibling entity or stage module only inside `if TYPE_CHECKING:` (for type hints), except in the registry file `entities/__init__.py`; a stage gets the entities it uses by injection. |
| Adapter | `backend/app/adapters/` | `app.config`, `app.adapters._types`, any third-party library |
| Config | `backend/app/config/` | `app.config`, Pydantic, pydantic-settings |

`backend/scripts/check_imports.py` enforces this table. It parses every file under `backend/app`
with Python's `ast` module and records each import, noting whether it sits inside
`if TYPE_CHECKING:`. It prints every import that breaks a rule and exits with status 1. A relative
import (`from . import x`) always fails. `main.py`, `app/__init__.py`, `routers/__init__.py` and
`routers/config.py` are treated as wiring: they may import any `app` module, but their third-party
imports are still limited to FastAPI, Pydantic and Starlette.

### Why every third-party library sits behind one adapter

| Library | Only imported by | What the adapter offers |
|---|---|---|
| `anthropic` | `adapters/llm_adp.py` | `AnthropicAdapterClass.extract()` |
| `openai` | `adapters/openai_adp.py` | `OpenAIAdapterClass.extract()` |
| `typesafe_sdk` | `adapters/typesafe_adp.py` | `TypeSafeAdapterClass.evaluate()` (Jev) |
| `pypdf` | `adapters/pdf_adp.py` | `PdfAdapterClass.inspect()` |
| `rapidfuzz` | `adapters/match_adp.py` | `MatchAdapterClass.ratio()` |
| SQLAlchemy engine and sessions | `adapters/db_adp.py` | `DBAdapterClass.run()`, `create_tables()`, `close()` |
| uploads and seed files on disk | `adapters/store_adp.py` | `StoreAdapterClass.save()`, `read_data()` (seeding only), `clear_uploads()` |

Entities still import SQLAlchemy to declare their tables and write queries. Only the adapter owns
the engine, the sessions and the worker threads. `PromptEntityClass` reads its two prompt files from
disk itself, with the standard library.

The reasons:

- **One place to change.** Retries and error wording for the model API live in one file.
  Moving to another PDF library or another model provider changes one file.
- **No network without a key.** In recorded mode neither model adapter is built (`AdapterRegistry.LLM`
  and `.Jev` are both `None`). The extraction entity replays the stored reading of a sample whose PDF
  has the same bytes, and stage 6 replays Jev's answers from `data/jev_recordings.json`, keyed by a
  digest of the request, so tests and the demo run without a network.
- **Entities stay testable.** `RulesEntityClass` does no input or output at all. Its tests build
  values and call methods directly.
- **The rule is checked by a script**, so it cannot quietly erode.

### The two registries and startup

There are two registries. They are built once, when the app starts, in the `lifespan` function of
`backend/app/main.py`.

```
uvicorn starts the app
   |
   v
AdapterRegistry()                                     adapters/__init__.py
   |  DB    = DBAdapterClass(var/ps1.sqlite3)          SQLite in WAL mode, busy timeout 5000 ms
   |  LLM   = OpenAIAdapterClass(...) or AnthropicAdapterClass(...)  if the resolved mode is "live"
   |          None                       otherwise (recorded mode reads from the samples table)
   |  Jev   = TypeSafeAdapterClass(...)  if TYPESAFE_API_KEY is set
   |          None                       otherwise (recorded answers replay from jev_recordings.json)
   |  Match = MatchAdapterClass()
   |  Pdf   = PdfAdapterClass()
   |  Store = StoreAdapterClass(var/uploads, data)
   v
EntitiesRegistry.initialize(adapter)                  entities/__init__.py
   |  construct, in dependency order:
   |  Prompt, Rules, Vendor, Order, Invoice, Run, Seed, Extraction, Pipeline
   |
   |  DB.create_tables()             create any missing table (no migrations)
   |
   |  then, together (asyncio.gather):
   |    Prompt.initialize()  read system-extract.md and context-extract.md
   |    Run.initialize()     mark any run left "running" by a dead process as "error"
   |    Seed.initialize()    seed every table if there are no vendors, else the samples
   |                         (samples.json plus each sample's PDF and reading from
   |                         data/invoices/) if the samples table is empty
   v
serve requests
   |
   v
shutdown: Pipeline.close() cancels scheduled runs, then the DB adapter disposes its connections
```

WAL (write-ahead log) is a SQLite journal mode in which readers do not wait for a writer, so API
reads are not held up by a run's write transaction.

`AdapterRegistry` is an object with one attribute per adapter. `EntitiesRegistry` is a class whose
attributes hold the one shared instance of each entity. Every entity receives its adapters and the
other entities it needs through its constructor. None of them builds its own.

Services are created on first use by a cached function (`functools.cache`), for example
`get_run_service_singleton_v1()`. FastAPI injects them through a type alias such as
`RunServiceRegistry.V1`. A service copies the entity instances it needs from `EntitiesRegistry`
when it is created.

### How a request travels

Take `GET /v1/orders`, the PO balances:

```
Browser  fetch("/v1/orders")
   |  Vite dev proxy forwards /v1 to http://localhost:8000
   v
order_c_v1.list_orders(service)                       controller
   |  FastAPI injects OrderServiceV1Class
   v
OrderServiceV1Class.balances()                        service
   v
OrderEntityClass.snapshots()                          entity
   |  builds a function that takes a session and runs two SELECTs
   v
DBAdapterClass.run(work)                              adapter
   |  runs the function in a worker thread inside one transaction,
   |  commits on return, rolls back on error
   v
SQLite (orders, order_lines)
   |
   v  back up: rows -> OrderSnapshot models -> OrderModels.V1.Orders
   v
JSON with short aliased keys:  {"data": [{"po": ..., "vendor": ..., "left": "24000.00", ...}]}
```

The database work runs in a worker thread through `asyncio.to_thread`. SQLAlchemy's async engine
needs the `greenlet` package, which is not a dependency, so plain sessions run off the event loop
instead. Money is stored with a custom column type, `DecimalText`, which writes a `Decimal` as its
exact text. SQLite never turns it into a float.

### File map of `backend/app`

| File | What it holds |
|---|---|
| `__init__.py` | Package marker. |
| `main.py` | Creates the FastAPI app, the lifespan (registries up, then down), CORS for the frontend origin (GET and POST only), and mounts the four routers. |
| `config/__init__.py` | Builds `ENVConfigs`, the single settings object. |
| `config/env_config.py` | Three pydantic-settings groups: `Llm`, `Rules`, `Storage`. Relative paths resolve against `backend/`. `_LlmConfigClass.active` resolves the mode. |
| `config/llm.env` | `LLM_MODE`, `LLM_MODEL`, `LLM_EFFORT`, `LLM_CACHE_TTL`, `LLM_MAX_TOKENS`, `LLM_REPLAY_DELAY`. |
| `config/jev.env` | `JEV_MODE`, `JEV_MODEL`, `JEV_TIMEOUT`, `JEV_SELECT_ABOVE`, `JEV_REJECT_AT_OR_BELOW`, `JEV_MAX_CANDIDATES`, `JEV_RECORD`, `JEV_RECORDINGS`. |
| `config/rules.env` | `RULES_TOLERANCE_PCT`, `RULES_TOLERANCE_FLOORS`, `RULES_PASS_SCORE`, `RULES_VENDOR_WEIGHTS`, `RULES_LINE_SURE_ABOVE`, `RULES_LINE_CANDIDATE_AT`, `RULES_PO_POOL_MAX`, `RULES_MAX_PAGES`, `RULES_MAX_BYTES`. |
| `config/storage.env` | `STORAGE_DB_PATH`, `STORAGE_UPLOAD_DIR`, `STORAGE_DATA_DIR`, `STORAGE_CORS_ORIGIN`. |
| `adapters/__init__.py` | `AdapterTypes` (types the entities may use, `LLM` among them), `AdapterRegistry` (`LLM` is `None` in recorded mode). |
| `adapters/_types/__init__.py` | Re-exports the adapter-shared types. |
| `adapters/_types/llm_types.py` | `LlmTypesClass.MediaType`, `LlmTypesClass.Usage` (token counts) and `ExtractionError`. |
| `adapters/db_adp.py` | `Base` for every table, the `DecimalText` column type, `DBAdapterClass` (engine, WAL, `run(work)` in a thread, `create_tables`, `close`). |
| `adapters/llm_adp.py` | `AnthropicAdapterClass`: the one live model call, with caching, one retry and error mapping. |
| `adapters/openai_adp.py` | `OpenAIAdapterClass`: the same call through OpenAI when that provider is chosen, with the prompt ordered for caching. |
| `adapters/typesafe_adp.py` | `TypeSafeAdapterClass.evaluate()`: one Jev request of named yes/no questions, returning a probability each. |
| `adapters/match_adp.py` | `MatchAdapterClass.ratio()`: rapidfuzz token-set ratio, 0 to 100. |
| `adapters/pdf_adp.py` | `PdfAdapterClass.inspect()`: identifies PDF, PNG or JPEG by its first bytes; for a PDF reports encryption, page count and text layer. |
| `adapters/store_adp.py` | `StoreAdapterClass`: saves uploads, reads seed files (only while seeding), deletes uploads on reset. |
| `entities/__init__.py` | `EntitiesRegistry` and its startup order; re-exports public types (`MasterTypes`, `OrderTypes`, `VendorTypes` among them). |
| `entities/_types/__init__.py` | Re-exports `AmountTypes`, `ExtractionTypes`, `FindingTypes`, `IdentityTypes`, `MasterTypes`, `StageTypes`. |
| `entities/_types/identity_types.py` | `IdentityTypes`: the one definition of PO-number patterns and of how tax IDs, accounts, bank codes, names and invoice numbers are normalised and compared (vendor creation, vendor and bank stages, register, duplicate stage). |
| `entities/_types/amount_types.py` | `AmountTypes`: amount formatting and price arithmetic shared by the validation, amount and decision stages. |
| `entities/_types/master_types.py` | `MasterTypes.MasterError`: why a vendor or PO cannot be created, with a reason (`invalid`, `unknown`, `conflict`) the services turn into 422, 404 or 409. |
| `entities/_types/extraction_types.py` | `InvoiceRead` (the model's all-text wire schema) and `InvoiceExtraction` (the typed form), with their parts. |
| `entities/_types/finding_types.py` | The 45 finding codes (42 of them still raised), the three severities and the `Finding` model. |
| `entities/_types/stage_types.py` | Every stage result model, run and step states, the run modes (`live`, `recorded`, `manual`), `StepEvent`, `RunClosed`, `RunClosing`, `OrderSnapshot`. |
| `entities/prompt_ent.py` | `PromptEntityClass`: loads the prompt pair once and fills the context template. |
| `entities/prompts/system-extract.md` | The system prompt: the reader's job, what every field means, rules R1 to R7, two worked examples. |
| `entities/prompts/context-extract.md` | The per-document prompt: the instruction and, when the upload had a name, a `{{FILENAME}}` line. |
| `entities/rules_ent.py` | `RulesEntityClass`: the AP policy several stages share: `tolerance()`, `gstin_shaped()` (the one GSTIN pattern), the reject codes, the sum and rate bands, `ordered()` (reason order). No input or output. |
| `entities/vendor_ent.py` | `VendorDB.Vendors` table; `records()` (what the vendor and bank stages compare against), `names()`, `vendors()`, `vendor()`, `tolerance_pct()` and `create()` (a new vendor, refused when its tax ID or bank account is taken). |
| `entities/order_ent.py` | `OrderDB.Orders` and `OrderDB.OrderLines` tables; `create()` (a new PO numbered `PO-YYYY-NNNN`); `snapshots()`; line mapping (`map_lines`, `map_split`) shared by the PO and amount stages; `consume_in()`. |
| `entities/invoice_ent.py` | `InvoiceDB.Invoices` (the invoice register); `register_in()`, `approved()` (what the duplicate stage compares) and `briefs()`. |
| `entities/stages/intake_stage.py` | Stage 0 `IntakeStageClass`: size, type, pages, encryption; stores an accepted file. |
| `entities/stages/extraction_stage.py` | Stage 1 `ExtractionStageClass`: calls the adapter, turns `InvoiceRead` into `InvoiceExtraction`, maps failures to `EXTRACTION_FAILED`; `typed()` converts a manually entered `InvoiceRead` without a model; in recorded mode it replays the reading the seed entity finds for the document's bytes. |
| `entities/stages/validation_stage.py` | Stage 2 `ValidationStageClass`: taxable value, tax rate, header and arithmetic findings; `priced_lines()`. |
| `entities/stages/vendor_stage.py` | Stage 3 `VendorStageClass`: the vendor ladder (tax ID, account, name), suspension and tax-ID vetoes. |
| `entities/stages/bank_stage.py` | Stage 4 `BankStageClass`: payee account (and bank code) vs the vendor master. |
| `entities/stages/duplicate_stage.py` | Stage 5 `DuplicateStageClass`: exact duplicate or suspected re-send against approved invoices. |
| `entities/stages/po_stage.py` | Stage 6 `PoStageClass`: the candidate pool (printed PO(s), else the vendor's open POs), line scoring, refusals; hands the lines it is not sure of to Jev. |
| `entities/stages/po_jev.py` | Stage 6, part 2 `PoJevClass`: one yes/no question per not-sure invoice line and candidate PO line, the confidence bands, recordings. |
| `entities/stages/amounts_stage.py` | Stage 7 `AmountsStageClass`: one PO or a split, every line and balance. |
| `entities/stages/decision_stage.py` | Stage 8 `DecisionStageClass`: outcome, summary, the four gates, the PO update(s). |
| `entities/stages/persist_stage.py` | Stage 9 `PersistStageClass`: one transaction that closes the run, registers the invoice and consumes the POs. |
| `entities/run_ent.py` | `RunDB.Runs` and `RunDB.Steps` tables, run history, `sample_states()` (latest run of every sample, for the samples listing), and the in-process event bus the live view follows. |
| `entities/seed_ent.py` | `SeedDB.Samples` table and `SeedEntityClass`: serves the samples, one sample's PDF (`sample`) and the recorded reading for a document's SHA-256 (`recording`); seeds and resets samples, vendors and POs from the seed files. |
| `entities/pipeline_invoice.py` | `PipelineEntityClass`: orchestration only. The only place that knows the stage order; calls each stage in turn; `submit()` (a file) and `enter()` (a typed invoice); the vendor locks, background tasks, failure handling. |
| `routers/__init__.py` | Exposes `AdminRouters`, `OrderRouters`, `RunRouters`, `VendorRouters`. |
| `routers/config.py` | `RouterBaseModel` and the wire formats `RouterTypes.Money` (two decimals as text), `RouterTypes.Quantity` and `RouterTypes.Percent` (both as their shortest exact text). |
| `routers/run/__init__.py` | Package marker for the run router. |
| `routers/run/config.py` | `RunConfig`: path and query rules (`RunId`, `SampleId`, `OutcomeFilter`). |
| `routers/run/run_models.py` | `RunModels.V1`: `Created`, `Manual` (a typed invoice, closed to unknown keys), `Run`, `Detail`, `Runs`, `Done`, `Step`, with short JSON aliases. |
| `routers/run/controllers/__init__.py` | `RunRouters`. |
| `routers/run/controllers/run_c_v1.py` | Run routes: upload, run a sample, enter an invoice by hand, list, get one, follow events. |
| `routers/run/services/__init__.py` | `RunServiceRegistry.V1` dependency alias. |
| `routers/run/services/run_s_v1.py` | `RunServiceV1Class`: starts runs (upload, any sample at any time, typed invoice), frames the event stream, and composes the run row: vendor name and invoice number and amount read through the run's ids, reasons rebuilt from the steps' findings. |
| `routers/order/__init__.py` | Package marker for the order router. |
| `routers/order/order_models.py` | `OrderModels.V1`: `NewOrder` (request), `Order`, `Orders`, with aliases. |
| `routers/order/controllers/__init__.py` | `OrderRouters`. |
| `routers/order/controllers/order_c_v1.py` | `GET /v1/orders`, `POST /v1/orders`. |
| `routers/order/services/__init__.py` | `OrderServiceRegistry.V1`. |
| `routers/order/services/order_s_v1.py` | `OrderServiceV1Class`: `balances()`, `create()` (maps a refusal to 422, 404 or 409). |
| `routers/vendor/__init__.py` | Package marker for the vendor router. |
| `routers/vendor/vendor_models.py` | `VendorModels.V1`: `NewVendor` (request), `Vendor`, `Vendors`, with aliases. |
| `routers/vendor/controllers/__init__.py` | `VendorRouters`. |
| `routers/vendor/controllers/vendor_c_v1.py` | `GET /v1/vendors`, `POST /v1/vendors`. |
| `routers/vendor/services/__init__.py` | `VendorServiceRegistry.V1`. |
| `routers/vendor/services/vendor_s_v1.py` | `VendorServiceV1Class`: `listing()`, `create()` (maps a refusal to 422 or 409). |
| `routers/admin/__init__.py` | Package marker for the admin router. |
| `routers/admin/admin_models.py` | `AdminModels.V1`: `Sample`, `Samples`, `Meta`, `Reset`. |
| `routers/admin/controllers/__init__.py` | `AdminRouters`. |
| `routers/admin/controllers/admin_c_v1.py` | Samples, meta and reset routes. |
| `routers/admin/services/__init__.py` | `AdminServiceRegistry.V1`. |
| `routers/admin/services/admin_s_v1.py` | `AdminServiceV1Class`: sample states, mode, rate-limited reset. |

Other backend folders:

| Path | What it holds |
|---|---|
| `backend/data/` | Seed CSVs, `samples.json`, `golden.json`, `jev_recordings.json`, and `invoices/` with each sample PDF and its recorded reading. |
| `backend/scripts/check_imports.py` | The layer rule checker. |
| `backend/scripts/make_invoices.py` | Generates the sample PDFs and their `.read.json` readings (reportlab; the scanned one through pypdfium2 and Pillow). |
| `backend/scripts/run_golden.py` | Resets the database, runs every sample of the `samples` table in step order through the real pipeline, compares each with its `golden.json` row, prints a pass or fail table. |
| `backend/scripts/jev_trigger_report.py` | Runs every sample on a throwaway database with the saved readings (no model) and prints, per sample, whether the PO stage asked Jev, about which lines and how many questions, and how each line was settled. Without `TYPESAFE_API_KEY` (or with `--spy`) a spy stands in for Jev; with the key it calls real Jev. `--show INV-NNN` prints that sample's full request and answers. |
| `backend/tests/` | The pytest suite ([section 12](#12-tests-and-checks)). |
| `backend/var/` | Created at runtime: the SQLite file and `uploads/<run id>/<file name>`. Git-ignored. |

### Settings

All settings come from the `.env` files in `backend/app/config/`. Any of them can be overridden by
an environment variable with the same name.

| Variable | Default | Used for |
|---|---|---|
| `LLM_MODE` | `auto` | `auto`, `live` or `recorded` |
| `LLM_MODEL` | `claude-opus-5-5` | model id for the live call |
| `LLM_EFFORT` | `low` | `output_config.effort` on the live call |
| `LLM_CACHE_TTL` | `5m` | prompt cache lifetime, `5m` or `1h` |
| `LLM_MAX_TOKENS` | `16000` | output cap; validated to be above 0 and at most 20000 |
| `LLM_REPLAY_DELAY` | `0.8` | seconds recorded mode waits before replaying a reading, so the live view is visible |
| `ANTHROPIC_API_KEY` | none | from `backend/.env`; its presence switches `auto` to live |
| `TYPESAFE_API_KEY` | none | from `backend/.env`; its presence switches `JEV_MODE=auto` to live Jev PO selection (else recorded answers from `data/jev_recordings.json` replay) |
| `JEV_SELECT_ABOVE` / `JEV_REJECT_AT_OR_BELOW` | `0.80` / `0.50` | in `app/config/jev.env`: a PO is selected above the first, rejected at or below the second, unsure in between |
| `JEV_RECORD` | `false` | `true` with a key writes every live Jev answer to the recordings file |
| `OPENAI_API_KEY` | none | from `backend/.env`; its presence switches `auto` to live, and chooses OpenAI when no Anthropic key is set |
| `LLM_PROVIDER` | `auto` (set to `openai` in `llm.env`) | `auto`, `anthropic` or `openai` |
| `LLM_OPENAI_MODEL` | `gpt-6-luna` | the model used when the provider is OpenAI |
| `RULES_TOLERANCE_PCT` | `2.0` | tolerance percentage (a vendor may override it); the tolerance is how far an invoice may go over the PO balance and still pass ([section 5](#shared-vocabulary)) |
| `RULES_TOLERANCE_FLOORS` | `{"INR": 500, "USD": 10, "EUR": 10, "GBP": 10, "JPY": 1000}` | smallest tolerance per PO currency (JSON); a currency not listed has no floor, only the percentage |
| `RULES_PASS_SCORE` | `80` | a vendor or a PO is matched only when its weights add up to more than this (sums are capped at 100) |
| `RULES_VENDOR_WEIGHTS` | `{"tax_id": 80, "bank_account": 60, "name": 30}` | points an invoice field earns when it equals the vendor master (JSON, every key required) |
| `RULES_LINE_SURE_ABOVE` / `RULES_LINE_CANDIDATE_AT` | `90` / `70` | a line match is sure above the first when no other pool line reaches the second; otherwise Jev decides it |
| `RULES_LINE_COVERAGE_AT` | `80` | a sure line must also cover most of the PO description, so "Monitor" is not taken for "Monitor arm" |
| `RULES_PO_POOL_MAX` | `10` | with no PO number printed, the vendor's open POs searched |
| `RULES_MAX_PAGES` | `10` | intake page limit |
| `RULES_MAX_BYTES` | `20971520` | intake size limit (20 MB) |
| `STORAGE_DB_PATH` | `var/ps1.sqlite3` | SQLite file |
| `STORAGE_UPLOAD_DIR` | `var/uploads` | stored uploads |
| `STORAGE_DATA_DIR` | `data` | seed files, read only when seeding |
| `STORAGE_CORS_ORIGIN` | `http://localhost:5173` | the one browser origin allowed |

---

## 4. The life of one run

A **run** is one attempt to process one document. A **step** is one stage of a run.

### From click to result

```
POST /v1/runs (file)  or  POST /v1/runs/samples/INV-001  or  POST /v1/runs/manual (typed invoice)
   |
   v
RunServiceV1Class.upload / .sample / .manual
   |  upload: reads at most max_bytes + 1 bytes, so intake can still see "too big"
   |  sample: one query for the sample's id and PDF (Seed.sample); 404 if not in the samples
   |          table; no order is enforced
   |  manual: builds an InvoiceRead from the typed fields with no source quotes
   v
PipelineEntityClass.submit(filename, data, sample)    or    .enter(reading)
   |  1. submit cleans the name: keep the last path segment; a missing, "", "." or ".." name is
   |     stored as null, never invented. Samples and typed invoices store no file name;
   |     the UI labels them from `sample` and `mode`
   |  2. RunEntityClass.start(): insert a runs row, status "running", id = random hex,
   |     mode live or recorded (submit) or manual (enter)
   |  3. asyncio.create_task(_process(...)) and keep the task in a set
   v
202 Accepted  {"id": "<run id>"}            <-- returned at once, before any stage runs
   |
   v
browser opens /runs/<id>: GET /v1/runs/<id>  and  EventSource /v1/runs/<id>/events

meanwhile, in the background task:
_process                                    (starts at once; runs proceed in parallel)
   |  try:
   |     _read      stages 0 intake, 1 extraction (a typed invoice: intake skipped, extraction converts)
   |     _identify  stages 2 validation, 3 vendor, 4 bank              <- no lock
   |     take the matched vendor's lock (none when no vendor matched)
   |        _check   stages 5 duplicate, 6 po (Jev inside), 7 amounts
   |        _finish  stages 8 decision, 9 persist
   |     release the vendor's lock; the next run of that vendor continues
   |  except any Exception:
   |     _fail    mark the current step failed and close the run as "error"
```

### One lock per vendor

Stages 0 to 4 read only the run's own document and the vendor master, so every run does them at
once, including the model call. Stages 5 to 9 read and write data shared with other runs **of the
same vendor**: that vendor's POs (every PO belongs to one vendor) and its approved invoices (the
duplicate check only compares within a vendor). `PipelineEntityClass` keeps one `asyncio.Lock` per
vendor id and holds it around stages 5 to 9:

```
A (V001) ─ 0..4 ─► lock[V001] free  ─► 5..9 ─► release ─┐
B (V001) ─ 0..4 ─► lock[V001] taken ─► waits ───────────┴─► 5..9: sees A's approval and PO usage
C (V002) ─ 0..4 ─► lock[V002] free  ─► 5..9, alongside A
```

Without it, two copies of one invoice arriving together are both approved, and two invoices on one
PO can each fit its balance and together overdraw it (both were reproduced with the lock disabled:
PO-2024-0142 went to -12,000.00). A run with no matched vendor takes no lock: `VENDOR_NOT_FOUND`
blocks approval, so it consumes no PO and never counts in a duplicate check.

**Reset does not wait for runs.** A run it deletes stores nothing more:

- `RunEntityClass.record` inserts a step with `INSERT ... SELECT ... WHERE EXISTS (the run)`, one
  statement, so a step of a deleted run is never stored.
- The persist transaction starts with `close_in`, an `UPDATE` of the run row. It is the
  transaction's first write, so it takes SQLite's write lock before anything else is written; when
  it matches no row the run was reset away, and the register row, the PO usage and the persist step
  are all skipped. A reset therefore lands entirely before or entirely after a persist.

### How a step is announced, stored and published

Every stage goes through `PipelineEntityClass._step`:

```
_step(run_id, name, work)
   |
   |  publish  StepEvent(state="running", millis=0, data={}, findings=[])   (not stored)
   |
   |  result = work()              a stage result model; awaited if it is async
   |
   |  data     = result as JSON, without its findings
   |  findings = result.findings
   |
   |  RunEntityClass.record(StepEvent(state="done", millis=elapsed, data, findings))
   |     -> insert a steps row      (stored first)
   |     -> publish to followers    (then sent)
   v
return result to the pipeline, which keeps it on the run's Trace
```

A stage whose input does not exist is recorded as `skipped`, with `data = {"reason": "..."}` and no
findings. `running` events are only published. `done`, `skipped` and `failed` events are stored and
then published.

The `Trace` (in `pipeline_invoice.py`) holds the results so far, and the name of the stage now
running. `Trace.findings()` returns every finding so far, in stage order.

### The event stream and replay

The live view uses **Server-Sent Events (SSE)**: one long HTTP response in which the server writes
a text event whenever something happens. The browser's `EventSource` reads it.

The event bus is in `RunEntityClass`. It is a dictionary from run id to a set of `asyncio.Queue`
objects, one queue per open connection. `RunEntityClass.follow(run_id)` works like this:

```
follow(run_id)
   |  1. add a new queue for this follower        (subscribe FIRST)
   |  2. read the run and its stored steps from the database
   |  3. send every stored step; remember its name as finished
   |  4. if the run is no longer "running": send a closing event, stop
   |  5. otherwise loop on the queue:
   |        closing event              -> send it, stop
   |        step already finished      -> drop it (it was in the replay)
   |        otherwise                  -> send it; remember it if its state is final
   v
finally: remove the queue; drop the run's entry when no follower is left
```

Subscribing before reading the stored steps means no event published between the two can be
lost. Dropping events for finished steps means a step is never shown going back from `done` to
`running`. The result: you can open or refresh a run page at any time, during or after the run,
and you get the full history and then the live tail.

### Stage 9 commits everything at once

Stage 8 (decision) is a normal step. Stage 9 (persist) writes in **one database transaction**:

1. the invoice register row, when an extraction exists (whatever the outcome); `register_in` flushes
   it and returns its id;
2. the PO consumption, only when the decision is `approved` and carries an update;
3. the persist step row;
4. the closing values on the run row: status `completed`, outcome, summary, the vendor id, the PO
   number and the register row's id, duration. The run holds references only: the vendor's name,
   the invoice number and the amount (the taxable value: the invoice amount before tax) stay in the
   `vendors` and `invoices` tables, the reasons stay in the steps' findings, and the token usage stays
   in the extraction step's `data`. The run row is the only home of the outcome and the summary.

Only after the commit does it publish the persist step and the closing event. If anything in that
transaction fails, all four roll back together. An approval never consumes a PO without the run
saying so, and the reverse cannot happen either.

### Crashes and restarts

| What happens | Result |
|---|---|
| A stage raises an unexpected exception | `_fail` logs it, stores a `failed` step for the stage named in `trace.stage` with `data = {"error": "<type>: <message>"}`, and closes the run with status `error`, no outcome, and the summary "The run failed unexpectedly at the `<stage>` stage; nothing was approved." |
| Even storing the failure fails | The failed step and the closing event are still published, so the browser stops waiting. The row stays `running` until the next startup. |
| The model call fails in a known way | Not a crash. The adapter raises `ExtractionError`, the extraction stage turns it into an `EXTRACTION_FAILED` finding, and the run completes as `needs_attention`. |
| The server is stopped mid-run | Shutdown cancels the task. Cancellation is not an `Exception`, so `_fail` does not run and the row stays `running`. On the next startup, `RunEntityClass.initialize()` sets every `running` row to `error` with the summary "The server stopped while this run was in progress; nothing was approved." |
| A reset happens while a run is in progress | The run stops storing anything: its later steps and its persist stage write nothing, so the fresh data stays clean. Its background task finishes quietly. |
| A reset happens while pages are following runs | After the reset, `end_streams` sends every open stream a closing event with status `error` and "The demo data was reset; this run no longer exists." |

---

## 5. The ten stages

### Overview

| # | Step name | Lives in | Returns | Skipped when |
|---|---|---|---|---|
| 0 | `intake` | `stages/intake_stage.py` `IntakeStageClass.run` | `IntakeResult` | never |
| 1 | `extraction` | `stages/extraction_stage.py` `ExtractionStageClass.extract` | `ExtractionResult` | intake rejected the file |
| 2 | `validation` | `stages/validation_stage.py` `ValidationStageClass.run` | `ValidationResult` | no extraction |
| 3 | `vendor` | `stages/vendor_stage.py` `VendorStageClass.run` | `VendorMatch` | no extraction |
| 4 | `bank` | `stages/bank_stage.py` `BankStageClass.run` | `BankCheck` | no extraction, or vendor unknown |
| 5 | `duplicate` | `stages/duplicate_stage.py` `DuplicateStageClass.run` | `DuplicateCheck` | no extraction, or vendor unknown |
| 6 | `po` | `stages/po_stage.py` `PoStageClass.run` (Jev: `stages/po_jev.py`) when the code's match is vague | `OrderMatch` (Jev's part in `assist`) | no extraction |
| 7 | `amounts` | `stages/amounts_stage.py` `AmountsStageClass.run` | `AmountCheck` | no extraction, no matched PO, or no taxable value |
| 8 | `decision` | `stages/decision_stage.py` `DecisionStageClass.run` | `Decision` | never |
| 9 | `persist` | `stages/persist_stage.py` `PersistStageClass.run` | step data `registered`, `po_updated`, `remaining_after` | never |

### Stage order

```
 0 intake ------ rejected file? --yes--> skip 1-7 ---------------------------+
    |no                                                                       |
    v                                                                         |
 1 extraction -- nothing read? --yes---> skip 2-7 ---------------------------+
    |                                                                         |
    v                                                                         |
 2 validation    taxable value, arithmetic checks                            |
    v                                                                         |
 3 vendor        who sent it                                                  |
    v                                                                         |
 4 bank -------- vendor unknown? -------------> skipped                       |
    v            payee account vs vendor master                               |
 5 duplicate --- vendor unknown? -------------> skipped                       |
    v            already registered? (needs no PO, so it comes first)         |
 6 po            which PO line each invoice line bills; a line the code      |
    v            cannot settle -> Jev decides it, else the invoice is held     |
 7 amounts  ---- no PO or no taxable value? --> skipped                       |
    v                                                                         |
 8 decision  <----------------------------------------------------------------+
    v
 9 persist       one transaction, then the "done" event
```

**Report-only.** A blocking finding never stops later stages. If the vendor is unknown, the PO and
amount checks still run where they can. The reviewer sees every problem in one pass, not just the
first one. A stage is skipped only when its input does not exist.

### Shared vocabulary

- **Finding**: `Finding(code, severity, message, data)`. `code` is one of 45 fixed codes, 42 of them raised. `severity`
  is `info` (a fact or a passed check), `warn` (worth a look, does not stop payment) or `block`
  (stops automatic approval). `message` is a full sentence with the numbers in it. `data` holds the
  same numbers as strings.
- **Taxable value**: the invoice amount before tax (the "taxable value" line on an Indian GST
  invoice, "Subtotal" on a US one, "Nettobetrag" on a German one, the total minus the VAT on a UK
  VAT-inclusive one). It is the figure compared with the PO, because POs are stored before tax.
- **Tolerance**: how far above the PO's remaining balance an invoice may go and still pass:

  ```
  tolerance = max(pct / 100 * remaining balance, floor),  rounded to 0.01
  pct   = the vendor's tolerance_pct if set, else RULES_TOLERANCE_PCT (2.0)
  floor = RULES_TOLERANCE_FLOORS[the PO's currency]   (INR 500, USD/EUR/GBP 10, JPY 1000; else 0)
  ```

  On an INR balance of 24,000 the tolerance is 500 (2 percent is only 480). On 98,000 it is 1,960.
  When nothing is left on the PO, it is still 500. A USD PO with 4,500 left gets 90 (2 percent beats
  the 10 floor). A floor is an amount of money, so it has to be set per currency: a flat 500 would be
  six dollars on a rupee PO but five hundred on a dollar one.

### Stage 0: intake

- **Where:** `IntakeStageClass.run` (`entities/stages/intake_stage.py`), using `PdfAdapterClass.inspect` and `StoreAdapterClass.save`.
- **Input:** the file name and the raw bytes.
- **Returns:** `IntakeResult`: `filename`, `media_type` (or `"unknown"`), `size_bytes`, `page_count`,
  `encrypted`, `text_layer`, `accepted`, `findings`.

The type comes from the file's first bytes, not from its name or the browser's claim. `%PDF-` is a
PDF, the PNG signature is PNG, `FF D8 FF` is JPEG. For a PDF, pypdf reports encryption, the page
count, and whether any page has extractable text (the **text layer**; a scan has none).

The checks, in order. The first that fails gives the reason:

| Check | Reason in the finding |
|---|---|
| size above `RULES_MAX_BYTES` (20 MB); checked first, before the file is opened | "is larger than the 20 MB limit" |
| not PDF, PNG or JPEG | "is not a PDF, PNG or JPEG" |
| pypdf cannot open it | "could not be opened" |
| encrypted PDF | "is password-protected" |
| more than `RULES_MAX_PAGES` (10) pages | "has N pages, over the 10 page limit" |

| Finding | Severity | When |
|---|---|---|
| `FILE_REJECTED` | block (reject set) | any check above failed; `data` has `size_bytes`, `reason` |

An accepted file is saved to `var/uploads/<run id>/<file name>`. A rejected file is never saved,
and it never reaches the model, so it costs nothing. After a rejection, stages 1 to 7 are stored as
skipped with "The file was rejected at intake.", and stages 8 and 9 still run. The decision is
`rejected` and no register row is written.

**A manual run** (`POST /v1/runs/manual`) has no file. Intake is stored as `skipped` with the reason
"Entered manually; there is no file.", nothing is saved to `var/uploads/`, and the run goes straight
to stage 1.

### Stage 1: extraction

- **Where:** `ExtractionStageClass.extract` (`entities/stages/extraction_stage.py`), the prompts in
  `entities/prompts/`, the adapter `llm_adp.py` or `openai_adp.py` (live), `SeedEntityClass.recording` (recorded).
- **Input:** the bytes, the media type, the file name, and a one-line source hint from intake.
- **Returns:** `ExtractionResult`: `extraction` (an `InvoiceExtraction`, or none on failure),
  `usage` (`input`, `cache_read`, `cache_write`, `output` tokens), `mode` (`live`, `recorded` or
  `manual`), `model`, `seconds`, `findings`.

This is the only stage that **reads** with a model, and the only one that calls a generative model
at all. It reads; it never judges. (Stage 6 asks Jev, an evaluation model, which PO line a line
bills; see [stage 6](#stage-6-po-matching-by-lines).)

**A manual run** calls no model and looks up no recording. `ExtractionStageClass.typed` converts the
typed `InvoiceRead` with the same `convert` a model reading goes through (amounts and dates parsed,
blanks to none) and stores the step as `done` with `mode` `manual`, `model` `none`, `usage` none
(so the run's `tokens` is null), `seconds` 0 and no findings. Stages 2 to 9 then run unchanged: a
typed invoice with no number still blocks, a wrong bank account still blocks, duplicates are still
caught, and an approval still consumes the PO.

#### Two models: what the model fills, and what the code uses

**`InvoiceRead`** is the only schema the model ever sees. Every field is required. Every value is
text, except one boolean and three lists. The model writes `""` for anything not printed, and copies
amounts exactly as printed, for example `"1,70,000.00"`.

| Field | Meaning |
|---|---|
| `supplier_name`, `supplier_tax_id`, `supplier_address`, `supplier_email` | the seller |
| `bank_account_name`, `bank_account_number`, `bank_code`, `bank_name` | the seller's bank details: the account number or IBAN, and the IFSC, SWIFT/BIC, ABA routing number or UK sort code |
| `buyer_name`, `buyer_tax_id` | the customer being billed |
| `invoice_number` | as printed |
| `invoice_date`, `due_date` | asked for as YYYY-MM-DD |
| `currency` | ISO 4217 code |
| `date_order` | `"DMY"`, `"MDY"`, `"YMD"` or `""`: how the document prints numeric dates, judged from its country and any unambiguous date on it |
| `decimal_separator` | `"."`, `","` or `""`: the decimal character of the document's amounts |
| `po_reference_text` | any order reference, verbatim |
| `prices_include_tax` | true when the printed prices and line amounts already include tax, even if the tax is also shown separately |
| `lines` | list of `LineRead`: `description`, `quantity`, `unit`, `unit_price`, `line_amount`, `tax_rate_pct` |
| `subtotal` | pre-tax subtotal or taxable value |
| `tax_lines` | list of `TaxRead`: `label`, `rate_pct`, `amount` |
| `total` | grand total |
| `sources` | list of `SourceRead`: `field`, `text`, the printed text quoted for `invoice_number`, `invoice_date`, `due_date`, `subtotal`, `total`, `po_reference_text` |

Each field carries a `description`, which is sent to the model as part of the schema. The wire models
use `extra="forbid"`, so every object in the JSON schema is closed (no extra keys allowed).

**`InvoiceExtraction`** is what every later stage reads. Code builds it from `InvoiceRead`:

| Field | Type |
|---|---|
| `supplier`, `buyer` | `Party`: `name`, `tax_id`, `address`, `email` (each text or none; the buyer's address and email are always none) |
| `supplier_bank` | `BankDetails`: `account_name`, `account_number`, `bank_code`, `bank_name` |
| `invoice_number`, `currency`, `po_reference_text` | text or none (currency uppercased) |
| `invoice_date`, `due_date` | `date` or none |
| `ambiguous_dates` | the date fields whose printed form reads either way on a document that does not show its order |
| `prices_include_tax` | boolean |
| `lines` | `LineItem`: `description`, `unit` text; `quantity`, `unit_price`, `line_amount`, `tax_rate_pct` as `Decimal` or none |
| `subtotal`, `total` | `Decimal` or none |
| `tax_lines` | `TaxLine`: `label`; `rate_pct`, `amount` as `Decimal` or none |
| `sources` | `SourceQuote`: `field`, `text` (empty quotes dropped) |

**Why two models.** Structured output works best with a simple, closed schema: no optional fields,
no "this or that" types, no free-form objects. A flat all-text schema stays well inside those limits.
It also moves every judgement about numbers and dates out of the model and into small, tested
functions. "1,70,000.00" (Indian grouping for 170,000) is parsed the same way every time, and a value
the code cannot be sure of becomes "unknown" rather than a guess.

#### Conversion rules (`_Helper` in `stages/extraction_stage.py`)

Text: strip spaces; an empty string becomes none.

Amounts (`_Helper.amount`), parsed in the document's own style, given by `decimal_separator`:

```
remove all whitespace, and an apostrophe between digits (Swiss 1'234.50)
find the first run of digits, dots and commas that starts with a digit
   none found                               -> none
drop trailing "." or ","
decimal_separator ","  (1.234,56, 1 234,56):
   more than one comma                      -> none
   remove the dots, the comma becomes the decimal point
otherwise (1,234.56 and Indian 1,70,000.00):
   more than one "."                        -> none   ("1.200.50")
   comma used as the decimal mark (last comma after last dot) and either
      a dot is present or the comma is followed by 1-2 digits at the end
                                            -> none   ("36.000,00", "1,20")
   remove the commas
read as Decimal
a "-" anywhere before or after the number,
   or "(" before and ")" after              -> negative
```

So `"Rs. 1,70,000.00"` gives 170000.00, `"INR 59,000"` gives 59000, `"(500.00)"` gives -500.00, and
on a document with `decimal_separator` `","` `"14.994,00 €"` gives 14994.00. Without it, a
European-looking `"36.000,00"` gives none rather than a wrong number. When the reading reports no
decimal character at all (`""`), a whole number grouped by dots (`"36.000"`, `"1.250 €"`) also gives
none: it could be thirty-six thousand or thirty-six. The prompt asks for `","` whenever dots or
spaces group the thousands, even without decimals.

Dates. The model writes each date as YYYY-MM-DD and reports `date_order`. Code then checks the order
against the printed text (`_Helper.dated`), because a slash date such as 11/04/2024 means 4 November
in the US and 11 April in India, the UK and most of Europe:

```
the model's value, parsed (_Helper.day)          -> none: stays none
the field's source quote has no numeric date     -> keep the model's value
the quote can be read (as d/m or m/d) to give
   the model's value?                            no: the quote is about another date; keep the value
date_order "DMY" or "MDY"                        -> read the quote in that order
one of the two numbers is above 12               -> that one is the day
both 12 or less and different                    -> read day first, and list the field in
                                                    ambiguous_dates (validation warns DATE_AMBIGUOUS)
```

`_Helper.day` accepts YYYY-MM-DD, a numeric date in the document's order (day first when unknown),
and month names: `4 Nov 2024`, `4 November 2024`, `04-Nov-2024`, `4. November 2024`, `Nov 4, 2024`,
`November 4, 2024`. Anything else becomes none.

#### Prompts

- `system-extract.md`: the same for every document. It says the reader reads and never decides,
  explains every field for invoices from any country, and sets rules R1 to R7 (empty means not
  printed, never guess; copy amounts as printed; number styles differ by country and the decimal
  character is reported, not converted; dates go out as YYYY-MM-DD with the order judged from the
  document and reported; read scans the same way; never move a value between seller and buyer; one
  object per document). It ends with three worked examples: an Indian GST invoice, a German invoice
  with decimal commas, MwSt. and an IBAN (different values from the German sample, so the sample is
  never answered from the prompt), and an Indian receipt with GST-inclusive prices.
- `context-extract.md`: "Read the attached invoice and return every field." and the file name.
  `PromptEntityClass.context()` fills `{{FILENAME}}` by plain string replacement, and leaves the
  line out when the upload had no usable name. Nothing else about the file is restated: the model
  receives the document itself (a PDF arrives as each page's text and image), so it already sees
  the type, the page count and whether a page is scanned.

Both files are read once at startup.

#### The live adapters

There are two, with the same `extract` contract; `LLM_PROVIDER` picks. **`llm.env` sets
`LLM_PROVIDER=openai`, so `OpenAIAdapterClass` is what runs** unless that line is changed.

##### `OpenAIAdapterClass` (the configured one)

One call to `AsyncOpenAI().responses.parse(...)`, with `text_format=InvoiceRead` and
`reasoning.effort`. Three choices in it are worth knowing:

- **`store=False`.** Invoices carry bank details, and OpenAI retains responses for 30 days by
  default.
- **Explicit prompt caching.** The system prompt goes first, as a `developer` message carrying a
  `prompt_cache_breakpoint` of mode `explicit` that closes the cached prefix; the document and its
  file name come after it. A fixed `prompt_cache_key` sends every call to the same cache. Explicit
  mode matters: the automatic breakpoint would write each invoice into the cache, where nothing
  could ever read it again. Measured on 2026-11-13: the first call wrote 3,535 tokens to the cache
  and the second read 3,535 back.
- **Token normalisation.** OpenAI counts cached tokens inside `input_tokens`; the adapter subtracts
  them, so `input`, `cache_read`, `cache_write` and `output` mean the same for either provider.

##### `AnthropicAdapterClass`

One call to `AsyncAnthropic().messages.parse(...)`:

```
model          LLM_MODEL
max_tokens     LLM_MAX_TOKENS
output_config  {"effort": LLM_EFFORT}
output_format  InvoiceRead                      (the SDK builds the JSON schema and parses the reply)
system         [ system prompt text, cache_control {"type": "ephemeral", "ttl": LLM_CACHE_TTL} ]
messages       user: [ document block (PDF, base64) or image block (PNG/JPEG, base64),
                       context prompt text ]
```

**Caching.** The system prompt is the same for every document and comes first, marked for caching.
Repeat calls within the cache lifetime can then read it from the cache instead of paying for it
again. The document comes after it, so changing documents do not break the cached part. Usage is
recorded as `input`, `cache_read` (from `cache_read_input_tokens`), `cache_write` (from
`cache_creation_input_tokens`) and `output`, and shown on the run. This has never been observed
running ([section 13](#13-known-limits)).

**Retry and errors.** The client is built with `max_retries=0`, so the adapter's own retry is the
only one.

| What goes wrong | What happens |
|---|---|
| `RateLimitError`, `InternalServerError` (5xx), `APIConnectionError` | wait 2.0 seconds, try once more; a second failure raises `ExtractionError` "The model could not be reached for `<file>` after one retry: `<error type>`." |
| any other `APIStatusError` | `ExtractionError` "The model API returned status `<code>`." |
| the reply does not validate against `InvoiceRead` | `ExtractionError` explaining that a max_tokens cut-off or a refusal are the usual causes |
| `stop_reason` is `refusal` or `max_tokens`, or there is no parsed output | `ExtractionError` "The model did not return a complete reading of `<file>` (stop reason ...)." |

#### Recorded mode

With no key there is no model adapter. `ExtractionStageClass.extract` asks
`SeedEntityClass.recording(document)` for the stored reading of the sample whose PDF has the same
SHA-256 digest as the document (`samples.digest` is indexed; only the `reading` column is
selected). On a match it waits `LLM_REPLAY_DELAY` (0.8 s) so the live view has something to show,
converts the reading and records zero token usage. The file name plays no part: a byte-identical
copy of a sample replays under any name, and any other document, even one named `INV-001.pdf`,
gets no reading. The readings were validated against `InvoiceRead` when they were seeded. They are
written by `scripts/make_invoices.py` from the same specification that draws the PDFs, and are what
a careful reader should produce; they were not captured from a model.

| Finding | Severity | When |
|---|---|---|
| `EXTRACTION_FAILED` | block | live: the adapter raised `ExtractionError`. Message: "The invoice could not be read: `<error>`. Retry the run or enter the invoice manually." Recorded: no sample has these bytes. Message: "The invoice could not be read: no recorded reading matches this document; set ANTHROPIC_API_KEY to read new documents, or enter the invoice by hand." |

Skipped when intake rejected the file. When extraction fails, stages 2 to 7 are skipped with
"Nothing was extracted." and the run ends `needs_attention`.

### Stage 2: validation

- **Where:** `ValidationStageClass.run` (`entities/stages/validation_stage.py`), using `RulesEntityClass` for the shared bands and the GSTIN shape.
- **Input:** `InvoiceExtraction` and today's date.
- **Returns:** `ValidationResult`: `taxable_value`, `tax_total`, `tax_rate_pct`, `derived`, `findings`.

It computes the taxable value once, so stages 5, 6 and 7 compare the same number:

```
subtotal printed?  --yes--> taxable = subtotal                         derived = false
      |no
      v
total printed and tax line amounts printed?
      --yes--> taxable = total - sum(tax amounts)                      derived = true
      |no
      v
total printed and a tax rate known?
   (rate = sum of tax line rates when they share one rate, such as CGST 9 + SGST 9;
    else the single rate shared by all lines)
      --yes--> taxable = total / (1 + rate / 100), rounded to 0.01    derived = true
      |no
      v
taxable = none
```

`tax_total` is the sum of the tax line amounts, else `total - taxable`. `tax_rate_pct` is the sum of
the tax line rates, else `tax_total / taxable * 100` rounded to 0.01. Tax lines at **different**
rates (German VAT at 19 and 7 percent) tax different lines, so their sum is no rate at all:
`tax_rate_pct` stays none and each line is checked only against the rate it prints.

| Finding | Severity | Rule |
|---|---|---|
| `MISSING_INVOICE_NUMBER` | block | no invoice number |
| `MISSING_DATE` | warn | no readable invoice date |
| `DATE_IN_FUTURE` | warn | invoice date later than today |
| `TAX_ID_FORMAT_INVALID` | warn | the supplier tax ID, uppercased with non-alphanumerics removed, looks like a GSTIN (15 characters, two digits then five letters) but is not GSTIN-shaped (GSTIN is defined below the table); other countries' IDs, such as a 15-digit Indonesian NPWP, are never checked |
| `DATE_AMBIGUOUS` | warn | a printed date reads as day/month or month/day and the document does not show which; it was read day first |
| `TAXABLE_VALUE_DERIVED` | info | the taxable value was derived from the total |
| `MISSING_TOTAL` | block | no total, or no taxable value could be found |
| `NON_POSITIVE_AMOUNT` | block | taxable value zero or below, or any line quantity or line amount negative (a credit note or a misread) |
| `LINE_SUM_MISMATCH` | block | not derived, lines carry amounts, and the line amounts differ from the taxable value by more than 1 |
| `TAX_SUM_MISMATCH` | block | subtotal, total and at least one tax amount are present, and subtotal + tax differs from total by more than 1 |

A **GSTIN** is India's 15-character GST registration number. The shape check is: 2 digits (state
code), 5 letters, 4 digits, 1 letter (those ten characters are the PAN, the income tax number), one
character from 1-9 or A-Z, the letter Z, and one final letter or digit. The final character is a
check digit. The code does not compute it.

Never skipped once there is an extraction.

### Stage 3: vendor identification

- **Where:** `VendorStageClass.run` (`entities/stages/vendor_stage.py`), using `VendorEntityClass.records` and `MatchAdapterClass.ratio`.
- **Input:** the supplier `Party` and `BankDetails` from the extraction, and the vendor master
  (the table of vendors the company may pay).
- **Returns:** `VendorMatch`: `vendor_id`, `legal_name`, `method` (`tax_id`, `bank_account`,
  `name_fuzzy`, `none`), `score`, `needed`, `evidence` (per field: `signal`, `state`, `weight`,
  `points`), `candidates` (`vendor_id`, `legal_name`, `score`, `basis`: name similarity or weighted score), `findings`.

Tax IDs, account numbers and bank codes are compared after uppercasing and removing everything that
is not a letter or digit, so spaces and dashes never matter. Three international rules sit on top:

- **VAT prefix.** One side may drop its two-letter country prefix: "123 4567 89" on a UK invoice is
  GB123456789, and 287654321 is DE287654321. Two different prefixes never match, and a GSTIN starts
  with digits, so it is compared exactly.
- **IBAN.** An IBAN equals the domestic account number it ends with (GB29NWBK60161331926819 holds
  31926819), so an invoice printing either form pays the same account. The shorter number needs at
  least six characters.
- **Bank code.** A domestic account number (a UK 8-digit account, a US account) repeats across
  banks, so when the invoice and the master both carry a bank code they must agree too. An
  11-character BIC ending in XXX equals its 8-character form. An IBAN is unique on its own.

Every master vendor is scored. A printed field that equals the master earns its weight
(`RULES_VENDOR_WEIGHTS`); a field that differs or is not printed earns nothing; the sum is capped at
100. A vendor is identified only when the sum is more than `RULES_PASS_SCORE` (80).

| Field | Weight | Counts as equal when |
|---|---|---|
| Tax ID | 80 | the keys are equal (prefix rule) |
| Bank account | 60 | the account is the vendor's (IBAN rule) and the bank codes agree |
| Legal name | 30 | the folded names have a token-set ratio of 90 or more |

```
does any vendor match on tax ID or bank account?
   |no --> name match only (below)
   |yes: take the vendor with the highest score
   v
a second such vendor ties, or also scores above 80?  --yes--> VENDOR_AMBIGUOUS (block), no vendor
   |no
   v
score above 80?  (tax ID + name 100, tax ID + account 100, account + name 90)
   --yes--> VENDOR_IDENTIFIED (info), method tax_id or bank_account
   --no---> VENDOR_SCORE_LOW (block); the vendor id IS kept   (tax ID alone 80, account alone 60)

name match only: fold accents (Müller -> muller), drop dots (S.p.A. -> spa), keep letters of any
            script, lowercase, drop the suffix words pvt ltd limited private inc llc llp co corp
            technologies india plc gmbh ag kg sa sas sarl bv nv srl spa ltda pty oy ab kk, score
            every vendor with the token-set ratio (0-100)
   |
   +-- best >= 90 and second best < 85 --> method name_fuzzy, score 30, VENDOR_NAME_ONLY (block)
   |                                       (the vendor id IS kept, so later checks still run)
   +-- otherwise any vendor >= 70 -------> VENDOR_AMBIGUOUS (block), candidates listed, no vendor
   +-- otherwise ------------------------> VENDOR_NOT_FOUND (block), no vendor

then, for the chosen vendor, at any score:
   status "suspended"?                          --> VENDOR_SUSPENDED (block, reject set)
   the invoice prints a tax ID that is not
   the master's (prefix rule applies)?          --> TAX_ID_MISMATCH (block)
```

The payee account is **not** compared here. That is [stage 4](#stage-4-bank-validation), which runs
next for every matched vendor.

| Finding | Severity | Meaning |
|---|---|---|
| `VENDOR_IDENTIFIED` | info | matched by GSTIN or tax ID, or by bank account (shown masked, last 4 characters) |
| `VENDOR_NAME_ONLY` | block | matched by name alone; a person must confirm |
| `VENDOR_SCORE_LOW` | block | one identifier matched with nothing to back it, so the score is not above the pass score |
| `TAX_ID_MISMATCH` | block | the invoice prints a tax ID that differs from the chosen vendor's |
| `VENDOR_AMBIGUOUS` | block | the name is close to one or more vendors without a clear winner |
| `VENDOR_NOT_FOUND` | block | no tax ID, account or name match |
| `VENDOR_SUSPENDED` | block, reject set | the vendor is suspended in the master and must not be paid |

A tax ID match wins over an account match. If the tax ID points to vendor A and the account belongs
to vendor B, the result is A, and stage 4 then raises the bank mismatch. Never skipped once there is
an extraction.

### Stage 4: bank validation

- **Where:** `BankStageClass.run` (`entities/stages/bank_stage.py`), using `VendorEntityClass.records`
  and `IdentityTypes.same_payee`.
- **Input:** the matched vendor id and the `BankDetails` read from the invoice.
- **Returns:** `BankCheck`: `vendor_id`, `state` (`matched`, `mismatched`, `absent`),
  `invoice_account`, `master_account` (both masked to their last four characters), `findings`.
- **Skipped** with "The vendor is unknown, so there is no master account to compare." when stage 3
  found no vendor.

It asks one question: is the account the invoice wants to be paid into the one the vendor master
holds? The accounts are compared exactly as identification compares them, so the IBAN and bank-code
rules of [stage 3](#stage-3-vendor-identification) apply here too.

```
no account printed on the invoice   --> BANK_DETAILS_ABSENT (info): payment goes to the master account
same payee (account and bank code)  --> BANK_ACCOUNT_VERIFIED (info)
otherwise                           --> BANK_ACCOUNT_MISMATCH (block)
```

| Finding | Severity | When |
|---|---|---|
| `BANK_ACCOUNT_VERIFIED` | info | the printed account is the vendor master's |
| `BANK_DETAILS_ABSENT` | info | the invoice prints no account; payment goes to the master's |
| `BANK_ACCOUNT_MISMATCH` | block | a different account, **or** the same domestic number at a different bank code |

The mismatch blocks **at any vendor score**, because a changed payee account on an otherwise
correct invoice is the classic payment-diversion fraud. The message ends "Verify the change with
the vendor by phone before paying." The same domestic number at another bank code is treated as a
different account, because a domestic account number repeats across banks.

### Stage 5: duplicate check

- **Where:** `DuplicateStageClass.run` (`entities/stages/duplicate_stage.py`), using `InvoiceEntityClass.approved`.
- **Input:** the vendor id, the extraction, the taxable value.
- **Returns:** `DuplicateCheck`: `verdict` (`none`, `exact`, `suspected`), `prior_run`, `findings`.
- **Skipped** with "The vendor is unknown." when stage 3 found no vendor.

It compares the invoice only with this vendor's earlier **approved** invoices in the register.

```
normalised number: uppercase, remove spaces, "-" and "/"
same vendor and same normalised number as an approved invoice?
   --yes--> DUPLICATE_EXACT (block, reject set), prior_run set
   |no
   v
same vendor, same taxable value, and (same invoice date or same line fingerprint)?
   --yes--> DUPLICATE_SUSPECTED (block): "may be a re-send under a new number"
   |no
   v
NO_DUPLICATE (info)
```

The **line fingerprint** is the sorted list of (description in lowercase words, quantity, unit
price) for every line, stored as JSON.

### Stage 6: PO matching, by lines

One rule for any number of POs
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
  unmatched or dropped → `LINES_NOT_MATCHED` (block) and stage 7 (amounts) is skipped. A printed PO no line landed on →
  `PO_UNUSED` (block). Amounts never choose a PO; stage 7 (amounts) checks them afterwards.

### Stage 7: amount and line check

- **Where:** `AmountsStageClass.run` (`entities/stages/amounts_stage.py`), with `OrderEntityClass.map_lines` and `RulesEntityClass.tolerance`.
- **Input:** the matched PO (`OrderSnapshot`: header, `remaining = total - consumed`, lines with
  consumed quantities), the taxable value, the invoice currency, the priced lines, the line mapping,
  the tolerance percentage, the invoice tax rate.
- **Returns:** `AmountCheck`: `basis` (always `taxable_value`), `invoice_amount`, `remaining_before`,
  `variance`, `tolerance`, `within`, `level` (`line` or `header`), `lines`, `findings`. Each line
  result has `description`, `po_line`, `quantity`, `ordered`, `consumed_before`, `unit_price`,
  `po_price`, `ok`.
- **Skipped** with "No PO was matched." or "There is no taxable value to compare."

**Priced lines.** When `prices_include_tax` is true, each unit price is brought to the pre-tax basis
POs use: `price / (1 + rate / 100)`, rounded to 0.01. The rate is the line's rate, else the invoice
rate from validation. Otherwise prices are left as they are.

**Line mapping** (`OrderEntityClass._map_line`), one invoice line at a time:

```
best token-set ratio between the invoice description and a PO line description >= 70?
   --yes--> that PO line
   |no
   v
the PO has exactly one line and the unit price is within 0.5 percent of it?
   --yes--> that line
   |no
   v
unmapped
```

**The check:**

```
invoice currency present and different from the PO currency? --> CURRENCY_MISMATCH (block)
                                                                  (the comparison still continues)
variance = taxable value - remaining balance
variance > tolerance?
   --yes--> OVER_CONSUMPTION (block)
   |no
   v
AMOUNT_WITHIN_TOLERANCE (info), and
   taxable >= remaining --> PO_FULLY_INVOICED (info)
   otherwise            --> PARTIAL_DELIVERY (info) "N will remain open on PO"

no invoice lines, or any line unmapped?
   --yes--> HEADER_LEVEL_ONLY (block), level "header", no line checks
   |no
   v
for each mapped line (quantities drawn by earlier lines of the same invoice count too):
   consumed + quantity > ordered                         --> QTY_EXCEEDS_ORDERED (block)
   unit price differs from the PO price by more than pct percent
                                                         --> UNIT_PRICE_MISMATCH (block)
   line rate (else the invoice rate) differs from the PO line rate by more than 0.1
                                                         --> TAX_RATE_MISMATCH (block)
```

When the invoice is above the balance but inside the tolerance, the `AMOUNT_WITHIN_TOLERANCE`
message states the overage: "The taxable value X exceeds the Y left on PO by Z, within the T
tolerance." Otherwise it reads "The taxable value X is within the Y left on PO (tolerance T)."

The tolerance is **one-sided**. Only going above the remaining balance is tested. Invoicing less
is a partial delivery, never an error. The unit price uses the same percentage as the tolerance
(2.0 by default) but with no floor.

The floor comes from the **PO's** currency, which is authoritative for the comparison; an invoice
in another currency is held with `CURRENCY_MISMATCH` anyway.

| Finding | Severity |
|---|---|
| `CURRENCY_MISMATCH` | block |
| `CURRENCY_NOT_READ` | warn |
| `OVER_CONSUMPTION` | block |
| `AMOUNT_WITHIN_TOLERANCE` | info |
| `PO_FULLY_INVOICED` | info |
| `PARTIAL_DELIVERY` | info |
| `HEADER_LEVEL_ONLY` | block |
| `QTY_EXCEEDS_ORDERED` | block |
| `UNIT_PRICE_MISMATCH` | block |
| `TAX_RATE_MISMATCH` | block |

The bands used in stages 2 to 7, side by side:

| Comparison | Band |
|---|---|
| taxable value against the PO balance | tolerance = max(pct of remaining, the PO currency's floor) |
| unit price against the PO line | pct percent, no floor |
| tax rate against the PO line | 0.1 percentage points |
| line sum and tax sum arithmetic | 1 |
| lone-line mapping (a one-line PO matched by price) | 0.5 percent |
| description match | token-set ratio 70 |

### Stage 8: decision

- **Where:** `DecisionStageClass.run` (`entities/stages/decision_stage.py`), using `RulesEntityClass.ordered` and the reject set.
- **Input:** every finding of the run (`Trace.findings()`), the amount check, the PO number.
- **Returns:** `Decision`: `outcome`, `summary`, `reasons`, `gates` (four rows: `vendor`, `bank`,
  `po`, `amount`, each with `passed` and a one-sentence `detail`; a gate passes when its stage ran
  and raised no block finding), `update` (`OrderUpdate`: `po_number`,
  `amount`, `remaining_after`, `quantities` by PO line number). `outcome`, `summary` and `reasons`
  live in memory only: the outcome and summary are stored once, on the run row, and the reasons are
  not stored.

```
sort all findings: reject-set blocks, other blocks, warnings, information
(within a group they keep stage order)
   |
   v
any block?
   |yes                                          |no
   v                                             v
first block in the reject set?                approved
   yes --> rejected                              summary:
   no  --> needs_attention                         "Approved as partial delivery; N remains
summary = the first block's message                 open on PO-..."  or
          + " N more issue(s) to review."          "Approved; PO-... is now fully invoiced, N over
update  = none                                      its balance and within the T tolerance."
                                                   (when it went over), or
                                                   "Approved; PO-... is now fully invoiced."
                                                   ("against inferred PO-..." when inferred)
                                                 update = amount, remaining after,
                                                          quantities per PO line
```

The reject set (`RulesEntityClass._reject_codes`): `FILE_REJECTED`, `VENDOR_SUSPENDED`,
`PO_CANCELLED`, `DUPLICATE_EXACT`. Because the reasons are sorted, "the first block is in the
reject set" means "any block is in the reject set". The sort is `RulesEntityClass.ordered`, the one
implementation of reason order: `decide` uses it, and so does the run service when it rebuilds a
run's reasons on read.

The decision step's `data` holds only `update` (`Decision.outcome`, `summary` and `reasons` are
excluded from the dump). It has no findings of its own. The run page shows the decision's outcome and
summary from the run itself (the `done` event, then the detail reload).

### Stage 9: persist

- **Where:** `PersistStageClass.run` (`entities/stages/persist_stage.py`), with
  `InvoiceEntityClass.register_in`, `OrderEntityClass.consume_in`, `RunEntityClass.record_in` and
  `close_in`.
- **Step data:** `registered` (whether a register row was written), `po_updated` (the PO number or
  none), `remaining_after`.

In one transaction (see [section 4](#stage-9-commits-everything-at-once)):

- **Register row** for every run that produced an extraction, whatever the outcome. It holds the
  vendor, the PO, the number and its normalised key, the date, the taxable value, the fingerprint,
  the outcome and the run id.
- **Consumption**, only when approved: `consume_in` refuses (raises) an amount of zero or below or
  a negative quantity, so a credit can never give a PO its balance back. It adds the amount to the
  PO's `consumed`, adds each quantity to its line's `consumed_qty`, and recomputes the status:

  ```
  cancelled              --> stays cancelled
  consumed <= 0          --> open
  consumed >= total      --> fully_invoiced
  otherwise              --> partially_invoiced
  ```

The PO's consumed **amount** is a header column, separate from the line quantities. A header-level
approval would consume an amount without quantities.

---

## 6. Data model

Tables are created at startup from the ORM classes (`Base.metadata.create_all`). There are no
migrations. Money and quantities use `DecimalText` (exact text).

### `vendors` (`VendorDB.Vendors`)

| Column | Type | Notes |
|---|---|---|
| `vendor_id` | text, primary key | `V001` ... |
| `legal_name` | text | |
| `country` | text(2) | |
| `tax_id` | text | GSTIN, or EIN for the US vendor |
| `pan` | text | Indian income tax number |
| `bank_account`, `bank_code`, `bank_name` | text | |
| `currency` | text(3) | |
| `email_domain`, `payment_terms` | text | |
| `status` | text | `approved` or `suspended` |
| `tolerance_pct` | decimal, nullable | per-vendor tolerance override; empty in the seed |

`POST /v1/vendors` adds a row: the next id after the highest `V` number, `status` `approved`, `pan`
read from an Indian GSTIN (characters 3 to 12) and empty otherwise, `email_domain` and
`payment_terms` empty. A tax ID or bank account that, normalised the way identification compares
them (uppercase, letters and digits only), already belongs to a vendor is refused, because the
vendor ladder relies on both being unique.

### `orders` (`OrderDB.Orders`)

| Column | Type | Notes |
|---|---|---|
| `po_number` | text, primary key | |
| `vendor_id` | text, indexed | |
| `vendor_name` | text | copied from the vendor at seed or creation time |
| `po_date` | date | |
| `currency` | text(3) | |
| `total` | decimal | sum of line quantity times unit price, before tax |
| `consumed` | decimal | amount approved so far |
| `status` | text | `open`, `partially_invoiced`, `fully_invoiced`, `cancelled` |

`POST /v1/orders` adds a PO numbered `PO-<year of its date>-<one more than the highest sequence
across all POs>`, in the vendor's currency, `open`, with nothing consumed.

### `order_lines` (`OrderDB.OrderLines`)

| Column | Type |
|---|---|
| `id` | integer, primary key |
| `po_number` | text, foreign key to `orders`, indexed |
| `line_no` | integer |
| `description`, `unit` | text |
| `quantity`, `unit_price`, `tax_rate_pct`, `consumed_qty` | decimal |

### `invoices` (`InvoiceDB.Invoices`), the invoice register

| Column | Type |
|---|---|
| `id` | integer, primary key |
| `run_id` | text, indexed |
| `vendor_id` | text, nullable, indexed |
| `po_number`, `invoice_number`, `number_key` | text, nullable |
| `invoice_date` | date, nullable |
| `taxable` | decimal, nullable |
| `fingerprint` | text |
| `outcome` | text |

### `runs` (`RunDB.Runs`)

| Column | Type | Notes |
|---|---|---|
| `id` | text, primary key | random hex |
| `filename` | text, nullable | the uploaded name, last path segment only; null for a sample, a typed invoice or an upload with no usable name. Accepted files are saved on disk as `var/uploads/<run id>/document.<ext>`, never under the uploaded name |
| `sample` | text, nullable, indexed, foreign key to `samples` | sample id when started from the scenario |
| `status` | text | `running`, `completed`, `error` |
| `outcome` | text, nullable | |
| `vendor_id` | text, nullable, indexed, foreign key to `vendors` | the identified vendor |
| `po_number` | text, nullable, foreign key to `orders` | the matched PO |
| `invoice_id` | integer, nullable, foreign key to `invoices` | the register row written in stage 8 |
| `summary` | text, nullable | |
| `mode` | text | `live`, `recorded` or `manual` |
| `created_at` | timestamp with time zone | |
| `millis` | integer, nullable | run duration |

Run status and decision outcome are two separate fields. "The process crashed" (`error`, no outcome)
and "the invoice was rejected" (`completed`, `rejected`) are different facts.

A run references its vendor, PO and invoice by id and copies none of their values. The run service
reads the vendor's legal name (`VendorEntityClass.names`) and the invoice number and taxable value
(`InvoiceEntityClass.briefs`) for all the runs of one response in one pair of queries. A run with no
register row (rejected at intake, failed at extraction, or crashed before stage 8) shows no invoice
number and no amount. SQLite does not enforce the foreign keys (the connection never sets
`PRAGMA foreign_keys`); they document the references.

### `steps` (`RunDB.Steps`)

| Column | Type |
|---|---|
| `id` | integer, primary key (gives storage order) |
| `run_id` | text, foreign key to `runs`, indexed |
| `name`, `state` | text |
| `millis` | integer |
| `data` | JSON, the stage result without its findings |
| `findings` | JSON, the only place findings are stored |

### `samples` (`SeedDB.Samples`)

| Column | Type | Notes |
|---|---|---|
| `id` | text, primary key | `INV-001` ... |
| `step` | integer, unique | the order of the scenario |
| `title`, `expect` | text | |
| `outcome` | text | the expected outcome |
| `pdf` | binary, deferred | the sample PDF's bytes |
| `digest` | text(64), indexed | SHA-256 hex digest of `pdf` |
| `reading` | JSON, deferred | the recorded `InvoiceRead` |

Seeded from `samples.json`, `invoices/<id>.pdf` and `invoices/<id>.read.json` at startup when the
table is empty, and restored from them on every reset. Nothing else reads those files. `GET
/v1/admin/samples`, the sample-run route and `run_golden.py` read this table;
the sample-run route takes the PDF from `pdf`, and recorded mode finds a reading by `digest`. `pdf`
and `reading` are deferred columns (`mapped_column(..., deferred=True)`), so a listing's
`select(SeedDB.Samples)` never loads them. The two reads that need one name it explicitly:
`SeedEntityClass.sample(id)` (the sample-run route) selects only the sample's `id` and `pdf`, and
`recording(document)` selects only `reading`.

A run's reasons are not stored. `GET /v1/runs/{run_id}` collects the findings of its stored steps in
storage order and sorts them with `RulesEntityClass.ordered`, which gives the same list `decide`
produced. A crashed run shows the findings of the steps that finished before the crash; a running
run shows none yet.

A run's token usage is not stored on the run either. It is the `usage` in the extraction step's
`data`. History reads it for all listed runs in one query (`RunEntityClass.usages`, which selects
only the run id and `data.usage` of extraction steps), alongside the vendor-name and invoice reads;
detail takes it from the steps it already loaded. A run with no extraction step, or one with no
usage (skipped, failed, or the model call failed), has `tokens` null.

### Seed files (`backend/data/`)

| File | Content |
|---|---|
| `vendors.csv` | The vendor master. All columns of `vendors`. |
| `purchase_orders.csv` | `po_number`, `vendor_id`, `po_date`, `currency`, `cancelled`. Totals are not stored; they are computed from the lines. |
| `po_lines.csv` | `po_number`, `line_no`, `description`, `quantity`, `unit`, `unit_price`, `tax_rate_pct`, `consumed_qty`. A non-zero `consumed_qty` seeds consumption: the PO's `consumed` is the sum of `consumed_qty * unit_price`. |
| `samples.json` | The seed of the `samples` table: `step`, `id`, `title`, `expect`, `outcome`. |
| `golden.json` | Expected results per sample: `outcome`, `codes` that must be present, `vendor`, `po`, `left` (PO balance after the run). |
| `invoices/INV-NNN.pdf` | The sample invoice; seeded into `samples.pdf`. |
| `invoices/INV-NNN.read.json` | Its recorded `InvoiceRead`; seeded into `samples.reading`. |

The buyer on the Indian and US sample invoices is Meridian Retail Pvt Ltd, GSTIN 29AAGCM4567H1Z9, in
Bengaluru; the German one bills Meridian Retail Europe GmbH (Berlin) and the UK one Meridian Retail
UK Ltd (London).

**Vendors (`vendors.csv`)**

| Id | Legal name | Tax ID | Status | Why it is there |
|---|---|---|---|---|
| V001 | Acme Supplies Pvt Ltd | 29AACCA1234F1Z5 | approved | duplicate and re-issue, missing number, credit note |
| V002 | Zenith Traders LLP | 27AAEFZ5678K1Z3 | approved | scanned invoice with tax-inclusive prices; a name that matches no vendor |
| V003 | Nova Components Pvt Ltd | 33AAECN4321P1Z8 | approved | PO inferred |
| V004 | Orion Logistics Pvt Ltd | 07AABCO8765L1Z2 | approved | PO ambiguous |
| V005 | Pixel Print Solutions | 29AAKFP2468M1Z6 | approved | bank mismatch, cancelled PO, the line checks |
| V006 | Nova Component Systems Pvt Ltd | 33AAHCN9753Q1Z4 | suspended | a look-alike of V003, for the name and suspension rules |
| V007 | Global Parts Inc | 84-1234567 (EIN) | approved | US vendor, USD, SWIFT CHASUS33: month-first dates (INV-010) |
| V008 | Kessler Industrietechnik GmbH | DE287654321 (VAT) | approved | German vendor, EUR, IBAN: decimal commas, MwSt. (INV-011) |
| V009 | Harbour & Finch Ltd | GB123456789 (VAT) | approved | UK vendor, GBP, sort code: VAT-inclusive prices (INV-012) |
| V010 | Brightline Infotech Pvt Ltd | 29AAFCB7421K1Z3 | approved | IT and office equipment; multi-line POs (sample 16) |
| V011 | Saffron Facility Supplies Pvt Ltd | 29AAKCS3141R1Z7 | approved | three open POs; the currency and lump-sum samples (5, 22, 23) |
| V012 | Kaveri Office Interiors Pvt Ltd | 29AAJCK5567L1Z2 | approved | reworded and ambiguous lines: both Jev samples (19, 20) |
| V013 | Vertex Workplace Solutions Pvt Ltd | 29AAHCV2468N1Z4 | approved | two POs for the same chair at different prices; unused seed data since the scenario was cut, with no sample and no test |

**Purchase orders (`purchase_orders.csv` and `po_lines.csv`), all before tax.** PO-2024-0142 to
PO-2024-0158 carry one line each; PO-2024-0159 onward are the multi-line POs the line matching and
the Jev samples need.

| PO | Vendor | Date | Line | Total | Seeded status |
|---|---|---|---|---|---|
| PO-2024-0142 | V001 | 2024-10-01 | 50 laptop stands at 1,200.00 | 60,000.00 | open |
| PO-2024-0143 | V002 | 2024-10-05 | 20 ergonomic office chairs at 8,500.00 | 170,000.00 | open |
| PO-2024-0144 | V003 | 2024-10-08 | 300 USB-C docking connector modules at 150.00 | 45,000.00 | open |
| PO-2024-0145 | V004 | 2024-10-10 | 1 lot freight Bengaluru to Mumbai at 80,000.00 | 80,000.00 | open |
| PO-2024-0146 | V004 | 2024-10-12 | 1 lot freight Bengaluru to Delhi at 82,000.00 | 82,000.00 | open |
| PO-2024-0147 | V005 | 2024-10-15 | 5,000 product brochures at 5.00 | 25,000.00 | open |
| PO-2024-0148 | V002 | 2024-09-02 | 10 standing desks at 15,000.00, all 10 already invoiced | 150,000.00 | fully_invoiced |
| PO-2024-0149 | V005 | 2024-09-20 | 2,000 boxes of business cards at 2.50 | 5,000.00 | cancelled |
| PO-2024-0150 | V007 | 2024-10-18 | 100 precision bearings at 45.00, 0 percent tax, in USD | 4,500.00 | open |
| PO-2024-0151 | V002 | 2024-10-21 | 40 monitor arms at 2,450.00 | 98,000.00 | open |
| PO-2024-0152 | V005 | 2024-10-23 | 200 printed tote bags at 250.00 | 50,000.00 | open |
| PO-2024-0153 | V001 | 2024-10-25 | 25 wireless keyboard and mouse combos at 1,800.00 | 45,000.00 | open |
| PO-2024-0154 | V008 | 2024-10-28 | 12 torque wrench sets at 1,249.50, 19 percent VAT, in EUR | 14,994.00 | open |
| PO-2024-0155 | V009 | 2024-10-30 | 8 oak display shelving units at 412.50 net, 20 percent VAT, in GBP | 3,300.00 | open |
| PO-2024-0156 | V002 | 2024-10-26 | 30 ergonomic office chairs at 8,500 | 255,000.00 | open |
| PO-2024-0157 | V005 | 2024-10-27 | 500 spiral notebooks at 60, 12 percent GST | 30,000.00 | open |
| PO-2024-0158 | V001 | 2024-10-29 | 100 cable management trays at 650 | 65,000.00 | open |
| PO-2024-0159 | V010 | 2024-10-30 | four lines: 10 laptops at 52,000; 10 docking stations at 6,500; 20 monitors at 11,000; 20 headsets at 3,200 | 869,000.00 | open |
| PO-2024-0160 | V010 | 2024-10-31 | two lines: 15 office desks at 9,000; 10 filing cabinets at 5,500 | 190,000.00 | open |
| PO-2024-0161 | V010 | 2024-11-01 | 20 magnetic whiteboards at 2,400 | 48,000.00 | open |
| PO-2024-0162 | V011 | 2024-11-04 | three lines: 20 ergonomic mesh task chairs at 7,500; 30 LED desk lamps at 1,800; 12 acoustic partition panels at 6,200 | 278,400.00 | open |
| PO-2024-0163 | V011 | 2024-11-05 | three lines: 6 laser printers at 18,000; 8 paper shredders at 9,500; 5 laminating machines at 4,200 | 205,000.00 | open |
| PO-2024-0164 | V011 | 2024-11-06 | two lines: 4 water dispensers at 14,000; 6 microwave ovens at 8,500 | 107,000.00 | open |
| PO-2024-0165 | V012 | 2024-11-08 | three lines: 40 stackable visitor chairs at 2,600; 6 glass whiteboards (6 x 4 ft) at 12,500; 25 ergonomic footrests at 1,400 | 214,000.00 | open |
| PO-2024-0166 | V012 | 2024-11-12 | three lines: 60 stackable visitor chairs at 2,600; 4 round meeting tables at 32,000; 30 under-desk cable trays at 900 | 311,000.00 | open |
| PO-2024-0167 | V013 | 2024-11-14 | three lines: 40 cafeteria chairs at 2,600; 20 monitor arms at 2,450; 20 wireless headsets at 3,200 | 217,000.00 | open |
| PO-2024-0168 | V013 | 2024-11-20 | 60 cafeteria chairs at 2,800 | 168,000.00 | open |

Every Indian PO line expects 18 percent tax, except PO-2024-0157 at 12 percent; PO-2024-0150
expects 0 percent (a US export), PO-2024-0154 19 percent and PO-2024-0155 20 percent.

The same chair appears on PO-2024-0165 and PO-2024-0166 at one price, and on PO-2024-0167 and
PO-2024-0168 at two different prices. That is deliberate: it is what makes samples 38 to 40 need
Jev, and what the oldest-PO-first allocation rule exists for.

---

## 7. Finding codes and the decision rule

Every code that exists, in the order of `FindingTypes.FindingCode`:

| Code | Severity | Stage | Meaning |
|---|---|---|---|
| `FILE_REJECTED` | block (reject) | 0 intake | too big, wrong type, unreadable, encrypted or too many pages |
| `EXTRACTION_FAILED` | block | 1 extraction | the document could not be read into the schema |
| `MISSING_INVOICE_NUMBER` | block | 2 validation | no invoice number, so it cannot be approved or checked for duplicates |
| `MISSING_TOTAL` | block | 2 validation | no total or no taxable value, so amounts cannot be checked |
| `MISSING_DATE` | warn | 2 validation | no readable invoice date |
| `DATE_AMBIGUOUS` | warn | 2 validation | a numeric date whose day/month order the document does not show |
| `DATE_IN_FUTURE` | warn | 2 validation | invoice dated after today |
| `LINE_SUM_MISMATCH` | block | 2 validation | line amounts do not add up to the taxable value (band 1) |
| `TAX_SUM_MISMATCH` | block | 2 validation | subtotal plus tax does not equal the total (band 1) |
| `TAX_ID_FORMAT_INVALID` | warn | 2 validation | a GSTIN-like tax ID that is not GSTIN-shaped |
| `TAXABLE_VALUE_DERIVED` | info | 2 validation | no subtotal printed; taxable value derived from the total |
| `NON_POSITIVE_AMOUNT` | block | 2 validation | zero or negative taxable value, or a negative line |
| `VENDOR_IDENTIFIED` | info | 3 vendor | matched by tax ID or bank account |
| `VENDOR_NOT_FOUND` | block | 3 vendor | no match at all |
| `VENDOR_AMBIGUOUS` | block | 3 vendor | the name is close to vendors without a clear winner |
| `VENDOR_NAME_ONLY` | block | 3 vendor | matched by name only |
| `VENDOR_SCORE_LOW` | block | 3 vendor | one identifier matched alone; the score is not above the pass score |
| `VENDOR_SUSPENDED` | block (reject) | 3 vendor | the vendor is suspended |
| `TAX_ID_MISMATCH` | block | 3 vendor | printed tax ID differs from the master |
| `BANK_ACCOUNT_MISMATCH` | block | 4 bank | payment account differs from the master, or the same number at another bank code |
| `BANK_ACCOUNT_VERIFIED` | info | 4 bank | the printed account is the vendor master's |
| `BANK_DETAILS_ABSENT` | info | 4 bank | no account printed; payment goes to the master's account |
| `PO_MATCHED` | info | 6 po | every line settled, by printed reference or by the lines (raised alongside `LINES_MATCHED_BY_JEV` when Jev settled some) |
| `PO_NOT_FOUND` | block | 6 po | the referenced PO does not exist, the reference is malformed, or the pool is empty |
| `PO_VENDOR_MISMATCH` | block | 6 po | the PO belongs to another vendor |
| `PO_CANCELLED` | block (reject) | 6 po | the PO was cancelled |
| `PO_INFERRED` | — | — | **no longer raised**; weighted PO inference was replaced by line matching |
| `PO_AMBIGUOUS` | — | — | **no longer raised**; superseded by line matching |
| `PO_SELECTED_BY_JEV` | — | — | **no longer raised**; Jev now settles lines, not whole POs |
| `PO_UNUSED` | block | 6 po | a printed PO that no invoice line landed on |
| `LINES_NOT_MATCHED` | block | 6 po | a line could not be settled with confidence; amounts skipped |
| `LINES_MATCHED_BY_JEV` | info | 6 po | Jev settled the lines the code was not sure about |
| `CURRENCY_MISMATCH` | block | 7 amounts | invoice currency differs from the PO currency |
| `CURRENCY_NOT_READ` | warn | 7 amounts | no currency could be read; the amounts were compared as if in the PO's currency |
| `AMOUNT_WITHIN_TOLERANCE` | info | 7 amounts | taxable value within the remaining balance plus tolerance |
| `PARTIAL_DELIVERY` | info | 7 amounts | less than the remaining balance; the rest stays open |
| `PO_FULLY_INVOICED` | info | 7 amounts | the invoice covers the whole remaining balance |
| `OVER_CONSUMPTION` | block | 7 amounts | above the remaining balance by more than the tolerance |
| `QTY_EXCEEDS_ORDERED` | block | 7 amounts | cumulative quantity above the ordered quantity |
| `UNIT_PRICE_MISMATCH` | block | 7 amounts | unit price off by more than the tolerance percentage |
| `TAX_RATE_MISMATCH` | block | 7 amounts | tax rate off by more than 0.1 |
| `HEADER_LEVEL_ONLY` | block | 7 amounts | lines could not all be mapped; prices and quantities unverified |
| `NO_DUPLICATE` | info | 5 duplicate | no earlier approved invoice matches |
| `DUPLICATE_EXACT` | block (reject) | 5 duplicate | same vendor, same normalised number as an approved invoice |
| `DUPLICATE_SUSPECTED` | block | 5 duplicate | same vendor and taxable value, and same date or same lines |

All 45 codes are defined in `FindingTypes.FindingCode`; **42 are raised**. The three marked
above are left in the type so that runs stored before PO matching was rewritten still load.

**The decision rule:**

```
any block finding in the reject set
(FILE_REJECTED, VENDOR_SUSPENDED, PO_CANCELLED, DUPLICATE_EXACT)  --> rejected
any other block finding                                           --> needs_attention
no block finding                                                  --> approved
```

Warnings never change the outcome. They are listed in the reasons for the reviewer.

---

## 8. HTTP API

All routes are under `/v1`. FastAPI's interactive docs are at `http://localhost:8000/docs`.

| Method and path | Request | Success | Errors |
|---|---|---|---|
| `POST /v1/runs` | multipart form, field `file` | 202 `{"id"}` | 422 when `file` is missing |
| `POST /v1/runs/samples/{sample}` | `sample` matches `INV-` and 3 digits | 202 `{"id"}` | 404 "Unknown sample ..." (not in the samples table); 422 for a malformed id. Any sample runs at any time. |
| `POST /v1/runs/manual` | JSON `Manual` (below) | 202 `{"id"}` | 422 for an unknown key, more than 50 lines or 10 tax lines, a string over 500 characters, or a wrong type |
| `GET /v1/runs?outcome=` | optional `approved`, `needs_attention`, `rejected`; an empty value means all | 200 `{"data": [Run]}`, newest first | 422 for another value |
| `GET /v1/runs/{run_id}` | `run_id` up to 64 characters | 200 `Detail` | 404 "Run not found" |
| `GET /v1/runs/{run_id}/events` | | 200 `text/event-stream` | 404 "Run not found" |
| `GET /v1/runs/{run_id}/document` | | 200 the stored PDF or image, with no download header so the browser shows it | 404 "This run has no stored document" (unknown run, typed invoice, rejected file) |
| `GET /v1/orders` | | 200 `{"data": [Order]}` ordered by PO number | |
| `POST /v1/orders` | JSON `NewOrder` (below) | 201 `Order` | 404 "No vendor has the id ..."; 409 "No purchase order can be raised for a suspended vendor, ..."; 422 for no lines or more than 20, a blank description, a quantity or unit price not above zero, a tax rate outside 0 to 100 |
| `GET /v1/vendors` | | 200 `{"data": [Vendor]}` ordered by id | |
| `POST /v1/vendors` | JSON `NewVendor` (below) | 201 `Vendor` | 422 for a blank name, tax ID, account, bank code or bank, a country that is not two letters, a currency that is not three letters, an Indian tax ID that is not a well-formed GSTIN, a tolerance not above 0 or above 20; 409 "The tax ID ... already belongs to ..." or "The bank account ... already belongs to ..." |
| `GET /v1/admin/samples` | | 200 `{"data": [Sample]}` | |
| `GET /v1/admin/samples/{sample}/document` | | 200 the sample's PDF, inline | 404 "Unknown sample" |
| `GET /v1/admin/meta` | | 200 `{"mode", "model"}` | |
| `POST /v1/admin/reset` | | 200 `{"ok": true}` | 429 "Reset at most once every 10 seconds" |

Errors use FastAPI's shape: `{"detail": "..."}`. The refusals listed above for `POST /v1/vendors`
and `POST /v1/orders` carry one sentence in `detail`. A missing required key or a value of the wrong
type, and every 422 of `POST /v1/runs/manual`, comes from request validation, whose `detail` is
FastAPI's list of errors.

**Request bodies.**

| Model | Keys |
|---|---|
| `NewVendor` | `name` (required), `country` (ISO 3166 alpha-2, default `IN`), `tax_id` (required for an Indian vendor, which must have a GSTIN; optional elsewhere, for a supplier that is not VAT-registered), `account` (required; an account number or an IBAN), `bank_code` (required unless the account is an IBAN), `bank` (required), `currency` (ISO 4217, default `INR`), `tolerance` (optional number, percent, above 0 and at most 20). Country and currency are uppercased; an Indian tax ID is stored uppercase with only letters and digits. |
| `NewOrder` | `vendor_id` (required), `date` (ISO date, default today), `lines` (1 to 20), each line `text`, `qty`, `unit` (default `pcs`), `price`, `tax` (percent; when left out, 18 for an Indian vendor, and a 422 "Line N needs a tax rate for a US vendor; enter 0 when no tax applies." for any other). Numbers may be sent as JSON numbers or strings. |
| `Manual` | The fields of `InvoiceRead` without `sources`, every one optional: `supplier_name`, `supplier_tax_id`, `supplier_address`, `supplier_email`, `bank_account_name`, `bank_account_number`, `bank_code`, `bank_name`, `buyer_name`, `buyer_tax_id`, `invoice_number`, `invoice_date`, `due_date`, `currency`, `po_reference_text` (strings, default `""`), `date_order` (default `""`), `decimal_separator` (default `"."`), `prices_include_tax` (default `false`), `lines` (default `[]`, at most 50, each `description`, `quantity`, `unit`, `unit_price`, `line_amount`, `tax_rate_pct`), `subtotal`, `tax_lines` (default `[]`, at most 10, each `label`, `rate_pct`, `amount`), `total`. Every string at most 500 characters; unknown keys are refused. Amounts and dates are typed as a person would write them (`"36,000.00"`, `"04/11/2024"`). |

**Response keys.** Wire models use short aliases (`serialization_alias`):

| Model | Keys |
|---|---|
| `Run` | `id`, `file` (the uploaded name, or null for a sample, a typed invoice or a nameless upload), `sample` (sample id or null), `status`, `outcome`, `vendor_id`, `vendor` (the vendor's legal name), `po`, `invoice_id` (the register row), `number`, `amount` (text, 2 decimals), `currency` (the invoice's, as read), `summary`, `tokens` (`in`, `read`, `write`, `out`, or null; null for a typed invoice), `mode` (`live`, `recorded` or `manual`), `at` (ISO 8601), `ms`. `vendor`, `number` and `amount` are read through `vendor_id` and `invoice_id`; `tokens` is the extraction step's usage |
| `Detail` | every `Run` key plus `reasons` (list of Finding, derived on read from the steps' findings in reason order) and `steps` (list of Step) |
| `Step` | `name`, `state`, `ms`, `data` (the stage result without findings), `findings` |
| `Finding` | `code`, `level` (the severity), `text` (the message), `data` |
| `Order` | `po`, `vendor`, `currency`, `status`, `total`, `used`, `left` (amounts as text), `lines` (each `no`, `text`, `qty`, `used`, `price`, `tax`) |
| Order line | `no`, `text`, `qty`, `used`, `price` |
| `Sample` | `step`, `id`, `title`, `expect`, `outcome`, `ran` (its latest run completed), `result` (that run's outcome) |
| `Vendor` | `id`, `name`, `country`, `tax_id`, `account`, `bank_code`, `bank`, `currency`, `status` (`approved` or `suspended`), `tolerance` (percent as its shortest exact text, for example `"2.5"`, or null) |

Aliases apply to the top-level shapes only. Inside a step's `data`, the stage result keeps its
internal field names (for example `po_number`, not `po`). Findings always arrive as Finding rows,
whose shape (`level`, `text`) the frontend's `normaliseFinding` reads.

### Event stream format

`GET /v1/runs/{run_id}/events` sends two kinds of events. Each is an `event:` line, a `data:` line
with JSON, and a blank line:

```
event: step
data: {"name":"intake","state":"running","ms":0,"data":{},"findings":[]}

event: step
data: {"name":"intake","state":"done","ms":4,"data":{"filename":null,...},"findings":[]}

...

event: done
data: {"status":"completed","outcome":"approved","summary":"Approved as partial delivery; ..."}
```

On connect it replays every stored step, then streams live ones. `done` is always last, and the
stream then ends. For a finished run, the replay is followed by `done` straight away. The service
(`RunServiceV1Class._frames`) writes the framed text. The controller returns it through FastAPI's
`EventSourceResponse`.

---

## 9. Frontend

Vite, React, TypeScript, Tailwind and React Router, with small local UI components in
`frontend/src/components/ui/` and icons from lucide-react. The app is called "Invoice Desk".

| Route | Page | File |
|---|---|---|
| `/` | Process | `src/pages/process.tsx` |
| `/runs/:id` | Run (the live view) | `src/pages/run.tsx` |
| `/dashboard` | Dashboard | `src/pages/dashboard.tsx` |
| anything else | Not found | `src/pages/not-found.tsx` |

`src/components/layout.tsx` gives every page the header with the Process and Dashboard links.

### `src/lib/api.ts`

The typed client: one function per endpoint (`api.meta`, `api.samples`, `api.reset`, `api.runs`,
`api.run`, `api.orders`, `api.runSample`, `api.upload`). It also defines `ApiError` (carries the HTTP
status and the `detail` text; status 0 means the backend is not reachable), `STEP_ORDER` (the ten
step names), and `subscribeRun`, which opens an `EventSource`, listens for `step` and `done`, and
closes itself after `done` or an error.

### `src/lib/use-run.ts`: following a run

```
useRun(id)
   |  GET /v1/runs/<id>            fill the header and any stored steps
   |  subscribeRun(id)             open the event stream
   |
   |  on "step"   --> put the step in the map, keyed by step name
   |                  (a later state replaces an earlier one)
   |  on "done"   --> remember the closing event, reload the run once (the stored truth)
   |  on error    --> mark "degraded" and poll GET /v1/runs/<id> every 1500 ms
   |                  until the status is no longer "running"
   v
returns { detail, steps, done, error, degraded }
```

The first load merges with any steps already streamed rather than replacing them. A reload after
the run has finished replaces them. A 404 shows "This run does not exist."

### Process page

- A mode badge from `/v1/admin/meta`: "Live model" or "Recorded extraction", with the model id.
- An upload zone (click or drag and drop). The browser checks the type or extension (PDF, PNG,
  JPG) before sending. The server checks again by content.
- A "View PDF" link on every sample, which opens `/v1/admin/samples/{sample}/document` in a new tab,
  before or after the sample is run.
- The demo scenario from `/v1/admin/samples`. Each step shows its number, title, sample id,
  expectation, expected outcome and, once run, its last result marked "as expected" or "differs
  from expected". Every step's Run button is enabled while no run is starting.
- "Reset demo data". A 429 is shown as "Demo data was reset a moment ago...".
- Starting a run navigates to `/runs/<id>`.

### Run page, the live view

- Header: file name, outcome badge (or "Processing", or "Error"), mode, duration, start time, total
  tokens. If the stream drops: "Live updates disconnected; refreshing every few seconds."
- Decision banner, once finished: green, amber or red. It shows the outcome, the summary, the
  blocking and warning reasons, and the info findings folded under "Checks passed (N)".
- The invoice as received, from `/v1/runs/{run_id}/document`: a PDF in the browser's own viewer or
  an image, beside the timeline on a wide screen and below it on a narrow one, with an "Open in
  new tab" link. It is shown only when intake accepted a file, so a typed or rejected invoice has none.
- A timeline of the ten steps. Each step shows its state (pending, running, done, skipped, failed,
  not run), its time, its findings coloured by severity, the skip reason, and an "Evidence" panel
  (`src/components/evidence.tsx`) that renders the step's `data`:

| Step | Evidence panel shows |
|---|---|
| intake | file, type, size, pages, text layer ("Scanned, no text layer"), encrypted, accepted |
| extraction | supplier and bank, buyer, invoice fields, line items, totals and tax lines, the quoted source text, and the model block: mode, model, model time and tokens (input, cache read, cache write, output), or "No tokens used" in recorded mode |
| validation | taxable value, tax total, tax rate, whether the value was derived |
| vendor | vendor, id, method, score, candidates |
| bank | the payee account, the master account, and whether they match |
| po | PO, method, signals, candidate POs |
| amounts | basis, numbers, level ("Line level" or "Header level only"), per-line checks |
| duplicate | verdict: no duplicate, exact or suspected |
| decision | outcome, summary, reasons most serious first, PO update (amount consumed, remaining after) |
| persist | invoice register recorded or not, PO balance updated or unchanged, left on PO |

### Dashboard

It refreshes every 4000 ms.

- Tiles: total runs and counts per outcome. Clicking a tile filters the table.
- Run history: time, file, vendor, PO, invoice number, amount, outcome, summary, tokens. A row
  opens its run. The outcome filter is applied in the browser.
- PO balances: PO, vendor, total, used, left (red when below zero), a consumption bar, status.
  The amounts are before tax.

### Development helpers

- `npm run build` runs the TypeScript compiler, then the Vite build.
- `npm run mock` starts `frontend/scripts/mock-api.mjs`, a stand-in API on port 8100 (`MOCK_PORT`)
  with fake step delays, for working on the UI without the backend. Its data is a hand-written copy,
  not the seed files, and differs from them in places.

---

## 10. The sample scenario

Twenty-three sample invoices, in the `samples` table (the story with each PDF and its recorded reading,
seeded from `backend/data/samples.json` and `backend/data/invoices/` at startup and on every reset) and `backend/data/golden.json` (the expected result). Run them in order after a reset. The "left" column is the PO's balance
after the step, taken from `golden.json`. It assumes every earlier step has run; the system does
not enforce the order, so a sample run out of order is checked against the data as it then is.

| Step | Sample | What it shows | Outcome | Codes expected (at least) | PO, left after |
|---|---|---|---|---|---|
| 1 | INV-001 | Happy path: clean PDF. Acme invoices 30 of 50 laptop stands. Approved as a partial delivery; PO-2024-0142 drops from 60,000 to 24,000. | approved | `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0142, 24,000.00 |
| 2 | INV-002 | Scanned PDF, with tax inside the prices. A scanned Zenith invoice with no text layer, printing GST-inclusive prices and one total of 2,00,600.00 with no taxable value row. Vision reads the document and the taxable 1,70,000.00 is derived from the 18% rate. Approved; PO-2024-0143 is fully invoiced. | approved | `TAXABLE_VALUE_DERIVED`, `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PO_FULLY_INVOICED` | PO-2024-0143, 0.00 |
| 3 | INV-003 | Password-protected PDF. Acme sends its invoice as a password-protected PDF. It cannot be opened, so intake refuses it and nothing else runs. Rejected: ask the supplier for an unlocked copy. | rejected | `FILE_REJECTED` | none |
| 4 | INV-004 | No invoice number, and sums that do not add up. An Acme invoice that prints no invoice number. Its line adds up to 45,000.00 but the taxable value printed is 45,500.00, and 45,500.00 plus 8,100.00 GST is not the 54,000.00 printed. The missing number alone blocks, and sums that do not add up are never paid automatically. Needs attention; PO-2024-0153 keeps its 45,000. | needs_attention | `MISSING_INVOICE_NUMBER`, `LINE_SUM_MISMATCH`, `TAX_SUM_MISMATCH`, `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PO_FULLY_INVOICED` | PO-2024-0153, 45,000.00 |
| 5 | INV-006 | Warnings only: future and unclear date, no currency. Saffron bills 8 paper shredders against PO-2024-0163. The invoice prints no GSTIN, no address and no currency, and is dated 03/04/2030: in the future, and nothing on the page says whether that is 3 April or 4 March. Saffron is still identified by its bank account and name. Each of these is a warning and none blocks, so it is approved with the warnings shown. | approved | `DATE_IN_FUTURE`, `DATE_AMBIGUOUS`, `PO_MATCHED`, `CURRENCY_NOT_READ`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0163, 129,000.00 |
| 6 | INV-008 | Credit note: negative amounts, no date, no total. Acme sends a credit note for 5 returned cable trays: a negative quantity and negative amounts (-3,250.00 plus GST). It prints no date and no grand total either. A credit note is never processed or consumed automatically, and without a total the amounts cannot be checked. Needs attention; the missing date is only a warning. | needs_attention | `MISSING_DATE`, `MISSING_TOTAL`, `NON_POSITIVE_AMOUNT`, `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0158, 65,000.00 |
| 7 | INV-010 | International: US invoice, month-first date. A US export invoice in USD printed 11/04/2024. The document is American, so it is read month first as 4 November 2024, not 11 April. Approved; PO-2024-0150 is fully invoiced. | approved | `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PO_FULLY_INVOICED` | PO-2024-0150, 0.00 |
| 8 | INV-011 | International: German invoice, decimal comma and VAT. A German invoice in EUR with amounts like 14.994,00, MwSt. 19 % and an IBAN. Read with the comma as the decimal point. Approved; PO-2024-0154 is fully invoiced. | approved | `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PO_FULLY_INVOICED` | PO-2024-0154, 0.00 |
| 9 | INV-012 | International: UK invoice, VAT-inclusive prices. A UK invoice in GBP whose prices include 20% VAT, with the VAT shown but no net total. The taxable value 3,300.00 is derived from the total and the VAT. Approved; PO-2024-0155 is fully invoiced. | approved | `TAXABLE_VALUE_DERIVED`, `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PO_FULLY_INVOICED` | PO-2024-0155, 0.00 |
| 10 | INV-013 | Supplier not in the vendor master. Lotus Workspace Interiors is not an approved vendor: its GSTIN, bank account and name match nobody. No PO is printed, and no PO can be matched for an unknown vendor. Needs attention: onboard the vendor or refuse the invoice. | needs_attention | `VENDOR_NOT_FOUND`, `PO_NOT_FOUND` | none |
| 11 | INV-014 | Rejected: suspended vendor. Nova Component Systems is suspended in the vendor master. The invoice is rejected whatever it bills; Jev is never asked. | rejected | `VENDOR_SUSPENDED`, `PO_NOT_FOUND` | none |
| 12 | INV-015 | GSTIN misprinted. Acme's invoice prints GSTIN 29AACCA1234F0Z5: not a valid GSTIN shape, and not the 29AACCA1234F1Z5 in the vendor master. The bank account and name still point to Acme, but a different tax ID blocks payment until someone confirms who is invoicing. | needs_attention | `TAX_ID_FORMAT_INVALID`, `TAX_ID_MISMATCH`, `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0153, 45,000.00 |
| 13 | INV-016 | Bank account matches, name does not. An invoice from 'ZT Office Solutions', with no GSTIN, asks to be paid into Zenith's bank account. Only the bank account matches the vendor master (60 points, more than 80 needed), so the vendor is not identified with confidence. Needs attention: confirm it is Zenith. | needs_attention | `VENDOR_SCORE_LOW`, `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0156, 255,000.00 |
| 14 | INV-017 | Vendor found by name only. Orion prints neither its GSTIN nor bank details. The name matches Orion exactly, but a name alone does not prove who is invoicing, so a person must confirm the vendor. Needs attention. | needs_attention | `VENDOR_NAME_ONLY`, `BANK_DETAILS_ABSENT`, `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PO_FULLY_INVOICED` | PO-2024-0145, 80,000.00 |
| 15 | INV-018 | Name close to two vendors. 'Nova Component Pvt Ltd', with no GSTIN or bank details, is close to two vendors in the master: Nova Components (approved) and Nova Component Systems (suspended). The process refuses to choose between them. Needs attention. | needs_attention | `VENDOR_AMBIGUOUS`, `PO_NOT_FOUND` | none |
| 16 | INV-019 | Bank account mismatch. Everything matches except the bank account, which differs from the vendor master. Needs attention as a fraud signal. | needs_attention | `BANK_ACCOUNT_MISMATCH`, `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PO_FULLY_INVOICED` | PO-2024-0147, 25,000.00 |
| 17 | INV-021 | Duplicate invoice. Acme re-sends invoice ACM/24-25/0187. Rejected as an exact duplicate of step 1. | rejected | `DUPLICATE_EXACT`, `PO_MATCHED`, `OVER_CONSUMPTION`, `QTY_EXCEEDS_ORDERED` | PO-2024-0142, 24,000.00 |
| 18 | INV-023 | Re-issued under a new number. Acme sends the same 30 laptop stands as step 1, same date and amount, under a new invoice number. The number check passes, but the details match INV-001, so it is held as a suspected duplicate. PO-2024-0142 keeps its 24,000. Run INV-001 first. | needs_attention | `DUPLICATE_SUSPECTED`, `PO_MATCHED`, `OVER_CONSUMPTION`, `QTY_EXCEEDS_ORDERED` | PO-2024-0142, 24,000.00 |
| 19 | INV-027 | Partial invoice, no PO number: matched by its line. Brightline bills 10 of the 20 monitors on PO-2024-0159 and prints no PO number. Only PO-2024-0159 orders monitors and the description matches exactly, so the code is sure of the line and matches it without Jev. Approved as a partial delivery. | approved | `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0159, 759,000.00 |
| 20 | INV-028 | One invoice split between two POs. Brightline bills headsets from PO-2024-0159 and desks from PO-2024-0160 on one invoice and prints both PO numbers. Each line sits on exactly one of the POs, so the invoice is split: 64,000 on PO-2024-0159 and 135,000 on PO-2024-0160. Approved; both balances drop in one step. | approved | `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0159, 695,000.00 |
| 21 | INV-029 | PO number of another vendor. Orion quotes PO-2024-0153, which belongs to Acme. The PO is not matched to Orion and nothing is drawn from Acme's PO. Needs attention. | needs_attention | `PO_VENDOR_MISMATCH` | none |
| 22 | INV-030 | Rejected: cancelled PO. Pixel Print bills business cards against PO-2024-0149, which was cancelled. Rejected; a printed PO is never handed to Jev. | rejected | `PO_CANCELLED` | none |
| 23 | INV-033 | No PO number: lines found on two POs. Saffron prints no PO number and has three open POs. The invoice bills 10 of 20 chairs and all 30 desk lamps from PO-2024-0162 and 2 of 6 printers from PO-2024-0163. Every line matches one PO line exactly, so the code splits the invoice without asking Jev: 129,000 and 36,000. Approved. | approved | `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0162, 149,400.00 |
| 24 | INV-034 | Jev keeps reworded lines. Kaveri bills 10 'Ergo footrest pad' and 2 'Glass marker board 6ft x 4ft' against PO-2024-0165. They score 74 and 72 against 'Ergonomic footrest' and 'Glass whiteboard (6 x 4 ft)', below the 90 the code needs to be sure, so Jev is asked and confirms both. Approved as a partial delivery. | approved | `PO_MATCHED`, `LINES_MATCHED_BY_JEV`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0165, 175,000.00 |
| 25 | INV-035 | An ambiguous line: Jev is not confident, so a person decides. Kaveri bills 12 'Meeting table wire tray' against PO-2024-0166. The code's best text match is the meeting table (score 72), so it is not sure and asks Jev. Jev rejects the table and is not confident (0.80 or less) that the under-desk cable tray is the same item, so the line stays unmatched. Needs attention: match the line by hand. | needs_attention | `LINES_NOT_MATCHED`, `PO_UNUSED` | PO-2024-0166, 311,000.00 |
| 26 | INV-037 | Freight and a look-alike item on no PO line. Vertex bills 10 wireless headsets, 5 'Monitor' and a freight charge, with no PO number. The headsets match exactly, so the code settles that line without Jev. Jev is asked about the other two: 'Monitor' is not the PO's monitor arm, even at the same price, and freight is on no PO line. Needs attention: those two lines are not on any PO. | needs_attention | `LINES_NOT_MATCHED` | PO-2024-0167, 217,000.00 |
| 27 | INV-038 | Same item on two POs: oldest first. Kaveri bills 30 stackable visitor chairs with no PO number. Both its open POs order that chair at the same price, so the code is not sure which; Jev says both are right, and the code fills the oldest PO first: 30 chairs from PO-2024-0165. Approved; 10 chairs remain on that PO line. Run INV-034 first. | approved | `PO_MATCHED`, `LINES_MATCHED_BY_JEV`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0165, 97,000.00 |
| 28 | INV-039 | One line split across two POs, oldest first. Kaveri bills 70 stackable visitor chairs with no PO number. Jev says the line draws on both POs' chair lines; the code fills the oldest PO first: the last 10 chairs of PO-2024-0165, then 60 of PO-2024-0166. Approved; both chair lines are fully used. Run INV-038 first. | approved | `PO_MATCHED`, `LINES_MATCHED_BY_JEV`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0165, 71,000.00 |
| 29 | INV-040 | Same item on two POs at different prices. Vertex bills 30 cafeteria chairs at 2,800 with no PO number. Both its POs order that chair: at 2,600 (PO-2024-0167, older) and at 2,800 (PO-2024-0168). Jev picks the line with the invoice's price, so the line goes to PO-2024-0168 instead of the code's suggestion. Approved as a partial delivery. | approved | `PO_MATCHED`, `LINES_MATCHED_BY_JEV`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0168, 84,000.00 |
| 30 | INV-043 | Price raised and the wrong GST rate. Pixel Print bills 400 spiral notebooks at 66.00 against the 60.00 of PO-2024-0157, and charges 18% GST on notebooks the PO priced at 12%. The 26,400.00 fits easily inside the 30,000 balance, so only the line checks catch it. Needs attention; PO-2024-0157 keeps its 30,000. | needs_attention | `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY`, `UNIT_PRICE_MISMATCH`, `TAX_RATE_MISMATCH` | PO-2024-0157, 30,000.00 |
| 31 | INV-044 | Quantity more than what is left, on a split invoice. Saffron bills 14 chairs and 2 microwaves with no PO number. The lines match PO-2024-0162 and PO-2024-0164, but only 10 chairs are left on PO-2024-0162 after INV-033. The quantity check fails, so the invoice needs attention and neither PO is consumed. Run INV-033 first. | needs_attention | `PO_MATCHED`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY`, `QTY_EXCEEDS_ORDERED` | PO-2024-0162, 149,400.00 |
| 32 | INV-045 | Invoice in the wrong currency. Saffron bills 2 water dispensers against PO-2024-0164, but in USD; the PO is in INR. A currency difference blocks, so the invoice needs attention. | needs_attention | `PO_MATCHED`, `CURRENCY_MISMATCH`, `AMOUNT_WITHIN_TOLERANCE`, `PARTIAL_DELIVERY` | PO-2024-0164, 107,000.00 |
| 33 | INV-046 | Split invoice with a line that has no unit price. Saffron prints PO-2024-0162 and PO-2024-0164 and bills 4 acoustic panels as a lump sum (24,800.00, no unit price) and 2 water dispensers. Without a unit price the invoice cannot be split between the two POs line by line, so only the totals are compared and the invoice needs attention; neither PO is consumed. | needs_attention | `PO_MATCHED`, `HEADER_LEVEL_ONLY` | PO-2024-0162, 149,400.00 |

The samples follow the pipeline's stages, so the demo walks through one stage at a time: INV-001 (the happy path)
comes first because later samples re-use its invoice and its PO, then intake (2-3), validation (4-6), other countries'
formats (7-9), vendor (10-15), bank (16), duplicates (17-18), PO matching (19-23), Jev (24-29) and the money
checks (30-33). Every finding code the
pipeline raises is shown by at least one sample, except `EXTRACTION_FAILED` (a model failure), which the tests cover.
Three codes are defined but no longer raised: `PO_INFERRED`, `PO_AMBIGUOUS` and `PO_SELECTED_BY_JEV`.

Several samples carry more than one defect, so that one card can prove more than one rule: INV-004 prints no invoice
number *and* sums that do not add up, INV-008 is a credit note with no date and no total, and INV-043 raises the unit
price *and* charges the wrong GST rate. Nothing is stacked onto an invoice whose point is that it passes: INV-001,
INV-002 and INV-006 each carry exactly what their story needs.

How the rules produce the harder ones:

- **INV-004.** Two blocks on one invoice. `MISSING_INVOICE_NUMBER`: "No invoice number could be
  read, so the invoice cannot be approved or checked for duplicates." A register row is still
  written with an empty number, but its outcome is `needs_attention`, so it never counts in a later
  duplicate check. On the same page the line adds up to 45,000.00 but the taxable value printed is
  45,500.00 (`LINE_SUM_MISMATCH`), and 45,500.00 + 8,100.00 GST is not the 54,000.00 printed
  (`TAX_SUM_MISMATCH`). A total that does not add up may be misread or altered, so it is never paid
  automatically. The PO keeps its 45,000.
- **INV-006.** Every problem on this invoice is a warning: the date 03/04/2030 is in the future
  (`DATE_IN_FUTURE`), nothing on the page tells day-first from month-first (`DATE_AMBIGUOUS`, read day
  first), and no currency is printed (`CURRENCY_NOT_READ`). No GSTIN is printed either, but the bank
  account (60) and the name (30) still identify Saffron above 80. Warnings never block, so it is approved.
- **INV-002.** The document is a scan with no text layer, so it is read by vision. No subtotal and
  no tax amounts are printed either. The one line rate, 18, gives 2,00,600 / 1.18 = 1,70,000.00
  (`TAXABLE_VALUE_DERIVED`). Because `prices_include_tax` is true, the unit price is brought to
  pre-tax the same way: 10,030 / 1.18 = 8,500.00, the PO price.
- **INV-008.** A credit note with three things wrong at once. The quantity and the amounts are
  negative (`NON_POSITIVE_AMOUNT`), so it is never processed or consumed automatically. It prints no
  grand total (`MISSING_TOTAL`, a block: without a total the amounts cannot be checked) and no date
  (`MISSING_DATE`, only a warning).
- **INV-010 to INV-012.** Other countries' formats, each read end to end: `date_order` `"MDY"`
  settles "11/04/2024" as 4 November, not 11 April (INV-010); `decimal_separator` `","` makes
  "14.994,00" 14994.00 rather than something misread, and a VAT ID of 11 characters is not held to
  the GSTIN shape (INV-011); a gross-price invoice has its line amounts checked against the total
  rather than the taxable value, and its taxable value derived from the total and the VAT (INV-012,
  `TAXABLE_VALUE_DERIVED`). The same rules are also covered by `test_extraction.py` and
  `test_rules.py`.
- **INV-015 to INV-018.** Vendor identity: tax ID 80, bank account 60 and name 30 points, more than 80
  needed. A misprinted GSTIN that differs from the master blocks at any score (INV-015); a bank account
  alone is 60 (INV-016, `VENDOR_SCORE_LOW`); a name alone is never enough (INV-017, `VENDOR_NAME_ONLY`);
  a name close to two vendors is never guessed (INV-018, `VENDOR_AMBIGUOUS`).
- **INV-019.** The GSTIN matches V005, so the vendor is certain. Only the payment account differs.
- **INV-021.** The same normalised number as the approved INV-001: `DUPLICATE_EXACT`, in the reject
  set, so `rejected`. Report-only mode means the amount stage still runs and also reports the
  over-consumption (36,000 against the 24,000 left).
- **INV-023.** The same vendor, date and taxable value as INV-001 under a new number: the number
  check passes, so it is not an exact duplicate, but the details match and it is held
  (`DUPLICATE_SUSPECTED`). Report-only mode means the amount stage still runs, and it reports the
  over-consumption too: 36,000 against the 24,000 INV-001 left on the PO.
- **INV-034 and INV-035.** Jev is asked only about lines the code is not sure of (best text score 90
  or less, coverage under 80, or another PO line at 70 or more). The pair shows both answers. On
  INV-034 Jev confirms two reworded lines ("Ergo footrest pad" scores 74 against "Ergonomic
  footrest") and the invoice is approved. On INV-035 Jev rejects the code's best guess and is not
  confident about anything else, so the line stays unmatched (`LINES_NOT_MATCHED`), the printed PO
  gets no line (`PO_UNUSED`) and a person decides.
- **INV-043.** Two line checks on one invoice, both inside the PO balance, so only the line
  comparison catches them. The unit price 66.00 is 10 percent above the 60.00 of PO-2024-0157,
  beyond the 2 percent band (`UNIT_PRICE_MISMATCH`), and the invoice charges 18 percent GST on
  notebooks the PO priced at 12 percent (`TAX_RATE_MISMATCH`). The 26,400.00 sits well inside the
  30,000 left, so the amount check alone would have passed it. An invoice that lands just over a
  PO balance but inside tolerance no longer has a sample; `test_rules.py` covers that wording.

## 11. Design decisions

**Why one model call to read, and not several?**
Reading a document of any layout, including a scan or a photo, is the one job that needs a model.
Everything after it is comparison and arithmetic on known values, which code does exactly,
instantly and for free. One call also means one cost per invoice, one place to fail, and one point
to retry.

**Why is there a second model at all?**
One judgement after the reading is not arithmetic: is "Ergo footrest pad" the same item as
"Ergonomic footrest"? That is language, and a text ratio gets it wrong in both directions: 74 for
two names of one item, 100 for "Monitor" against "Monitor arm". So the code settles the lines it
can be sure of and hands the rest to Jev, an evaluation model that answers a yes/no question with a
probability instead of text. A probability can be thresholded, so "held because 0.63 fell between
the bands" is auditable in a way "the model said maybe" is not; the questions are independent, so
no answer can drag another; and the answer cannot be malformed, so there is no parsing step to
fail. Jev picks PO lines and nothing else. It is shown unit prices, because two PO lines can order
the same chair at two prices and only the invoice's price tells them apart, but it does no
arithmetic, allocates no quantity and decides no outcome. Every failure mode (error, timeout, no
recording, an unconfident answer) leaves the line unmatched, which blocks: no Jev failure can
approve an invoice.

**Why do rules decide, and not the model?**
The same invoice must get the same answer every time, including in a live demo. A finance user
must be able to change the tolerance from 2 to 3 percent in a settings file, not in a prompt. An
auditor needs "this rule fired on these numbers", and every finding carries its numbers in `data`.
The model reads, the rules decide, and a person handles what the rules cannot settle.

**Why compare the taxable value and not the total?**
POs record what was agreed before tax. The tax on an invoice depends on things the PO does not fix:
CGST plus SGST inside a state, IGST across states. Comparing totals would make a correct invoice for
60,000 plus 18 percent GST (70,800) look 10,800 over. Tax is checked separately: the rate per line
against the PO line, and the arithmetic in validation.

**Why is the tolerance one-sided?**
Invoicing less than the remaining balance is normal: a partial delivery, or one invoice of several.
Only going over the balance can mean paying for something not ordered. A two-sided tolerance would
flag a legitimate 36,000 invoice against a 60,000 PO. (PO inference uses two-sided closeness,
because there the question is "is this the PO?", not "is this too much?")

**Why a percentage with a floor?**
A pure percentage blocks small invoices on rounding: 2 percent of 1,000 is 20. A pure fixed amount
means nothing across sizes: 500 is large on 1,000 and tiny on 10,00,000. The greater of the two
works for both. A vendor can carry its own percentage.

**Why does a name-only vendor match block?**
A name is not an identity. Anyone can print a name close to a real vendor's. With the code's own
matcher, "Acme Supplies Trading Co" scores 100 against "Acme Supplies Pvt Ltd": the suffixes are
dropped, and a token-set ratio scores a name that contains all of another name's words as a full
match. The tax ID and bank account were checked when the vendor was onboarded. A
name alone keeps the vendor id, so the rest of the checks still run, but a person must confirm it.

**Why is a bank account difference a block?**
A changed bank account on an otherwise correct invoice is the classic invoice fraud: someone
intercepts the invoice and replaces the payee account. The safe action is to confirm by phone with
the vendor, which is what the finding says.

**Why are duplicates checked against approved invoices only?**
Only an approved invoice can be paid twice. If held or rejected invoices counted, an invoice held
for a fixable reason could never be corrected and run again: it would be rejected as a duplicate of
its own earlier attempt.

**Why report-only: why do later stages run after a block?**
A reviewer handed "vendor unknown" fixes that, re-runs, and only then learns that the PO is also
over-consumed. Running every stage that has its input shows every problem in one pass. The block
still decides the outcome. Nothing is approved because later checks passed.

**Why a lock per vendor, and not one for the whole pipeline?**
Approving means reading a PO's balance or the approved invoices and then writing. Two concurrent
runs that share that data could both read the same state and both approve. That data is always one
vendor's, so runs of different vendors never race, and the slow part (the model call) touches no
shared data at all. A lock per vendor around stages 5 to 9 removes the race while unrelated
invoices, and every extraction, run in parallel.

**Why two extraction models (`InvoiceRead` and `InvoiceExtraction`)?**
Structured output is most reliable with a closed, all-required schema of simple types. Optional
fields, unions and free-form objects are where schema limits and refusals come from. So the model
gets plain strings, writes `""` when a value is absent, and copies amounts as printed. Code then
parses numbers and dates with small, tested functions, in the style the model reports for the
document (decimal character, date order). A form that is still unclear is refused or flagged
instead of guessed. Every later stage gets
real `Decimal` and `date` values or none.

**Why send the whole PDF to the model instead of extracting text first?**
A scanned PDF has no text layer, so text extraction returns nothing for the most ordinary scan.
Text extraction also flattens tables. So the code sends the PDF itself to the model as a base64
document block (and a PNG or JPEG as an image block), with the same prompt. That gives one path for
clean PDFs, scans and photos. Intake only tells the model, in the source hint, whether the PDF has a
text layer.

**Why do the lines pick the PO, and never the amount?**
An earlier design weighed signals: one open PO, an amount close to a balance, matching lines. The
rule sets contradicted each other as soon as an invoice touched more than one PO, and an amount
that happens to equal a balance is a coincidence, not evidence. Now there is one rule for any
number of POs: the lines decide which POs an invoice bills, and the amount is only a check
afterwards, in stage 7. A printed PO number restricts the search to that PO rather than selecting
it, so a line that fits none of the printed POs is held instead of paid against a PO the vendor
never named.

**Why can a mistyped PO reference not be replaced by a guess?**
If the invoice prints something that looks like a PO number but does not resolve, the vendor meant
a specific PO. Silently matching it to a different one could bill the wrong commitment. A person
confirms it.

**Why SQLite, and why exact decimals?**
The data is relational (invoice to vendor, invoice to PO, PO balance as a sum), and approving plus
consuming must be one transaction. SQLite gives both with nothing to run on demo day. Money stored
as exact text never passes through a float.

**Why a recorded mode?**
So the whole process can be run, tested and demonstrated without an API key or a network, with the
same pipeline as live mode. A reading is replayed only for a document whose bytes equal a sample's,
because a file name is not evidence of content.

---

## 12. Tests and checks

Run from `backend/`:

```bash
uv run ruff check .                        # lint
uv run ruff format --check .               # formatting
uv run python scripts/check_imports.py     # layer rules; prints "Import rules: OK"
uv run pytest -q                           # the test suite
uv run python scripts/run_golden.py        # the scenario against golden.json
```

`run_golden.py` builds the real registries on the configured database, **resets it**, runs every
sample of the `samples` table in step order against its `golden.json` row, and prints a table: sample, PASS or FAIL, outcome, vendor,
PO, balance left, and notes. It uses whatever mode is configured, so with a key it is the live test
of extraction. A sample passes when the outcome, vendor, PO and balance equal the expected values,
and every expected code is present (others may appear too). Do not run it during a demo.

`uv run python scripts/make_invoices.py` regenerates the sample PDFs and recordings. It needs the
dev dependencies (reportlab, pypdfium2, Pillow), which `uv` installs by default.

The test suite (`backend/tests/`) uses a temporary database and upload folder, and forces recorded
mode (`LLM_MODE=recorded`) with no replay delay (`conftest.py`). No test touches the network. The
live adapter is tested with a stub client in place of the SDK client.

| File | What it covers |
|---|---|
| `conftest.py` | Test settings (temp database, recorded mode, no key) and fixtures: `rules` (default tolerance), `adapter`, `registry` (entities on freshly reset seed data), `client` (an HTTP client on the app with its lifespan running). Helpers build readings from a sample's recording with fields changed. |
| `test_extraction.py` | Amount and date parsing cases; decimal-comma amounts (1.249,50, 1 234,56) and Swiss apostrophes; the printed date settling day/month order (month first on a US reading, ambiguous flagged, a quote about another date ignored, an empty value kept empty); month-name dates; conversion of blanks and Indian grouping; an unknown document in recorded mode gives `EXTRACTION_FAILED`. |
| `test_recorded.py` | Sample rows carry the PDF bytes, digest and reading after a reset and after startup seeds an empty table; a sample run works with every seed-folder read made to fail; a sample's exact bytes under another name replay to its normal outcome; listing samples never selects `samples.pdf` or `samples.reading` (checked on the emitted SQL); the sample route gives 404 for an unknown sample, and INV-028 runs straight after a reset (approved, PO-2024-0159 left at 805,000.00); that route sends exactly one samples query (`id` and `pdf`, no join), no `runs` query, and never reads `reading`. |
| `test_schema.py` | `InvoiceRead`'s JSON schema is closed and fully required; every recorded reading validates; the live adapter's request shape (document and image blocks, cache marker), using a stub client; refusal, truncation, missing or unparseable output raise; a rate limit is retried once; a second connection failure raises; status errors carry the status. |
| `test_rules.py` | Validation (clean invoice, missing values, future date and bad GSTIN, sum mismatches, taxable value derived from the rate and from tax amounts); tolerance (2 percent with a floor, both sides of the boundary, an overage inside tolerance stated in numbers, the floor when nothing is left); partial invoices; unit price, quantity, tax rate and currency checks; the header-level fallback; the decision's ordering and outcome; the PO update on approval. |
| `test_vendor.py` | Tax ID wins; bank account identifies without a tax ID; name alone blocks; close names are ambiguous; an unknown supplier is not found; the suspended look-alike is blocked; a changed bank account. |
| `test_order.py` | Explicit references (also in a line description and as a bare number); unknown, other vendor's and cancelled POs; a fully invoiced PO still matches; the candidate pool with no PO printed (open POs, dated on or before the invoice, newest first, capped) and with an unknown vendor; line mapping by description and by price on a one-line PO; consumption moves the status and balance; a reset restores seeded consumption. |
| `test_duplicate.py` | Same number is exact; a changed suffix is only suspected; the same lines on another date are suspected; a different amount or vendor is not a duplicate; rejected invoices do not count. |
| `test_pipeline.py` | The whole golden scenario (every sample of `samples.json`, in step order) in recorded mode, with the steps checked contiguous; detailed checks on INV-002 and INV-004; the SSE stream to `done` and the exact wire keys; history, orders and samples; an out-of-order sample gets 409; reset is rate limited; an unknown run gets 404; uploads run the pipeline; unusable files are rejected at intake; a document named `INV-001.pdf` whose bytes differ from the sample's gets `EXTRACTION_FAILED` with the recorded-mode sentence; an empty outcome filter; the decision step stores no reasons and the detail's reasons are derived from the steps; the derived reasons for INV-001, INV-023 and INV-021 equal what `decide` returned, no stored step data holds `reasons` and the `runs` table has no `reasons` column; a run stores only the vendor, PO and invoice ids and history resolves the name, number and amount through them, for every sample; a reset clears the register and the next run resolves again; a crashing stage ends the run as an error with no vendor, invoice, number or amount; a crash after the PO stage keeps the findings gathered before it; rejected and unrecorded runs resolve to nulls; the decision step's data holds only `update`; the `runs` table has no `usage` column; history `tokens` equals the extraction step's usage and is null for a run rejected at intake; the Run entity reads usage from extraction steps for many runs at once and for one run's detail. |
| `test_master.py` | Samples are seeded into their table and served from it; startup seeds an empty samples table without a reset; the sample route 404s for a sample removed from the table; a reset restores samples, vendors and POs after a vendor and a PO were created; vendors are listed by id with the exact keys; a vendor is created with the next id, its PAN and normalised fields; every vendor validation failure (422) and a taken tax ID or account (409); a PO is created with the next number across years, the vendor's currency and default unit and tax; the date defaults to today; every PO validation failure (404, 409, 422). |
| `test_manual.py` | A typed INV-001 is approved with the same codes as the sample and leaves 24,000.00; typed again it is an exact duplicate; a wrong bank account and a missing number need attention; an empty body runs and needs attention; unknown keys and body limits give 422; the run shows mode `manual`, null tokens, intake skipped and ten steps; a vendor and PO created over HTTP are used by a typed invoice, which is approved and lowers the new PO's balance. |
| `test_assist.py` | Jev settings and the trigger; building the request (the whole invoice without bank details or tax IDs, every pool PO in full, the code's conclusions); the recordings file and its request digest; reading the confidence bands; and the pipeline's fallback when Jev errors, times out or has no recording. |
| `test_jev_triggers.py` | One case per condition for *when* the PO stage hands a line to Jev and when it must not, using a spy that answers 0.02 to everything: which invoice lines are asked about, and which PO lines are offered. No model is called. |
| `test_jev_real_world.py` | Real-world invoice wording against real-world POs: each scenario creates its own vendor and POs over the API, enters an invoice by hand, and sets Jev's answers by PO-line label, then checks whether the code settled the line itself or asked, and how the invoice ended. |
| `test_openai.py` | The OpenAI adapter against a stub client: request construction (developer message, cache breakpoint and key, `store=False`, document vs image block), token normalisation, and how the provider is chosen from the keys and `LLM_PROVIDER`. |
| `test_typesafe.py` | The **real** TypeSafe adapter and SDK driven against a fake TypeSafe server over an injected transport, so the SDK's own serialisation is exercised: what Jev receives, and how its answers are read back. |
| `test_regressions.py` | One test per defect found in review, each failing before its fix: credit notes blocked; consumption refuses non-positive amounts; malformed or unknown PO numbers; a reference without a PO number still infers; the stream never shows a finished step as running again; name-only vendor blocks; header-level-only blocks; an approved run's reasons read as a full note; within tolerance but short is a partial; a held invoice re-run is not its own duplicate; signs and European number forms; the strict ISO date branch; a zero-rate line is respected; the summary leads with the reason; server errors retried once; schema failures explain the likely stop reason; `max_tokens` is capped; odd upload names; a failing failure handler still closes the stream; a mistyped reference beats a PO number in a description. |

---

## 13. Known limits

**One invoice for several POs**

- An invoice is split between POs whenever its lines land on more than one, whether or not the PO numbers are
  printed (INV-028 prints both; a vendor's open POs are searched by line when none is printed). What a split needs is every line settled: a freight or
  discount line that is on no PO line holds the whole invoice for a person.
- One invoice line may draw on several PO lines only when they order the same item (near-identical descriptions
  and the same numbers). Jev choosing several different items holds the line instead.
- The run list and the invoice register show the first PO number only; both balances are updated.

Stated as they are.

**Not verified**

- **The live model has been run only on the samples that existed on 2026-11-13** (`gpt-6-luna`,
  low effort, through the OpenAI Responses API). All of them gave their expected outcome, and two
  were compared field by field with the answer key. There are now 33 samples, so the current set
  has not all been run live. How well it reads real invoices, photos and unusual layouts is
  unverified.
- The recordings that recorded mode replays were generated from the same specification as the PDFs.
  They show what a correct reading looks like, not what the model produces.

**Recorded mode**

- A document is matched to a recording **by its exact bytes** (SHA-256). Only the 33 sample
  PDFs, under any name, have one. Any other document, including a re-saved or re-scanned copy of a
  sample, ends as "needs attention" with `EXTRACTION_FAILED` ("no recorded reading matches this
  document; ...").
- Without a key, manual entry (`POST /v1/runs/manual`) is the way to process a new invoice: a
  person types the fields and every rule from stage 2 on applies.

**Scope: countries and formats**

- Readings carry the document's own number and date style (`decimal_separator`, `date_order`), and
  code parses with them: Indian grouping, 1,234.56, 1.234,56, 1 234,56 and 1'234.50 amounts;
  day-first, month-first and ISO dates and month names. Three samples (US, German, UK) prove it end
  to end. A numeric date whose order the document does not show is read day first and flagged
  `DATE_AMBIGUOUS` for a person to confirm.
- Tax handling is generic: any named tax line (GST, VAT, MwSt., sales tax) with its rate, tax added
  on top or contained in gross prices. Rules specific to one country (reverse charge, withholding,
  US state sales-tax rules, e-invoicing schemas such as ZUGFeRD or the Indian IRN) are not modelled.
- Tolerance floors are set per currency in `rules.env`; a currency with no entry has no floor.
- No currency conversion. An invoice in a different currency from its PO is held with
  `CURRENCY_MISMATCH`. An invoice with no readable currency is not checked for currency at all.
- The GSTIN check is shape only. The check character is not computed, and nothing is looked up in
  the GST registry. Only a 15-character ID that starts with two digits (a GSTIN's state code) is held
  to it. Other countries' tax IDs (EU/UK VAT numbers, US EINs) are matched against the vendor master
  but not format-checked, and VIES or IRS lookups are not done.
- Item descriptions are compared with PO lines as text, so a PO in one language and an invoice in
  another match poorly; the German sample keeps the PO's English item name, as cross-border
  invoices often do.
- PO numbers must have the form `PO-YYYY-NNNN` (or `NNNN-NNNN`). A buyer whose POs are numbered
  differently, such as `4500012` in the prompt's own example, would get `PO_NOT_FOUND`, because that
  reference looks like a PO but does not fit the pattern.
- An amount written with the Indian "/-" ending, such as `1,200/-`, is read as negative (-1200),
  because a minus sign after the number marks it negative. The prompt asks the model to copy amounts
  as printed, so such an invoice would end with `NON_POSITIVE_AMOUNT`.
- Two-way match only (invoice against PO). There is no goods receipt (three-way match).
- One buyer. The buyer's name and tax ID are read but not checked.

**Process**

- A held invoice has no "resolve" action. A person can only correct the document and run it again.
- When the vendor is unknown, the duplicate check is skipped.
- A credit note is never processed automatically. It always blocks with `NON_POSITIVE_AMOUNT`.
- An approval can take a PO below zero within the tolerance (for example 99,000 against 98,000
  leaves -1,000). The PO is then `fully_invoiced` with a negative balance.
- The per-vendor tolerance override exists in the table but no seed vendor uses it. A vendor
  created through the API may set one.
- Vendors and POs can be created but not edited, suspended, cancelled or deleted through the API.
  A reset removes every one that was created.
- PO numbers carry a four-digit sequence. Creation does not stop at 9999; a 10,000th PO would get a
  five-digit sequence that the matcher's pattern does not read.

**Running it**

- **Deployment config exists but is not proof of a running service.** `backend/Dockerfile` and
  `backend/railway.json` (healthcheck `/v1/admin/meta`) deploy the API to Railway, and
  `frontend/vercel.json` serves the frontend on Vercel, rewriting `/v1` to the Railway host. Check
  the URL before relying on it; otherwise it runs on a local machine.
- No authentication. Anyone who can reach the backend can upload, read every run, and reset all
  data. The reset rate limit (once per 10 seconds) is kept in memory.
- The vendor locks, the event bus and the task list live in one process. Running the backend with
  more than one worker would give each worker its own locks, so two workers could approve the same
  invoice twice, and the live view would miss events from the other worker.
- Run history has no paging. The dashboard loads every run every 4 seconds.
- No database migrations. A schema change needs the SQLite file deleted (`backend/var/`). An
  existing file gains a missing `samples` table at startup, but `create_all` adds no column to a
  table that exists: a file whose `samples` table predates the `pdf`, `digest` and `reading` columns
  fails on those reads until that table is dropped (it holds only seed data, so dropping it and
  restarting loses nothing) or the file is recreated. Its `runs` table keeps the old definition
  without the foreign key on `sample` (SQLite does not enforce foreign keys here anyway).
