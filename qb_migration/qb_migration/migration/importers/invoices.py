import frappe

from .sales_orders import SalesOrderImporter


class SalesInvoiceImporter(SalesOrderImporter):
    source_type = "QB_INVOICE"
    target_doctype = "Sales Invoice"
    json_file = "invoices.json"
    json_key = "invoices"

    def _ensure_fractional_uoms(self):
        """Ensure fractional UOMs exist in the database before processing invoices."""
        for uom_name in ["Unit", "Piece"]:
            try:
                existing = frappe.db.get_value("UOM", {"uom_name": uom_name}, ["name", "must_be_whole_number"], as_dict=True)
                if existing:
                    if existing.must_be_whole_number:
                        # Update to allow fractions
                        frappe.db.set_value("UOM", existing.name, "must_be_whole_number", 0)
                        frappe.db.commit()
                else:
                    # Create new
                    uom = frappe.get_doc({
                        "doctype": "UOM",
                        "uom_name": uom_name,
                        "must_be_whole_number": 0,
                        "enabled": 1,
                    })
                    uom.flags.ignore_permissions = True
                    uom.insert()
                    frappe.db.commit()
            except Exception as e:
                print(f"  WARN: Could not ensure UOM {uom_name}: {e}")

    def run(self, dry_run: bool = False):
        """Run invoice import with UOM setup."""
        print(f"\n[{self.source_type}] Preparing fractional UOMs...")
        self._ensure_fractional_uoms()
        return super().run(dry_run=dry_run)

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

        # For fractional quantities, use a UOM that allows them
        if not qty_value.is_integer():
            # Try Unit first (should exist from initialization)
            existing = frappe.db.get_value("UOM", {"uom_name": "Unit"}, "name")
            if existing:
                return existing
            
            # Try Piece as fallback
            existing = frappe.db.get_value("UOM", {"uom_name": "Piece"}, "name")
            if existing:
                return existing
            
            # If neither exists, try to create Unit on the fly
            try:
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
            except Exception:
                pass
            
            # Final fallback: return "Nos" which will cause validation error
            # but at least will fail clearly
            return fallback_uom or "Nos"

        # For whole quantities, use the fallback UOM
        return fallback_uom or "Nos"

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
            "stock_uom": "Unit",  # Use Unit to support fractional quantities
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

    def _resolve_item_tax_template(self, tax_code):
        if not tax_code:
            return None

        existing = frappe.db.get_value("Item Tax Template", {"title": tax_code}, "name")
        if existing:
            return existing

        result = frappe.db.sql(
            "select name from `tabItem Tax Template` where lower(title)=lower(%s) limit 1",
            tax_code,
        )
        return result[0][0] if result else None

    def _resolve_tax_category(self, tax_code):
        if not tax_code:
            return None

        existing = frappe.db.get_value("Tax Category", {"name": tax_code}, "name")
        if existing:
            return existing

        existing = frappe.db.get_value("Tax Category", {"title": tax_code}, "name")
        if existing:
            return existing

        try:
            doc = frappe.get_doc({
                "doctype": "Tax Category",
                "title": str(tax_code),
            })
            doc.flags.ignore_permissions = True
            doc.insert()
            frappe.db.commit()
            return doc.name
        except Exception:
            return None

    def ensure_item_supports_uom(self, item_code, uom_name):
        """
        Ensure the item's stock_uom supports the given UOM.
        For items receiving fractional quantities, update stock_uom to Unit (best practice).
        """
        if not item_code or uom_name not in ("Unit", "Piece"):
            return
        
        try:
            item = frappe.get_doc("Item", item_code)
            
            # If stock_uom is already Unit or Piece, no need to update
            if item.stock_uom in ("Unit", "Piece"):
                return
            
            # For fractional-quantity items, always use Unit as stock_uom for consistency
            # This ensures proper ERPNext compliance and inventory tracking
            if item.stock_uom != "Unit":
                item.stock_uom = "Unit"
                item.flags.ignore_permissions = True
                item.save()
                frappe.db.commit()
        except frappe.DoesNotExistError:
            pass  # Item doesn't exist (may be created later)
        except Exception as e:
            print(f"  WARN: Could not update item {item_code} for UOM support: {e}")

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
            
            # Ensure item's stock_uom supports fractional quantities if needed
            if item_code and uom_value in ("Unit", "Piece"):
                try:
                    qty_float = float(qty)
                    if not qty_float.is_integer():
                        self.ensure_item_supports_uom(item_code, uom_value)
                except (TypeError, ValueError):
                    pass

            item_row = {
                "idx": idx,
                "item_code": item_code,
                "item_name": line.get("description") or item_name or "",
                "qty": qty,
                "uom": uom_value,
                "rate": rate_value,
                "amount": amount_value,
                "description": line.get("description") or "",
                "income_account": self.resolve_income_account(),
            }

            tax_template = self._resolve_item_tax_template(line.get("tax_code"))
            if tax_template:
                item_row["item_tax_template"] = tax_template
            else:
                tax_category = self._resolve_tax_category(line.get("tax_code"))
                if tax_category:
                    item_row["tax_category"] = tax_category

            items.append(item_row)

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

        if record.get("txn_id"):
            doc["name"] = str(record.get("txn_id"))

        if record.get("inv_no"):
            si_meta = frappe.get_meta("Sales Invoice")
            if si_meta.has_field("invoice_number"):
                doc["invoice_number"] = record.get("inv_no")

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
