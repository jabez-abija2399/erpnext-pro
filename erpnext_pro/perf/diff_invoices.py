"""Differential test data: run invoice, tax and payment scenarios through ERPNext and record
the money results, mapped to Ferrum's accounts. Ferrum's test
(ferrum-erp: crates/db/tests/erpnext_invoices.rs) runs the same steps and must agree.
Dev/test sites only (developer_mode).

    bench --site erp.localhost execute erpnext_pro.perf.diff_invoices.run --kwargs '{"out": "/tmp/erpnext_invoices_reference.json"}'

Each scenario lists steps:
  {"op": "purchase"|"sale", "ref": "S1", "date": ..., "lines": [{"item": "cement", "qty": "2", "rate": "10.50"}], "tax": "15" or null}
      A line may carry its own "tax" ("15" = VAT, "2" = turnover tax, null = none): then each
      line is taxed alone through ERPNext item tax templates, one tax row per tax used.
      "inclusive": true means the prices already include the tax (ERPNext "included in print rate").
      A line may name a "unit": "Box" (cement and nails: 1 Box = 12 Nos); qty and rate are then per box.
      An invoice may be in another currency: "currency": "EUR", "rate": "1.0837" (1 EUR in the
      company's USD); its prices are then in EUR and the books in USD.
      A line may have "discount": "10" (percent off its rate); the invoice may have
      "discount": {"percent": "5"} or {"amount": "50"} (off the net total, before tax).
      "withhold": "2" (purchases) withholds that percent of the amount before tax from the supplier
      (ERPNext tax withholding, no thresholds), owed to the tax office instead.
  {"op": "pay", "ref": "PAY1", "invoice": "S1", "date": ..., "amount": "100"}
  {"op": "cancel_payment", "payment": "PAY1"}
  {"op": "cancel_invoice", "invoice": "S1"}
  {"op": "order", "ref": "SO1", "kind": "sales"|"purchase", "date": ..., "lines": [{"item", "qty", "rate"}]}
  {"op": "deliver", "ref": "DN1", "order": "SO1", "date": ..., "lines": [{"line": 0, "qty": "2"}]}
      (a delivery note for a sales order, a purchase receipt for a purchase order)
  {"op": "bill_order", "ref": "S1", "order": "SO1", "date": ..., "tax": ..., "lines": [{"line": 0, "qty": "1"}],
   "from": "DN1" (optional: bill everything that delivery brought, without moving stock again)}
Items: "cement" and "nails" are stock items (FIFO, whole units); "delivery" is a service
(not stock) sold or bought by the kg, so quantities may have decimals.
"""

import json
import random
from decimal import Decimal

import frappe
from frappe.utils import add_days

from erpnext_pro.perf.demo_data import ABBR, COMPANY, _guard

WAREHOUSE = "Diff Invoices"

# ERPNext account -> Ferrum account code. Anything else appearing is reported as unmapped.
ACCOUNTS = {
	f"Cash - {ABBR}": "1000",
	f"Debtors - {ABBR}": "1200",
	f"Stock In Hand - {ABBR}": "1300",
	f"VAT Input - {ABBR}": "1400",
	f"Creditors - {ABBR}": "2000",
	f"VAT Output - {ABBR}": "2100",
	f"TOT Output - {ABBR}": "2100",
	f"TOT Input - {ABBR}": "1400",
	f"TDS Payable - {ABBR}": "2150",
	# Party accounts in EUR (ERPNext needs one per currency); in USD on the books like Ferrum's.
	f"Debtors EUR - {ABBR}": "1200",
	f"Creditors EUR - {ABBR}": "2000",
	f"Stock Received But Not Billed - {ABBR}": "2300",
	f"Sales - {ABBR}": "4000",
	f"Cost of Goods Sold - {ABBR}": "5000",
	f"Miscellaneous Expenses - {ABBR}": "5900",
	f"Round Off - {ABBR}": "5950",
}


# Line tax -> (VAT %, turnover tax %) in its item tax template; "none" taxes nothing.
LINE_TAXES = {"15": (15, 0), "2": (0, 2), "none": (0, 0)}
TAX_NAMES = {"15": "VAT", "2": "TOT"}
UNITS = {"Box": 12}
WITHHOLDING = "DIFFINV WHT 2"


