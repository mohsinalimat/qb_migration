import frappe

from ..base_importer import BaseImporter


class SalesTaxCodesImporter(BaseImporter):
    source_type = "QB_SALES_TAX_CODE"
    target_doctype = "Tax Category"
    json_file = "sales_tax_codes.json"
    json_key = "sales_tax_codes"

    def get_source_id(self, record):
        return str(record.get("list_id") or record.get("name") or "")

    def find_existing_target(self, doc_data):
        title = doc_data.get("title")
        if not title:
            return None

        return (
            frappe.db.exists("Tax Category", {"name": title})
            or frappe.db.exists("Tax Category", {"title": title})
        )

    def map_record(self, record):
        title = str(record.get("name") or "").strip()
        if not title:
            return {
                "_skip": True,
                "_skip_reason": "MISSING_NAME",
                "ref_no": record.get("list_id", ""),
            }

        description = str(record.get("description") or "").strip()
        active = bool(record.get("active", True))

        doc_data = {
            "doctype": "Tax Category",
            "title": title,
            "disabled": 0 if active else 1,
        }

        # The QuickBooks payload carries taxable/non-taxable intent, but the
        # ERPNext Tax Category doctype does not expose a direct field for it in
        # all setups. We persist the safe, stable fields here.
        meta = frappe.get_meta("Tax Category")
        if meta.has_field("description") and description:
            doc_data["description"] = description

        return doc_data
