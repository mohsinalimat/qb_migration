import frappe

from ..base_importer import BaseImporter


class SalesTaxItemsImporter(BaseImporter):
    source_type = "QB_SALES_TAX_ITEM"
    target_doctype = "Item Tax Template"
    json_file = "sales_tax_items.json"
    json_key = "sales_tax_items"

    def get_source_id(self, record):
        return str(record.get("list_id") or record.get("name") or "")

    def find_existing_target(self, doc_data):
        title = doc_data.get("title")
        if not title:
            return None
        return frappe.db.exists("Item Tax Template", {"title": title})

    def _field_exists(self, doctype, fieldname):
        try:
            meta = frappe.get_meta(doctype)
        except Exception:
            return False
        return bool(meta.get_field(fieldname))

    def _resolve_account_head(self, record):
        company = frappe.defaults.get_global_default("company")
        candidates = []
        for value in [record.get("name"), record.get("description")]:
            if value:
                candidates.append(str(value).strip())

        for candidate in candidates:
            if not candidate:
                continue

            existing = frappe.db.get_value("Account", {"company": company, "account_name": candidate}, "name")
            if existing:
                return existing

            row = frappe.db.sql(
                "select name from `tabAccount` where company=%s and lower(account_name)=lower(%s) limit 1",
                (company, candidate),
            )
            if row:
                return row[0][0]

        return self._ensure_tax_account(company, candidates[0] or "Sales Tax")

    def _ensure_tax_account(self, company, account_name):
        if not account_name:
            account_name = "Sales Tax"

        existing = frappe.db.get_value("Account", {"company": company, "account_name": account_name}, "name")
        if existing:
            return existing

        parent_account = frappe.db.get_value(
            "Account",
            {"company": company, "account_name": "Current Liabilities"},
            "name",
        )
        if not parent_account:
            parent_account = frappe.db.get_value(
                "Account",
                {"company": company, "root_type": "Liability", "is_group": 1},
                "name",
            )

        doc = frappe.get_doc({
            "doctype": "Account",
            "account_name": account_name,
            "company": company,
            "account_type": "Tax",
            "parent_account": parent_account,
            "root_type": "Liability",
            "is_group": 0,
        })
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name

    def map_record(self, record):
        title = str(record.get("name") or "").strip()
        if not title:
            return {"_skip": True, "_skip_reason": "MISSING_NAME", "ref_no": record.get("list_id", "")}

        try:
            tax_rate = abs(float(record.get("tax_rate") or 0))
        except (TypeError, ValueError):
            tax_rate = 0.0

        account_head = self._resolve_account_head(record)
        description = str(record.get("description") or title).strip() or title
        company = frappe.defaults.get_global_default("company")

        doc_data = {
            "doctype": "Item Tax Template",
            "title": title,
            "company": company,
            "disabled": 0 if record.get("active") else 1,
            "taxes": [{
                "doctype": "Item Tax Template Detail",
                "tax_type": account_head,
                "tax_rate": tax_rate,
            }],
        }

        if self._field_exists("Item Tax Template", "description") and description:
            doc_data["description"] = description

        if self._field_exists("Item Tax Template", "quickbooks_id"):
            doc_data["quickbooks_id"] = str(record.get("list_id") or "")

        return doc_data