def _scenarios(seed: int = 11):
	rng = random.Random(seed)
	d = lambda i: str(add_days("2026-02-01", i))  # noqa: E731
	scenarios = [
		{
			"name": "service_sale_with_tax_paid_in_two_parts",
			"steps": [
				{"op": "sale", "ref": "S1", "date": d(0), "tax": "15", "lines": [{"item": "delivery", "qty": "3", "rate": "333.33"}]},
				{"op": "pay", "ref": "PAY1", "invoice": "S1", "date": d(1), "amount": "500"},
				{"op": "pay", "ref": "PAY2", "invoice": "S1", "date": d(2), "amount": "649.99"},
			],
		},
		{
			"name": "buy_stock_then_sell_it_fifo",
			"steps": [
				{"op": "purchase", "ref": "P1", "date": d(0), "tax": "15", "lines": [{"item": "cement", "qty": "10", "rate": "1000"}]},
				{"op": "purchase", "ref": "P2", "date": d(1), "tax": None, "lines": [{"item": "cement", "qty": "5", "rate": "1100.50"}]},
				{"op": "sale", "ref": "S1", "date": d(2), "tax": "15", "lines": [{"item": "cement", "qty": "12", "rate": "1500"}, {"item": "delivery", "qty": "1", "rate": "250"}]},
				{"op": "pay", "ref": "PAY1", "invoice": "P1", "date": d(3), "amount": "11500"},
				{"op": "pay", "ref": "PAY2", "invoice": "S1", "date": d(4), "amount": "10000"},
			],
		},
		{
			"name": "cancel_a_payment_then_the_invoices",
			"steps": [
				{"op": "purchase", "ref": "P1", "date": d(0), "tax": "15", "lines": [{"item": "nails", "qty": "40", "rate": "12.25"}, {"item": "delivery", "qty": "2", "rate": "45"}]},
				{"op": "sale", "ref": "S1", "date": d(1), "tax": "15", "lines": [{"item": "nails", "qty": "15", "rate": "19.99"}]},
				{"op": "pay", "ref": "PAY1", "invoice": "S1", "date": d(2), "amount": "100"},
				{"op": "cancel_payment", "payment": "PAY1"},
				{"op": "cancel_invoice", "invoice": "S1"},
				{"op": "sale", "ref": "S2", "date": d(3), "tax": None, "lines": [{"item": "nails", "qty": "40", "rate": "20"}]},
				{"op": "cancel_invoice", "invoice": "S2"},
			],
		},
		{
			"name": "awkward_amounts_and_rounding",
			"steps": [
				{"op": "sale", "ref": "S1", "date": d(0), "tax": "15", "lines": [
					{"item": "delivery", "qty": "2.5", "rate": "19.99"},
					{"item": "delivery", "qty": "0.75", "rate": "13.33"},
					{"item": "delivery", "qty": "1.125", "rate": "0.99"},
				]},
				{"op": "sale", "ref": "S2", "date": d(1), "tax": "15", "lines": [{"item": "delivery", "qty": "1", "rate": "0.03"}]},
				{"op": "purchase", "ref": "P1", "date": d(2), "tax": "15", "lines": [{"item": "delivery", "qty": "3.3", "rate": "7.77"}]},
				{"op": "pay", "ref": "PAY1", "invoice": "S1", "date": d(3), "amount": "10.01"},
			],
		},
		{
			# Exact half cents: banker's rounding (to even) and rounding half up differ here.
			"name": "half_cents",
			"steps": [
				{"op": "sale", "ref": "S1", "date": d(0), "tax": None, "lines": [{"item": "delivery", "qty": "0.5", "rate": "0.05"}]},
				{"op": "sale", "ref": "S2", "date": d(1), "tax": "15", "lines": [{"item": "delivery", "qty": "1", "rate": "0.30"}]},
				{"op": "sale", "ref": "S3", "date": d(2), "tax": "15", "lines": [{"item": "delivery", "qty": "1", "rate": "0.70"}]},
				{"op": "purchase", "ref": "P1", "date": d(3), "tax": "15", "lines": [{"item": "delivery", "qty": "0.5", "rate": "0.85"}]},
			],
		},
		{
			# Credit notes (returns): part of a sale and part of a purchase come back.
			"name": "credit_notes",
			"steps": [
				{"op": "purchase", "ref": "P1", "date": d(0), "tax": "15", "lines": [{"item": "cement", "qty": "10", "rate": "1000"}]},
				{"op": "sale", "ref": "S1", "date": d(1), "tax": "15", "lines": [{"item": "cement", "qty": "4", "rate": "1500"}, {"item": "delivery", "qty": "1", "rate": "200"}]},
				{"op": "pay", "ref": "PAY1", "invoice": "S1", "date": d(2), "amount": "1000"},
				{"op": "credit", "ref": "C1", "invoice": "S1", "date": d(3), "lines": [{"line": 0, "qty": "1"}, {"line": 1, "qty": "1"}]},
				{"op": "credit", "ref": "C2", "invoice": "P1", "date": d(4), "lines": [{"line": 0, "qty": "2"}]},
				{"op": "credit", "ref": "C3", "invoice": "S1", "date": d(5), "lines": [{"line": 0, "qty": "2"}]},
			],
		},
		{
			# A tax per line (D-027): VAT on some lines, turnover tax on others, none on the rest;
			# each tax rounded once on its own lines. Then a credit note takes back the right tax.
			"name": "a_tax_per_line",
			"steps": [
				{"op": "purchase", "ref": "P1", "date": d(0), "tax": None, "lines": [
					{"item": "cement", "qty": "10", "rate": "1000.07", "tax": "15"},
					{"item": "nails", "qty": "30", "rate": "12.33", "tax": "2"},
					{"item": "delivery", "qty": "1.5", "rate": "40.01", "tax": None},
				]},
				{"op": "sale", "ref": "S1", "date": d(1), "tax": None, "lines": [
					{"item": "cement", "qty": "3", "rate": "1500.33", "tax": "15"},
					{"item": "delivery", "qty": "2.5", "rate": "333.33", "tax": "2"},
					{"item": "nails", "qty": "7", "rate": "19.99", "tax": None},
					{"item": "cement", "qty": "1", "rate": "0.03", "tax": "15"},
				]},
				{"op": "pay", "ref": "PAY1", "invoice": "S1", "date": d(2), "amount": "2000"},
				{"op": "credit", "ref": "C1", "invoice": "S1", "date": d(3), "lines": [{"line": 0, "qty": "1"}, {"line": 1, "qty": "0.5"}, {"line": 2, "qty": "2"}]},
				{"op": "credit", "ref": "C2", "invoice": "P1", "date": d(4), "lines": [{"line": 1, "qty": "10"}]},
			],
		},
		{
			# Prices that already include tax (D-028): the net is worked out from each price;
			# rounding left over goes to the last tax, so the total is exactly what was typed.
			"name": "prices_include_tax",
			"steps": [
				{"op": "purchase", "ref": "P1", "date": d(0), "tax": None, "inclusive": True, "lines": [
					{"item": "cement", "qty": "10", "rate": "1150.00", "tax": "15"},
					{"item": "nails", "qty": "30", "rate": "12.55", "tax": "2"},
					{"item": "delivery", "qty": "1", "rate": "40.01", "tax": None},
				]},
				{"op": "sale", "ref": "S1", "date": d(1), "tax": None, "inclusive": True, "lines": [
					{"item": "cement", "qty": "3", "rate": "1725.38", "tax": "15"},
					{"item": "delivery", "qty": "2.5", "rate": "340.00", "tax": "2"},
					{"item": "nails", "qty": "7", "rate": "19.99", "tax": None},
					{"item": "cement", "qty": "1", "rate": "0.05", "tax": "15"},
				]},
				{"op": "sale", "ref": "S2", "date": d(2), "tax": "15", "inclusive": True, "lines": [
					{"item": "delivery", "qty": "3", "rate": "333.33"},
					{"item": "delivery", "qty": "1", "rate": "0.07"},
				]},
				{"op": "pay", "ref": "PAY1", "invoice": "S1", "date": d(3), "amount": "1000"},
				{"op": "credit", "ref": "C1", "invoice": "S1", "date": d(4), "lines": [{"line": 0, "qty": "1"}, {"line": 1, "qty": "0.5"}]},
				{"op": "credit", "ref": "C2", "invoice": "P1", "date": d(5), "lines": [{"line": 0, "qty": "2"}]},
			],
		},
		{
			# Withholding tax (D-029): 2% of the amount before tax is kept back from the
			# supplier and owed to the tax office; VAT is still claimed in full.
			"name": "withholding",
			"steps": [
				{"op": "purchase", "ref": "P1", "date": d(0), "tax": "15", "withhold": "2", "lines": [
					{"item": "cement", "qty": "10", "rate": "1000.07"},
					{"item": "delivery", "qty": "2.5", "rate": "333.33"},
				]},
				{"op": "pay", "ref": "PAY1", "invoice": "P1", "date": d(1), "amount": "5000"},
				{"op": "purchase", "ref": "P2", "date": d(2), "tax": None, "withhold": "2", "lines": [
					{"item": "nails", "qty": "7", "rate": "19.99"},
				]},
				{"op": "credit", "ref": "C1", "invoice": "P1", "date": d(3), "lines": [{"line": 0, "qty": "2"}]},
			],
		},
		{
			# Invoices in another unit (D-030): bought and sold by the box (12 each) and by the
			# piece; stock is kept in pieces. A credit note returns a box.
			"name": "units",
			"steps": [
				{"op": "purchase", "ref": "P1", "date": d(0), "tax": "15", "lines": [
					{"item": "cement", "qty": "2", "rate": "1150.00", "unit": "Box"},
					{"item": "nails", "qty": "3", "rate": "140.35", "unit": "Box"},
				]},
				{"op": "sale", "ref": "S1", "date": d(1), "tax": "15", "lines": [
					{"item": "cement", "qty": "5", "rate": "120.00"},
					{"item": "cement", "qty": "1", "rate": "1399.99", "unit": "Box"},
					{"item": "nails", "qty": "2", "rate": "185.55", "unit": "Box"},
				]},
				{"op": "credit", "ref": "C1", "invoice": "S1", "date": d(2), "lines": [{"line": 1, "qty": "1"}]},
				{"op": "credit", "ref": "C2", "invoice": "P1", "date": d(3), "lines": [{"line": 1, "qty": "1"}]},
			],
		},
		{
			# Discounts (D-093): a percent off a line's price; a percent or an amount off the
			# whole invoice before tax, spread over the lines. Credit notes take back their share.
			"name": "discounts",
			"steps": [
				{"op": "purchase", "ref": "P1", "date": d(0), "tax": "15", "discount": {"amount": "100.01"}, "lines": [
					{"item": "cement", "qty": "10", "rate": "1000.07"},
					{"item": "nails", "qty": "30", "rate": "12.33", "discount": "7.5"},
					{"item": "delivery", "qty": "1.5", "rate": "40.01"},
				]},
				{"op": "sale", "ref": "S1", "date": d(1), "tax": "15", "discount": {"percent": "3.33"}, "lines": [
					{"item": "cement", "qty": "3", "rate": "1500.33", "discount": "12.5"},
					{"item": "delivery", "qty": "2.5", "rate": "333.33"},
					{"item": "nails", "qty": "7", "rate": "19.99"},
				]},
				{"op": "sale", "ref": "S2", "date": d(2), "tax": None, "lines": [
					{"item": "nails", "qty": "3", "rate": "19.99", "discount": "33.33"},
				]},
				{"op": "pay", "ref": "PAY1", "invoice": "S1", "date": d(3), "amount": "1000"},
				# (Returning 1 and 1 gives a tax of exactly 238.695: Ferrum's exact decimals round it
				# to 238.70, ERPNext's floating point sees 238.69499.. and gives 238.69. Not a rule to
				# copy, so the quantities avoid an exact half cent.)
				{"op": "credit", "ref": "C1", "invoice": "S1", "date": d(4), "lines": [{"line": 0, "qty": "1"}, {"line": 1, "qty": "2"}]},
				# An amount off the whole invoice: ERPNext copies all of it onto any credit note,
				# Ferrum gives back each returned line's share (D-093), so only a full return
				# is compared.
				{"op": "purchase", "ref": "P2", "date": d(5), "tax": "15", "discount": {"amount": "7.77"}, "lines": [
					{"item": "delivery", "qty": "2", "rate": "40.01"},
					{"item": "delivery", "qty": "1", "rate": "19.99", "discount": "10"},
				]},
				{"op": "credit", "ref": "C2", "invoice": "P2", "date": d(6), "lines": [{"line": 0, "qty": "2"}, {"line": 1, "qty": "1"}]},
			],
		},
		{
			# Invoices in another currency (D-095): prices in EUR, books in USD at the invoice's
			# rate; VAT, a line discount and a credit note too. (Payments in EUR come next.)
			"name": "foreign_currency",
			"steps": [
				{"op": "purchase", "ref": "P1", "date": d(0), "tax": "15", "currency": "EUR", "rate": "1.0837", "lines": [
					{"item": "cement", "qty": "10", "rate": "920.33"},
					{"item": "delivery", "qty": "1.5", "rate": "36.91"},
				]},
				{"op": "sale", "ref": "S1", "date": d(1), "tax": "15", "currency": "EUR", "rate": "1.0912", "lines": [
					{"item": "cement", "qty": "3", "rate": "1375.55", "discount": "5"},
					{"item": "delivery", "qty": "2.5", "rate": "305.47"},
				]},
				{"op": "sale", "ref": "S2", "date": d(2), "tax": None, "currency": "EUR", "rate": "1.0899", "lines": [
					{"item": "cement", "qty": "1", "rate": "0.07"},
				]},
				{"op": "credit", "ref": "C1", "invoice": "S1", "date": d(3), "lines": [{"line": 0, "qty": "1"}]},
			],
		},
		{
			# Orders invoiced in parts; delivered or received first, invoiced later.
			"name": "orders_and_deliveries",
			"steps": [
				{"op": "purchase", "ref": "P0", "date": d(0), "tax": None, "lines": [{"item": "cement", "qty": "20", "rate": "1000"}]},
				{"op": "order", "ref": "SO1", "kind": "sales", "date": d(1), "lines": [{"item": "cement", "qty": "4", "rate": "1500"}, {"item": "delivery", "qty": "1", "rate": "200"}]},
				{"op": "bill_order", "ref": "S1", "order": "SO1", "date": d(2), "tax": "15", "lines": [{"line": 0, "qty": "1"}]},
				{"op": "bill_order", "ref": "S2", "order": "SO1", "date": d(3), "tax": "15", "lines": [{"line": 0, "qty": "3"}, {"line": 1, "qty": "1"}]},
				{"op": "order", "ref": "SO2", "kind": "sales", "date": d(4), "lines": [{"item": "cement", "qty": "3", "rate": "1500"}]},
				{"op": "deliver", "ref": "DN1", "order": "SO2", "date": d(5), "lines": [{"line": 0, "qty": "3"}]},
				{"op": "bill_order", "ref": "S3", "order": "SO2", "date": d(6), "tax": None, "lines": [{"line": 0, "qty": "3"}], "from": "DN1"},
				{"op": "order", "ref": "PO1", "kind": "purchase", "date": d(7), "lines": [{"item": "cement", "qty": "10", "rate": "900"}]},
				{"op": "deliver", "ref": "GR1", "order": "PO1", "date": d(8), "lines": [{"line": 0, "qty": "6"}]},
				{"op": "bill_order", "ref": "P1", "order": "PO1", "date": d(9), "tax": "15", "lines": [{"line": 0, "qty": "6"}], "from": "GR1"},
				{"op": "bill_order", "ref": "P2", "order": "PO1", "date": d(10), "tax": None, "lines": [{"line": 0, "qty": "4"}]},
			],
		},
	]
	# Random business: buy and sell stock and services, pay some, cancel some.
	steps, stock, refs, open_inv, payments = [], 0, 0, [], []
	for i in range(30):
		roll = rng.random()
		refs += 1
		tax = rng.choice(["15", None])
		if roll < 0.3 or stock < 5:
			q = rng.randint(5, 30)
			steps.append({"op": "purchase", "ref": f"P{refs}", "date": d(i), "tax": tax, "lines": [
				{"item": "cement", "qty": str(q), "rate": f"{rng.randint(90000, 120000) / 100:.2f}"},
			]})
			stock += q
			open_inv.append(f"P{refs}")
		elif roll < 0.7:
			q = rng.randint(1, stock)
			lines = [{"item": "cement", "qty": str(q), "rate": f"{rng.randint(130000, 170000) / 100:.2f}"}]
			if rng.random() < 0.5:
				lines.append({"item": "delivery", "qty": f"{rng.randint(1, 400) / 100:.2f}", "rate": f"{rng.randint(1000, 9999) / 100:.2f}"})
			steps.append({"op": "sale", "ref": f"S{refs}", "date": d(i), "tax": tax, "lines": lines})
			stock -= q
			open_inv.append(f"S{refs}")
		elif open_inv:
			inv = rng.choice(open_inv)
			open_inv.remove(inv)
			steps.append({"op": "pay", "ref": f"PAY{refs}", "invoice": inv, "date": d(i), "amount": "full"})
			payments.append(f"PAY{refs}")
	if payments:
		steps.append({"op": "cancel_payment", "payment": payments[-1]})
	scenarios.append({"name": "random_month_of_business", "steps": steps})
	return scenarios


