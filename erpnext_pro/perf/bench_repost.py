"""Measure stock reposting: a backdated receipt forces every later stock ledger row
for that item (and dependent transfers) to be revalued, plus their GL entries.

Run (dev/test sites only):
    bench --site erp.localhost execute erpnext_pro.perf.bench_repost.run

Writes nothing permanent: the backdated entry is cancelled at the end
(the cancellation repost is measured as a second sample).
"""

import cProfile
import io
import json
import os
import pstats
import re
import time
from collections import defaultdict

import frappe

from erpnext_pro.perf.demo_data import ABBR, COMPANY, START, _guard


class QueryStats:
	"""Wrap frappe.db.sql to count queries and time them, grouped by query shape."""

	def __init__(self):
		self.by_shape = defaultdict(lambda: [0, 0.0])
		self._orig = None

	@staticmethod
	def shape(query) -> str:
		q = re.sub(r"\s+", " ", str(query)).strip()
		q = re.sub(r"'[^']*'|\b\d+(\.\d+)?\b", "?", q)
		return q[:140]

	def __enter__(self):
		self._orig = frappe.db.sql

		def wrapped(query, *args, **kwargs):
			t = time.perf_counter()
			try:
				return self._orig(query, *args, **kwargs)
			finally:
				s = self.by_shape[self.shape(query)]
				s[0] += 1
				s[1] += time.perf_counter() - t

		frappe.db.sql = wrapped
		return self

	def __exit__(self, *exc):
		frappe.db.sql = self._orig

	def summary(self, top=12):
		total_n = sum(v[0] for v in self.by_shape.values())
		total_t = sum(v[1] for v in self.by_shape.values())
		rows = sorted(self.by_shape.items(), key=lambda kv: kv[1][1], reverse=True)[:top]
		return {
			"queries": total_n,
			"query_seconds": round(total_t, 3),
			"top": [{"count": n, "seconds": round(t, 3), "shape": q} for q, (n, t) in rows],
		}


def _profile(fn):
	prof = cProfile.Profile()
	with QueryStats() as qs:
		t = time.perf_counter()
		prof.enable()
		fn()
		prof.disable()
		elapsed = time.perf_counter() - t
	out = io.StringIO()
	pstats.Stats(prof, stream=out).sort_stats("cumulative").print_stats(r"erpnext/|frappe/database", 25)
	return {"seconds": round(elapsed, 3), **qs.summary(), "profile": out.getvalue()}


def _busiest_item(warehouse):
	return frappe.db.sql(
		"""select item_code, count(*) n from `tabStock Ledger Entry`
		where warehouse=%s and is_cancelled=0 group by item_code order by n desc limit 1""",
		warehouse,
	)[0][0]


def _process_pending_reposts():
	from erpnext.stock.doctype.repost_item_valuation.repost_item_valuation import repost

	names = frappe.get_all(
		"Repost Item Valuation", filters={"status": "Queued", "docstatus": 1}, pluck="name", order_by="creation"
	)
	for name in names:
		repost(frappe.get_doc("Repost Item Valuation", name))
	return names


def run(save: int = 1):
	_guard()
	_process_pending_reposts()  # start from a clean queue
	frappe.db.commit()

	warehouse = f"Perf Main - {ABBR}"
	item = _busiest_item(warehouse)
	future_rows = frappe.db.count(
		"Stock Ledger Entry", {"item_code": item, "is_cancelled": 0}
	)
	result = {
		"dataset": {
			"stock_ledger_entries": frappe.db.count("Stock Ledger Entry", {"is_cancelled": 0}),
			"gl_entries": frappe.db.count("GL Entry", {"is_cancelled": 0}),
			"item": item,
			"item_ledger_rows_all_warehouses": future_rows,
		}
	}

	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Receipt"
	se.company = COMPANY
	se.set_posting_time = 1
	se.posting_date = START
	se.posting_time = "00:00:01"
	se.append("items", {"item_code": item, "qty": 5, "basic_rate": 500, "t_warehouse": warehouse, "conversion_factor": 1})

	def submit():
		se.insert()
		se.submit()
		frappe.db.commit()

	result["backdated_submit"] = _profile(submit)
	queued = frappe.get_all("Repost Item Valuation", filters={"status": "Queued"}, pluck="name")
	result["reposts_queued"] = len(queued)
	result["repost_after_submit"] = _profile(lambda: (_process_pending_reposts(), frappe.db.commit()))

	def cancel():
		se.reload()
		se.cancel()
		frappe.db.commit()

	result["backdated_cancel"] = _profile(cancel)
	result["repost_after_cancel"] = _profile(lambda: (_process_pending_reposts(), frappe.db.commit()))

	if int(save):
		# not frappe.get_app_path: it rewrites "-" to "_" in path parts
		path = os.path.join(os.path.dirname(frappe.get_app_path("erpnext_pro")), "docs", "perf", "latest-repost.json")
		with open(path, "w") as f:
			json.dump(result, f, indent=1)
	return {
		k: (v if not isinstance(v, dict) or "seconds" not in v else {x: v[x] for x in ("seconds", "queries", "query_seconds")})
		for k, v in result.items()
	}
