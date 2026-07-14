import frappe

from ..base_importer import BaseImporter


class ItemReceiptImporter(BaseImporter):
    source_type = "QB_ITEM_RECEIPT"
    target_doctype = "Purchase Receipt"
    json_file = "item_receipts.json"
    json_key = "item_receipts"

    def _has_field(self, doctype, fieldname):
        try:
            meta = frappe.get_meta(doctype)
        except Exception:
            return False
        return any(field.fieldname == fieldname for field in meta.fields)

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("ref_no") or "")

    def resolve_supplier(self, qb_vendor_name):
        if not qb_vendor_name:
            raise ValueError("Supplier name missing on item receipt record")

        supplier = frappe.db.get_value("Supplier", {"supplier_name": qb_vendor_name}, "name")
        if supplier:
            return supplier

        result = frappe.db.sql(
            "select name from `tabSupplier` where lower(supplier_name)=lower(%s) limit 1",
            qb_vendor_name,
        )
        return result[0][0] if result else None

    def resolve_item(self, qb_item_name):
        if not qb_item_name:
            return None

        item = frappe.db.get_value("Item", {"item_code": qb_item_name}, "name")
        if item:
            return item

        result = frappe.db.sql(
            "select name from `tabItem` where lower(item_code)=lower(%s) or lower(item_name)=lower(%s) limit 1",
            (qb_item_name, qb_item_name),
        )
        if result:
            return result[0][0]

        item_doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": qb_item_name,
            "item_name": qb_item_name,
            "description": qb_item_name,
            "item_group": "All Item Groups",
            "stock_uom": "Nos",
            "is_stock_item": 1,
            "is_purchase_item": 1,
            "is_sales_item": 0,
        })
        item_doc.flags.ignore_permissions = True
        item_doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return item_doc.name

    def _ensure_uom(self, uom_name):
        if not uom_name:
            uom_name = "Nos"

        existing = frappe.db.get_value("UOM", {"uom_name": uom_name}, "name")
        if existing:
            return existing

        uom = frappe.get_doc({
            "doctype": "UOM",
            "uom_name": uom_name,
            "must_be_whole_number": 0,
            "enabled": 1,
        })
        uom.flags.ignore_permissions = True
        uom.insert(ignore_permissions=True)
        frappe.db.commit()
        return uom.name

    def _resolve_cost_center(self, cc_name):
        if not cc_name:
            return None

        company = frappe.defaults.get_global_default("company")
        leaf = cc_name.split(":")[-1].strip()
        name = frappe.db.get_value("Cost Center", {"cost_center_name": leaf, "company": company}, "name")
        if name:
            return name

        row = frappe.db.sql(
            "select name from `tabCost Center` where lower(cost_center_name)=lower(%s) and company=%s limit 1",
            (leaf, company),
        )
        if row:
            return row[0][0]

        return None

    def _resolve_warehouse(self):
        company = frappe.defaults.get_global_default("company")
        if not company:
            return None

        warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
        if warehouse:
            return warehouse

        warehouse_name = "Stores"
        existing = frappe.db.get_value("Warehouse", {"warehouse_name": warehouse_name, "company": company}, "name")
        if existing:
            return existing

        company_abbr = frappe.db.get_value("Company", company, "abbr")
        if company_abbr:
            alt_name = f"Stores - {company_abbr}"
            existing = frappe.db.get_value("Warehouse", {"name": alt_name}, "name")
            if existing:
                return existing

        warehouse_doc = frappe.get_doc({
            "doctype": "Warehouse",
            "warehouse_name": warehouse_name,
            "company": company,
            "is_group": 0,
        })
        warehouse_doc.flags.ignore_permissions = True
        warehouse_doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return warehouse_doc.name

    def _resolve_purchase_order(self, po_num):
        if not po_num:
            return None
        po = frappe.db.get_value("Purchase Order", {"name": po_num}, "name")
        if po:
            return po
        return frappe.db.get_value("Purchase Order", {"po_no": po_num}, "name")

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        supplier = self.resolve_supplier(record.get("vend_name"))
        if not supplier:
            raise ValueError(f"Supplier not found: {record.get('vend_name')}")

        warehouse = self._resolve_warehouse()
        purchase_order = self._resolve_purchase_order(record.get("po_num"))
        items = []
        total_amt = 0.0

        # Collect dict-like lines
        lines = [l for l in record.get("lines", []) if isinstance(l, dict)]

        # If there are lines and all have qty == 0, skip the whole transaction
        if lines:
            kept_lines = []
            for l in lines:
                try:
                    qty_val = float(l.get("qty") or 0)
                except (TypeError, ValueError):
                    qty_val = 0

                if qty_val == 0:
                    continue
                kept_lines.append(l)

            if not kept_lines:
                return {"_skip": True, "_skip_reason": "ALL_LINES_ZERO_QTY", "ref_no": record.get("txn_id", "")}
        else:
            # No lines at all
            return {"_skip": True, "_skip_reason": "NO_ITEMS", "ref_no": record.get("txn_id", "")}

        for idx, line in enumerate(kept_lines, 1):
            item_code = line.get("item") or ""
            # Resolve item only for lines we will process
            item_name = self.resolve_item(item_code)
            if not item_name:
                continue

            try:
                qty = float(line.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0

            try:
                amount = float(line.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0

            rate = amount / qty if qty else 0.0
            total_amt += amount

            item_row = {
                "idx": idx,
                "item_code": item_code,
                "item_name": item_code,
                "description": line.get("description") or item_code or "",
                "qty": qty,
                "uom": self._ensure_uom(line.get("unitms")),
                "rate": rate,
                "amount": amount,
                "warehouse": warehouse,
                "cost_center": self._resolve_cost_center(line.get("class_name")),
            }

            if purchase_order and self._has_field("Purchase Receipt Item", "purchase_order"):
                item_row["purchase_order"] = purchase_order

            item_tax_template = line.get("tax_code")
            if item_tax_template and self._has_field("Purchase Receipt Item", "item_tax_template"):
                item_row["item_tax_template"] = item_tax_template

            sales_partner = line.get("sales_rep")
            if sales_partner and self._has_field("Purchase Receipt Item", "sales_partner"):
                item_row["sales_partner"] = sales_partner

            items.append(item_row)

        if not items:
            return {"_skip": True, "_skip_reason": "NO_ITEMS", "ref_no": record.get("txn_id", "")}

        doc = {
            "doctype": "Purchase Receipt",
            "supplier": supplier,
            "company": company,
            "posting_date": self.normalize_date(record.get("date")),
            "supplier_delivery_note": record.get("txn_id") or record.get("ref_no") or "",
            "remarks": record.get("memo") or "",
            "tax_category": record.get("sales_tax_code") or "",
            "grand_total": total_amt or float(record.get("total_amt") or 0),
            "items": items,
        }

        if self._has_field("Purchase Receipt", "included_in_print_rate"):
            doc["included_in_print_rate"] = 1 if record.get("is_tax_included") else 0

        return doc