def _ensure_masters():
	if not frappe.db.exists("Warehouse", f"{WAREHOUSE} - {ABBR}"):
		frappe.get_doc({"doctype": "Warehouse", "warehouse_name": WAREHOUSE, "company": COMPANY}).insert()
	for name, parent, kind in (
		("VAT Output", "Duties and Taxes", "Tax"), ("VAT Input", "Current Assets", "Tax"),
		("TOT Output", "Duties and Taxes", "Tax"), ("TOT Input", "Current Assets", "Tax"),
		("TDS Payable", "Duties and Taxes", "Tax"),
	):
		if not frappe.db.exists("Account", f"{name} - {ABBR}"):
			frappe.get_doc({
				"doctype": "Account", "account_name": name, "company": COMPANY,
				"parent_account": f"{parent} - {ABBR}", "account_type": kind,
			}).insert()
	frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 0)
	for name, parent, kind in (("Debtors EUR", "Accounts Receivable", "Receivable"), ("Creditors EUR", "Accounts Payable", "Payable")):
		if not frappe.db.exists("Account", f"{name} - {ABBR}"):
			frappe.db.set_value("Currency", "EUR", "enabled", 1)
			frappe.get_doc({
				"doctype": "Account", "account_name": name, "company": COMPANY,
				"parent_account": f"{parent} - {ABBR}", "account_type": kind, "account_currency": "EUR",
			}).insert()
	if not frappe.db.exists("Tax Withholding Category", WITHHOLDING):
		frappe.get_doc({
			"doctype": "Tax Withholding Category", "category_name": WITHHOLDING,
			"tax_deduction_basis": "Net Total", "round_off_tax_amount": 0,
			"disable_cumulative_threshold": 1, "disable_transaction_threshold": 1,
			"rates": [{"tax_withholding_rate": 2, "single_threshold": 0, "cumulative_threshold": 0,
			           "from_date": "2000-01-01", "to_date": "2099-12-31"}],
			"accounts": [{"company": COMPANY, "account": f"TDS Payable - {ABBR}"}],
		}).insert(set_name=WITHHOLDING)
	# One item tax template per line tax: its own tax at its rate, every other tax at 0.
	for key, (vat, tot) in LINE_TAXES.items():
		title = f"DIFFINV {key}"
		if frappe.db.exists("Item Tax Template", f"{title} - {ABBR}"):
			continue
		rates = []
		for side in ("Output", "Input"):
			rates.append({"tax_type": f"VAT {side} - {ABBR}", "tax_rate": vat})
			rates.append({"tax_type": f"TOT {side} - {ABBR}", "tax_rate": tot})
		frappe.get_doc({"doctype": "Item Tax Template", "title": title, "company": COMPANY, "taxes": rates}).insert()
	for uom in ("Nos", "Kg", "Box"):
		if not frappe.db.exists("UOM", uom):
			frappe.get_doc({"doctype": "UOM", "uom_name": uom}).insert()
	frappe.db.set_value("UOM", "Kg", "must_be_whole_number", 0)


