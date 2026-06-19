import frappe

from ..base_importer import BaseImporter

QB_ACCOUNT_TYPE_MAP = {
    "Bank": ("Bank", "Asset"),
    "AccountsReceivable": ("Receivable", "Asset"),
    "AccountsPayable": ("Payable", "Liability"),
    "CreditCard": ("Liability", "Liability"),
    "Income": ("Income Account", "Income"),
    "Expense": ("Expense Account", "Expense"),
    "CostOfGoodsSold": ("Cost of Goods Sold", "Expense"),
    "FixedAsset": ("Fixed Asset", "Asset"),
    "OtherCurrentAsset": ("Current Asset", "Asset"),
    "OtherCurrentLiability": ("Liability", "Liability"),
    "OtherAsset": ("Current Asset", "Asset"),
    "Equity": ("Equity", "Equity"),
    "LongTermLiability": ("Liability", "Liability"),
    "OtherIncome": ("Income Account", "Income"),
    "OtherExpense": ("Expense Account", "Expense"),
}

DEFAULT_PARENT_GROUPS = {
    "Bank": "Bank Accounts",
    "AccountsReceivable": "Current Assets",
    "AccountsPayable": "Current Liabilities",
    "CreditCard": "Current Liabilities",
    "Income": "Income",
    "OtherIncome": "Income",
    "Expense": "Expenses",
    "CostOfGoodsSold": "Stock Expenses",
    "OtherExpense": "Expenses",
    "FixedAsset": "Fixed Assets",
    "OtherCurrentAsset": "Current Assets",
    "OtherAsset": "Current Assets",
    "OtherCurrentLiability": "Current Liabilities",
    "Equity": "Equity",
    "LongTermLiability": "Non-Current Liabilities",
}

ROOT_TYPE_GROUPS = {
    "Asset": "Current Assets",
    "Liability": "Current Liabilities",
    "Income": "Income",
    "Expense": "Expenses",
    "Equity": "Equity",
}


