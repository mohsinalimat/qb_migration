import frappe

from ..base_importer import BaseImporter
from .customer_currency import ensure_customer_currency


class SalesOrderImporter(BaseImporter):
    source_type = "QB_SALES_ORDER"
    target_doctype = "Sales Order"
    json_file = "sales_orders.json"
    json_key = "sales_orders"

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

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("ref_no") or "")

    def resolve_customer(self, qb_customer_name):
        if not qb_customer_name:
            raise ValueError("Customer name missing on sales order record")

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
        ensure_customer_currency(qb_customer_name, new_customer)
        new_customer.flags.ignore_permissions = True
        new_customer.insert()
        frappe.db.commit()
        return new_customer.name

    def resolve_item(self, qb_item_name):
        if not qb_item_name:
            raise ValueError("Item name missing on sales order line")

        item = frappe.db.get_value("Item", {"item_code": qb_item_name}, "name")
        if item:
            return item

        result = frappe.db.sql(
            "select name from `tabItem` where lower(item_code)=lower(%s) limit 1",
            qb_item_name,
        )
        if result:
            return result[0][0]

        new_item = frappe.get_doc({
            "doctype": "Item",
            "item_code": qb_item_name,
            "item_name": qb_item_name,
            "description": qb_item_name,
            "item_group": self.get_or_create_item_group(),
            "stock_uom": "Nos",
            "is_stock_item": 0,
            "is_purchase_item": 1,
            "is_sales_item": 1,
        })
        new_item.flags.ignore_permissions = True
        new_item.insert()
        frappe.db.commit()
        return new_item.item_code

    def resolve_sales_person(self, salesman):
        if not salesman:
            return None

        person = frappe.db.get_value("Sales Person", {"sales_person_name": salesman}, "name")
        if person:
            return person

        result = frappe.db.sql(
            "select name from `tabSales Person` where lower(sales_person_name)=lower(%s) limit 1",
            salesman,
        )
        if result:
            return result[0][0]

        new_person = frappe.get_doc({
            "doctype": "Sales Person",
            "sales_person_name": salesman,
            "enabled": 1,
        })
        new_person.flags.ignore_permissions = True
        new_person.insert()
        frappe.db.commit()
        return new_person.name

    def resolve_project(self, project_name):
        if not project_name:
            return None

        project = frappe.db.get_value("Project", {"project_name": project_name}, "name")
        if project:
            return project

        result = frappe.db.sql(
            "select name from `tabProject` where lower(project_name)=lower(%s) limit 1",
            project_name,
        )
        if result:
            return result[0][0]

        return None

    def find_existing_target(self, doc_data):
        if doc_data.get("name"):
            return frappe.db.get_value("Sales Order", {"name": doc_data["name"]}, "name")

        return None

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        customer = self.resolve_customer(record.get("cust_name"))

        items = []
        for idx, line in enumerate(record.get("lines", []), 1):
            qty = line.get("qty")
            if qty is None:
                qty = 1
            if (qty == 0 or qty == 0.0) and not (line.get("price", 0) > 0 or line.get("ext_price", 0) > 0):
                continue
            if (qty == 0 or qty == 0.0) and line.get("qty_invoiced", 0) > 0:
                qty = line["qty_invoiced"]
            if (qty == 0 or qty == 0.0) and not line.get("qty_invoiced", 0):
                qty = 1

            items.append({
                "idx": idx,
                "item_code": self.resolve_item(line.get("item", "")),
                "qty": qty,
                "rate": line.get("price", 0),
                "amount": line.get("ext_price", 0),
                "description": line.get("description", ""),
                "delivered_qty": line.get("qty_invoiced", 0),
            })

        if not items:
            return {
                "_skip": True,
                "_skip_reason": "Skipped sales order because all line items have qty 0.0",
                "ref_no": record.get("ref_no", ""),
            }

        sales_team = []
        sales_person = self.resolve_sales_person(record.get("salesman"))
        if sales_person:
            sales_team.append({
                "sales_person": sales_person,
                "allocated_percentage": 100,
                "commission_rate": 0,
            })

        project = self.resolve_project(record.get("project_name"))

        doc = {
            "doctype": "Sales Order",
            "customer": customer,
            "transaction_date": self.normalize_date(record.get("date")),
            "delivery_date": self.normalize_date(record.get("ship_date") or record.get("date")),
            "customer_po_no": record.get("po_num", ""),
            "shipping_rule": record.get("ship_via") or "",
            "company": company,
            "grand_total": record.get("total_amt", 0),
            "status": "Closed" if record.get("is_fully_inv") else "Draft",
            "remarks": record.get("memo") or f"Imported from QuickBooks txn_id {record.get('txn_id')}",
            "items": items,
        }

        if project:
            doc["project"] = project

        if record.get("txn_id"):
            doc["name"] = str(record.get("txn_id"))

        if sales_team:
            doc["sales_team"] = sales_team

        return doc
