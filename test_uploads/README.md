# Test uploads

Ten invoices that are not demo samples: new invoice numbers, quantities and wording against the seeded vendors and
POs. Upload them on the Process page. They need the backend started with live OpenAI reading (the `.env` debug
entry): in saved-readings mode only the demo samples can be read. Each one is meant to run on its own, right after
"Reset demo data". The `.read.json` next to each PDF is what a careful reader should extract; it was used to check
the expected results below without OpenAI.

| File | What it tests | Expected |
|---|---|---|
| UP-01-acme-partial-stands | 15 of 50 laptop stands, PO printed | approved, 42,000.00 left on PO-2024-0142 |
| UP-02-brightline-laptops-docks | 4 laptops + 4 docking stations from a four-line PO | approved, 635,000.00 left on PO-2024-0159 |
| UP-03-saffron-no-po-two-pos | No PO printed; lamps on one PO, laminators on another | approved, split 21,600.00 / 8,400.00 |
| UP-04-zenith-chairs | 12 of 30 chairs | approved, 153,000.00 left on PO-2024-0156 |
| UP-05-kaveri-reworded-chairs | "Visitor chair - stackable, grey", no PO, chair on two POs | approved: Jev confirms, oldest PO first (149,000.00 left on PO-2024-0165) |
| UP-06-vertex-reworded-arm | Headsets + "Single monitor arm with desk clamp" | approved (close enough that the code is sure, no Jev), 178,200.00 left on PO-2024-0167 |
| UP-07-pixel-brochures | 2,000 of 5,000 brochures | approved, 15,000.00 left on PO-2024-0147 |
| UP-08-orion-freight-overpriced | Freight billed at 86,500 against 82,000 | needs attention: `OVER_CONSUMPTION`, `UNIT_PRICE_MISMATCH` |
| UP-09-us-bearings-partial | US invoice in USD, 60 of 100 bearings | approved, 1,800.00 left on PO-2024-0150 |
| UP-10-saffron-too-many-microwaves | 9 microwaves, only 6 ordered | needs attention: `QTY_EXCEEDS_ORDERED` |

Regenerate with the script that made them only if master data changes.
