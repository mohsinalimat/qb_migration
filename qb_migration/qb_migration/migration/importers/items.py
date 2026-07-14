import frappe
from ..base_importer import BaseImporter


QB_ITEM_TYPE_MAP = {
    "INV": "Stock Item",
    "SVC": "Service Item",
    "NON": "Non Stock Item",
    "GRPITEM": "Service Item",
    "OTHCHG": "Service Item",
    "OCH": "Service Item",
    "DSC": "Service Item",
    "GRP": "Service Item",
    "PAY": "Service Item",
    "TAX": "Service Item",
    "TAXGROUP": "Service Item",
    "SalesTaxGroup": "Service Item",
    "SUBTOTAL": "Service Item",
    "OTHER": "Service Item",
    "inventoryassembly": "Stock Item",
}


class ItemImporter(BaseImporter):
    source_type = "QB_ITEM"
    target_doctype = "Item"
    json_file = "items.json"
    json_key = "items"

    # Cache for the safe leaf group
    _safe_leaf_group = None

    def _assert_leaf_item_group(self, group_name):
        is_group = frappe.db.get_value("Item Group", group_name, "is_group")
        if int(is_group or 0) != 0:
            raise ValueError(
                f"Resolved Item Group must be non-group/leaf, got group node: {group_name}"
            )
        return group_name

    def _get_root_item_group(self):
        root = frappe.db.get_value(
            "Item Group",
            {"is_group": 1, "parent_item_group": ["is", "not set"]},
            "name",
        )
        return root or "All Item Groups"

    def _get_or_create_safe_leaf_group(self):
        if self._safe_leaf_group:
            return self._safe_leaf_group

        root = self._get_root_item_group()
        leaf_name = "QuickBooks Items"

        leaf = frappe.db.get_value(
            "Item Group",
            {"item_group_name": leaf_name, "parent_item_group": root},
            "name",
        )
        if leaf:
            self._safe_leaf_group = self._assert_leaf_item_group(leaf)
            return self._safe_leaf_group

        leaf_doc = frappe.get_doc(
            {
                "doctype": "Item Group",
                "item_group_name": leaf_name,
                "parent_item_group": root,
                "is_group": 0,
            }
        )
        leaf_doc.flags.ignore_permissions = True
        leaf_doc.insert()
        self._safe_leaf_group = self._assert_leaf_item_group(leaf_doc.name)
        return self._safe_leaf_group

    def resolve_account(self, qb_name):
        if not qb_name:
            return None

        leaf = qb_name.split(":")[-1].strip()
        company = frappe.defaults.get_global_default("company")
        return frappe.db.get_value(
            "Account", {"account_name": leaf, "company": company}, "name"
        )

    def find_existing_target(self, doc_data):
        return frappe.db.get_value("Item", {"item_code": doc_data.get("item_code")}, "name")

    def resolve_item_group(self, item_group_name):
        if not item_group_name:
            return self._assert_leaf_item_group(self._get_or_create_safe_leaf_group())

        item_group_name = item_group_name.strip()
        group = frappe.db.get_value(
            "Item Group", {"item_group_name": item_group_name}, "name"
        )
        if group:
            # Check if it is a leaf
            is_group = frappe.db.get_value("Item Group", group, "is_group")
            if not is_group:
                return self._assert_leaf_item_group(group)

            # If it's a group, create a leaf under it
            leaf_name = f"{item_group_name} - Items"
            leaf = frappe.db.get_value(
                "Item Group",
                {"item_group_name": leaf_name, "parent_item_group": group},
                "name",
            )
            if leaf:
                return self._assert_leaf_item_group(leaf)

            leaf_doc = frappe.get_doc(
                {
                    "doctype": "Item Group",
                    "item_group_name": leaf_name,
                    "parent_item_group": group,
                    "is_group": 0,
                }
            )
            leaf_doc.flags.ignore_permissions = True
            leaf_doc.insert()
            return self._assert_leaf_item_group(leaf_doc.name)

        # Group not found, fall back to safe leaf
        return self._assert_leaf_item_group(self._get_or_create_safe_leaf_group())

    def map_record(self, record):
        item_type = QB_ITEM_TYPE_MAP.get(record.get("item_type"), "Service Item")

        if item_type == "Stock Item":
            is_stock = 1
        else:
            is_stock = 0
        company = frappe.defaults.get_global_default("company")

        doc = {
            "doctype": "Item",
            "item_code": record["item"],
            "item_name": record["item"],
            "description": record.get("description") or record["item"],
            "item_group": self.resolve_item_group(record.get("item_group")),
            "stock_uom": record.get("stock_uom", "Nos"),
            "is_stock_item": is_stock,
            "is_purchase_item": 1,
            "is_sales_item": 1,
            "valuation_rate": record.get("cost", 0),
            "standard_rate": record.get("price", 0),
        }

        income_acct = self.resolve_account(record.get("income_acct"))
        cogs_acct = self.resolve_account(record.get("cogs_acct"))
        asset_acct = self.resolve_account(record.get("asset_acct"))

        if income_acct or cogs_acct or asset_acct:
            doc["item_defaults"] = [
                {
                    "company": company,
                    "income_account": income_acct,
                    "expense_account": cogs_acct,
                    "asset_account": asset_acct if is_stock else None,
                }
            ]

        return doc