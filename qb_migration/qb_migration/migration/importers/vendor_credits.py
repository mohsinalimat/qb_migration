import frappe

from .bills import PurchaseInvoiceImporter


class VendorCreditImporter(PurchaseInvoiceImporter):
    source_type = "QB_VENDOR_CREDIT"
    target_doctype = "Purchase Invoice"
    json_file = "vendor_credits.json"
    json_key = "vendor_credits"

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("ref_no") or "")

    def resolve_return_against(self, bill_ref):
        if not bill_ref:
            return None

        invoice_name = frappe.db.get_value("Purchase Invoice", {"bill_no": bill_ref}, "name")
        if invoice_name:
            return invoice_name

        result = frappe.db.sql(
            "select name from `tabPurchase Invoice` where lower(bill_no)=lower(%s) limit 1",
            bill_ref,
        )
        return result[0][0] if result else None

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        supplier_name = record.get("vend_name")
        supplier = self.resolve_supplier(supplier_name)
        currency = record.get("currency") or "PKR"

        items = []
        for line in record.get("lines", []):
            raw_qty = line.get("qty") or 0
            qty = float(raw_qty) if raw_qty not in (None, "") else 0
            qty_signed = -abs(qty) if qty else -1

            raw_amount = line.get("amount") or 0
            amount = float(raw_amount) if raw_amount not in (None, "") else 0
            amount_signed = -abs(amount) if amount else 0

            if qty_signed and amount_signed:
                rate = abs(amount_signed) / abs(qty_signed)
            else:
                rate = 0

            items.append({
                "item_code": self.resolve_item(line.get("item", "")),
                "qty": qty_signed,
                "rate": rate,
                "amount": amount_signed,
                "expense_account": self._resolve_account(line.get("gl_code")),
                "description": line.get("description", ""),
            })

        doc = {
            "doctype": "Purchase Invoice",
            "supplier": supplier,
            "posting_date": self.normalize_date(record.get("date")),
            "bill_no": record.get("txn_id") or record.get("ref_no", ""),
            "bill_date": self.normalize_date(record.get("date")),
            "company": company,
            "currency": currency,
            "debit_to": self.resolve_payable_account(supplier, currency),
            "remarks": record.get("memo") or "",
            "is_return": 1,
            "items": items,
        }

        if frappe.get_meta("Purchase Invoice").has_field("supplier_invoice_no"):
            doc["supplier_invoice_no"] = record.get("ref_no", "")

        return_against = self.resolve_return_against(record.get("bill_ref"))
        if return_against:
            doc["return_against"] = return_against

        return doc
