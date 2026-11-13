# PO stage: live test plan (real Jev)

Date: 2026-11-15. Status: run live on 2026-11-13 (results at the end). The TypeSafe key is in `backend/.env`.

Note: the sample numbers below predate the 2026-11-13 renumbering (see 2026-11-13-sample-coverage.md); old INV-035 to
INV-038 are now INV-034, INV-035, INV-038 and INV-039, and old INV-006 and INV-030 were merged away.

Every case runs on a throwaway database with typed or saved invoice readings, so no extraction model is called.
Groups A, B and E never reach Jev, and that is what they test: they must make **zero** Jev calls even with the key
set. Groups C and F are the ones real Jev answers. Group D tests failures with a fake Jev, since a real outage
can't be staged.

Bands (fixed): Jev's yes > 0.80 = chosen, ≤ 0.50 = rejected, anything between = unsure → held.
Sure rule (fixed): best text score > 90, coverage ≥ 80, no other pool line ≥ 70.

## A. Which POs are searched (no Jev, ever)

| # | Case | Setup | Jev | Expected code decision |
|---|---|---|---|---|
| A1 | One printed PO, valid | Invoice prints PO-X of this vendor | not asked | Pool = PO-X only; continue to lines |
| A2 | Two printed POs, valid | Prints PO-X / PO-Y | not asked | Pool = both; continue |
| A3 | Printed PO does not exist | Prints PO-2024-0999 | not asked | Block `PO_NOT_FOUND` (cause refused), needs attention |
| A4 | Printed PO of another vendor | Prints a PO of vendor V001 on V010's invoice | not asked | Block `PO_VENDOR_MISMATCH` |
| A5 | Printed PO cancelled | Prints PO-2024-0149 | not asked | Block `PO_CANCELLED` → **rejected** |
| A6 | One printed PO among several is bad | Prints a valid PO + a cancelled one | not asked | All refused; rejected |
| A7 | Mistyped PO-like reference | "PO-24-0159" | not asked | Block `PO_NOT_FOUND` (bad_reference); never replaced by another PO |
| A8 | Nothing printed, vendor unknown | Supplier not in master | not asked | Block `PO_NOT_FOUND` (no_vendor) |
| A9 | Nothing printed, no lines on the invoice | Lines empty | not asked | Block `PO_NOT_FOUND` (no_lines) |
| A10 | Nothing printed, vendor has no open PO | Suspended vendor V006 | not asked | Block `PO_NOT_FOUND` (no_open_po) |
| A11 | Invoice dated before every open PO | PO dated after invoice | not asked | No open PO → held |
| A12 | PO number inside a line description | "Laptop stand against PO 2024 0142" | not asked | Pool = PO-2024-0142 |
| A13 | More than 10 open POs, item on the 11th oldest | 11 POs | asked (wrong pool) | Item not in pool → Jev offered only other items → expected all no → held (documents the known limit) |

## B. The code is sure (no Jev)

| # | Case | Example | Jev | Expected code decision |
|---|---|---|---|---|
| B1 | Exact description | "Wireless headset" vs "Wireless headset" | not asked | `code`; approved if money checks pass |
| B2 | SKU prefix | "WH-200 Wireless Headset" | not asked | `code` (text 100, coverage 82) |
| B3 | Small typo | "Wirless headset" | not asked | `code` (96.8) |
| B4 | Case / plural | "WIRELESS HEADSETS" | not asked | `code` (97) |
| B5 | Reworded but complete | "Cement OPC 53 grade 50kg bag" | not asked | `code` (90.6 / 83) |
| B6 | Exact lines on two printed POs | Headset (PO-X) + desk (PO-Y) | not asked | `code` ×2, split, approved |
| B7 | Exact lines on two POs, nothing printed | Same, no reference | not asked | Both POs found by lines, approved |
| B8 | Lines in a different order than the PO | Desk then headset | not asked | `code` ×2 |
| B9 | Same item twice on the invoice (two deliveries) | 10 + 10 of a 20 line | not asked | `code` ×2, approved |
| B10 | Two deliveries exceed the PO | 15 + 10 of 20 | not asked | `code` ×2, held `QTY_EXCEEDS_ORDERED` |
| B11 | Raised price, exact description | 1,300 vs agreed 1,200 | not asked | `code`, held `UNIT_PRICE_MISMATCH` |
| B12 | Fully invoiced printed PO | Exact line, nothing left | not asked | `code`, held `OVER_CONSUMPTION` / quantity |

