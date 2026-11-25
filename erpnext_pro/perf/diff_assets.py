"""Depreciation schedules made by ERPNext, for Ferrum's differential test (Ferrum D-062).

Each case is an asset bought outright, depreciated straight line, monthly, from the day it
is available for use. The output lists ERPNext's schedule (date, amount) per case. Ferrum's
`crates/core/tests/erpnext_depreciation.rs` reads it and compares with its own schedule.

Run:
	bench --site <site> execute erpnext_pro.perf.diff_assets.run --kwargs '{"out": "/tmp/erpnext_depreciation_reference.json"}'
"""

import json
from decimal import Decimal

import frappe
from frappe.utils import add_months, get_last_day, getdate

from erpnext_pro.perf.diff_invoices import ABBR, COMPANY

CATEGORY = "DIFFDEP Equipment"
ITEM = "DIFFDEP-ASSET"
LOCATION = "DIFFDEP Store"

# (name, cost, left over, months, available for use)
CASES = [
	("first_of_month", "36000", "0", 36, "2026-01-01"),
	("mid_month", "36000", "0", 36, "2026-01-15"),
	("left_over_and_odd_cents", "10000", "1000", 7, "2026-03-01"),
	("odd_cents_mid_month", "1000", "0", 3, "2026-02-10"),
]


def _ensure_masters():
	if not frappe.db.exists("Location", LOCATION):
		frappe.get_doc({"doctype": "Location", "location_name": LOCATION}).insert()
	if not frappe.db.exists("Asset Category", CATEGORY):
		frappe.get_doc({
			"doctype": "Asset Category",
			"asset_category_name": CATEGORY,
			"accounts": [{
				"company_name": COMPANY,
				"fixed_asset_account": f"Office Equipment - {ABBR}",
				"accumulated_depreciation_account": f"Accumulated Depreciation - {ABBR}",
				"depreciation_expense_account": f"Depreciation - {ABBR}",
			}],
		}).insert()
	if not frappe.db.exists("Item", ITEM):
		frappe.get_doc({
			"doctype": "Item", "item_code": ITEM, "item_name": ITEM, "item_group": "All Item Groups",
			"stock_uom": "Nos", "is_stock_item": 0, "is_fixed_asset": 1, "asset_category": CATEGORY,
		}).insert()


def _schedule(name, cost, left_over, months, start):
	start = getdate(start)
	asset = frappe.get_doc({
		"doctype": "Asset",
		"asset_name": f"DIFFDEP {name} {frappe.generate_hash(length=6)}",
		"item_code": ITEM,
		"asset_category": CATEGORY,
		"company": COMPANY,
		"location": LOCATION,
		"is_existing_asset": 1,
		"purchase_date": start,
		"available_for_use_date": start,
		"net_purchase_amount": float(cost),
		"purchase_amount": float(cost),
		"calculate_depreciation": 1,
		"finance_books": [{
			"depreciation_method": "Straight Line",
			"total_number_of_depreciations": months,
			"frequency_of_depreciation": 1,
			"depreciation_start_date": get_last_day(start),
			"expected_value_after_useful_life": float(left_over),
		}],
	})
	asset.insert()
	asset.submit()
	rows = frappe.db.sql(
		"""SELECT ds.schedule_date, ds.depreciation_amount
		   FROM `tabDepreciation Schedule` ds
		   JOIN `tabAsset Depreciation Schedule` ads ON ads.name = ds.parent
		   WHERE ads.asset = %s AND ads.docstatus = 1 ORDER BY ds.idx""",
		asset.name,
	)
	return [[str(d), str(Decimal(str(a)).quantize(Decimal("0.01")))] for d, a in rows]


def run(out: str = "/tmp/erpnext_depreciation_reference.json"):
	_ensure_masters()
	cases = []
	for name, cost, left_over, months, start in CASES:
		cases.append({
			"name": name, "cost": cost, "left_over": left_over, "months": months, "start": start,
			"erpnext": _schedule(name, cost, left_over, months, start),
		})
	frappe.db.rollback()  # nothing is kept in the site
	versions = {app: frappe.get_attr(f"{app}.__version__") for app in ("frappe", "erpnext")}
	with open(out, "w") as f:
		json.dump({"versions": versions, "cases": cases}, f, indent=1)
	return {"cases": len(cases), "out": out}
