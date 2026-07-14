import traceback

import frappe

from .invoices import SalesInvoiceImporter
from .customer_currency import ensure_customer_currency


class CreditMemoImporter(SalesInvoiceImporter):
    source_type = "QB_CREDIT_MEMO"
    target_doctype = "Sales Invoice"
    json_file = "credit_memos.json"
    json_key = "credit_memos"

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("ref_no") or "")

    def resolve_customer(self, qb_customer_name):
        if not qb_customer_name:
            raise ValueError("Customer name missing on credit memo record")

        qb_customer_name = str(qb_customer_name).strip()
        if not qb_customer_name:
            raise ValueError("Customer name missing on credit memo record")

        customer = frappe.db.get_value("Customer", {"customer_name": qb_customer_name}, "name")
        if customer:
            return customer

        result = frappe.db.sql(
            "select name from `tabCustomer` where lower(customer_name)=lower(%s) limit 1",
            qb_customer_name,
        )
        if result:
            return result[0][0]

        new_customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": qb_customer_name,
            "customer_type": "Individual",
            "customer_group": self.get_or_create_customer_group(),
            "territory": "All Territories",
        })
        ensure_customer_currency(qb_customer_name, new_customer)
        new_customer.flags.ignore_permissions = True
        new_customer.insert()
        frappe.db.commit()
        return new_customer.name

    def resolve_project(self, project_name):
        if not project_name:
            return None

        project = frappe.db.get_value("Project", {"project_name": project_name}, "name")
        if project:
            return project

        result = frappe.db.sql(
            "select name from `tabProject` where lower(project_name)=lower(%s) limit 1",
            project_name,
        )
        if result:
            return result[0][0]

        return None

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

    def _get_or_create_tax_account(self, tax_item):
        """Get or create a liability account for the given tax item."""
        company = frappe.defaults.get_global_default("company")
        account_name = (tax_item or "Sales Tax").strip()
        existing = frappe.db.get_value(
            "Account",
            {"account_name": account_name, "company": company, "is_group": 0},
            "name",
        )
        if existing:
            return existing

        parent_account = frappe.db.get_value(
            "Account",
            {"account_type": "Payable", "is_group": 1, "company": company},
            "name",
        )
        if not parent_account:
            parent_account = frappe.db.get_value(
                "Account",
                {"root_type": "Liability", "is_group": 1, "company": company},
                "name",
            )
        if not parent_account:
            parent_account = frappe.db.get_value(
                "Account",
                {"root_type": "Liability", "is_group": 1, "company": company},
                "name",
            )
            if not parent_account:
                raise ValueError("No Liability group account found to create tax account")

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
        if not item_code or uom_name not in ("Unit", "Piece"):
            return
        try:
            item = frappe.get_doc("Item", item_code)
            if item.stock_uom in ("Unit", "Piece"):
                return
            if item.stock_uom != "Unit":
                item.stock_uom = "Unit"
                item.flags.ignore_permissions = True
                item.save()
                frappe.db.commit()
        except frappe.DoesNotExistError:
            pass
        except Exception as e:
            print(f"  WARN: Could not update item {item_code} for UOM support: {e}")

    def _get_line_item_tax_template(self, record, line):
        line_tax_code = str(line.get("tax_code") or "").strip()
        if line_tax_code.lower() != "tax":
            return None

        parent_tax_item = str(record.get("tax_item") or "").strip()
        if not parent_tax_item:
            return None

        return self._resolve_item_tax_template(parent_tax_item)

    def resolve_return_against(self, original_invoice_no):
        if not original_invoice_no:
            return None

        invoice_name = frappe.db.get_value("Sales Invoice", {"name": original_invoice_no}, "name")
        if invoice_name:
            return invoice_name

        result = frappe.db.sql(
            "select name from `tabSales Invoice` where lower(ifnull(invoice_number, '')) = lower(%s) limit 1",
            original_invoice_no,
        )
        if result:
            return result[0][0]

        result = frappe.db.sql(
            "select name from `tabSales Invoice` where lower(ifnull(customer_reference, '')) = lower(%s) limit 1",
            original_invoice_no,
        )
        return result[0][0] if result else None

    def run(self, dry_run: bool = False):
        records = self.load_data()
        total = len(records)
        success = failed = skipped = 0

        print(f"\n[{self.source_type}] Starting: {total} records")

        for i, record in enumerate(records):
            source_id = self.get_source_id(record)
            if not source_id:
                failed += 1
                print(f"  FAIL: missing source id for record {i+1}")
                continue

            if self.is_imported(source_id):
                skipped += 1
                continue

            try:
                doc_data = self.map_record(record)
                if doc_data is None:
                    skipped += 1
                    continue

                if isinstance(doc_data, dict) and doc_data.get("_skip"):
                    skipped += 1
                    reason = doc_data.get("_skip_reason", "SKIPPED")
                    ref_no = doc_data.get("ref_no") or doc_data.get("reference_no") or record.get("ref_no") or record.get("ref_number")
                    print(f"  SKIP [{source_id}] ref_no={ref_no or 'N/A'} reason={reason}")
                    self.log_skip(source_id, reason, ref_no)
                    continue

                if dry_run:
                    print(f"  DRY RUN: {source_id} → {doc_data.get('name', doc_data.get('item_code', '?'))}")
                    success += 1
                    continue

                existing_target = self.find_existing_target(doc_data)
                if existing_target:
                    print(f"  SUCCESS: {source_id} → {existing_target}")
                    self.log_success(source_id, existing_target, doc_data.get("doctype", self.target_doctype))
                    success += 1
                    continue

                doc = frappe.get_doc(doc_data)
                doc.flags.ignore_permissions = True
                doc.flags.ignore_mandatory = False
                doc.insert()

                if self.target_doctype in (
                    "Purchase Invoice",
                    "Sales Invoice",
                    "Payment Entry",
                    "Journal Entry",
                ):
                    doc.submit()

                if hasattr(self, "post_insert"):
                    self.post_insert(doc, record)

                frappe.db.commit()
                self.log_success(source_id, doc.name, getattr(doc, "doctype", self.target_doctype))
                success += 1

            except Exception as exc:
                frappe.db.rollback()
                self.log_failure(source_id, traceback.format_exc())
                failed += 1
                print(f"  FAIL [{source_id}]: {exc}")

            if (i + 1) % self.batch_size == 0:
                frappe.db.commit()
                print(f"  Progress: {i + 1}/{total}")

        frappe.db.commit()
        print(f"[{self.source_type}] Done — Success: {success}, Failed: {failed}, Skipped: {skipped}")
        return {"success": success, "failed": failed, "skipped": skipped}

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        customer = self.resolve_customer(record.get("cust_name"))

        items = []
        item_idx = 0
        skipped_taxable_line = False
        remaining_taxable_line = False
        imported_subtotal = 0.0

        for idx, line in enumerate(record.get("lines", []), 1):
            item_name = line.get("item") or line.get("item_list_id") or ""
            if not item_name and not line.get("description"):
                continue

            item_code = self.resolve_item(item_name) if item_name else None

            # Determine qty value carefully. We only skip lines explicitly
            # when the source provided a numeric qty that equals 0.0.
            qty_raw = line.get("qty")
            qty_value = None
            if qty_raw is not None and qty_raw != "":
                try:
                    qty_value = float(qty_raw)
                except (TypeError, ValueError):
                    qty_value = None

            # Rule: if the line has an explicit qty and it's 0.0, skip this line.
            if qty_value is not None and qty_value == 0.0:
                if (line.get("tax_code") or "").strip().lower() == "tax":
                    skipped_taxable_line = True
                continue

            # Fallback behaviour: if qty was provided and parsed, use it,
            # otherwise default to 0 (preserve previous behaviour for missing/invalid qty).
            if qty_value is None:
                qty_signed = 0
            else:
                qty_signed = -abs(qty_value) if qty_value else 0

            rate_value = 0
            try:
                rate_input = line.get("price")
                if rate_input not in (None, ""):
                    rate_value = abs(float(rate_input))
            except (TypeError, ValueError):
                rate_value = 0

            # compute extended amount if provided
            try:
                amount_value = abs(float(line.get("ext_price") or 0))
            except (TypeError, ValueError):
                amount_value = 0

            imported_subtotal += amount_value

            item_idx += 1
            item_row = {
                "idx": item_idx,
                "item_code": item_code,
                "item_name": line.get("description") or item_name or "",
                "qty": qty_signed,
                "rate": rate_value,
                "amount": amount_value,
                "description": line.get("description") or "",
                "income_account": self.resolve_income_account(),
            }

            line_tax_code = str(line.get("tax_code") or "").strip()
            if line_tax_code.lower() == "tax":
                remaining_taxable_line = True
                tax_template = self._get_line_item_tax_template(record, line)
                if tax_template:
                    item_row["item_tax_template"] = tax_template

            items.append(item_row)

        if not items:
            # If all lines were skipped due to qty == 0.0 (or no valid lines),
            # don't treat this as a failure. Return a dict that signals the
            # runner to skip this transaction.
            ref_no = record.get("ref_no") or record.get("ref_number") or record.get("txn_id") or ""
            return {"_skip": True, "_skip_reason": "All line items had qty 0.0 or no valid item lines", "ref_no": ref_no}

        project = self.resolve_project(record.get("project_name"))

        doc = {
            "doctype": "Sales Invoice",
            "name": str(record.get("txn_id") or ""),
            "customer": customer,
            "posting_date": self.normalize_date(record.get("date")),
            "due_date": self.normalize_date(record.get("date")),
            "company": company,
            "update_stock": 1,
            "customer_reference": record.get("ref_no") or record.get("txn_id") or "",
            "remarks": record.get("memo") or f"Imported from QuickBooks txn_id {record.get('txn_id')}",
            "items": items,
            "is_return": 1,
            "is_pos": 0,
        }

        if project:
            doc["project"] = project

        # If the source provided a taxes-and-charges template name, prefer it
        if record.get("tax_item"):
            template = self.resolve_taxes_template(record.get("tax_item"))
            if template:
                doc["taxes_and_charges"] = template

        has_item_level_tax = any(item.get("item_tax_template") for item in items)

        # ---- Tax handling (similar to invoices) ----
        sales_tax_total = abs(float(record.get("sales_tax_total") or 0))
        # If all taxable QuickBooks lines were skipped, clear invoice-level tax totals
        # and make the grand total equal the subtotal of imported items.
        skip_invoice_tax = skipped_taxable_line and not remaining_taxable_line

        if sales_tax_total > 0 and not has_item_level_tax and not skip_invoice_tax:
            tax_account = self._get_or_create_tax_account(record.get("tax_item"))
            tax_row = {
                "charge_type": "Actual",
                "account_head": tax_account,
                "rate": abs(float(record.get("sales_tax_pct") or 0)),
                "tax_amount": sales_tax_total,
                "included_in_print_rate": 0,
                "description": f"Sales Tax ({record.get('tax_item') or 'Default'})",
            }
            doc.setdefault("taxes", []).append(tax_row)

        if not has_item_level_tax and not skip_invoice_tax:
            tax_category = self._resolve_tax_category(record.get("tax_code"))
            if tax_category:
                doc["tax_category"] = tax_category

        # Set totals similar to invoices so ERPNext picks the right values on import
        doc["total"] = imported_subtotal
        doc["total_taxes_and_charges"] = 0 if skip_invoice_tax else sales_tax_total
        doc["grand_total"] = imported_subtotal if skip_invoice_tax else abs(float(record.get("total_amt") or 0))
        doc["base_total"] = imported_subtotal
        doc["base_total_taxes_and_charges"] = 0 if skip_invoice_tax else sales_tax_total
        doc["base_grand_total"] = imported_subtotal if skip_invoice_tax else abs(float(record.get("total_amt") or 0))

        return_against = self.resolve_return_against(
            record.get("return_against")
            or record.get("original_invoice_no")
            or record.get("orig_invoice_no")
            or record.get("orig_ref_no")
        )
        if return_against:
            doc["return_against"] = return_against

        return doc