## C. The code is not sure → real Jev decides (the live part)

For each: what real Jev should answer, and what the code then does.

| # | Case | Invoice line → candidate PO lines | Why the code asks | Expected Jev answer | Expected code decision |
|---|---|---|---|---|---|
| C1 | Verbose wording | "Wireless Headset (Model WH-200, black)" → Wireless headset | coverage 63 | headset **yes** (>0.80) | `jev_kept`, approved |
| C2 | Abbreviation | "Hdst wireless" → Wireless headset | text 89.7 | headset **yes** | `jev_kept`, approved |
| C3 | Short word inside a different item (trap) | "Monitor" → Monitor arm (only line) | coverage 41 | arm **no** (≤0.50) | `jev_dropped`, held `LINES_NOT_MATCHED` |
| C4 | Code's best line is wrong | "Meeting table wire tray" → table (72), cable tray, chair | text 72 | table **no**; cable tray yes or unsure | held if unsure (INV-036 is expected held since 2026-11-13: real Jev stays between 0.20 and 0.58 on the cable tray) |
| C5 | Reworded, code right | "Ergo footrest pad" → footrest (74) | text 74 | footrest **yes** | `jev_kept`, approved (INV-035) |
| C6 | Two sizes on one PO | "Monitor 27\"" → 24" and 27" monitors | rival at 82 | 27" **yes**, 24" **no** | `jev_kept` 27", approved |
| C7 | Two models | "HP 26A toner" → 85A and 26A | rival at 80 | 26A **yes**, 85A **no** | `jev_kept` 26A, approved |
| C8 | Sizes on two POs | "TMT 16mm" → 12 mm (PO-1), 16 mm (PO-2) | text 59 | 16 mm **yes**, 12 mm **no** | `jev_kept`, approved |
| C9 | Service, different words | "Annual maintenance - AC units" → "AMC - air conditioners" | text 42 | AMC **yes** | `jev_kept`, approved |
| C10 | Model code only | "Dell P2422H 24in monitor" → 24" and 27" | text 65 | 24" **yes** | approved |
| C11 | Same item on two POs, fits one | 30 chairs → chair PO-0165 (40 left), chair PO-0166 | rival at 100 | one chair line **yes** (or both) | one PO: approved; both: same item → oldest first, approved (INV-037) |
| C12 | Same item, needs both POs | 70 chairs → 10 left + 60 left | rival at 100 | **both yes** | split 10 + 60 oldest first, approved (INV-038) |
| C13 | Same item, two prices | 30 chairs at 2,800 → chair at 2,600 (PO-A), 2,800 (PO-B) | rival | PO-B **yes** (price seen in context) | approved; if Jev says PO-A → held price |
| C14 | Freight line with goods | headset (sure) + "Freight & handling" | text 32 | freight: every line **no** | headset `code`, freight `jev_dropped`, held |
| C15 | Services on no PO | HVAC AMC + catering → Saffron goods | text ~40 | all **no** | dropped, held (INV-030) |
| C16 | Ambiguous freight POs | "Freight charges, October consignment" → Mumbai, Delhi freight | text 61 | **unsure** (0.50–0.80) or **no** | held (INV-006) |
| C17 | Mixed invoice | exact desk + "Cordless headset" | only line 2 unsure | headset **yes** for line 2 only | desk `code`, headset `jev_kept`, approved |
| C18 | Printed PO, item on another PO | prints PO-0160, bills monitors (only on 0159) | not on pool | offered only PO-0160 lines; all **no** | held `LINES_NOT_MATCHED` + `PO_UNUSED`; never pays PO-0159 |
| C19 | Printed PO, reworded line | prints PO-0159, "Cordless headset" | text 81 | headset **yes** | `jev_kept`, approved |
| C20 | Jev picks two different items | one line, Jev says yes to Mumbai and Delhi freight | — | (if Jev does this) | held: "different items" |

