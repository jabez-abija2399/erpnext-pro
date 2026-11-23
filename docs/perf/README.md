# Performance measurements

## How to reproduce (dev/test site with `developer_mode` on)

```bash
bench --site erp.localhost execute erpnext_pro.perf.demo_data.setup_company
bench --site erp.localhost execute erpnext_pro.perf.demo_data.make_stock_history --kwargs '{"entries": 1000}'
bench --site erp.localhost execute erpnext_pro.perf.bench_repost.run
```

`bench_repost.run` posts a backdated Material Receipt for the busiest item, runs the queued
Repost Item Valuation, cancels the receipt and runs that repost too. It records time, query
count, the slowest query shapes and a cProfile of ERPNext code in `docs/perf/latest-repost.json`
(not committed).

Dataset: 1,000 Stock Entries (receipts, transfers, issues) over 300 days, 20 items,
3 warehouses: about 4,000 stock ledger rows and 1,568 GL rows. The repost touches one item
with 249 ledger rows plus dependent transfers.

## Results

### 1. Cache Accounting Dimensions during repost

Fork branch: `jabez-abija2399/erpnext` `perf/cache-accounting-dimensions-in-repost` (based on `version-16`).

Finding: `get_accounting_dimensions` ran 1,534 times in one repost, one query each,
about 27% of repost time. Fix: read once per repost via `cached_accounting_dimensions()`.

| Repost after backdated receipt (3 warm runs) | Time | Queries |
|---|---|---|
| version-16 | 4.34 / 4.52 / 4.70 s | 3,868 |
| with dimension cache | 3.04 / 3.15 / 3.01 s | 2,335 |

About 32% faster and 40% fewer queries. ERPNext tests run with the change, all passing:
`test_accounting_dimension` (8, one new), `test_repost_item_valuation` (42),
`test_stock_ledger_entry` (42).

Raw output: `repost-baseline.json` (first, cold run before the change) and
`repost-dimension-cache.json` (first run after it).

### Next candidates (from the same profile)
- Child tables re-read per voucher: `tabStock Entry Detail` and `tabLanded Cost Taxes and Charges`
  (397 queries each).
- `tabStock Entry` loaded 279 times for 118 vouchers, then fully re-saved (118 full-row UPDATEs).
- `tabCost Center Allocation` looked up per GL row (124 queries).
