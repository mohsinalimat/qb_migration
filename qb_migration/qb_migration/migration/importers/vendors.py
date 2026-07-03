import frappe

from ..base_importer import BaseImporter


class SupplierImporter(BaseImporter):
    source_type = "QB_VENDOR"
    target_doctype = "Supplier"
    json_file = "vendors.json"
    json_key = "vendors"

    def _split_contact_name(self, full_name):
        if not full_name:
            return "", ""

        parts = full_name.strip().split()
        if len(parts) == 1:
            return parts[0], ""

        return parts[0], " ".join(parts[1:])

    def _get_default_city(self, state=None, country=None):
        state = (state or "").strip().upper()
        country = (country or "").strip().lower()

        state_cities = {
            "CA": [
                "San Francisco",
                "Palo Alto",
                "Oakland",
                "San Jose",
                "Burlingame",
                "Sunnyvale",
                "Mountain View",
                "San Mateo",
            ],
            "NY": ["New York", "Buffalo", "Rochester", "Albany"],
        }

        if state in state_cities:
            return state_cities[state][0]

        if country in ("pakistan", "pk", "pak"):
            return "Karachi"

        return "Unknown"

    def _should_create_contact(self, contact_name):
        if not contact_name:
            return False
        contact_text = str(contact_name).strip().upper()
        return contact_text not in ("", "N/A", "NONE")

    def map_record(self, record):
        doc = {
            "doctype": "Supplier",
            **({"name": record.get("list_id")} if record.get("list_id") else {}),
            "supplier_name": record.get("name"),
            "supplier_type": "Company" if record.get("company") else "Individual",
            "supplier_group": "All Supplier Groups",
        }

        if record.get("currency"):
            doc["default_currency"] = record.get("currency")

        if record.get("terms"):
            doc["payment_terms_template"] = record.get("terms")

        if record.get("email"):
            doc["email_id"] = record.get("email")

        if record.get("phone"):
            doc["phone_no"] = record.get("phone")

        if record.get("fax"):
            doc["fax_no"] = record.get("fax")

        if record.get("notes"):
            doc["supplier_details"] = record.get("notes")

        if record.get("account_no"):
            doc["account_number"] = record.get("account_no")

        active_flag = record.get("active")
        if active_flag is not None:
            doc["disabled"] = 0 if bool(active_flag) else 1

        address = {
            "address_line1": record.get("addr1", ""),
            "address_line2": record.get("addr2", ""),
            "city": record.get("city", ""),
            "state": record.get("state", ""),
            "pincode": record.get("zip", ""),
            "country": record.get("country", ""),
            "phone": record.get("phone", ""),
            "is_primary_address": True,
        }
        record["_address"] = address

        contact_name = record.get("contact")
        if self._should_create_contact(contact_name):
            first_name, last_name = self._split_contact_name(contact_name)
            contact = {
                "first_name": first_name,
                "last_name": last_name,
                "email_id": record.get("email", ""),
                "phone": record.get("phone", ""),
                "fax": record.get("fax", ""),
                "company_name": record.get("name"),
                "status": "Open" if active_flag is not False else "Passive",
            }
            record["_contact"] = contact

        return doc

    def find_existing_target(self, doc_data):
        supplier_name = doc_data.get("name") or doc_data.get("supplier_name")
        if not supplier_name:
            return None

        existing = frappe.db.get_value("Supplier", {"name": supplier_name}, "name")
        if existing:
            return existing

        if doc_data.get("supplier_name"):
            existing = frappe.db.get_value("Supplier", {"supplier_name": doc_data.get("supplier_name")}, "name")
            return existing

        return None

    def post_insert(self, doc, source_record):
        address = source_record.get("_address")
        if address and any(
            address.get(field)
            for field in (
                "address_line1",
                "address_line2",
                "city",
                "state",
                "pincode",
                "country",
                "phone",
            )
        ):
            city = address.get("city") or self._get_default_city(
                address.get("state"), address.get("country")
            )

            address_doc = {
                "doctype": "Address",
                "address_title": doc.supplier_name or doc.name,
                "address_type": "Billing",
                "links": [{"link_doctype": "Supplier", "link_name": doc.name}],
            }

            for field, value in [
                ("address_line1", address.get("address_line1")),
                ("address_line2", address.get("address_line2")),
                ("city", city),
                ("state", address.get("state")),
                ("pincode", address.get("pincode")),
                ("country", address.get("country") or "United States"),
                ("phone", address.get("phone")),
            ]:
                if value not in (None, ""):
                    address_doc[field] = value

            frappe.get_doc(address_doc).insert(ignore_permissions=True)

        contact = source_record.get("_contact")
        if contact and contact.get("first_name"):
            contact_doc = {
                "doctype": "Contact",
                "first_name": contact["first_name"],
                "company_name": contact["company_name"],
                "status": contact["status"],
                "links": [{"link_doctype": "Supplier", "link_name": doc.name}],
            }
            if contact.get("last_name"):
                contact_doc["last_name"] = contact["last_name"]
            if contact.get("email_id"):
                contact_doc["email_ids"] = [{"email_id": contact["email_id"], "is_primary": 1}]
            if contact.get("phone"):
                contact_doc["phone_nos"] = [{"phone": contact["phone"], "is_primary_phone": 1}]
            if contact.get("fax"):
                contact_doc["fax"] = contact["fax"]

            frappe.get_doc(contact_doc).insert(ignore_permissions=True)