class AccountImporter(BaseImporter):
    source_type = "QB_ACCOUNT"
    target_doctype = "Account"
    json_file = "accounts.json"
    json_key = "accounts"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._parent_groups = None
        self._records_by_name = None

    @property
    def parent_groups(self):
        if self._parent_groups is None:
            self._parent_groups = set()
            for record in self.load_data():
                if record.get("account_type") == "NonPosting":
                    continue
                parent = (record.get("parent") or "").strip()
                if parent:
                    self._parent_groups.add(parent)
        return self._parent_groups

    @property
    def records_by_name(self):
        if self._records_by_name is None:
            self._records_by_name = {}
            for record in self.load_data():
                name = (record.get("name") or "").strip()
                if name:
                    if name in self._records_by_name:
                        print(f"WARNING: Duplicate QB account name found: {name}. Use account_number for lookup.")
                    self._records_by_name[name] = record
        return self._records_by_name

    def run(self, dry_run=False):
        result = super().run(dry_run)
        if not dry_run:
            self.post_import_validation_and_repair()
        return result

    def _find_account(self, account_name, company, account_number=None, parent_name=None):
        if not account_name:
            return None

        account_name = account_name.strip()

        # Build precise filter
        filters = {"account_name": account_name, "company": company}
        if account_number:
            filters["account_number"] = account_number.strip()

        # Try finding by precise criteria
        accounts = frappe.get_all("Account", filters=filters, fields=["name", "parent_account"])

        # If parent_name provided, filter by it
        if parent_name:
            parent_account_name = self._find_account(parent_name, company)
            filtered_accounts = [a for a in accounts if a.parent_account == parent_account_name]
            if filtered_accounts:
                return filtered_accounts[0]["name"]

        if accounts:
            return accounts[0]["name"]

        # Fallback to case-insensitive lookup if no precise match
        row = frappe.db.sql(
            "select name from `tabAccount` where lower(account_name)=lower(%s) and company=%s limit 1",
            (account_name, company),
        )
        return row[0][0] if row else None

    def _default_parent_name(self, qb_type, root_type):
        return DEFAULT_PARENT_GROUPS.get(qb_type) or ROOT_TYPE_GROUPS.get(root_type)

    def _get_qb_type_for_account_name(self, account_name, fallback_qb_type):
        record = self.records_by_name.get((account_name or "").strip())
        if record:
            return record.get("account_type") or fallback_qb_type
        return fallback_qb_type

    def _ensure_account_group(self, account_name, qb_type, root_type, company, parent_account=None):
        if not account_name:
            return None

        existing = self._find_account(account_name, company)
        if existing:
            existing_doc = frappe.get_doc("Account", existing)
            # Ensure it is a group
            if not existing_doc.is_group:
                print(f"CORRECTION: Converting {account_name} to group account; clearing account_type={existing_doc.account_type}")
                existing_doc.account_type = None
                existing_doc.is_group = 1
                existing_doc.flags.ignore_permissions = True
                existing_doc.save(ignore_permissions=True)
            return existing

        if not parent_account:
            parent_name = self._default_parent_name(qb_type, root_type)
            parent_account = self._find_account(parent_name, company)

        print(f"CREATED GROUP ACCOUNT: {account_name} under {parent_account}")
        doc = frappe.get_doc({
            "doctype": "Account",
            "account_name": account_name,
            "company": company,
            "parent_account": parent_account,
            "root_type": root_type,
            "is_group": 1,
        })
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        return doc.name

    def find_existing_target(self, doc_data):
        return self._find_account(doc_data.get("account_name"), doc_data.get("company"), doc_data.get("account_number"))

    def map_record(self, record):
        if record.get("account_type") == "NonPosting":
            print(f"SKIP NonPosting account: {record.get('name')}")
            return {"_skip": True, "_skip_reason": "NonPosting"}

        company = frappe.defaults.get_global_default("company")
        qb_type = record.get("account_type", "Expense")
        acct_type, root_type = QB_ACCOUNT_TYPE_MAP.get(
            qb_type, ("Expense Account", "Expense")
        )

        name = record.get("name", "").strip()
        qb_parent = record.get("parent", "").strip()
        account_number = record.get("account_number")

        # Resolve Parent
        parent_account = None
        if qb_parent:
            parent_account = self._find_account(qb_parent, company)
            if not parent_account:
                # Parent doesn't exist, create it as a group using the parent's own type if available
                parent_qb_type = self._get_qb_type_for_account_name(qb_parent, qb_type)
                _, parent_root_type = QB_ACCOUNT_TYPE_MAP.get(parent_qb_type, ("Expense Account", "Expense"))
                parent_account = self._ensure_account_group(qb_parent, parent_qb_type, parent_root_type, company)
            else:
                # Parent exists, ensure it is a group
                parent_doc = frappe.get_doc("Account", parent_account)
                if not parent_doc.is_group:
                    print(f"CORRECTION: Converting {qb_parent} to group account; clearing account_type={parent_doc.account_type}")
                    parent_doc.account_type = None
                    parent_doc.is_group = 1
                    parent_doc.flags.ignore_permissions = True
                    parent_doc.save(ignore_permissions=True)
        else:
            # No parent, use default parent based on type
            default_parent_name = self._default_parent_name(qb_type, root_type)
            parent_account = self._find_account(default_parent_name, company)

        is_group = 1 if name in self.parent_groups else 0
        account_type = None if is_group else acct_type

        # Log for debug
        print(
            f"DEBUG: QB={name}, Parent={qb_parent}, Resolved={parent_account}, "
            f"InParentGroups={name in self.parent_groups}, is_group={is_group}"
        )

        return {
            "doctype": "Account",
            "account_name": name,
            "account_number": account_number,
            "account_type": account_type,
            "root_type": root_type,
            "company": company,
            "parent_account": parent_account,
            "is_group": is_group,
        }

    def post_import_validation_and_repair(self):
        """
        Verify hierarchy and repair existing accounts.
        """
        company = frappe.defaults.get_global_default("company")
        records = self.load_data()

        for record in records:
            qb_name = record.get("name", "").strip()
            qb_parent = record.get("parent", "").strip()
            qb_number = record.get("account_number")
            qb_type = record.get("account_type", "Expense")
            if qb_type == "NonPosting": continue

            _, root_type = QB_ACCOUNT_TYPE_MAP.get(qb_type, ("Expense Account", "Expense"))

            if not qb_name: continue

            child_account = self._find_account(qb_name, company, account_number=qb_number)
            if not child_account:
                print(f"  ERROR: Account {qb_name} not found in ERPNext during validation.")
                continue

            child_doc = frappe.get_doc("Account", child_account)

            # 1. Repair is_group: Only upgrade to 1, never downgrade
            if qb_name in self.parent_groups and child_doc.is_group == 0:
                print(f"CORRECTION: Converting {qb_name} to group account; clearing account_type={child_doc.account_type}")
                child_doc.account_type = None
                child_doc.is_group = 1
                child_doc.flags.ignore_permissions = True
                child_doc.save(ignore_permissions=True)

            # 2. Repair parent
            if qb_parent:
                expected_parent = self._find_account(qb_parent, company)
                if not expected_parent:
                    # Parent missing during validation, create it now
                    parent_qb_type = self._get_qb_type_for_account_name(qb_parent, qb_type)
                    _, parent_root_type = QB_ACCOUNT_TYPE_MAP.get(parent_qb_type, ("Expense Account", "Expense"))
                    expected_parent = self._ensure_account_group(qb_parent, parent_qb_type, parent_root_type, company)
                    print(f"  RESOLVED missing parent {qb_parent} for {qb_name}: {expected_parent}")

                if child_doc.parent_account != expected_parent:
                    print(
                        f"  CORRECTION: Corrected parent for {qb_name} "
                        f"from {child_doc.parent_account} to {expected_parent}"
                    )
                    child_doc.parent_account = expected_parent
                    child_doc.flags.ignore_permissions = True
                    child_doc.save(ignore_permissions=True)
            else:
                # No QB parent, only repair if missing
                if not child_doc.parent_account:
                    expected_parent = self._find_account(self._default_parent_name(qb_type, root_type), company)
                    if expected_parent:
                        print(f"  CORRECTION: Setting default parent for {qb_name} to {expected_parent}")
                        child_doc.parent_account = expected_parent
                        child_doc.flags.ignore_permissions = True
                        child_doc.save(ignore_permissions=True)
                else:
                    print(f"  INFO: Leaving top-level account {qb_name} parent unchanged: {child_doc.parent_account}")
        frappe.db.commit()