def _items(run_id):
	codes = {}
	for key, stock in (("cement", 1), ("nails", 1), ("delivery", 0)):
		code = f"DIFFINV-{key}-{run_id}"
		frappe.get_doc({
			"doctype": "Item", "item_code": code, "item_name": code, "item_group": "Products",
			"stock_uom": "Nos" if stock else "Kg", "is_stock_item": stock, "valuation_method": "FIFO",
			# Another unit (D-051/D-030 in Ferrum): 1 Box = 12 of the stock unit.
			"uoms": [{"uom": "Nos", "conversion_factor": 1}, {"uom": "Box", "conversion_factor": 12}] if stock else [],
		}).insert()
		codes[key] = code
	return codes


def _party(doctype, name):
	if not frappe.db.exists(doctype, name):
		field = "customer_name" if doctype == "Customer" else "supplier_name"
		frappe.get_doc({"doctype": doctype, field: name}).insert()
	return name


def _invoice(step, items, customer, supplier):
	sale = step["op"] == "sale"
	inv = frappe.new_doc("Sales Invoice" if sale else "Purchase Invoice")
	inv.company = COMPANY
	inv.set_posting_time = 1
	inv.posting_date = step["date"]
	inv.due_date = step["date"]
	inv.update_stock = 1
	inv.disable_rounded_total = 1
	if sale:
		inv.customer = customer
	else:
		inv.supplier = supplier
		inv.bill_date = step["date"]
		if step.get("withhold"):
			frappe.db.set_value("Supplier", supplier, "tax_withholding_category", WITHHOLDING)
			inv.apply_tds = 1
	if step.get("currency"):
		frappe.db.set_value("Currency", step["currency"], "enabled", 1)
		inv.currency = step["currency"]
		inv.conversion_rate = float(step["rate"])
		inv.plc_conversion_rate = float(step["rate"])
		if sale:
			inv.debit_to = f"Debtors {step['currency']} - {ABBR}"
		else:
			inv.credit_to = f"Creditors {step['currency']} - {ABBR}"
	for line in step["lines"]:
		row = {"item_code": items[line["item"]], "qty": float(line["qty"]), "rate": float(line["rate"]), "conversion_factor": 1}
		if line.get("unit"):
			row.update(uom=line["unit"], conversion_factor=UNITS[line["unit"]])
		if line.get("discount"):
			# ERPNext works the rate out from the list price only when no rate is given.
			row.update(price_list_rate=float(line["rate"]), discount_percentage=float(line["discount"]), rate=0)
		if line["item"] != "delivery":
			row["warehouse"] = f"{WAREHOUSE} - {ABBR}"
		elif not sale:
			row["expense_account"] = f"Miscellaneous Expenses - {ABBR}"
		if sale:
			row["income_account"] = f"Sales - {ABBR}"
		if "tax" in line:
			row["item_tax_template"] = f"DIFFINV {line['tax'] or 'none'} - {ABBR}"
		inv.append("items", row)
	# One tax row per tax, in the order the taxes first appear on the lines (Ferrum's order).
	line_taxes = list(dict.fromkeys(line["tax"] for line in step["lines"] if line.get("tax")))
	for rate in line_taxes:
		tax = {
			"charge_type": "On Net Total", "rate": float(rate),
			"account_head": f"{TAX_NAMES[rate]} {'Output' if sale else 'Input'} - {ABBR}", "description": TAX_NAMES[rate],
			"included_in_print_rate": 1 if step.get("inclusive") else 0,
		}
		if not sale:
			tax.update(category="Total", add_deduct_tax="Add")
		inv.append("taxes", tax)
	if step["tax"]:
		tax = {
			"charge_type": "On Net Total", "rate": float(step["tax"]),
			"account_head": f"VAT {'Output' if sale else 'Input'} - {ABBR}", "description": "VAT",
			"included_in_print_rate": 1 if step.get("inclusive") else 0,
		}
		if not sale:
			tax.update(category="Total", add_deduct_tax="Add")
		inv.append("taxes", tax)
	if step.get("discount"):
		inv.apply_discount_on = "Net Total"
		if "percent" in step["discount"]:
			inv.additional_discount_percentage = float(step["discount"]["percent"])
		else:
			inv.discount_amount = float(step["discount"]["amount"])
	inv.insert()
	inv.submit()
	return inv


