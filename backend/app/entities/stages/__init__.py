"""
The ten stages of an invoice run, one module each; the pipeline (pipeline_invoice.py) is the only place that knows
their order:

0 intake_stage, 1 extraction_stage, 2 validation_stage, 3 vendor_stage, 4 bank_stage, 5 duplicate_stage,
6 po_stage (with po_jev, Jev's PO selection), 7 amounts_stage, 8 decision_stage, 9 persist_stage.

A stage holds the logic of its check. The entities beside this package own the data (tables, master records, the
register, runs); a stage reads and writes them only through the entity instances it is given.
"""