## D. Jev failures (fake Jev; a real outage can't be staged)

| # | Case | Expected code decision |
|---|---|---|
| D1 | Timeout / network error | unsure lines unmatched, held; reason recorded; run never fails |
| D2 | Answer to a question never asked / missing answers | treated as no answer, held |
| D3 | Value outside 0–1 | treated as no answer, held |
| D4 | No key and no recording | held, "no recorded Jev answer" |
| D5 | Recording file corrupt / not writable | held or answered, never a crash |

## E. Earlier stages keep Jev out (no Jev, even with unsure lines)

| # | Case | Expected |
|---|---|---|
| E1 | Bank account differs | not asked; sure lines still matched; held `BANK_ACCOUNT_MISMATCH` |
| E2 | Exact duplicate of an approved invoice | not asked; **rejected** `DUPLICATE_EXACT` |
| E3 | Suspected re-send | not asked; held `DUPLICATE_SUSPECTED` |
| E4 | Invoice number missing | not asked; held |
| E5 | Tax ID differs | not asked; held |
| E6 | Vendor suspended | not asked; **rejected** |

## F. Money after Jev (code checks Jev's lines)

| # | Case | Jev answer | Expected code decision |
|---|---|---|---|
| F1 | Jev picks a line, price differs | yes on a wrong-price line | held `UNIT_PRICE_MISMATCH` |
| F2 | Jev picks one line, quantity too big | yes on one line | held `QTY_EXCEEDS_ORDERED`; no redistribution |
| F3 | Split overflow | yes on both, total exceeds both | newest takes the rest, held |
| F4 | Split where the oldest covers everything | yes on both identical items | one portion on the oldest, approved |
| F5 | Two invoice lines on one PO line | yes for both onto the same line | combined quantity checked |
| F6 | Split with earlier lines already drawing the PO line | — | later portion uses what's really left |

## G. Many-to-many over time

| # | Case | Expected |
|---|---|---|
| G1 | One PO, several invoices | each invoice reduces line "already invoiced"; PO goes open → partially → fully invoiced |
| G2 | One invoice, several POs | one update per PO, all in one transaction |
| G3 | Both at once (INV-022 → 024 → 031) | balances consistent; ledger check passes |
| G4 | Two copies of one invoice at the same instant | one approved, one rejected as duplicate; PO consumed once |

## How it will be run

1. `scripts/jev_trigger_report.py` live: the six Jev samples (C4, C5, C11, C12, C15, C16) against real Jev.
2. A live scenario run of groups C and F (the real-world scenarios, with real Jev instead of the fake), printing for
   each case the request, Jev's real probabilities, and the code's decision, next to the expectation above.
3. Groups A, B, D, E, G: the existing automated tests, plus an assertion that the live run made no Jev call for them.
4. Real answers are recorded (`JEV_RECORD=true`) on a throwaway database; any case where real Jev differs from the
   expectation is reported, not edited.

## Live results (2026-11-13, jev-1.13.0, item-identity questions)

Each question asks only whether one invoice line and one PO line are the same item; quantities, prices and which PO
stay with the code. Identical lines on several POs both get a yes and the code fills the oldest PO first.