def _credit(step, invoice):
	"""A return (credit note) against `invoice`: only the listed lines, the given quantities.
	It lowers the original's outstanding amount (update_outstanding_for_self = 0)."""
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	ret = make_return_doc(invoice.doctype, invoice.name)
	ret.set_posting_time = 1
	ret.posting_date = step["date"]
	ret.due_date = step["date"]
	if ret.meta.has_field("update_outstanding_for_self"):
		ret.update_outstanding_for_self = 0
	wanted = {line["line"]: line["qty"] for line in step["lines"]}
	keep = []
	for i, row in enumerate(ret.items):
		if i in wanted:
			row.qty = -float(wanted[i])
			if row.meta.has_field("received_qty"):
				row.received_qty = row.qty
			keep.append(row)
	ret.items = keep
	ret.insert()
	ret.submit()
	return ret


def _order(step, items, customer, supplier):
	sale = step["kind"] == "sales"
	o = frappe.new_doc("Sales Order" if sale else "Purchase Order")
	o.company = COMPANY
	o.transaction_date = step["date"]
	o.disable_rounded_total = 1
	if sale:
		o.customer = customer
		o.delivery_date = step["date"]
	else:
		o.supplier = supplier
		o.schedule_date = step["date"]
	for line in step["lines"]:
		row = {"item_code": items[line["item"]], "qty": float(line["qty"]), "rate": float(line["rate"]), "conversion_factor": 1}
		if line["item"] != "delivery":
			row["warehouse"] = f"{WAREHOUSE} - {ABBR}"
		if sale:
			row["delivery_date"] = step["date"]
		else:
			row["schedule_date"] = step["date"]
			if line["item"] == "delivery":
				row["expense_account"] = f"Miscellaneous Expenses - {ABBR}"
		o.append("items", row)
	o.insert()
	o.submit()
	return o


