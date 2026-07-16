import frappe
from frappe.utils import cint

from ..base_importer import BaseImporter


class InventoryAdjustmentImporter(BaseImporter):
    source_type = "QB_INVENTORY_ADJUSTMENT"
    target_doctype = "Stock Reconciliation"
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

    def find_existing_target(self, doc_data):
        if not doc_data:
            return None

        remarks = (doc_data or {}).get("remarks") or ""
        marker = "QB_TXN:"
        txn_id = None

        if marker in remarks:
            txn_id = remarks.split(marker, 1)[1].split()[0].strip()

        if txn_id:
            try:
                if frappe.db.has_column("Stock Reconciliation", "remarks"):
                    existing = frappe.db.get_value(
                        "Stock Reconciliation",
                        {"remarks": ["like", f"%{marker}{txn_id}%"]},
                        "name",
                    )
                    if existing:
                        return existing
            except Exception:
                pass

        incoming_items = []
        for item in (doc_data.get("items") or []):
            incoming_items.append(
                (
                    item.get("item_code"),
                    item.get("warehouse"),
                    self._parse_float(item.get("qty")),
                    self._parse_float(item.get("valuation_rate")),
                )
            )

        if not incoming_items:
            return None

        filters = {
            "company": doc_data.get("company"),
            "purpose": "Stock Reconciliation",
        }
        posting_date = doc_data.get("posting_date")
        if posting_date:
            filters["posting_date"] = self.normalize_date(posting_date)

        existing_docs = frappe.get_all(
            "Stock Reconciliation",
            filters=filters,
            fields=["name"],
            limit=20,
            order_by="creation desc",
        )

        for existing_doc in existing_docs:
            try:
                existing_record = frappe.get_doc("Stock Reconciliation", existing_doc["name"])
            except Exception:
                continue

            existing_items = []
            for item in existing_record.get("items") or []:
                existing_items.append(
                    (
                        item.get("item_code"),
                        item.get("warehouse"),
                        self._parse_float(item.get("qty")),
                        self._parse_float(item.get("valuation_rate")),
                    )
                )

            if incoming_items == existing_items:
                return existing_doc["name"]

        return None

    def post_insert(self, doc, record):
        if hasattr(doc, "submit"):
            doc.submit()

    def _parse_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def get_current_stock_qty(self, item_code, warehouse):
        if not item_code:
            return 0

        filters = {
            "item_code": item_code,
            "is_cancelled": 0,
        }

        if warehouse:
            filters["warehouse"] = warehouse

        sle = frappe.get_all(
            "Stock Ledger Entry",
            filters=filters,
            fields=["qty_after_transaction"],
            order_by="posting_date desc, posting_time desc, creation desc",
            limit=1,
        )

        return sle[0].qty_after_transaction if sle else 0

    def _get_latest_sle(self, item_code, warehouse):
        """Return latest stock ledger entry dict or None"""
        if not item_code:
            return None

        filters = {"item_code": item_code, "is_cancelled": 0}
        if warehouse:
            filters["warehouse"] = warehouse

        sle = frappe.get_all(
            "Stock Ledger Entry",
            filters=filters,
            fields=["qty_after_transaction", "valuation_rate", "warehouse", "posting_date", "posting_time"],
            order_by="posting_date desc, posting_time desc, creation desc",
            limit=1,
        )

        if not sle:
            return None

        return sle[0]

    def map_record(self, record):
        company = self._get_company()
        if not company:
            return {"_skip": True, "_skip_reason": "MISSING_COMPANY", "ref_no": record.get("ref_no")}
        # Build a Stock Reconciliation document according to mapping:
        # header: txn_id -> name (left as remarks/external), ref_no -> remarks, date -> posting_date, memo -> remarks
        warehouse = self._resolve_warehouse()
        if not warehouse:
            return {"_skip": True, "_skip_reason": "MISSING_WAREHOUSE", "ref_no": record.get("ref_no")}

        lines = record.get("lines") or []
        if not lines:
            return {"_skip": True, "_skip_reason": "NO_LINES", "ref_no": record.get("ref_no")}

        items = []
        for idx, line in enumerate(lines, start=1):
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


            raw_new_qty = line.get("new_quantity")
            qty_difference = line.get("quantity_difference")

            # Parse provided values
            raw_new_qty = line.get("new_quantity")
            qty_difference = line.get("quantity_difference")

            new_qty = self._parse_float(raw_new_qty)
            qty_diff = self._parse_float(qty_difference)

            # Current stock in ERPNext
            current_qty = self.get_current_stock_qty(
                item_code=item_doc,
                warehouse=warehouse,
            )

            # Prefer computing from QuantityDifference whenever it exists.
            if qty_difference is not None and str(qty_difference).strip() != "":
                qty = current_qty + qty_diff
            elif raw_new_qty is not None and str(raw_new_qty).strip() != "":
                qty = new_qty
            else:
                qty = current_qty

        # Prefer valuation from QBD if available
        valuation_rate = self._parse_float(line.get("new_value"))

        # Ensure we have a valuation_rate: prefer provided new_value, else try latest SLE, else item master
        if not valuation_rate:
            sle = self._get_latest_sle(item_doc, warehouse)
            if sle and sle.get("valuation_rate"):
                valuation_rate = self._parse_float(sle.get("valuation_rate"))
            else:
                valuation_rate = self._parse_float(
                    frappe.db.get_value("Item", item_doc, "valuation_rate")
                )

            item_row = {
                "idx": idx,
                "item_code": item_doc,
                "warehouse": warehouse,
                "qty": qty,
                "valuation_rate": valuation_rate,
            }

            # Map variance/expense account if present
            acct = line.get("account") or line.get("account_list_id")
            if acct:
                # try to resolve account name within company
                diff_account = self._resolve_difference_account()
                if diff_account:
                    item_row["difference_account"] = diff_account

            if line.get("memo"):
                item_row["remarks"] = line.get("memo")

            items.append(item_row)

        if not items:
            return {"_skip": True, "_skip_reason": "NO_VALID_ITEMS", "ref_no": record.get("ref_no")}

        doc = {
            "doctype": "Stock Reconciliation",
            "company": company,
            "purpose": "Stock Reconciliation",
            "posting_date": self.normalize_date(record.get("date")),
            "remarks": record.get("memo") or record.get("ref_no") or "",
            "items": items,
            "difference_account": self._resolve_difference_account(),
        }

        # If QB txn_id is provided, include it as an external reference in remarks
        txn_id = record.get("txn_id")
        if txn_id:
            doc["remarks"] = f"QB_TXN:{txn_id} " + (doc.get("remarks") or "")

        return doc
