import frappe

from ..base_importer import BaseImporter


class CustomerImporter(BaseImporter):
    source_type = "QB_CUSTOMER"
    target_doctype = "Customer"
    json_file = "customers.json"
    json_key = "customers"

    # Cache for the safe leaf group to avoid repeated DB hits
    _safe_leaf_group = None

    def _assert_leaf_customer_group(self, group_name):
        is_group = frappe.db.get_value("Customer Group", group_name, "is_group")
        if int(is_group or 0) != 0:
            raise ValueError(
                f"Resolved Customer Group must be non-group/leaf, got group node: {group_name}"
            )
        return group_name

    def _get_root_customer_group(self):
        """
        Find the root Customer Group.
        In ERPNext, the root is usually 'All Customer Groups' (is_group=1, parent_customer_group is empty).
        If not found, we fall back to the first Customer Group that is a group and has no parent.
        """
        root = frappe.db.get_value(
            "Customer Group",
            {"is_group": 1, "parent_customer_group": ["is", "not set"]},
            "name",
        )
        if root:
            return root

        # Fallback: any group without a parent
        root = frappe.db.get_value(
            "Customer Group",
            {"is_group": 1, "parent_customer_group": ""},
            "name",
        )
        return root or "All Customer Groups"  # ultimate fallback

    def _get_or_create_safe_leaf_group(self):
        """
        Get or create a safe leaf Customer Group under the root.
        Used as fallback when QB group is missing or invalid.
        Returns the name of a leaf Customer Group (is_group=0).
        """
        if self._safe_leaf_group:
            return self._safe_leaf_group

        root = self._get_root_customer_group()
        leaf_name = "QuickBooks Customers"

        # Check if leaf already exists under the root
        leaf = frappe.db.get_value(
            "Customer Group",
            {"customer_group_name": leaf_name, "parent_customer_group": root},
            "name",
        )
        if leaf:
            self._safe_leaf_group = self._assert_leaf_customer_group(leaf)
            return self._safe_leaf_group

        # Create the leaf group
        leaf_doc = frappe.get_doc(
            {
                "doctype": "Customer Group",
                "customer_group_name": leaf_name,
                "parent_customer_group": root,
                "is_group": 0,  # Must be a leaf (non-group)
            }
        )
        leaf_doc.flags.ignore_permissions = True
        leaf_doc.insert()
        # Note: We do not commit here; let BaseImporter handle commit after the Customer insert
        self._safe_leaf_group = self._assert_leaf_customer_group(leaf_doc.name)
        return self._safe_leaf_group

    def resolve_customer_group(self, qb_group_name):
        """
        Resolve a Customer Group name to a leaf (non-group) Customer Group.
        ERPNext does not allow assigning a group-type Customer Group to a Customer.
        Rules:
        1. If qb_group_name is provided and exists as a leaf, return it.
        2. If qb_group_name is provided and exists as a group, return/create a leaf under it.
        3. If qb_group_name is not provided or not found, return the safe leaf group under the root.
        """
        if not qb_group_name:
            return self._assert_leaf_customer_group(self._get_or_create_safe_leaf_group())

        # Try to find the group by name
        group = frappe.db.get_value(
            "Customer Group", {"customer_group_name": qb_group_name}, "name"
        )
        if not group:
            # Group not found, fall back to safe leaf
            return self._assert_leaf_customer_group(self._get_or_create_safe_leaf_group())

        # Check if it is a leaf (non-group)
        is_group = frappe.db.get_value("Customer Group", group, "is_group")
        if not is_group:
            # Already a leaf, safe to use
            return self._assert_leaf_customer_group(group)

        # It is a group, we need a leaf under it
        leaf_name = f"{qb_group_name} - Customers"
        # Check if leaf already exists under this group
        leaf = frappe.db.get_value(
            "Customer Group",
            {"customer_group_name": leaf_name, "parent_customer_group": group},
            "name",
        )
        if leaf:
            return self._assert_leaf_customer_group(leaf)

        # Create the leaf group under the QB group
        leaf_doc = frappe.get_doc(
            {
                "doctype": "Customer Group",
                "customer_group_name": leaf_name,
                "parent_customer_group": group,
                "is_group": 0,  # Must be a leaf (non-group)
            }
        )
        leaf_doc.flags.ignore_permissions = True
        leaf_doc.insert()
        # Note: We do not commit here; let BaseImporter handle commit after the Customer insert
        return self._assert_leaf_customer_group(leaf_doc.name)

    def map_record(self, record):
        doc = {
            "doctype": "Customer",
            "customer_name": record["name"],
            "customer_type": "Company" if record.get("is_company") else "Individual",
            "customer_group": self.resolve_customer_group(record.get("customer_group")),
            "territory": "All Territories",
        }

        if record.get("currency"):
            doc["default_currency"] = record["currency"]

        if record.get("address"):
            doc["_address"] = record["address"]

        return doc

    def find_existing_target(self, doc_data):
        """
        Find an existing Customer by customer_name to avoid duplicates on rerun.
        Returns the name of the existing Customer if found, else None.
        """
        customer_name = doc_data.get("customer_name")
        if not customer_name:
            return None

        existing = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
        return existing

    def post_insert(self, doc, source_record):
        address = source_record.get("_address")
        if not address:
            return

        frappe.get_doc(
            {
                "doctype": "Address",
                "address_type": "Billing",
                "address_line1": address.get("line1", ""),
                "city": address.get("city", ""),
                "state": address.get("state", ""),
                "pincode": address.get("zip", ""),
                "country": address.get("country", "United States"),
                "links": [{"link_doctype": "Customer", "link_name": doc.name}],
            }
        ).insert(ignore_permissions=True)