def _keep_lines(doc, order, step, detail_field):
	"""Keep only the rows for the order lines the step names, with the step's quantities."""
	wanted = {order.items[line["line"]].name: float(line["qty"]) for line in step["lines"]}
	keep = []
	for row in doc.items:
		if row.get(detail_field) in wanted:
			row.qty = wanted[row.get(detail_field)]
			if row.meta.has_field("received_qty"):
				row.received_qty = row.qty
			keep.append(row)
	doc.items = keep


def _deliver(step, order):
	sale = order.doctype == "Sales Order"
	if sale:
		from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note as make
	else:
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt as make
	doc = make(order.name)
	doc.set_posting_time = 1
	doc.posting_date = step["date"]
	_keep_lines(doc, order, step, "so_detail" if sale else "purchase_order_item")
	for row in doc.items:
		row.warehouse = f"{WAREHOUSE} - {ABBR}"
	doc.insert()
	doc.submit()
	return doc


def _bill_order(step, order, docs):
	"""An invoice from the order (stock moves with it) or from a delivery (it already moved)."""
	sale = order.doctype == "Sales Order"
	if step.get("from"):
		delivery = docs[step["from"]]
		if sale:
			from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice as make
		else:
			from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice as make
		inv = make(delivery.name)
		inv.update_stock = 0
	else:
		if sale:
			from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice as make
		else:
			from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice as make
		inv = make(order.name)
		_keep_lines(inv, order, step, "so_detail" if sale else "po_detail")
		inv.update_stock = 1
		for row in inv.items:
			if frappe.db.get_value("Item", row.item_code, "is_stock_item"):
				row.warehouse = f"{WAREHOUSE} - {ABBR}"
	inv.set_posting_time = 1
	inv.posting_date = step["date"]
	inv.due_date = step["date"]
	inv.disable_rounded_total = 1
	if not sale:
		inv.bill_date = step["date"]
	inv.set("taxes", [])
	if step["tax"]:
		tax = {
			"charge_type": "On Net Total", "rate": float(step["tax"]),
			"account_head": f"VAT {'Output' if sale else 'Input'} - {ABBR}", "description": "VAT",
		}
		if not sale:
			tax.update(category="Total", add_deduct_tax="Add")
		inv.append("taxes", tax)
	inv.insert()
	inv.submit()
	return inv


