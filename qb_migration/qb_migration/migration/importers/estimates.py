import frappe

from ..base_importer import BaseImporter


class EstimateImporter(BaseImporter):
    source_type = "QB_ESTIMATE"
    target_doctype = "Quotation"
    json_file = "estimates.json"
    json_key = "estimates"

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("ref_no") or "")

    def get_or_create_customer_group(self):
        root = frappe.db.get_value(
            "Customer Group",
            {"is_group": 1, "parent_customer_group": ["is", "not set"]},
            "name",
        )
        if not root:
            root = frappe.db.get_value(
                "Customer Group",
                {"is_group": 1, "parent_customer_group": ""},
                "name",
            )
        if not root:
            root = "All Customer Groups"

        existing = frappe.db.get_value(
            "Customer Group",
            {"customer_group_name": "QuickBooks Customers", "parent_customer_group": root},
            "name",
        )
        if existing:
            return existing

        group = frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": "QuickBooks Customers",
            "parent_customer_group": root,
            "is_group": 0,
        })
        group.flags.ignore_permissions = True
        group.insert()
        frappe.db.commit()
        return group.name

    def get_or_create_item_group(self):
        root = frappe.db.get_value(
            "Item Group",
            {"is_group": 1, "parent_item_group": ["is", "not set"]},
            "name",
        )
        if not root:
            root = frappe.db.get_value(
                "Item Group",
                {"is_group": 1, "parent_item_group": ""},
                "name",
            )
        if not root:
            root = "All Item Groups"

        existing = frappe.db.get_value(
            "Item Group",
            {"item_group_name": "QuickBooks Items", "parent_item_group": root},
            "name",
        )
        if existing:
            return existing

        group = frappe.get_doc({
            "doctype": "Item Group",
            "item_group_name": "QuickBooks Items",
            "parent_item_group": root,
            "is_group": 0,
        })
        group.flags.ignore_permissions = True
        group.insert()
        frappe.db.commit()
        return group.name

    def resolve_customer(self, qb_customer_name):
        if not qb_customer_name:
            raise ValueError("Customer name missing on estimate record")

        customer = frappe.db.get_value("Customer", {"customer_name": qb_customer_name}, "name")
        if customer:
            return customer

        result = frappe.db.sql(
            "select name from `tabCustomer` where lower(customer_name)=lower(%s) limit 1",
            qb_customer_name,
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
        new_customer.flags.ignore_permissions = True
        new_customer.insert()
        frappe.db.commit()
        return new_customer.name

    def resolve_item(self, qb_item_name):
        if not qb_item_name:
            raise ValueError("Item name missing on estimate line")

        item = frappe.db.get_value("Item", {"item_code": qb_item_name}, ["name", "item_code", "item_name"])
        if item:
            return item[1] or item[0], item[2] or item[1] or item[0]

        result = frappe.db.sql(
            "select name, item_code, item_name from `tabItem` where lower(item_code)=lower(%s) limit 1",
            qb_item_name,
        )
        if result:
            name, item_code, item_name = result[0]
            return item_code or name, item_name or item_code or name

        new_item = frappe.get_doc({
            "doctype": "Item",
            "item_code": qb_item_name,
            "item_name": qb_item_name,
            "description": qb_item_name,
            "item_group": self.get_or_create_item_group(),
            "stock_uom": "Unit",
            "is_stock_item": 0,
            "is_purchase_item": 1,
            "is_sales_item": 1,
        })
        new_item.flags.ignore_permissions = True
        new_item.insert()
        frappe.db.commit()
        return new_item.item_code, new_item.item_name

    def _resolve_cost_center(self, cc_name):
        if not cc_name:
            return None

        company = frappe.defaults.get_global_default("company")
        leaf = cc_name.split(":")[-1].strip()
        existing = frappe.db.get_value("Cost Center", {"cost_center_name": leaf, "company": company}, "name")
        if existing:
            return existing

        result = frappe.db.sql(
            "select name from `tabCost Center` where lower(cost_center_name)=lower(%s) and company=%s limit 1",
            (leaf, company),
        )
        return result[0][0] if result else None

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

    def _ensure_uom(self, uom_name):
        if not uom_name:
            uom_name = "Nos"

        existing = frappe.db.get_value("UOM", {"uom_name": uom_name}, ["name", "must_be_whole_number"], as_dict=True)
        if existing:
            if uom_name in ("Unit", "Piece") and existing.must_be_whole_number:
                frappe.db.set_value("UOM", existing.name, "must_be_whole_number", 0)
            return existing.name

        uom = frappe.get_doc({
            "doctype": "UOM",
            "uom_name": uom_name,
            "must_be_whole_number": 0 if uom_name.lower() != "nos" else 1,
            "enabled": 1,
        })
        uom.flags.ignore_permissions = True
        uom.insert(ignore_permissions=True)
        frappe.db.commit()
        return uom.name

    def resolve_uom(self, qty, fallback_uom="Nos"):
        try:
            qty_value = float(qty)
        except (TypeError, ValueError):
            return fallback_uom or "Nos"

        if not qty_value.is_integer():
            existing = self._ensure_uom("Unit")
            if existing:
                return existing

            existing = self._ensure_uom("Piece")
            if existing:
                return existing

            return fallback_uom or "Nos"

        return fallback_uom or "Nos"

    def ensure_item_supports_uom(self, item_code, uom_name):
        if not item_code or uom_name not in ("Unit", "Piece"):
            return

        try:
            item = frappe.get_doc("Item", item_code)
            if item.stock_uom in ("Unit", "Piece"):
                return

            if item.stock_uom != "Unit":
                item.stock_uom = "Unit"
                item.flags.ignore_permissions = True
                item.save()
                frappe.db.commit()
        except frappe.DoesNotExistError:
            pass
        except Exception as e:
            print(f"  WARN: Could not update item {item_code} for UOM support: {e}")

    def _should_skip_line(self, line):
        item_name = (line.get("item") or line.get("item_name") or "").strip()
        description = (line.get("description") or "").strip()

        if not item_name and not description:
            return True

        if not item_name:
            normalized = description.upper()
            if normalized in {"SUBTOTAL", "LABOR", "MATERIALS"}:
                return True
            if "SEE ATTACHED" in normalized:
                return True
            if "CHANGE ORDER" in normalized:
                return True
            if normalized.startswith("NET CHANGE"):
                return True
            if normalized.startswith("TOTAL") or normalized.startswith("SUBTOTAL"):
                return True

        return False

    def resolve_sales_partner(self, salesman):
        if not salesman:
            return None

        partner = frappe.db.get_value("Sales Partner", {"partner_name": salesman}, "name")
        if partner:
            return partner

        result = frappe.db.sql(
            "select name from `tabSales Partner` where lower(partner_name)=lower(%s) limit 1",
            salesman,
        )
        if result:
            return result[0][0]

        new_partner = frappe.get_doc({
            "doctype": "Sales Partner",
            "partner_name": salesman,
            "commission_rate": 0,
            "enabled": 1,
        })
        new_partner.flags.ignore_permissions = True
        new_partner.insert()
        frappe.db.commit()
        return new_partner.name

    def resolve_payment_terms_template(self, terms):
        if not terms:
            return None

        template = frappe.db.get_value("Payment Terms Template", {"name": terms}, "name")
        if template:
            return template

        return None

    def resolve_taxes_template(self, tax_item):
        if not tax_item:
            return None

        template = frappe.db.get_value(
            "Sales Taxes and Charges Template",
            {"title": tax_item},
            "name",
        )
        if template:
            return template

        result = frappe.db.sql(
            "select name from `tabSales Taxes and Charges Template` where lower(title)=lower(%s) limit 1",
            tax_item,
        )
        return result[0][0] if result else None

    def post_insert(self, doc, source_record):
        if getattr(doc, "doctype", None) == "Quotation" and doc.docstatus == 0:
            doc.submit()
            frappe.db.commit()

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        customer = self.resolve_customer(record.get("cust_name"))

        items = []
        for idx, line in enumerate(record.get("lines", []), 1):
            if self._should_skip_line(line):
                continue

            item_name = line.get("item") or line.get("item_name") or ""
            if item_name:
                item_code, item_name_value = self.resolve_item(item_name)
            else:
                item_code, item_name_value = None, None

            qty = line.get("qty") if line.get("qty") is not None else line.get("quantity", 0)
            try:
                qty = float(qty)
            except (TypeError, ValueError):
                qty = 0

            amount = abs(float(line.get("ext_price") or line.get("amount") or 0))
            if qty <= 0:
                if amount > 0:
                    qty = 1
                else:
                    continue

            item_name_text = item_name_value or line.get("description") or item_name or ""
            if len(item_name_text) > 140:
                item_name_text = item_name_text[:137] + "..."

            uom_value = self.resolve_uom(qty, line.get("unitms") or "Nos")
            if item_code and uom_value in ("Unit", "Piece"):
                try:
                    if not float(qty).is_integer():
                        self.ensure_item_supports_uom(item_code, uom_value)
                except (TypeError, ValueError):
                    pass

            item_row = {
                "idx": idx,
                "item_code": item_code,
                "item_name": item_name_text,
                "qty": qty,
                "uom": uom_value,
                "rate": abs(float(line.get("price") or line.get("rate") or 0)),
                "amount": amount,
                "description": line.get("description") or "",
            }

            cost_center = self._resolve_cost_center(line.get("class_name"))
            if cost_center:
                item_row["cost_center"] = cost_center

            tax_template = self._resolve_item_tax_template(line.get("tax_code"))
            if tax_template:
                item_row["item_tax_template"] = tax_template

            items.append(item_row)

        if not items:
            raise ValueError("No valid item lines found for estimate")

        doc = {
            "doctype": "Quotation",
            "quotation_to": "Customer",
            "party_name": customer,
            "transaction_date": self.normalize_date(record.get("date")),
            "valid_till": self.normalize_date(record.get("due_date") or record.get("date")),
            "company": company,
            "po_no": record.get("ref_no") or record.get("po_num") or "",
            "customer_note": record.get("memo") or "",
            "items": items,
            "total": abs(float(record.get("subtotal") or 0)),
            "total_taxes_and_charges": abs(float(record.get("sales_tax_total") or 0)),
            "grand_total": abs(float(record.get("total_amt") or 0)),
            "base_total": abs(float(record.get("subtotal") or 0)),
            "base_total_taxes_and_charges": abs(float(record.get("sales_tax_total") or 0)),
            "base_grand_total": abs(float(record.get("total_amt") or 0)),
        }

        if record.get("txn_id"):
            doc["name"] = str(record.get("txn_id"))

        tax_template = self.resolve_taxes_template(record.get("tax_item"))
        if tax_template:
            doc["taxes_and_charges"] = tax_template

        if record.get("sales_tax_pct") not in (None, ""):
            doc["taxes_and_charges_rate"] = abs(float(record.get("sales_tax_pct") or 0))

        sales_partner = self.resolve_sales_partner(record.get("salesman"))
        if sales_partner:
            doc["sales_partner"] = sales_partner

        payment_terms_template = self.resolve_payment_terms_template(record.get("terms"))
        if payment_terms_template:
            doc["payment_terms_template"] = payment_terms_template

        return doc
