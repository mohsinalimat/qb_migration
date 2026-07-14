import traceback

import frappe

from .invoices import SalesInvoiceImporter
from .customer_currency import ensure_customer_currency


class SalesReceiptImporter(SalesInvoiceImporter):
    source_type = "QB_SALES_RECEIPT"
    target_doctype = "Sales Invoice"
    json_file = "sales_receipts.json"
    json_key = "sales_receipts"

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("ref_no") or "")

    def resolve_customer(self, qb_customer_name):
        if not qb_customer_name:
            raise ValueError("Customer name missing on sales receipt record")

        qb_customer_name = str(qb_customer_name).strip()
        if not qb_customer_name:
            raise ValueError("Customer name missing on sales receipt record")

        # Prefer the exact QuickBooks customer name when it exists.
        customer = frappe.db.get_value("Customer", {"customer_name": qb_customer_name}, "name")
        if customer:
            return customer

        result = frappe.db.sql(
            "select name from `tabCustomer` where lower(customer_name)=lower(%s) limit 1",
            qb_customer_name,
        )
        if result:
            return result[0][0]
            
        normalized_name = qb_customer_name.split(":")[0].strip()
        customer = frappe.db.get_value("Customer", {"customer_name": normalized_name}, "name")
        if customer:
            return customer

        result = frappe.db.sql(
            "select name from `tabCustomer` where lower(customer_name)=lower(%s) limit 1",
            normalized_name,
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

    def _ensure_uom(self, uom_name):
        if not uom_name:
            uom_name = "Nos"

        existing = frappe.db.get_value("UOM", {"uom_name": uom_name}, ["name", "must_be_whole_number"])
        if existing:
            name, must_be_whole = existing
            if must_be_whole:
                uom_doc = frappe.get_doc("UOM", name)
                uom_doc.must_be_whole_number = 0
                uom_doc.flags.ignore_permissions = True
                uom_doc.save()
                frappe.db.commit()
            return name

        uom = frappe.get_doc({
            "doctype": "UOM",
            "uom_name": uom_name,
            "must_be_whole_number": 0,
            "enabled": 1,
        })
        uom.flags.ignore_permissions = True
        uom.insert()
        frappe.db.commit()
        return uom.name

    def resolve_uom(self, qty, fallback_uom="Nos"):
        try:
            qty_value = float(qty)
        except (TypeError, ValueError):
            return self._ensure_uom(fallback_uom or "Nos")

        if qty_value.is_integer():
            return self._ensure_uom(fallback_uom or "Nos")

        return self._ensure_uom("Unit")

    def _resolve_mode_of_payment(self, mode, account=None):
        mode_name = (mode or "Cash").strip() or "Cash"
        existing = frappe.db.get_value("Mode of Payment", {"mode_of_payment": mode_name}, "name")
        if existing:
            if account:
                mop_doc = frappe.get_doc("Mode of Payment", existing)
                has_account = any(row.default_account for row in mop_doc.accounts)
                if not has_account:
                    mop_doc.append("accounts", {
                        "company": frappe.defaults.get_global_default("company"),
                        "default_account": account,
                    })
                    mop_doc.flags.ignore_permissions = True
                    mop_doc.save()
                    frappe.db.commit()
            return existing

        existing = frappe.db.sql(
            "select name from `tabMode of Payment` where enabled=1 limit 1",
            as_dict=False,
        )
        if existing:
            return existing[0][0]

        account = account or self._resolve_cash_bank_account()
        mop_type = "Cash"
        if mode_name.lower() in ("check", "bank", "credit card", "cc", "debit card"):
            mop_type = "Bank"
        elif mode_name.lower() in ("phone", "online"):
            mop_type = "Phone"

        mop_doc = frappe.get_doc({
            "doctype": "Mode of Payment",
            "mode_of_payment": mode_name,
            "type": mop_type,
            "enabled": 1,
            "accounts": [{
                "company": frappe.defaults.get_global_default("company"),
                "default_account": account,
            }] if account else [],
        })
        mop_doc.flags.ignore_permissions = True
        mop_doc.insert()
        frappe.db.commit()
        return mop_doc.name

    def _resolve_receivable_account(self, company=None):
        company = company or frappe.defaults.get_global_default("company")
        company_currency = frappe.db.get_value("Company", company, "default_currency")

        filters = {
            "company": company,
            "account_type": "Receivable",
            "root_type": "Asset",
            "is_group": 0,
        }
        if company_currency:
            filters["account_currency"] = company_currency

        account = frappe.db.get_value("Account", filters, ["name", "account_currency"])
        if account:
            name, account_currency = account
            if company_currency and account_currency != company_currency:
                frappe.db.set_value("Account", name, "account_currency", company_currency)
                frappe.db.commit()
            return name

        fallback = frappe.db.get_value(
            "Account",
            {"company": company, "account_type": "Receivable", "root_type": "Asset", "is_group": 0},
            ["name", "account_currency"],
        )
        if fallback:
            name, account_currency = fallback
            if company_currency and account_currency != company_currency:
                frappe.db.set_value("Account", name, "account_currency", company_currency)
                frappe.db.commit()
            return name

        return None

    def _resolve_cash_bank_account(self, qb_account_name=None):
        company = frappe.defaults.get_global_default("company")
        if qb_account_name:
            leaf = qb_account_name.split(":")[-1].strip()
            account = frappe.db.get_value(
                "Account",
                {"account_name": leaf, "company": company, "is_group": 0},
                "name",
            )
            if account:
                return account

        row = frappe.db.sql(
            "select name from `tabAccount` where account_type='Bank' and root_type='Asset' and company=%s and is_group=0 limit 1",
            (company,),
        )
        if row:
            return row[0][0]

        row = frappe.db.sql(
            "select name from `tabAccount` where account_type='Cash' and root_type='Asset' and company=%s and is_group=0 limit 1",
            (company,),
        )
        return row[0][0] if row else None

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

    def post_insert(self, doc, source_record):
        return None

    def _build_payment_entry(self, sales_invoice, record):
        company = frappe.defaults.get_global_default("company")
        total_amt = frappe.utils.flt(record.get("total_amt") or 0, 2)
        if total_amt <= 0:
            raise ValueError("Sales receipt payment amount is invalid or zero")

        payment_method = (record.get("payment_method") or "").strip()
        paid_to_account = self._resolve_cash_bank_account(record.get("deposit_to_acct"))
        invoice_receivable_account = getattr(sales_invoice, "debit_to", None)
        receivable_account = invoice_receivable_account or self._resolve_receivable_account(company)

        if not receivable_account:
            raise ValueError("Could not resolve customer receivable account for payment entry")
        if not paid_to_account:
            raise ValueError("Could not resolve paid_to account for sales receipt payment")

        mode_of_payment = self._resolve_mode_of_payment(payment_method, account=paid_to_account)

        outstanding_amount = frappe.utils.flt(getattr(sales_invoice, "outstanding_amount", getattr(sales_invoice, "grand_total", 0)), 2)

        if total_amt > outstanding_amount:
            # If the invoice amount was reduced because zero-quantity lines were skipped,
            # use the actual outstanding balance instead of failing the import.
            total_amt = outstanding_amount

        if total_amt <= 0:
            raise ValueError("Sales receipt payment amount is invalid or zero")

        return {
            "doctype": "Payment Entry",
            "payment_type": "Receive",
            "company": company,
            "posting_date": self.normalize_date(record.get("date")),
            "mode_of_payment": mode_of_payment,
            "party_type": "Customer",
            "party": sales_invoice.customer,
            "party_account": receivable_account,
            "paid_from": receivable_account,
            "paid_to": paid_to_account,
            "paid_amount": total_amt,
            "received_amount": total_amt,
            "reference_no": record.get("ref_no") or str(record.get("txn_id") or ""),
            "reference_date": self.normalize_date(record.get("date")),
            "remarks": record.get("memo") or "",
            "references": [{
                "reference_doctype": "Sales Invoice",
                "reference_name": sales_invoice.name,
                "total_amount": frappe.utils.flt(getattr(sales_invoice, "grand_total", 0), 2),
                "allocated_amount": total_amt,
            }],
        }

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

    def run(self, dry_run: bool = False):
        records = self.load_data()
        total = len(records)
        success = failed = skipped = 0

        print(f"\n[{self.source_type}] Starting: {total} records")

        for i, record in enumerate(records):
            source_id = self.get_source_id(record)
            if not source_id:
                failed += 1
                self.append_detailed_log(
                    "Failed",
                    f"record_{i + 1}",
                    "Missing source id",
                    details={"record_index": i + 1},
                )
                print(f"  FAIL: missing source id for record {i+1}")
                continue

            if self.is_imported(source_id):
                skipped += 1
                print(f"  SKIP [{source_id}]: Already imported")
                self.append_detailed_log(
                    "Skipped",
                    source_id,
                    "Already imported",
                    details={"record_index": i + 1},
                )
                continue

            try:
                doc_data = self.map_record(record)
                if doc_data is None:
                    skipped += 1
                    print(f"  SKIP [{source_id}]: Mapper returned no document data")
                    self.append_detailed_log(
                        "Skipped",
                        source_id,
                        "Mapper returned no document data",
                        details={"record_index": i + 1},
                    )
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

                invoice = frappe.get_doc(doc_data)
                invoice.flags.ignore_permissions = True
                invoice.flags.ignore_mandatory = False
                if invoice.name and frappe.db.exists("Sales Invoice", invoice.name):
                    invoice = frappe.get_doc("Sales Invoice", invoice.name)
                    if invoice.docstatus == 0:
                        invoice.flags.ignore_permissions = True
                        invoice.submit()
                else:
                    invoice.insert()
                    invoice.submit()
                frappe.db.commit()

                # Reload invoice after submit to ensure auto-set fields like debit_to and outstanding_amount are available
                invoice.reload()

                payment_ref = record.get("ref_no") or str(record.get("txn_id") or "")
                payment_entry = None
                if payment_ref:
                    existing_payment = frappe.db.get_value(
                        "Payment Entry",
                        {"reference_no": payment_ref, "party": invoice.customer, "company": invoice.company},
                        "name",
                    )
                    if existing_payment:
                        payment_entry = frappe.get_doc("Payment Entry", existing_payment)
                        if payment_entry.docstatus == 0:
                            payment_entry.flags.ignore_permissions = True
                            payment_entry.submit()

                if not payment_entry:
                    payment_data = self._build_payment_entry(invoice, record)
                    payment_entry = frappe.get_doc(payment_data)
                    payment_entry.flags.ignore_permissions = True
                    payment_entry.flags.ignore_mandatory = False
                    payment_entry.insert()
                    payment_entry.submit()

                if hasattr(self, "post_insert"):
                    self.post_insert(invoice, record)

                frappe.db.commit()
                self.log_success(source_id, payment_entry.name, payment_entry.doctype)
                print(f"  SUCCESS: {source_id} → Invoice {invoice.name}, Payment Entry {payment_entry.name}")
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

    def _get_line_item_tax_template(self, record, line):
        line_tax_code = str(line.get("tax_code") or "").strip()
        if line_tax_code.lower() != "tax":
            return None

        parent_tax_item = str(record.get("tax_item") or "").strip()
        if not parent_tax_item:
            return None

        return self._resolve_item_tax_template(parent_tax_item)

    def _is_zero_qty(self, qty):
        if qty is None or qty == "":
            return False
        try:
            return float(qty) == 0
        except (TypeError, ValueError):
            return False

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        customer = self.resolve_customer(record.get("cust_name"))

        items = []
        item_idx = 0
        skipped_taxable_line = False
        remaining_taxable_line = False
        imported_subtotal = 0.0

        for line in record.get("lines", []):
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
                "_skip_reason": "All line items have qty 0.0",
                "ref_no": record.get("ref_no") or record.get("txn_id") or "",
            }

        total_amt = abs(float(record.get("total_amt") or 0))

        project = self.resolve_project(record.get("project_name"))

        # If all taxable QuickBooks lines were skipped, clear invoice-level tax totals
        # and make the grand total equal the subtotal of imported items.
        skip_invoice_tax = skipped_taxable_line and not remaining_taxable_line
        doc = {
            "doctype": "Sales Invoice",
            "name": str(record.get("txn_id") or ""),
            "customer": customer,
            "posting_date": self.normalize_date(record.get("date")),
            "due_date": self.normalize_date(record.get("date")),
            "company": company,
            "customer_reference": record.get("ref_no") or record.get("txn_id") or "",
            "remarks": record.get("memo") or f"Imported from QuickBooks txn_id {record.get('txn_id')}",
            "items": items,
            "update_stock": 1,
            "set_posting_time": 1,
            "total": imported_subtotal,
            "total_taxes_and_charges": 0 if skip_invoice_tax else abs(float(record.get("sales_tax_total") or 0)),
            "grand_total": imported_subtotal if skip_invoice_tax else total_amt,
            "base_total": imported_subtotal,
            "base_total_taxes_and_charges": 0 if skip_invoice_tax else abs(float(record.get("sales_tax_total") or 0)),
            "base_grand_total": imported_subtotal if skip_invoice_tax else total_amt,
        }

        if project:
            doc["project"] = project

        # ---- Tax handling ----
        sales_tax_total = abs(float(record.get("sales_tax_total") or 0))
        has_item_level_tax = any(item.get("item_tax_template") for item in items)

        if sales_tax_total > 0 and not has_item_level_tax and not skip_invoice_tax:
            tax_account = self._get_or_create_tax_account(record.get("tax_item"))
            tax_row = {
                "charge_type": "Actual",
                "account_head": tax_account,
                "rate": abs(float(record.get("sales_tax_pct") or 0)),
                "tax_amount": sales_tax_total,
                "included_in_print_rate": 0,
                "description": f"Sales Tax ({record.get('tax_item') or 'Default'})"
            }
            doc.setdefault("taxes", []).append(tax_row)

        # Optionally store the tax rate for informational purposes
        if record.get("sales_tax_pct") not in (None, ""):
            doc["taxes_and_charges_rate"] = abs(float(record.get("sales_tax_pct") or 0))

        if not has_item_level_tax and not skip_invoice_tax:
            tax_category = self._resolve_tax_category(record.get("tax_code"))
            if tax_category:
                doc["tax_category"] = tax_category

        return doc