def _pay(step, invoice):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	amount = invoice.outstanding_amount if step["amount"] == "full" else float(step["amount"])
	pe = get_payment_entry(invoice.doctype, invoice.name, party_amount=amount)
	pe.posting_date = step["date"]
	pe.mode_of_payment = "Cash"
	if invoice.doctype == "Sales Invoice":
		pe.paid_to = f"Cash - {ABBR}"
	else:
		pe.paid_from = f"Cash - {ABBR}"
	pe.paid_amount = pe.received_amount = amount
	pe.references[0].allocated_amount = amount
	pe.insert()
	pe.submit()
	step["amount"] = str(Decimal(str(amount)).quantize(Decimal("0.01")))
	return pe


def run(out: str = "/tmp/erpnext_invoices_reference.json", seed: int = 11, rounding: str = "Commercial Rounding"):
	"""Ferrum rounds exact half cents up, like ERPNext's "Commercial Rounding" (Ferrum decision
	D-011), so the reference is made with that setting; the site's own setting is put back."""
	_guard()
	_ensure_masters()
	previous = frappe.db.get_single_value("System Settings", "rounding_method")
	frappe.db.set_single_value("System Settings", "rounding_method", rounding)
	frappe.db.commit()
	frappe.clear_cache()
	try:
		return _run(out, int(seed))
	finally:
		frappe.db.set_single_value("System Settings", "rounding_method", previous)
		frappe.db.commit()
		frappe.clear_cache()


