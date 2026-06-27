import frappe

from .sales_orders import SalesOrderImporter


class SalesInvoiceImporter(SalesOrderImporter):
    source_type = "QB_INVOICE"
    target_doctype = "Sales Invoice"
    json_file = "invoices.json"
    json_key = "invoices"

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("inv_no") or record.get("ref_no") or "")

    def resolve_income_account(self):
        company = frappe.defaults.get_global_default("company")

        account = frappe.db.get_value(
            "Account",
            {"account_name": "Sales Income", "company": company},
            "name",
        )
        if account:
            return account

        parent_account = frappe.db.get_value(
            "Account",
            {"account_name": "Income", "company": company},
            "name",
        )
        if not parent_account:
            parent_account = frappe.db.get_value(
                "Account",
                {"root_type": "Income", "company": company},
                "name",
            )

        if not parent_account:
            parent = frappe.get_doc({
                "doctype": "Account",
                "account_name": "Income",
                "account_type": "Income Account",
                "root_type": "Income",
                "company": company,
                "is_group": 1,
            })
            parent.flags.ignore_permissions = True
            parent.insert()
            frappe.db.commit()
            parent_account = parent.name

        account_doc = frappe.get_doc({
            "doctype": "Account",
            "account_name": "Sales Income",
            "parent_account": parent_account,
            "account_type": "Income Account",
            "root_type": "Income",
            "company": company,
            "is_group": 0,
        })
        account_doc.flags.ignore_permissions = True
        account_doc.insert()
        frappe.db.commit()
        return account_doc.name

    def resolve_uom(self, qty, fallback_uom="Nos"):
        try:
            qty_value = float(qty)
        except (TypeError, ValueError):
            return fallback_uom or "Nos"

        if qty_value.is_integer():
            return fallback_uom or "Nos"

        existing = frappe.db.get_value("UOM", {"uom_name": "Unit"}, "name")
        if existing:
            return existing

        uom = frappe.get_doc({
            "doctype": "UOM",
            "uom_name": "Unit",
            "must_be_whole_number": 0,
            "enabled": 1,
        })
        uom.flags.ignore_permissions = True
        uom.insert()
        frappe.db.commit()
        return uom.name

    def resolve_item(self, qb_item_name):
        if not qb_item_name:
            raise ValueError("Item name missing on invoice line")

        item = frappe.db.get_value("Item", {"item_code": qb_item_name}, ["name", "item_code", "item_name"])
        if item:
            item_name, item_code, item_title = item
            return item_code or item_name

        result = frappe.db.sql(
            "select name, item_code, item_name from `tabItem` where lower(item_code)=lower(%s) limit 1",
            qb_item_name,
        )
        if result:
            name, item_code, item_name = result[0]
            return item_code or name

        new_item = frappe.get_doc({
            "doctype": "Item",
            "item_code": qb_item_name,
            "item_name": qb_item_name,
            "description": qb_item_name,
            "item_group": self.get_or_create_item_group(),
            "stock_uom": "Nos",
            "is_stock_item": 0,
            "is_purchase_item": 1,
            "is_sales_item": 1,
            "item_defaults": [{
                "company": frappe.defaults.get_global_default("company"),
                "income_account": self.resolve_income_account(),
            }],
        })
        new_item.flags.ignore_permissions = True
        new_item.insert()
        frappe.db.commit()
        return new_item.item_code

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

    def resolve_taxes_template(self, tax_item):
        if not tax_item:
            return None

        template = frappe.db.get_value(
            "Sales Taxes and Charges Template",
            {"title": tax_item},
            "name",
        )
        if template:
            return template

        result = frappe.db.sql(
            "select name from `tabSales Taxes and Charges Template` where lower(title)=lower(%s) limit 1",
            tax_item,
        )
        return result[0][0] if result else None

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        customer = self.resolve_customer(record.get("cust_name"))

        items = []
        for idx, line in enumerate(record.get("lines", []), 1):
            item_name = line.get("item") or line.get("item_list_id") or ""
            if not item_name and not line.get("description"):
                continue

            item_code = self.resolve_item(item_name) if item_name else None

            qty = line.get("qty") or 1
            try:
                qty_value = float(qty)
                qty = int(qty_value) if qty_value.is_integer() else qty_value
            except (TypeError, ValueError):
                qty = 1

            try:
                rate_value = abs(float(line.get("price") or 0))
            except (TypeError, ValueError):
                rate_value = 0

            try:
                amount_value = abs(float(line.get("ext_price") or 0))
            except (TypeError, ValueError):
                amount_value = 0

            uom_value = self.resolve_uom(qty, line.get("unitms") or "Nos")

            items.append({
                "idx": idx,
                "item_code": item_code,
                "item_name": line.get("description") or item_name or "",
                "qty": qty,
                "uom": uom_value,
                "rate": rate_value,
                "amount": amount_value,
                "description": line.get("description") or "",
                "income_account": self.resolve_income_account(),
            })

        if not items:
            raise ValueError("No valid item lines found for invoice")

        posting_date = self.normalize_date(record.get("inv_date") or record.get("date"))
        due_date = self.normalize_date(record.get("due_date") or record.get("inv_date") or record.get("date"))
        if due_date < posting_date:
            due_date = posting_date

        doc = {
            "doctype": "Sales Invoice",
            "customer": customer,
            "posting_date": posting_date,
            "due_date": due_date,
            "set_posting_time": 1,
            "po_no": record.get("po_num") or "",
            "company": company,
            "remarks": record.get("memo") or f"Imported from QuickBooks txn_id {record.get('txn_id')}",
            "items": items,
        }

        if record.get("ship_date"):
            doc["delivery_date"] = self.normalize_date(record.get("ship_date"))

        if record.get("ship_via"):
            doc["shipping_rule"] = record.get("ship_via")

        payment_terms_template = self.resolve_payment_terms_template(record.get("terms"))
        if payment_terms_template:
            doc["payment_terms_template"] = payment_terms_template

        taxes_template = self.resolve_taxes_template(record.get("tax_item"))
        if taxes_template:
            doc["taxes_and_charges"] = taxes_template

        sales_team = []
        sales_person = self.resolve_sales_person(record.get("salesman"))
        if sales_person:
            sales_team.append({
                "sales_person": sales_person,
                "allocated_percentage": 100,
                "commission_rate": 0,
            })

        if sales_team:
            doc["sales_team"] = sales_team

        if record.get("tax_amt") not in (None, ""):
            doc["total_taxes_and_charges"] = abs(float(record.get("tax_amt") or 0))

        return doc
