import frappe

from ..base_importer import BaseImporter


class PurchaseOrderImporter(BaseImporter):
    source_type = "QB_PURCHASE_ORDER"
    target_doctype = "Purchase Order"
    json_file = "purchase_orders.json"
    json_key = "purchase_orders"

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("ref_no") or "")

    def resolve_supplier(self, qb_vendor_name):
        if not qb_vendor_name:
            raise ValueError("Supplier name missing on purchase order record")

        name = frappe.db.get_value("Supplier", {"supplier_name": qb_vendor_name}, "name")
        if not name:
            result = frappe.db.sql(
                "select name from `tabSupplier` where lower(supplier_name)=lower(%s) limit 1",
                qb_vendor_name,
            )
            name = result[0][0] if result else None

        if not name:
            raise ValueError(f"Supplier not found: {qb_vendor_name}")
        return name

    def resolve_item(self, qb_item_name):
        default_item_code = "_General Expenses"
        if qb_item_name:
            exists = frappe.db.get_value("Item", {"item_code": qb_item_name}, "name")
            if exists:
                return qb_item_name

        existing = frappe.db.get_value("Item", {"item_code": default_item_code}, "name")
        if existing:
            return default_item_code

        item = frappe.get_doc({
            "doctype": "Item",
            "item_code": default_item_code,
            "item_name": default_item_code,
            "description": "General expense item for migrated purchase orders",
            "item_group": "All Item Groups",
            "stock_uom": "Nos",
            "is_stock_item": 0,
            "is_purchase_item": 1,
            "is_sales_item": 0,
        })
        item.flags.ignore_permissions = True
        item.insert()
        frappe.db.commit()
        return default_item_code

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        supplier_name = record.get("vend_name")
        supplier = self.resolve_supplier(supplier_name)

        items = []
        for idx, line in enumerate(record.get("lines", []), 1):
            qty = line.get("qty")
            if qty is None:
                qty = 1
            if qty == 0 or qty == 0.0:
                continue

            items.append({
                "idx": idx,
                "item_code": self.resolve_item(line.get("item", "")),
                "qty": qty,
                "rate": line.get("price", 0),
                "amount": line.get("ext_price", 0),
                "description": line.get("description", ""),
                "schedule_date": self.normalize_date(record.get("expected_date") or record.get("date")),
                "received_qty": line.get("qty_received", 0),
            })

        if not items:
            return {
                "_skip": True,
                "_skip_reason": "Skipped purchase order because all line items have qty 0.0",
                "ref_no": record.get("ref_no", ""),
            }

        # Determine status based on is_fully_rcvd
        status = "Completed" if record.get("is_fully_rcvd") else "To Receive"

        return {
            "doctype": "Purchase Order",
            "name": record.get("txn_id"),
            "supplier": supplier,
            "transaction_date": self.normalize_date(record.get("date")),
            "schedule_date": self.normalize_date(record.get("expected_date") or record.get("date")),
            "po_no": record.get("ref_no", ""),
            "company": company,
            "status": status,
            "remarks": record.get("memo", ""),
            "items": items,
            "grand_total": record.get("total_amt", 0),
        }
