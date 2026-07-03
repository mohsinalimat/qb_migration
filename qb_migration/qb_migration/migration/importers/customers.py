import random

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
            {
                "customer_group_name": leaf_name,
                "parent_customer_group": root,
                "is_group": 0,
            },
            "name",
        )
        if leaf:
            self._safe_leaf_group = leaf
            return self._safe_leaf_group

        existing = frappe.db.get_value(
            "Customer Group",
            {"customer_group_name": leaf_name, "parent_customer_group": root},
            "name",
        )
        if existing:
            # Existing placeholder group found, create a leaf underneath it.
            leaf_doc = frappe.get_doc(
                {
                    "doctype": "Customer Group",
                    "customer_group_name": f"{leaf_name} - Customers",
                    "parent_customer_group": existing,
                    "is_group": 0,
                }
            )
            leaf_doc.flags.ignore_permissions = True
            leaf_doc.insert()
            self._safe_leaf_group = self._assert_leaf_customer_group(leaf_doc.name)
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
            return random.choice(state_cities[state])

        if country in ("pakistan", "pk", "pak"):
            return random.choice(["Karachi", "Lahore", "Islamabad", "Rawalpindi", "Multan"])

        return random.choice(
            ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Miami", "Dallas"]
        )

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
            # QB 'list_id' -> ERPNext customer code
            **({"name": record.get("list_id")} if record.get("list_id") else {}),
            # QB 'name' -> ERPNext customer_name
            "customer_name": record.get("name"),
            # Determine customer type from presence of 'company'
            "customer_type": "Company" if record.get("company") else "Individual",
            "customer_group": self.resolve_customer_group(record.get("customer_group")),
            "territory": "All Territories",
        }

        # Direct mappings from QB fields to ERPNext fields
        if record.get("currency"):
            doc["default_currency"] = record.get("currency")

        if record.get("terms"):
            doc["payment_terms_template"] = record.get("terms")

        if record.get("price_level"):
            doc["default_price_list"] = record.get("price_level")

        if record.get("credit_limit") is not None:
            doc["credit_limit"] = record.get("credit_limit")

        if record.get("notes"):
            doc["remarks"] = record.get("notes")

        # Contact/communication fields
        if record.get("email"):
            doc["email_id"] = record.get("email")

        if record.get("phone"):
            doc["phone_no"] = record.get("phone")

        # active -> disabled (inverse)
        active_flag = record.get("active")
        if active_flag is not None:
            doc["disabled"] = 0 if bool(active_flag) else 1

        # Build an Address dict from top-level QB address fields and attach
        address = {
            "address_line1": record.get("addr1", ""),
            "address_line2": record.get("addr2", ""),
            "city": record.get("city", ""),
            "state": record.get("state", ""),
            "pincode": record.get("zip", ""),
            "country": record.get("country", ""),
            "email_id": record.get("email", ""),
            "phone": record.get("phone", ""),
            "is_primary_address": True,
        }

        # Attach normalized address to the original source record so post_insert can read it
        record["_address"] = address

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

        if not any(
            address.get(field)
            for field in (
                "address_line1",
                "address_line2",
                "city",
                "state",
                "pincode",
                "email_id",
                "phone",
            )
        ):
            return

        city = address.get("city") or self._get_default_city(
            address.get("state"), address.get("country")
        )

        addr_doc = {
            "doctype": "Address",
            "address_type": "Billing",
            "links": [{"link_doctype": "Customer", "link_name": doc.name}],
        }

        for field, value in [
            ("address_line1", address.get("address_line1")),
            ("address_line2", address.get("address_line2")),
            ("city", city),
            ("state", address.get("state")),
            ("pincode", address.get("pincode")),
            ("country", address.get("country") or "United States"),
            ("email_id", address.get("email_id")),
            ("phone", address.get("phone")),
        ]:
            if value not in (None, ""):
                addr_doc[field] = value

        frappe.get_doc(addr_doc).insert(ignore_permissions=True)