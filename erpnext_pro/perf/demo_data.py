"""Test data for performance measurements. Only for throwaway dev/test sites.

Run:
    bench --site erp.localhost execute erpnext_pro.perf.demo_data.setup_company
    bench --site erp.localhost execute erpnext_pro.perf.demo_data.make_stock_history --kwargs "{'entries': 1000}"
"""

import random

import frappe
from frappe.utils import add_days

COMPANY = "Perf Co"
ABBR = "PC"
START = "2026-01-01"
ITEM_PREFIX = "PERF-ITEM-"
WAREHOUSES = ("Perf Main", "Perf Store A", "Perf Store B")


def _guard():
	if not frappe.conf.get("developer_mode") and not frappe.flags.in_test:
		frappe.throw("Perf data generators only run on sites with developer_mode enabled.")


def setup_company():
	"""Finish the setup wizard with a fixed test company."""
	_guard()
	from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

	if frappe.is_setup_complete():
		return "setup already complete"
	result = setup_complete(
		{
			"language": "English",
			"country": "United States",
			"timezone": "America/New_York",
			"currency": "USD",
			"company_name": COMPANY,
			"company_abbr": ABBR,
			"chart_of_accounts": "Standard",
			"fy_start_date": "2026-01-01",
			"fy_end_date": "2026-12-31",
			"module_accounting": 1,
			"module_stock": 1,
		}
	)
	frappe.db.commit()
	return result


def _ensure_masters(items: int):
	for name in WAREHOUSES:
		if not frappe.db.exists("Warehouse", f"{name} - {ABBR}"):
			frappe.get_doc({"doctype": "Warehouse", "warehouse_name": name, "company": COMPANY}).insert()
	for i in range(items):
		code = f"{ITEM_PREFIX}{i:03d}"
		if not frappe.db.exists("Item", code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": code,
					"item_group": "Products",
					"stock_uom": "Nos",
					"is_stock_item": 1,
					"valuation_method": "FIFO",
					"valuation_rate": 100,
				}
			).insert()
		frappe.db.set_value("Item", code, "valuation_rate", 100)
	frappe.db.commit()


def make_stock_history(entries: int = 1000, items: int = 20, rows: int = 3, days: int = 300, seed: int = 42):
	"""Create submitted Stock Entries spread over `days` days: receipts, transfers and issues."""
	_guard()
	entries, items, rows, days = int(entries), int(items), int(rows), int(days)
	rng = random.Random(int(seed))
	_ensure_masters(items)
	whs = [f"{w} - {ABBR}" for w in WAREHOUSES]
	codes = [f"{ITEM_PREFIX}{i:03d}" for i in range(items)]
	frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)

	for n in range(entries):
		day = add_days(START, int(n * days / entries))
		kind = ("Material Receipt", "Material Transfer", "Material Issue")[n % 3]
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = kind
		se.company = COMPANY
		se.set_posting_time = 1
		se.posting_date = day
		se.posting_time = f"{8 + n % 10:02d}:{n % 60:02d}:00"
		for code in rng.sample(codes, min(rows, len(codes))):
			row = {"item_code": code, "qty": rng.randint(1, 20), "conversion_factor": 1}
			if kind == "Material Receipt":
				row.update(t_warehouse=whs[0], basic_rate=rng.randint(50, 150))
			elif kind == "Material Transfer":
				row.update(s_warehouse=whs[0], t_warehouse=rng.choice(whs[1:]))
			else:
				row.update(s_warehouse=rng.choice(whs))
			se.append("items", row)
		se.insert()
		se.submit()
		if n % 100 == 99:
			frappe.db.commit()
	frappe.db.commit()
	return {
		"stock_entries": frappe.db.count("Stock Entry", {"docstatus": 1}),
		"stock_ledger_entries": frappe.db.count("Stock Ledger Entry", {"is_cancelled": 0}),
		"gl_entries": frappe.db.count("GL Entry", {"is_cancelled": 0}),
	}

