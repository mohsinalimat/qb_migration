import frappe

from ..base_importer import BaseImporter


class OtherNamesImporter(BaseImporter):
    source_type = "QB_OTHER_NAME"
    target_doctype = "Contact"
    json_file = "other_names.json"
    json_key = "other_names"

    def get_source_id(self, record):
        return str(record.get("list_id") or record.get("name") or "")

    def find_existing_target(self, doc_data):
        first_name = (doc_data.get("first_name") or "").strip()
        company_name = (doc_data.get("company_name") or "").strip()
        if not first_name:
            return None
        return frappe.db.get_value(
            "Contact",
            {"first_name": first_name, "company_name": company_name},
            "name",
        )

    def _build_address_fields(self, record):
        return {
            "address_line1": str(record.get("addr1") or "").strip(),
            "address_line2": str(record.get("addr2") or "").strip(),
            "city": str(record.get("city") or "").strip(),
            "state": str(record.get("state") or "").strip(),
            "pincode": str(record.get("zip") or "").strip(),
            "fax": str(record.get("fax") or "").strip(),
        }

    def map_record(self, record):
        first_name = str(record.get("name") or "").strip()
        if not first_name:
            return {"_skip": True, "_skip_reason": "MISSING_NAME", "ref_no": record.get("list_id", "")}

        company_name = str(record.get("company") or "").strip()
        phone = str(record.get("phone") or "").strip()
        email_id = str(record.get("email") or "").strip()
        designation = str(record.get("notes") or "").strip()
        disabled = bool(record.get("active") is False)

        doc = {
            "doctype": "Contact",
            "first_name": first_name,
            "company_name": company_name,
            "designation": designation,
            "status": "Passive" if disabled else "Open",
            "email_ids": [],
            "phone_nos": [],
        }

        if email_id:
            doc["email_ids"].append({"email_id": email_id, "is_primary": 1})

        if phone:
            doc["phone_nos"].append({"phone": phone, "is_primary_phone": 1})

        return doc

    def post_insert(self, doc, source_record):
        address_fields = self._build_address_fields(source_record)
        if not any(address_fields.values()):
            return

        address_doc = frappe.get_doc({
            "doctype": "Address",
            "address_title": doc.first_name or doc.company_name or source_record.get("name") or "Contact",
            "address_type": "Office",
            "address_line1": address_fields["address_line1"],
            "address_line2": address_fields["address_line2"],
            "city": address_fields["city"] or "Unknown",
            "state": address_fields["state"],
            "pincode": address_fields["pincode"],
            "fax": address_fields["fax"],
            "links": [{
                "link_doctype": "Contact",
                "link_name": doc.name,
            }],
        })
        address_doc.flags.ignore_permissions = True
        address_doc.insert(ignore_permissions=True)
        frappe.db.commit()
