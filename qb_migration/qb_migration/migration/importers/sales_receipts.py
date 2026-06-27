import traceback

import frappe
from frappe.utils import flt

from .invoices import SalesInvoiceImporter


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
            "customer_name": normalized_name,
            "customer_type": "Individual",
            "customer_group": self.get_or_create_customer_group(),
            "territory": "All Territories",
        })
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

    def _resolve_mode_of_payment(self, mode):
        if mode:
            existing = frappe.db.get_value("Mode of Payment", {"mode_of_payment": mode}, "name")
            if existing:
                return existing

        existing = frappe.db.get_value("Mode of Payment", {}, "name")
        if existing:
            return existing

        mode_name = mode or "Bank"
        doc = frappe.get_doc({
            "doctype": "Mode of Payment",
            "mode_of_payment": mode_name,
        })
        doc.flags.ignore_permissions = True
        doc.insert()
        frappe.db.commit()
        return doc.name

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

    def post_insert(self, doc, source_record):
        return None

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

            items.append(item_row)

        if not items:
            raise ValueError("No valid item lines found for sales receipt")

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
            "is_pos": 1 if record.get("payment_method") else 0,
            "total": abs(float(record.get("subtotal") or 0)),
            "total_taxes_and_charges": abs(float(record.get("sales_tax_total") or 0)),
            "grand_total": abs(float(record.get("total_amt") or 0)),
            "base_total": abs(float(record.get("subtotal") or 0)),
            "base_total_taxes_and_charges": abs(float(record.get("sales_tax_total") or 0)),
            "base_grand_total": abs(float(record.get("total_amt") or 0)),
        }

        if record.get("payment_method"):
            mode_of_payment = self._resolve_mode_of_payment(record.get("payment_method"))
            doc["mode_of_payment"] = mode_of_payment

        account = self._resolve_cash_bank_account(record.get("deposit_to_acct"))
        if account:
            doc["cash_bank_account"] = account

        if record.get("payment_method") and account:
            doc["payments"] = [{
                "mode_of_payment": doc.get("mode_of_payment") or self._resolve_mode_of_payment(record.get("payment_method")),
                "amount": abs(float(record.get("total_amt") or 0)),
                "account": account,
            }]

        if record.get("tax_item"):
            template = self.resolve_taxes_template(record.get("tax_item"))
            if template:
                doc["taxes_and_charges"] = template

        tax_category = self._resolve_tax_category(record.get("tax_code"))
        if tax_category:
            doc["tax_category"] = tax_category

        if record.get("sales_tax_pct") not in (None, ""):
            doc["taxes_and_charges_rate"] = abs(float(record.get("sales_tax_pct") or 0))

        return doc
