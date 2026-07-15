import re
import uuid

import frappe

from .bills import PurchaseInvoiceImporter


class CCChargesImporter(PurchaseInvoiceImporter):
    """Map QuickBooks credit-card charges into ERPNext Purchase Invoices."""

    source_type = "QB_CC_CHARGE"
    target_doctype = "Purchase Invoice"
    json_file = "cc_charges.json"
    json_key = "cc_charges"

    def get_source_id(self, record):
        source_id = str(record.get("txn_id") or record.get("ref_no") or "").strip()
        if source_id:
            return source_id

        return f"QB_CC_CHARGE-{uuid.uuid4().hex}"

    def find_existing_target(self, doc_data):
        # CC charges should not reuse existing Purchase Invoices that were created
        # from bills or other source types. The migration log already guards reruns
        # by source_id, so duplicate detection stays source-specific and safe.
        return None

    def _extract_project_name(self, line):
        for key in ("customer", "customer_name", "project", "project_name"):
            value = line.get(key)
            if value:
                text = str(value).strip()
                if text:
                    return text

        raw_xml = line.get("raw_xml") or ""
        if not raw_xml:
            return None

        try:
            match = re.search(r"<FullName>(.*?)</FullName>", raw_xml)
            if match:
                full_name = match.group(1).strip()
                if ":" in full_name:
                    return full_name.split(":")[-1].strip()
                return full_name
        except Exception:
            return None

        return None

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

        cost_center = self._resolve_cost_center(line.get("class_name") or line.get("class"))
        if cost_center:
            item_data["cost_center"] = cost_center

        project_name = self._extract_project_name(line)
        if project_name:
            project = self._resolve_project(project_name)
            if project:
                item_data["project"] = project

        return item_data

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        supplier_name = record.get("payee") or record.get("vendor") or record.get("vend_name")
        supplier = self.resolve_supplier(supplier_name)
        currency = record.get("currency") or "PKR"

        items = []
        taxes = []
        lines = [line for line in record.get("lines", []) if isinstance(line, dict)]

        if lines and all("qty" in line and self._qty_is_zero(line.get("qty")) for line in lines):
            return {
                "_skip": True,
                "_skip_reason": "Skipped purchase invoice because all line items have qty 0.0",
                "ref_no": record.get("ref_no", "") or record.get("txn_id", ""),
            }

        for line in lines:
            line_type = str(line.get("line_type") or "").strip().lower()
            if line_type == "expense":
                tax_row = self._build_tax_row(line)
                if tax_row:
                    taxes.append(tax_row)
                continue

            if self._qty_is_zero(line.get("qty")):
                continue

            item_data = self._build_item_row(line)
            if item_data:
                items.append(item_data)

        if not items:
            for line in lines:
                items.append(self._build_fallback_item_row(line))
                break

        ref_no = str(record.get("ref_no") or "").strip()
        txn_id = str(record.get("txn_id") or "").strip()

        doc = {
            "doctype": "Purchase Invoice",
            "supplier": supplier,
            "posting_date": self.normalize_date(record.get("date") or record.get("txn_date")),
            "set_posting_time": 1,
            "due_date": self.normalize_date(record.get("due_date") or record.get("date") or record.get("txn_date")),
            "bill_no": ref_no or txn_id,
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
