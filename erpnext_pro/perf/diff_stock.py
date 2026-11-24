"""Differential test data: run stock scenarios through ERPNext and record its results.

The output JSON (scenarios + ERPNext's stock ledger) is the reference that Ferrum's
valuation must match. Dev/test sites only (developer_mode).

    bench --site erp.localhost execute erpnext_pro.perf.diff_stock.run --kwargs '{"out": "/tmp/erpnext_stock_reference.json"}'
"""

import json
import random

import frappe
from frappe.utils import add_days

from erpnext_pro.perf.demo_data import ABBR, COMPANY, _guard

WAREHOUSES = {"A": "Diff A", "B": "Diff B"}


def _scenarios(seed: int = 7):
	rng = random.Random(seed)

	def rate():
		return f"{rng.randint(5000, 15000) / 100:.2f}"

	def random_moves(n, transfers):
		moves, stock = [], {"A": 0, "B": 0}
		for _ in range(n):
			roll = rng.random()
			if roll < 0.45 or stock["A"] + stock["B"] == 0:
				wh = "A" if not transfers or rng.random() < 0.7 else "B"
				q = rng.randint(1, 20)
				moves.append({"kind": "receipt", "qty": str(q), "rate": rate(), "to": wh})
				stock[wh] += q
			elif transfers and roll < 0.65 and stock["A"] > 0:
				q = rng.randint(1, stock["A"])
				moves.append({"kind": "transfer", "qty": str(q), "from": "A", "to": "B"})
				stock["A"] -= q
				stock["B"] += q
			else:
				wh = rng.choice([w for w in ("A", "B") if stock[w] > 0])
				q = rng.randint(1, stock[wh])
				moves.append({"kind": "issue", "qty": str(q), "from": wh})
				stock[wh] -= q
		return moves

	scenarios = []
	for method in ("FIFO", "Moving Average"):
		key = "fifo" if method == "FIFO" else "ma"
		scenarios.append({
			"name": f"{key}_by_hand",
			"method": method,
			"moves": [
				{"kind": "receipt", "qty": "10", "rate": "100", "to": "A"},
				{"kind": "receipt", "qty": "10", "rate": "130", "to": "A"},
				{"kind": "issue", "qty": "15", "from": "A"},
				{"kind": "receipt", "qty": "3", "rate": "10.01", "to": "A"},
				{"kind": "issue", "qty": "8", "from": "A"},
			],
		})
		scenarios.append({"name": f"{key}_random", "method": method, "moves": random_moves(60, False)})
		scenarios.append({"name": f"{key}_transfers", "method": method, "moves": random_moves(60, True)})
	# Same history, but entered out of date order: ERPNext must repost to the same result.
	for s in list(scenarios):
		if s["name"].endswith("_random"):
			order = list(range(len(s["moves"])))
			# Enter receipts first, then everything else (later-dated receipts arrive "late").
			order.sort(key=lambda i: (s["moves"][i]["kind"] != "receipt", i))
			scenarios.append({**s, "name": s["name"] + "_backdated", "entry_order": order})
	start = "2026-01-01"
	for s in scenarios:
		for i, m in enumerate(s["moves"]):
			m["date"] = str(add_days(start, i))
	return scenarios


def _ensure_masters():
	for name in WAREHOUSES.values():
		if not frappe.db.exists("Warehouse", f"{name} - {ABBR}"):
			frappe.get_doc({"doctype": "Warehouse", "warehouse_name": name, "company": COMPANY}).insert()
	frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 0)


def _item(code, method):
	frappe.get_doc({
		"doctype": "Item",
		"item_code": code,
		"item_name": code,
		"item_group": "Products",
		"stock_uom": "Nos",
		"is_stock_item": 1,
		"valuation_method": method,
	}).insert()


def _submit(code, m):
	wh = lambda k: f"{WAREHOUSES[m[k]]} - {ABBR}"  # noqa: E731
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = {"receipt": "Material Receipt", "issue": "Material Issue", "transfer": "Material Transfer"}[m["kind"]]
	se.company = COMPANY
	se.set_posting_time = 1
	se.posting_date = m["date"]
	se.posting_time = "12:00:00"
	row = {"item_code": code, "qty": float(m["qty"]), "conversion_factor": 1}
	if m["kind"] == "receipt":
		row.update(t_warehouse=wh("to"), basic_rate=float(m["rate"]))
	elif m["kind"] == "issue":
		row.update(s_warehouse=wh("from"))
	else:
		row.update(s_warehouse=wh("from"), t_warehouse=wh("to"))
	se.append("items", row)
	se.insert()
	se.submit()


def run(out: str = "/tmp/erpnext_stock_reference.json", seed: int = 7):
	_guard()
	from erpnext_pro.perf.bench_repost import _process_pending_reposts

	_ensure_masters()
	run_id = frappe.generate_hash(length=6)
	results = []
	for s in _scenarios(int(seed)):
		code = f"DIFF-{s['name']}-{run_id}"
		_item(code, s["method"])
		for i in s.get("entry_order", range(len(s["moves"]))):
			_submit(code, s["moves"][i])
			_process_pending_reposts()
		frappe.db.commit()
		sles = frappe.get_all(
			"Stock Ledger Entry",
			filters={"item_code": code, "is_cancelled": 0},
			fields=["posting_date", "warehouse", "actual_qty", "qty_after_transaction", "valuation_rate", "stock_value", "stock_value_difference"],
			order_by="posting_date, posting_time, creation",
		)
		results.append({
			**s,
			"erpnext": [
				{
					"date": str(r.posting_date),
					"warehouse": next(k for k, v in WAREHOUSES.items() if r.warehouse == f"{v} - {ABBR}"),
					"qty": str(r.actual_qty),
					"qty_after": str(r.qty_after_transaction),
					"valuation_rate": repr(r.valuation_rate),
					"stock_value": repr(r.stock_value),
					"value_change": repr(r.stock_value_difference),
				}
				for r in sles
			],
		})
	versions = {app: frappe.get_attr(f"{app}.__version__") for app in ("frappe", "erpnext")}
	with open(out, "w") as f:
		json.dump({"source": "ERPNext", "versions": versions, "company_currency_precision": 2, "scenarios": results}, f, indent=1)
	return {"scenarios": len(results), "out": out, "versions": versions}
