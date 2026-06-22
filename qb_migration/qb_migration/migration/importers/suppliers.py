import frappe

from ..base_importer import BaseImporter


class SupplierImporter(BaseImporter):
    source_type = "QB_VENDOR"
    target_doctype = "Supplier"
    json_file = "vendors.json"
    json_key = "vendors"

    def map_record(self, record):
        doc = {
            "doctype": "Supplier",
            "supplier_name": record["name"],
            "supplier_type": "Company" if record.get("is_company") else "Individual",
            "supplier_group": "All Supplier Groups",
        }

        if record.get("currency"):
            doc["default_currency"] = record["currency"]

        if record.get("address"):
            doc["_address"] = record["address"]

        return doc

    def post_insert(self, doc, source_record):
        address = source_record.get("_address")
        if not address:
            return

        frappe.get_doc({
            "doctype": "Address",
            "address_type": "Billing",
            "address_line1": address.get("line1", ""),
            "city": address.get("city", ""),
            "state": address.get("state", ""),
            "pincode": address.get("zip", ""),
            "country": address.get("country", "United States"),
            "links": [{"link_doctype": "Supplier", "link_name": doc.name}],
        }).insert(ignore_permissions=True)
