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

    def resolve_payment_terms_template(self, terms):
        if not terms:
            return None

        template = frappe.db.get_value("Payment Terms Template", {"name": terms}, "name")
        if template:
            return template

        result = frappe.db.sql(
            "select name from `tabPayment Terms Template` where lower(name)=lower(%s) limit 1",
            terms,
        )
        return result[0][0] if result else None

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
        bill_no = str(doc_data.get("bill_no") or "").strip()
        if bill_no:
            return frappe.db.get_value(
                "Purchase Invoice",
                {"bill_no": bill_no, "company": doc_data.get("company")},
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

    def _normalize_qty(self, value):
        qty = value if value not in (None, "") else 1
        try:
            qty = float(qty)
            return int(qty) if qty.is_integer() else qty
        except (TypeError, ValueError):
            return 1

    def _qty_is_zero(self, value):
        try:
            return float(value) == 0
        except (TypeError, ValueError):
            return False

    def _normalize_rate(self, line, qty):
        rate = line.get("rate")
        if rate not in (None, ""):
            try:
                return float(rate)
            except (TypeError, ValueError):
                return 0

        try:
            return float(line.get("amount", 0)) / qty if qty else 0
        except (TypeError, ValueError, ZeroDivisionError):
            return 0

    def _resolve_cost_center(self, class_name):
        if not class_name:
            return None

        cost_center_name = str(class_name).strip()
        if not cost_center_name:
            return None

        return frappe.db.get_value("Cost Center", {"cost_center_name": cost_center_name}, "name")

    def _resolve_project(self, project_name):
        if not project_name:
            return None

        project_name = str(project_name).strip()
        if not project_name:
            return None

        project = frappe.db.get_value("Project", {"project_name": project_name}, "name")
        if project:
            return project

        result = frappe.db.sql(
            "select name from `tabProject` where lower(project_name)=lower(%s) limit 1",
            (project_name,),
        )
        return result[0][0] if result else None

    def _resolve_project_from_entity(self, entity):
        if not entity:
            return None

        entity_value = str(entity).strip()
        if ":" not in entity_value:
            return None

        project_name = entity_value.split(":")[-1].strip()
        if not project_name:
            return None

        return self._resolve_project(project_name)

    def _build_item_row(self, line):
        qty = self._normalize_qty(line.get("qty", 1))
        if qty == 0:
            return None

        rate = self._normalize_rate(line, qty)

        item_idx = line.get("line_no")
        try:
            item_idx = int(item_idx)
        except (TypeError, ValueError):
            item_idx = None

        item_data = {
            "item_code": self.resolve_item(line.get("item", "")),
            "qty": qty,
            "rate": rate,
            "amount": line.get("amount", 0),
            "expense_account": self._resolve_account(line.get("gl_code") or line.get("account")),
            "description": line.get("description", ""),
        }

        if item_idx is not None:
            item_data["idx"] = item_idx

        tax_template = line.get("tax_code")
        if tax_template:
            item_data["item_tax_template"] = tax_template

        cost_center = self._resolve_cost_center(line.get("class"))
        if cost_center:
            item_data["cost_center"] = cost_center

        project = self._resolve_project(line.get("customer"))
        if not project:
            project = self._resolve_project_from_entity(line.get("customer"))

        if project:
            item_data["project"] = project

        return item_data

    def _build_tax_row(self, line):
        account_head = self._resolve_account(line.get("account") or line.get("gl_code"))
        if not account_head:
            return None

        amount = line.get("amount", 0)
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            amount = 0

        return {
            "charge_type": "Actual",
            "account_head": account_head,
            "tax_amount": amount,
            "description": line.get("description") or line.get("account") or line.get("gl_code") or "",
            "included_in_print_rate": 0,
        }

    def _build_fallback_item_row(self, line=None):
        item_data = {
            "item_code": self.resolve_item(""),
            "qty": 1,
            "rate": 0,
            "amount": 0,
            "expense_account": self._resolve_account((line or {}).get("gl_code") or (line or {}).get("account")),
            "description": (line or {}).get("description") or "General expense",
        }

        if line and line.get("line_no") is not None:
            try:
                item_data["idx"] = int(line.get("line_no"))
            except (TypeError, ValueError):
                pass

        return item_data

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        supplier_name = record.get("vendor") or record.get("vend_name")
        supplier = self.resolve_supplier(supplier_name)
        currency = record.get("currency") or "PKR"  # Fallback to company currency if missing

        items = []
        taxes = []
        lines = [line for line in record.get("lines", []) if isinstance(line, dict)]

        if lines and all("amount" in line and self._qty_is_zero(line.get("amount")) for line in lines):
            return {
                "_skip": True,
                "_skip_reason": "Skipped purchase invoice because all line items have amount 0.0",
                "ref_no": record.get("ref_no", "") or record.get("txn_id", ""),
            }

        for line in lines:
            line_type = str(line.get("line_type") or "").strip().lower()
            if line_type == "expense":
                tax_row = self._build_tax_row(line)
                if tax_row:
                    taxes.append(tax_row)
                continue

            # Extract qty and amount for proper validation
            qty = line.get("qty")
            amount = line.get("amount", 0)

            # Skip only if both qty and amount are zero
            if self._qty_is_zero(qty) and self._qty_is_zero(amount):
                continue
            # If qty is zero but amount is not zero, set qty to 1
            if self._qty_is_zero(qty) and not self._qty_is_zero(amount):
                line["qty"] = 1

            item_data = self._build_item_row(line)
            if item_data:
                items.append(item_data)

        if not items:
            for line in lines:
                items.append(self._build_fallback_item_row(line))
                break

        doc = {
            "doctype": "Purchase Invoice",
            "supplier": supplier,
            "posting_date": self.normalize_date(record.get("date") or record.get("txn_date")),
            "set_posting_time": 1,
            "due_date": self.normalize_date(record.get("due_date") or record.get("date") or record.get("txn_date")),
            "bill_no": record.get("ref_no", ""),
            "bill_date": self.normalize_date(record.get("date") or record.get("txn_date")),
            "company": company,
            "update_stock": 1,
            "currency": currency,
            "credit_to": self.resolve_payable_account(supplier, currency),
            "items": items,
            "remarks": record.get("memo") or f"Imported from QuickBooks txn_id {record.get('txn_id')}",
            "is_return": record.get("is_credit", False),
        }

        if taxes:
            doc["taxes"] = taxes

        payment_terms_template = self.resolve_payment_terms_template(record.get("terms"))
        if payment_terms_template:
            doc["payment_terms_template"] = payment_terms_template

        return doc