| Case | Jev | Code decision | vs expected |
|---|---|---|---|
| C1 verbose headset | 0.96 | jev_kept, approved | match |
| C2 "Hdst wireless" | 0.96 | jev_kept, approved | match |
| C3 "Monitor" vs monitor arm | 0.08 | jev_dropped, held | match |
| C4 INV-036 wire tray | cable tray 0.52, table 0.13 | unsure, held | **differs**: expected cable tray yes |
| C5 INV-035 footrest | 0.86 | jev_kept, approved | match |
| C6 27" monitor | 0.96 / 0.04 | approved | match |
| C7 HP 26A | 0.99 / 0.03 | approved | match |
| C8 TMT 16mm | 0.97 / 0.03 | approved | match |
| C9 AMC | 0.93 | approved | match |
| C10 Dell P2422H | 0.88 / 0.03 | approved | match |
| C11 INV-037 twin chairs | 0.99 / 0.99 | oldest first, approved | match |
| C12 INV-038 70 chairs | 0.99 / 0.99 | split 10 + 60, approved | match |
| C13 chairs at two prices | 0.98 / 0.98 | oldest first (wrong price), held | **differs**: price no longer picks the PO |
| C14 freight line | 0.02 | jev_dropped, held | match |
| C15 INV-030 services | ≤ 0.03 | dropped, held | match |
| C16 INV-006 freight | Delhi 0.68, Mumbai 0.38 | unsure, held | match |
| C17 mixed desk + cordless | 0.93 | approved | match |
| A13 item outside the ten POs | ≤ 0.27 | held | match |

The first item-only wording ("another model, size, type") made Jev hesitate on extra detail (Dell 0.69, cordless
0.75); the wording now says brand, model code or extra detail that one side omits is not a difference.

### Tried and reverted: Jev weighing price and quantity in the same question

Asked "should invoice line n be billed against PO line k? decide by item, then price, then quantity". Price as the
deciding factor worked (matching-price chair 0.93 vs 0.06; 0.92 vs 0.07), but every case where the item was right
and price or quantity added doubt fell into the unsure band and was held: twin chairs 0.76 / 0.90, 70 chairs
0.73 / 0.94, older chair with 10 left 0.74, price matching neither PO 0.69 / 0.34, single headset at a raised price
0.76, INV-035 0.77. One blended probability carries every doubt, so the item-only wording stays.

### Current wording (2026-11-13): Jev chooses by item, then price

Jev keeps the full context (invoice prices and quantities, every PO line's agreed price, ordered, invoiced, left) and
each independent question asks "is PO-x line k a right line for invoice line n? judge the item, then the price".
Among lines ordering the item, only those at the invoice's price are right; equally right lines are all yes and the
code fills the oldest PO first. No price rule in code. Live:

| Case | Jev | Code decision | vs expected |
|---|---|---|---|
| Chairs at two prices, invoice 2,800 | 2,800 PO 0.93, 2,600 PO 0.06 | that PO, approved | match |
| INV-037 twin chairs | 0.96 / 0.97 | oldest first, approved | match |
| INV-038 70 chairs | 0.94 / 0.96 | split, approved | match |
| Same price, older has 10 left | 0.93 / 0.96 | split 10 + 20, approved | match |
| PO at the invoice price has 10 left | 0.93 / 0.07 | that PO, held quantity | match |
| Invoice price matches neither PO (2,700) | 0.67 / 0.52 | unsure, held | held as expected; reason "not matched" not "price" |
| Single headset at a raised price | 0.57 | unsure, held | held as expected; reason "not matched" not "price" |
| INV-035 footrest | 0.89 | approved | match |
| INV-036 wire tray | 0.52 | unsure, held | held (sample is ambiguous) |
| INV-006 freight | 0.41 / 0.30 | dropped, held | match |
| Dell 0.91, cordless 0.89, verbose 0.95, Hdst 0.97, AMC 0.96, toner 0.98, monitor 0.98, TMT 0.97 | | approved | match |
| Monitor arm trap 0.14, freight line 0.04, services ≤ 0.03 | | held | match |
