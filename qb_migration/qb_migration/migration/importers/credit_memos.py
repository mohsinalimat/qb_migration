import traceback

import frappe

from .invoices import SalesInvoiceImporter


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
        for idx, line in enumerate(record.get("lines", []), 1):
            item_name = line.get("item") or line.get("item_list_id") or ""
            if not item_name and not line.get("description"):
                continue

            item_code = self.resolve_item(item_name) if item_name else None

            qty = line.get("qty") or 0
            try:
                qty_value = float(qty)
                qty_signed = -abs(qty_value) if qty_value else 0
            except (TypeError, ValueError):
                qty_signed = 0

            rate_value = 0
            try:
                rate_input = line.get("price")
                if rate_input not in (None, ""):
                    rate_value = abs(float(rate_input))
            except (TypeError, ValueError):
                rate_value = 0

            item_row = {
                "idx": idx,
                "item_code": item_code,
                "item_name": line.get("description") or item_name or "",
                "qty": qty_signed,
                "rate": rate_value,
                "description": line.get("description") or "",
                "income_account": self.resolve_income_account(),
            }

            tax_template = self._resolve_item_tax_template(line.get("tax_code"))
            if tax_template:
                item_row["item_tax_template"] = tax_template

            items.append(item_row)

        if not items:
            raise ValueError("No valid item lines found for credit memo")

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
            "is_return": 1,
            "is_pos": 0,
        }

        if record.get("tax_item"):
            template = self.resolve_taxes_template(record.get("tax_item"))
            if template:
                doc["taxes_and_charges"] = template

        tax_category = self._resolve_tax_category(record.get("tax_code"))
        if tax_category:
            doc["tax_category"] = tax_category

        return doc
