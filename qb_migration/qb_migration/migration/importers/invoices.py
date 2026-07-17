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

    def find_existing_target(self, doc_data):
        """Find existing Sales Invoice by name (txn_id) to avoid duplicates on re-run."""
        name = doc_data.get("name")
        if name:
            return frappe.db.get_value("Sales Invoice", {"name": name}, "name")
        return None

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

    def _get_line_item_tax_template(self, record, line):
        line_tax_code = str(line.get("tax_code") or "").strip()
        if line_tax_code.lower() != "tax":
            return None

        parent_tax_item = str(record.get("tax_item") or "").strip()
        if not parent_tax_item:
            return None

        return self._resolve_item_tax_template(parent_tax_item)

    def _get_or_create_tax_account(self, tax_item):
        """Get or create a liability account for the given tax item."""
        company = frappe.defaults.get_global_default("company")
        # Use a clean account name based on the tax item, or fallback to "Sales Tax"
        account_name = (tax_item or "Sales Tax").strip()
        # Search for an existing account with the same name and company
        existing = frappe.db.get_value(
            "Account",
            {"account_name": account_name, "company": company, "is_group": 0},
            "name"
        )
        if existing:
            return existing

        # Find a suitable parent account (Current Liabilities or any Liability group)
        parent_account = frappe.db.get_value(
            "Account",
            {"account_type": "Payable", "is_group": 1, "company": company},
            "name"
        )
        if not parent_account:
            parent_account = frappe.db.get_value(
                "Account",
                {"root_type": "Liability", "is_group": 1, "company": company},
                "name"
            )
        if not parent_account:
            # Fallback: create under "Liabilities" if it exists, otherwise use the root
            parent_account = frappe.db.get_value(
                "Account",
                {"root_type": "Liability", "is_group": 1, "company": company},
                "name"
            )
            if not parent_account:
                raise ValueError("No Liability group account found to create tax account")

        # Create the new account
        account = frappe.get_doc({
            "doctype": "Account",
            "account_name": account_name,
            "company": company,
            "parent_account": parent_account,
            "account_type": "Tax",
            "root_type": "Liability",
            "is_group": 0,
        })
        account.flags.ignore_permissions = True
        account.insert()
        frappe.db.commit()
        return account.name

    def _is_zero_qty(self, qty):
        """Check if quantity is zero."""
        if qty is None or qty == "":
            return False
        try:
            return float(qty) == 0
        except (TypeError, ValueError):
            return False

    def resolve_cost_center(self, class_name):
        """Resolve or create a cost center."""
        if not class_name:
            return None

        company = frappe.defaults.get_global_default("company")
        existing = frappe.db.get_value(
            "Cost Center",
            {"cost_center_name": class_name, "company": company},
            "name",
        )
        if existing:
            return existing

        try:
            cost_center = frappe.get_doc({
                "doctype": "Cost Center",
                "cost_center_name": class_name,
                "company": company,
            })
            cost_center.flags.ignore_permissions = True
            cost_center.insert()
            frappe.db.commit()
            return cost_center.name
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
        item_idx = 0
        skipped_taxable_line = False
        remaining_taxable_line = False
        imported_subtotal = 0.0

        for line in record.get("lines", []):
            # Skip zero-qty lines and track if taxable lines were skipped
            if self._is_zero_qty(line.get("qty")):
                if (line.get("tax_code") or "").strip().lower() == "tax":
                    skipped_taxable_line = True
                continue

            if (line.get("tax_code") or "").strip().lower() == "tax":
                remaining_taxable_line = True

            item_name = line.get("item") or line.get("item_list_id") or ""
            if not item_name and not line.get("description"):
                continue

            item_code = self.resolve_item(item_name) if item_name else None

            qty = line.get("qty")
            if qty is None or qty == "":
                qty = 1

            try:
                qty_value = float(qty)
                qty = int(qty_value) if qty_value.is_integer() else qty_value
            except (TypeError, ValueError):
                qty = 1

            if qty == 0:
                continue

            try:
                rate_value = abs(float(line.get("price") or 0))
            except (TypeError, ValueError):
                rate_value = 0

            try:
                amount_value = abs(float(line.get("ext_price") or 0))
            except (TypeError, ValueError):
                amount_value = 0

            imported_subtotal += amount_value

            uom_value = self.resolve_uom(qty, line.get("unitms") or "Nos")

            # Ensure item's stock_uom supports fractional quantities if needed
            if item_code and uom_value in ("Unit", "Piece"):
                try:
                    qty_float = float(qty)
                    if not qty_float.is_integer():
                        self.ensure_item_supports_uom(item_code, uom_value)
                except (TypeError, ValueError):
                    pass

            item_idx += 1
            item_row = {
                "idx": item_idx,
                "item_code": item_code,
                "item_name": line.get("description") or item_name or "",
                "qty": qty,
                "uom": uom_value,
                "rate": rate_value,
                "amount": amount_value,
                "description": line.get("description") or "",
                "income_account": self.resolve_income_account(),
            }

            cost_center = self.resolve_cost_center(line.get("class_name"))
            if cost_center:
                item_row["cost_center"] = cost_center

            tax_template = self._get_line_item_tax_template(record, line)
            if tax_template:
                item_row["item_tax_template"] = tax_template

            items.append(item_row)

        if not items:
            return {
                "_skip": True,
                "_skip_reason": "Skipped sales invoice because all line items have qty 0.0 or no valid lines",
                "ref_no": record.get("inv_no") or record.get("ref_no") or record.get("txn_id") or "",
            }

        total_amt = abs(float(record.get("total_amt") or 0))
        project = self.resolve_project(record.get("project_name"))

        # If all taxable QuickBooks lines were skipped, clear invoice-level tax totals
        # and make the grand total equal the subtotal of imported items.
        skip_invoice_tax = skipped_taxable_line and not remaining_taxable_line
        doc = {
            "doctype": "Sales Invoice",
            "customer": customer,
            "posting_date": self.normalize_date(record.get("inv_date") or record.get("date")),
            "due_date": self.normalize_date(record.get("due_date") or record.get("inv_date") or record.get("date")),
            "company": company,
            "remarks": record.get("memo") or f"Imported from QuickBooks txn_id {record.get('txn_id')}",
            "items": items,
            "update_stock": 1,
            "set_posting_time": 1,
            "total": imported_subtotal,
            "total_taxes_and_charges": 0 if skip_invoice_tax else abs(float(record.get("tax_amt") or 0)),
            "grand_total": imported_subtotal if skip_invoice_tax else total_amt,
            "base_total": imported_subtotal,
            "base_total_taxes_and_charges": 0 if skip_invoice_tax else abs(float(record.get("tax_amt") or 0)),
            "base_grand_total": imported_subtotal if skip_invoice_tax else total_amt,
        }

        if project:
            doc["project"] = project

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

        if record.get("po_num"):
            doc["po_no"] = record.get("po_num")

        payment_terms_template = self.resolve_payment_terms_template(record.get("terms"))
        if payment_terms_template:
            doc["payment_terms_template"] = payment_terms_template

        # ---- Tax handling ----
        sales_tax_total = abs(float(record.get("tax_amt") or 0))
        has_item_level_tax = any(item.get("item_tax_template") for item in items)

        if sales_tax_total > 0 and not has_item_level_tax and not skip_invoice_tax:
            tax_account = self._get_or_create_tax_account(record.get("tax_item"))
            tax_row = {
                "charge_type": "Actual",
                "account_head": tax_account,
                "rate": abs(float(record.get("tax_pct") or 0)),
                "tax_amount": sales_tax_total,
                "included_in_print_rate": 0,
                "description": f"Sales Tax ({record.get('tax_item') or 'Default'})"
            }
            doc.setdefault("taxes", []).append(tax_row)

        if not has_item_level_tax and not skip_invoice_tax:
            tax_category = self._resolve_tax_category(record.get("tax_code"))
            if tax_category:
                doc["tax_category"] = tax_category

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

        return doc