def _run(out, seed):
	results = []
	for s in _scenarios(seed):
		run_id = frappe.generate_hash(length=6)
		items = _items(run_id)
		customer = _party("Customer", f"Diff Customer {run_id}")
		supplier = _party("Supplier", f"Diff Supplier {run_id}")
		docs, vouchers = {}, []
		for step in s["steps"]:
			if step["op"] in ("sale", "purchase"):
				docs[step["ref"]] = _invoice(step, items, customer, supplier)
				vouchers.append(docs[step["ref"]].name)
			elif step["op"] == "pay":
				docs[step["invoice"]].reload()
				docs[step["ref"]] = _pay(step, docs[step["invoice"]])
				vouchers.append(docs[step["ref"]].name)
			elif step["op"] == "order":
				docs[step["ref"]] = _order(step, items, customer, supplier)
			elif step["op"] == "deliver":
				docs[step["ref"]] = _deliver(step, docs[step["order"]])
				vouchers.append(docs[step["ref"]].name)
			elif step["op"] == "bill_order":
				docs[step["ref"]] = _bill_order(step, docs[step["order"]], docs)
				vouchers.append(docs[step["ref"]].name)
			elif step["op"] == "credit":
				docs[step["ref"]] = _credit(step, docs[step["invoice"]])
				vouchers.append(docs[step["ref"]].name)
			elif step["op"] == "cancel_payment":
				frappe.get_doc("Payment Entry", docs[step["payment"]].name).cancel()
			elif step["op"] == "cancel_invoice":
				d = docs[step["invoice"]]
				frappe.get_doc(d.doctype, d.name).cancel()
		frappe.db.commit()
		rows = frappe.db.sql(
			"""SELECT account, SUM(debit) - SUM(credit) FROM `tabGL Entry`
			   WHERE voucher_no IN %(v)s AND is_cancelled = 0 GROUP BY account""",
			{"v": vouchers},
		)
		balances, unmapped = {}, {}
		for account, amount in rows:
			amount = Decimal(str(amount)).quantize(Decimal("0.01"))
			if amount == 0:
				continue
			if account in ACCOUNTS:
				# Several ERPNext accounts may map to one Ferrum account (VAT and turnover tax).
				code = ACCOUNTS[account]
				balances[code] = str(Decimal(balances.get(code, "0")) + amount)
			else:
				unmapped[account] = str(amount)
		invoices = {}
		for ref, d in docs.items():
			if d.doctype in ("Sales Invoice", "Purchase Invoice"):
				d.reload()
				invoices[ref] = {
					# Returns carry negative amounts in ERPNext; Ferrum shows them positive.
					"net": str(abs(Decimal(str(d.net_total))).quantize(Decimal("0.01"))),
					"tax": str(abs(Decimal(str(d.total_taxes_and_charges))).quantize(Decimal("0.01"))),
					"total": str(abs(Decimal(str(d.grand_total))).quantize(Decimal("0.01"))),
					"owed": str(Decimal(str(d.outstanding_amount)).quantize(Decimal("0.01"))),
					"credit_note": bool(d.get("is_return")),
					"cancelled": d.docstatus == 2,
				}
		results.append({**s, "erpnext": {"balances": balances, "unmapped": unmapped, "invoices": invoices}})
	versions = {app: frappe.get_attr(f"{app}.__version__") for app in ("frappe", "erpnext")}
	rounding = frappe.db.get_single_value("System Settings", "rounding_method")
	with open(out, "w") as f:
		json.dump({"source": "ERPNext", "versions": versions, "rounding_method": rounding, "scenarios": results}, f, indent=1)
	return {"scenarios": len(results), "out": out, "versions": versions, "rounding_method": rounding}
