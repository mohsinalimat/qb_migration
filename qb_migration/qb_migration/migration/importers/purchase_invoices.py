import frappe

from ..base_importer import BaseImporter


class PurchaseInvoiceImporter(BaseImporter):
    source_type = "QB_BILL"
    target_doctype = "Purchase Invoice"
    json_file = "bills.json"
    json_key = "bills"

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("ref_no") or "")

    def resolve_supplier(self, qb_vendor_name):
        if not qb_vendor_name:
            raise ValueError("Supplier name missing on bill record")

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
            name = frappe.db.get_value("Item", {"item_code": qb_item_name}, "name")
            if name:
                return name

        existing = frappe.db.get_value("Item", {"item_code": default_item_code}, "name")
        if existing:
            return existing

        item = frappe.get_doc({
            "doctype": "Item",
            "item_code": default_item_code,
            "item_name": default_item_code,
            "description": "General expense item for migrated bills",
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

    def _resolve_account(self, qb_account_name):
        if not qb_account_name:
            return None

        company = frappe.defaults.get_global_default("company")
        leaf = qb_account_name.split(":")[-1].strip()
        result = frappe.db.get_value(
            "Account", {"account_name": leaf, "company": company}, "name"
        )
        if result:
            return result

        rows = frappe.db.sql(
            "select name from `tabAccount` where lower(account_name)=lower(%s) and company=%s limit 1",
            (leaf, company),
        )
        if rows:
            return rows[0][0]

        rows = frappe.db.sql(
            "select name from `tabAccount` where lower(account_name)=lower(%s) and company=%s limit 1",
            (qb_account_name, company),
        )
        return rows[0][0] if rows else None

    def find_existing_target(self, doc_data):
        if doc_data.get("bill_no"):
            return frappe.db.get_value(
                "Purchase Invoice",
                {"bill_no": doc_data["bill_no"], "company": doc_data.get("company")},
                "name",
            )
        return None

    def resolve_payable_account(self, supplier, currency):
        company = frappe.defaults.get_global_default("company")

        # Try to find an account for this supplier with the given currency
        # This is a simplification and assumes such accounts exist or can be resolved.
        # Ideally, we should check for "Payable - [Currency]"

        account_name = f"Creditors - T - {currency}"

        account = frappe.db.get_value(
            "Account",
            {"account_name": account_name, "company": company, "account_currency": currency},
            "name"
        )

        if not account:
            # Fallback or logic to create the account if needed
            # For now, try to find *any* account with this currency if the specific one doesn't exist
            account = frappe.db.get_value(
                "Account",
                {"company": company, "account_currency": currency, "account_type": "Payable"},
                "name"
            )

        if not account:
            raise ValueError(f"Could not resolve payable account for currency {currency} and company {company}")

        return account

    def _resolve_warehouse(self):
        company = frappe.defaults.get_global_default("company")
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

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        supplier_name = record.get("vendor") or record.get("vend_name")
        supplier = self.resolve_supplier(supplier_name)
        currency = record.get("currency") or "PKR" # Fallback to company currency if missing

        items = []
        for line in record.get("lines", []):
            qty = line.get("qty", 1) or 1
            items.append({
                "item_code": self.resolve_item(line.get("item", "")),
                "qty": qty,
                "rate": line.get("rate", line.get("amount", 0)),
                "amount": line.get("amount", 0),
                "expense_account": self._resolve_account(line.get("gl_code")),
                "warehouse": self._resolve_warehouse(),
                "description": line.get("description", ""),
            })

        return {
            "doctype": "Purchase Invoice",
            "supplier": supplier,
            "posting_date": self.normalize_date(record.get("txn_date")),
            "due_date": self.normalize_date(record.get("due_date") or record.get("txn_date")),
            "bill_no": record.get("ref_no", ""),
            "bill_date": self.normalize_date(record.get("date") or record.get("txn_date")),
            "company": company,
            "update_stock": 1,
            "currency": currency,
            "credit_to": self.resolve_payable_account(supplier, currency),
            "items": items,
            "is_return": record.get("is_credit", False),
        }
