import frappe
from frappe.utils import cint

from ..base_importer import BaseImporter


class InventoryAdjustmentImporter(BaseImporter):
    source_type = "QB_INVENTORY_ADJUSTMENT"
    target_doctype = "Stock Entry"
    json_file = "inventory_adjustments.json"
    json_key = "inventory_adjustments"

    def _get_company(self):
        return frappe.defaults.get_global_default("company") or ""

    def _resolve_item(self, item_name):
        if not item_name:
            return None

        item = frappe.db.get_value("Item", {"item_code": item_name}, "name")
        if not item:
            item = frappe.db.get_value("Item", {"item_name": item_name}, "name")

        if not item:
            return None

        if cint(frappe.db.get_value("Item", item, "is_stock_item")) != 1:
            frappe.db.set_value(
                "Item",
                item,
                {
                    "is_stock_item": 1,
                    "is_purchase_item": 1,
                    "is_sales_item": 1,
                },
            )

        return item

    def _resolve_difference_account(self):
        company = self._get_company()
        if not company:
            return None

        for account_name in ["Stock In Hand", "Temporary Opening", "Stock Adjustment"]:
            account = frappe.db.get_value(
                "Account",
                {"account_name": account_name, "company": company},
                "name",
            )
            if account:
                return account

        return frappe.db.get_value(
            "Account",
            {"company": company, "root_type": "Asset"},
            "name",
        )

    def _resolve_warehouse(self):
        company = self._get_company()
        if not company:
            return None

        warehouse = frappe.db.get_value(
            "Warehouse",
            {"warehouse_name": "Stores", "company": company},
            "name",
        )
        if warehouse:
            return warehouse

        return frappe.db.get_value("Warehouse", {"company": company}, "name")

    def post_insert(self, doc, record):
        if hasattr(doc, "submit"):
            doc.submit()

    def _parse_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def map_record(self, record):
        company = self._get_company()
        if not company:
            return {"_skip": True, "_skip_reason": "MISSING_COMPANY", "ref_no": record.get("ref_no")}

        warehouse = self._resolve_warehouse()
        if not warehouse:
            return {"_skip": True, "_skip_reason": "MISSING_WAREHOUSE", "ref_no": record.get("ref_no")}

        lines = record.get("lines") or []
        if not lines:
            return {"_skip": True, "_skip_reason": "NO_LINES", "ref_no": record.get("ref_no")}

        items = []
        has_negative = False
        has_positive = False

        for line in lines:
            item_name = line.get("item") or ""
            if not item_name:
                continue

            item_doc = self._resolve_item(item_name)
            if not item_doc:
                return {
                    "_skip": True,
                    "_skip_reason": f"MISSING_ITEM:{item_name}",
                    "ref_no": record.get("ref_no"),
                }

            qty = self._parse_float(line.get("new_quantity"))
            valuation_rate = self._parse_float(line.get("new_value"))
            qty_difference = self._parse_float(line.get("quantity_difference"))
            value_difference = self._parse_float(line.get("value_difference"))

            if qty == 0 and abs(qty_difference) > 0:
                qty = qty_difference

            if qty_difference < 0:
                has_negative = True
            elif qty_difference > 0:
                has_positive = True

            if qty == 0 and valuation_rate == 0:
                if abs(value_difference) > 0 and abs(qty_difference) > 0:
                    valuation_rate = abs(value_difference) / abs(qty_difference)
                elif abs(value_difference) > 0:
                    valuation_rate = abs(value_difference)
                elif abs(qty_difference) > 0:
                    valuation_rate = 0.01

            row = {
                "doctype": "Stock Entry Detail",
                "item_code": item_doc,
                "qty": abs(qty) or 1,
                "basic_rate": valuation_rate or 0.0,
                "allow_zero_valuation_rate": 1,
            }

            row["t_warehouse"] = warehouse

            if line.get("memo"):
                row["remarks"] = line.get("memo")

            items.append(row)

        if not items:
            return {"_skip": True, "_skip_reason": "NO_VALID_ITEMS", "ref_no": record.get("ref_no")}

        stock_entry_type = "Material Receipt"

        doc = {
            "doctype": "Stock Entry",
            "stock_entry_type": stock_entry_type,
            "company": company,
            "posting_date": self.normalize_date(record.get("date")),
            "remarks": record.get("memo") or record.get("ref_no") or "",
            "items": items,
        }

        return doc
