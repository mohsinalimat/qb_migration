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

    def _resolve_store_warehouse(self, item_doc, company):
        """Resolve the store warehouse for an item.

        QB's inventory_adjustments.json carries no warehouse/location per
        line, so this is a best-effort resolution: the item's configured
        default warehouse for this company, falling back to a warehouse
        named "Stores", falling back to any other company warehouse.
        """
        if item_doc:
            default_warehouse = frappe.db.get_value(
                "Item Default",
                {"parent": item_doc, "company": company},
                "default_warehouse",
            )
            if default_warehouse:
                return default_warehouse

        store = frappe.db.get_value(
            "Warehouse",
            {"warehouse_name": "Stores", "company": company},
            "name",
        )
        if store:
            return store

        return frappe.db.get_value(
            "Warehouse",
            {"company": company},
            "name",
        )

    def _parse_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _build_remarks(self, record):
        txn_id = record.get("txn_id") or ""
        ref_no = record.get("ref_no") or ""
        memo = record.get("memo") or ""

        lines = []
        if txn_id:
            lines.append(f"QB_TXN:{txn_id}")
        if ref_no:
            lines.append(f"Reference: {ref_no}")
        if memo:
            lines.append(memo)

        return "\n".join(lines)

    def _split_lines_by_sign(self, record):
        """Split lines into negative (issue) and positive (receipt) qty_diff."""
        negative_lines = []  # qty_diff < 0 -> Material Issue
        positive_lines = []  # qty_diff > 0 -> Material Receipt

        for line in record.get("lines") or []:
            item_name = line.get("item") or ""
            if not item_name:
                continue

            qty_diff = self._parse_float(line.get("quantity_difference"))
            if qty_diff == 0:
                continue

            item_doc = self._resolve_item(item_name)
            if not item_doc:
                # Skip unresolvable items; they'll be caught by validation
                continue

            if qty_diff < 0:
                negative_lines.append((item_doc, abs(qty_diff), line))
            else:
                positive_lines.append((item_doc, qty_diff, line))

        return negative_lines, positive_lines

    def _build_issue_items(self, negative_lines, company):
        """Build Stock Entry Detail rows for Material Issue (stock going OUT)."""
        items = []

        for item_doc, qty, line in negative_lines:
            store_warehouse = self._resolve_store_warehouse(item_doc, company)
            if not store_warehouse:
                return None, f"MISSING_STORE_WAREHOUSE:{item_doc}"

            row = {
                "item_code": item_doc,
                "qty": qty,
                "s_warehouse": store_warehouse,
                # No basic_rate needed for Material Issue - ERPNext pulls
                # valuation from the source warehouse's SLE history automatically
            }

            if line.get("memo"):
                row["description"] = line.get("memo")

            items.append(row)

        return items, None

    def _build_receipt_items(self, positive_lines, company):
        """Build Stock Entry Detail rows for Material Receipt (stock coming IN)."""
        items = []

        for item_doc, qty, line in positive_lines:
            store_warehouse = self._resolve_store_warehouse(item_doc, company)
            if not store_warehouse:
                return None, f"MISSING_STORE_WAREHOUSE:{item_doc}"

            value_diff = self._parse_float(line.get("value_difference"))
            if value_diff == 0:
                value_diff = 1.0  # Fallback to prevent zero rate

            basic_rate = abs(value_diff / qty) if qty else 1.0

            row = {
                "item_code": item_doc,
                "qty": qty,
                "t_warehouse": store_warehouse,
                "basic_rate": basic_rate,
            }

            if line.get("memo"):
                row["description"] = line.get("memo")

            items.append(row)

        return items, None

    def find_existing_target(self, doc_data):
        if not doc_data:
            return None

        remarks = (doc_data or {}).get("remarks") or ""
        marker = "QB_TXN:"
        if marker not in remarks:
            return None

        marker_line = remarks.split("\n", 1)[0].strip()
        if not marker_line.startswith(marker):
            return None

        return frappe.db.get_value(
            "Stock Entry",
            {"remarks": ["like", f"%{marker_line}%"], "docstatus": ["!=", 2]},
            "name",
        )

    def post_insert(self, doc, record):
        """After inserting the primary Stock Entry (Issue or Receipt),
        create and submit the counterpart if the adjustment has both
        positive and negative lines.
        """
        company = self._get_company()
        if not company:
            return

        negative_lines, positive_lines = self._split_lines_by_sign(record)

        # If we created a Material Issue (for negative lines), also create Material Receipt for positive lines
        if doc.purpose == "Material Issue" and positive_lines:
            receipt_items, skip_reason = self._build_receipt_items(positive_lines, company)
            if skip_reason:
                frappe.log_error(f"Inventory Adjustment {record.get('txn_id')}: {skip_reason}", "Inventory Adjustment Import")
                return

            if receipt_items:
                for idx, row in enumerate(receipt_items, start=1):
                    row["idx"] = idx

                receipt_doc = frappe.get_doc({
                    "doctype": "Stock Entry",
                    "stock_entry_type": "Material Receipt",
                    "purpose": "Material Receipt",
                    "company": company,
                    "posting_date": self.normalize_date(record.get("date")),
                    "set_posting_time": 1,
                    "remarks": self._build_remarks(record) + "\n[Auto-created Receipt for positive adjustments]",
                    "items": receipt_items,
                })
                receipt_doc.flags.ignore_permissions = True
                receipt_doc.insert()
                receipt_doc.submit()

        # If we created a Material Receipt (for positive lines), also create Material Issue for negative lines
        elif doc.purpose == "Material Receipt" and negative_lines:
            issue_items, skip_reason = self._build_issue_items(negative_lines, company)
            if skip_reason:
                frappe.log_error(f"Inventory Adjustment {record.get('txn_id')}: {skip_reason}", "Inventory Adjustment Import")
                return

            if issue_items:
                for idx, row in enumerate(issue_items, start=1):
                    row["idx"] = idx

                issue_doc = frappe.get_doc({
                    "doctype": "Stock Entry",
                    "stock_entry_type": "Material Issue",
                    "purpose": "Material Issue",
                    "company": company,
                    "posting_date": self.normalize_date(record.get("date")),
                    "set_posting_time": 1,
                    "remarks": self._build_remarks(record) + "\n[Auto-created Issue for negative adjustments]",
                    "items": issue_items,
                })
                issue_doc.flags.ignore_permissions = True
                issue_doc.insert()
                issue_doc.submit()

        if hasattr(doc, "submit"):
            doc.submit()

    def map_record(self, record):
        company = self._get_company()
        if not company:
            return {"_skip": True, "_skip_reason": "MISSING_COMPANY", "ref_no": record.get("ref_no")}

        lines = record.get("lines") or []
        if not lines:
            return {"_skip": True, "_skip_reason": "NO_LINES", "ref_no": record.get("ref_no")}

        negative_lines, positive_lines = self._split_lines_by_sign(record)

        if not negative_lines and not positive_lines:
            return {"_skip": True, "_skip_reason": "NO_VALID_ITEMS", "ref_no": record.get("ref_no")}

        # Prefer creating Material Issue first (for negative qty_diff)
        # If no negative lines, create Material Receipt
        if negative_lines:
            items, skip_reason = self._build_issue_items(negative_lines, company)
            purpose = "Material Issue"
            stock_entry_type = "Material Issue"
        else:
            items, skip_reason = self._build_receipt_items(positive_lines, company)
            purpose = "Material Receipt"
            stock_entry_type = "Material Receipt"

        if skip_reason:
            return {"_skip": True, "_skip_reason": skip_reason, "ref_no": record.get("ref_no")}

        if not items:
            return {"_skip": True, "_skip_reason": "NO_VALID_ITEMS", "ref_no": record.get("ref_no")}

        for idx, row in enumerate(items, start=1):
            row["idx"] = idx

        doc = {
            "doctype": "Stock Entry",
            "stock_entry_type": stock_entry_type,
            "purpose": purpose,
            "company": company,
            "posting_date": self.normalize_date(record.get("date")),
            "set_posting_time": 1,
            "remarks": self._build_remarks(record),
            "items": items,
        }

        return doc