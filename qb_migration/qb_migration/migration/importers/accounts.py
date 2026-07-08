import traceback

import frappe

from ..base_importer import BaseImporter

# ====================================================================
#  QB TYPE → (ERPNext account_type, root_type)
# ====================================================================
QB_ACCOUNT_TYPE_MAP = {
    # Assets
    "Bank":                  ("Bank", "Asset"),
    "AccountsReceivable":    ("Receivable", "Asset"),
    "OtherCurrentAsset":     (None, "Asset"),
    "FixedAsset":            ("Fixed Asset", "Asset"),
    "OtherAsset":            (None, "Asset"),
    # Liabilities
    "AccountsPayable":       ("Payable", "Liability"),
    "CreditCard":            ("Bank", "Liability"),
    "OtherCurrentLiability": (None, "Liability"),
    "LongTermLiability":     (None, "Liability"),
    # Equity
    "Equity":                (None, "Equity"),
    # Income
    "Income":                ("Income Account", "Income"),
    "OtherIncome":           ("Income Account", "Income"),
    # Expenses
    "Expense":               ("Expense Account", "Expense"),
    "OtherExpense":          ("Expense Account", "Expense"),
    "CostOfGoodsSold":       ("Cost of Goods Sold", "Expense"),
    # Non‑posting (groups only)
    "NonPosting":            (None, "Expense"),
}

# ====================================================================
#  QB SPECIAL TYPE → (ERPNext account_type, root_type)
# ====================================================================
QB_SPECIAL_TYPE_MAP = {
    "UndepositedFunds": ("Cash", "Asset"),
}

# ====================================================================
#  DEFAULT PARENT GROUP for each QB account type
# ====================================================================
DEFAULT_PARENT_GROUPS = {
    "Bank":                  "Bank Accounts",
    "AccountsReceivable":    "Current Assets",
    "OtherCurrentAsset":     "Current Assets",
    "FixedAsset":            "Fixed Assets",
    "OtherAsset":            "Other Assets",
    "AccountsPayable":       "Current Liabilities",
    "CreditCard":            "Current Liabilities",
    "OtherCurrentLiability": "Current Liabilities",
    "LongTermLiability":     "Non‑Current Liabilities",
    "Equity":                "Equity",
    "Income":                "Income",
    "OtherIncome":           "Income",
    "Expense":               "Expenses",
    "OtherExpense":          "Expenses",
    "CostOfGoodsSold":       "Expenses",
}

# ====================================================================
#  ROOT TYPE → fallback root account name (if not found)
# ====================================================================
ROOT_TYPE_GROUPS = {
    "Asset": "Assets",
    "Liability": "Liabilities",
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

    # -----------------------------------------------------------------
    #  Currency helpers
    # -----------------------------------------------------------------
    def _get_company_default_currency(self, company):
        if not company:
            return None

        try:
            company_currency = frappe.db.get_value("Company", company, "default_currency")
        except Exception:
            company_currency = None

        if company_currency:
            return company_currency

        return (
            frappe.defaults.get_global_default("default_currency")
            or frappe.defaults.get_global_default("currency")
        )

    def _get_account_currency(self, record, company):
        currency = (record.get("currency") or "").strip()
        if currency:
            return currency
        return self._get_company_default_currency(company)

    # -----------------------------------------------------------------
    #  Apply fields to an existing doc
    # -----------------------------------------------------------------
    def _apply_account_fields(self, doc, doc_data):
        if not doc:
            return None

        fields_to_set = {}
        if "account_name" in doc_data:
            fields_to_set["account_name"] = doc_data.get("account_name")
        if "account_number" in doc_data:
            fields_to_set["account_number"] = doc_data.get("account_number")
        if "account_type" in doc_data:
            fields_to_set["account_type"] = doc_data.get("account_type")
        if "root_type" in doc_data:
            fields_to_set["root_type"] = doc_data.get("root_type")
        if "parent_account" in doc_data:
            fields_to_set["parent_account"] = doc_data.get("parent_account")
        if "is_group" in doc_data:
            fields_to_set["is_group"] = doc_data.get("is_group")
        if "company" in doc_data:
            fields_to_set["company"] = doc_data.get("company")
        if "account_currency" in doc_data:
            fields_to_set["account_currency"] = doc_data.get("account_currency")

        for field_name, value in fields_to_set.items():
            setattr(doc, field_name, value)

        return doc

    # -----------------------------------------------------------------
    #  Insert or update an account
    # -----------------------------------------------------------------
    def _upsert_account(self, doc_data, existing_target=None):
        # Ensure parent_account is never None – set to "" if missing
        if doc_data.get("parent_account") is None:
            doc_data["parent_account"] = ""

        if existing_target:
            doc = frappe.get_doc("Account", existing_target)

            incoming_is_group = doc_data.get("is_group")
            if doc.is_group and incoming_is_group == 0:
                print(
                    f"PRESERVE GROUP: keeping existing group {doc.name} as group while import tried to set ledger"
                )
                doc_data = dict(doc_data)
                doc_data["is_group"] = 1
                doc_data["account_type"] = None

            if not doc.is_group and incoming_is_group == 1:
                doc_data = dict(doc_data)
                doc_data["account_type"] = None

            self._apply_account_fields(doc, doc_data)
            doc.flags.ignore_permissions = True
            doc.save(ignore_permissions=True)
            return doc

        doc_data = dict(doc_data)
        doc_data.pop("_qb_parent", None)
        doc = frappe.get_doc(doc_data)
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        return doc

    # -----------------------------------------------------------------
    #  Properties for parent_groups and records_by_name
    # -----------------------------------------------------------------
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

    # -----------------------------------------------------------------
    #  Main run method (with dry‑run support)
    # -----------------------------------------------------------------
    def run(self, dry_run=False):
        records = self.load_data()
        total = len(records)
        success = failed = skipped = 0

        print(f"\n[{self.source_type}] Starting: {total} records")

        for i, record in enumerate(records):
            source_id = self.get_source_id(record)
            if not source_id:
                failed += 1
                print(f"  FAIL: missing source id for record {i + 1}")
                continue

            if self.is_imported(source_id):
                skipped += 1
                continue

            try:
                doc_data = self.map_record(record)
                if doc_data is None:
                    skipped += 1
                    continue

                if isinstance(doc_data, dict) and doc_data.get("_skip"):
                    skipped += 1
                    reason = doc_data.get("_skip_reason", "SKIPPED")
                    ref_no = doc_data.get("ref_no") or doc_data.get("reference_no") or record.get("ref_no") or record.get("ref_number")
                    print(f"  SKIP [{source_id}] ref_no={ref_no or 'N/A'} reason={reason}")
                    self.log_skip(source_id, reason, ref_no)
                    continue

                if dry_run:
                    print(f"  DRY RUN: {source_id} → {doc_data.get('name', doc_data.get('item_code', '?'))}")
                    success += 1
                    continue

                existing_target = self.find_existing_target(doc_data)
                doc = self._upsert_account(doc_data, existing_target)
                self.log_success(source_id, doc.name, getattr(doc, "doctype", self.target_doctype))
                success += 1
                frappe.db.commit()

            except Exception as exc:
                frappe.db.rollback()
                self.log_failure(source_id, traceback.format_exc())
                failed += 1
                print(f"  FAIL [{source_id}]: {exc}")

            if (i + 1) % self.batch_size == 0:
                frappe.db.commit()
                print(f"  Progress: {i + 1}/{total}")

        if not dry_run:
            self.post_import_validation_and_repair()

        frappe.db.commit()
        print(f"[{self.source_type}] Done — Success: {success}, Failed: {failed}, Skipped: {skipped}")
        return {"success": success, "failed": failed, "skipped": skipped}

    # -----------------------------------------------------------------
    #  Core account lookup (with strict account_number matching)
    # -----------------------------------------------------------------
    def _find_account(self, account_name, company, account_number=None, parent_name=None):
        """
        Look for an existing account.

        If `account_number` is provided (non‑empty), the lookup is **strict**:
        it requires both `account_name` and `account_number` to match.
        If no such account exists, returns `None` (no fallback).

        If `account_number` is not provided, the lookup is by `account_name` only,
        with a case‑insensitive fallback.
        """
        if not account_name:
            return None

        account_name = account_name.strip()
        filters = {"account_name": account_name, "company": company}

        # If account_number is provided, make it a mandatory part of the filter
        if account_number is not None and account_number.strip() != "":
            filters["account_number"] = account_number.strip()
            # Strict lookup: only match with both name and number
            accounts = frappe.get_all("Account", filters=filters, fields=["name", "parent_account"])
            if accounts:
                # If parent_name is given, further filter by parent
                if parent_name:
                    parent_account_name = self._find_account(parent_name, company)
                    filtered_accounts = [a for a in accounts if a.parent_account == parent_account_name]
                    if filtered_accounts:
                        return filtered_accounts[0]["name"]
                return accounts[0]["name"]
            # No match with number – return None (do NOT fallback to name‑only)
            return None

        # No account_number – proceed with name‑only lookup
        accounts = frappe.get_all("Account", filters=filters, fields=["name", "parent_account"])

        if parent_name:
            parent_account_name = self._find_account(parent_name, company)
            filtered_accounts = [a for a in accounts if a.parent_account == parent_account_name]
            if filtered_accounts:
                return filtered_accounts[0]["name"]

        if accounts:
            return accounts[0]["name"]

        # Fallback to case‑insensitive lookup
        row = frappe.db.sql(
            "select name from `tabAccount` where lower(account_name)=lower(%s) and company=%s limit 1",
            (account_name, company),
        )
        return row[0][0] if row else None

    # -----------------------------------------------------------------
    #  Get the existing root account for a given root_type and company
    # -----------------------------------------------------------------
    def _get_root_account(self, root_type, company):
        """Return the name of the existing root account for this root_type."""
        root = frappe.db.get_value(
            "Account",
            {
                "root_type": root_type,
                "parent_account": ["in", ["", None]],
                "company": company,
            },
            "name"
        )
        return root

    # -----------------------------------------------------------------
    #  Ensure a group account exists (create if missing)
    # -----------------------------------------------------------------
    def _ensure_group_account(self, account_name, root_type, company, parent_account=None):
        """
        Create a group account if it does not exist.
        If parent_account is not given, we find the existing root account
        for this root_type and use it as the parent.
        """
        if not account_name:
            return None

        existing = self._find_account(account_name, company)
        if existing:
            doc = frappe.get_doc("Account", existing)
            if not doc.is_group:
                print(f"CORRECTION: Converting {account_name} to group; clearing account_type")
                doc.account_type = None
                doc.is_group = 1
                doc.flags.ignore_permissions = True
                doc.save(ignore_permissions=True)
            return existing

        if parent_account is None:
            root = self._get_root_account(root_type, company)
            if root:
                parent_account = root
            else:
                parent_account = ROOT_TYPE_GROUPS.get(root_type)
                if parent_account:
                    root_existing = self._find_account(parent_account, company)
                    if not root_existing:
                        print(f"CREATING ROOT GROUP: {parent_account} (root_type={root_type})")
                        doc = frappe.get_doc({
                            "doctype": "Account",
                            "account_name": parent_account,
                            "company": company,
                            "parent_account": "",
                            "root_type": root_type,
                            "is_group": 1,
                            "account_type": None,
                        })
                        doc.flags.ignore_permissions = True
                        doc.insert(ignore_permissions=True)
                    else:
                        parent_account = root_existing
                else:
                    print(f"WARNING: No root account found for root_type {root_type}, and no fallback. Setting parent to empty.")
                    parent_account = ""

        print(f"CREATING GROUP ACCOUNT: {account_name} under {parent_account or '(root)'}")
        doc = frappe.get_doc({
            "doctype": "Account",
            "account_name": account_name,
            "company": company,
            "parent_account": parent_account or "",
            "root_type": root_type,
            "is_group": 1,
            "account_type": None,
        })
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        return doc.name

    # -----------------------------------------------------------------
    #  Default parent name for a QB type
    # -----------------------------------------------------------------
    def _default_parent_name(self, qb_type, root_type):
        return DEFAULT_PARENT_GROUPS.get(qb_type) or ROOT_TYPE_GROUPS.get(root_type)

    # -----------------------------------------------------------------
    #  Prevent self‑parent (returns "" instead of None)
    # -----------------------------------------------------------------
    def _avoid_self_parent(self, account_name, company, parent_account):
        if not parent_account:
            return ""
        existing_target = self._find_account(account_name, company)
        if existing_target and existing_target == parent_account:
            return ""
        return parent_account

    # -----------------------------------------------------------------
    #  Get QB type for a given account name
    # -----------------------------------------------------------------
    def _get_qb_type_for_account_name(self, account_name, fallback_qb_type):
        record = self.records_by_name.get((account_name or "").strip())
        if record:
            return record.get("account_type") or fallback_qb_type
        return fallback_qb_type

    def _find_account_by_parent(self, account_name, company, parent_name=None, account_number=None):
        """Find an account by name, optionally constrained by parent and account number."""
        if not account_name:
            return None

        account_name = account_name.strip()
        filters = {"account_name": account_name, "company": company}

        if account_number is not None and account_number.strip() != "":
            filters["account_number"] = account_number.strip()

        accounts = frappe.get_all("Account", filters=filters, fields=["name", "parent_account"])
        if parent_name:
            parent_name = parent_name.strip()
            parent_account_name = self._find_account(parent_name, company)
            if parent_account_name:
                accounts = [a for a in accounts if a.get("parent_account") == parent_account_name]
            else:
                accounts = [a for a in accounts if a.get("parent_account") == parent_name]

        if accounts:
            return accounts[0]["name"]

        return None

    def _find_default_employee_advances_account(self, company):
        """Return the ERPNext default Employee Advances account under Loans and Advances (Assets)."""
        accounts = frappe.get_all(
            "Account",
            filters={"company": company, "account_name": "Employee Advances"},
            fields=["name", "parent_account"],
        )

        for account in accounts:
            parent_account = account.get("parent_account")
            if not parent_account:
                continue

            try:
                parent_doc = frappe.get_doc("Account", parent_account)
            except Exception:
                continue

            parent_name = (parent_doc.account_name or "").strip().lower()
            if "loans and advances" in parent_name and "assets" in parent_name:
                return account["name"]

        return None

    def _set_default_employee_advances_account_type(self, company):
        target_name = self._find_default_employee_advances_account(company)
        if not target_name:
            return False

        doc = frappe.get_doc("Account", target_name)
        if doc.account_type != "Current Asset":
            doc.account_type = "Current Asset"
            doc.flags.ignore_permissions = True
            doc.save(ignore_permissions=True)
            return True

        return False

    def _resolve_account_mapping(self, record, fallback_qb_type=None):
        if record is None:
            qb_type = fallback_qb_type or "Expense"
            return QB_ACCOUNT_TYPE_MAP.get(qb_type, ("Expense Account", "Expense"))

        special_type = (record.get("special_type") or "").strip()
        if special_type:
            special_mapping = QB_SPECIAL_TYPE_MAP.get(special_type)
            if special_mapping:
                return special_mapping

        qb_type = (record.get("account_type") or "").strip() or "Expense"
        return QB_ACCOUNT_TYPE_MAP.get(qb_type, ("Expense Account", "Expense"))

    # -----------------------------------------------------------------
    #  Find existing target account
    # -----------------------------------------------------------------
    def find_existing_target(self, doc_data):
        return self._find_account_by_parent(
            doc_data.get("account_name"),
            doc_data.get("company"),
            parent_name=doc_data.get("_qb_parent"),
            account_number=doc_data.get("account_number"),
        )

    # -----------------------------------------------------------------
    #  Map a QuickBooks account record to ERPNext Account doc_data
    # -----------------------------------------------------------------
    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        qb_type = (record.get("account_type") or "").strip() or "Expense"
        acct_type, root_type = self._resolve_account_mapping(record, fallback_qb_type=qb_type)

        name = record.get("name", "").strip()
        qb_parent = record.get("parent", "").strip()
        account_number = record.get("account_number")  # may be None or empty

        # ---------- Resolve Parent ----------
        parent_account = ""

        if qb_parent:
            parent_account = self._find_account(qb_parent, company)
            if not parent_account:
                parent_record = self.records_by_name.get(qb_parent)
                _, parent_root_type = self._resolve_account_mapping(
                    parent_record,
                    fallback_qb_type=qb_type,
                )
                parent_account = self._ensure_group_account(
                    qb_parent,
                    parent_root_type,
                    company,
                    parent_account=None
                )
            else:
                parent_doc = frappe.get_doc("Account", parent_account)
                if not parent_doc.is_group:
                    print(f"CORRECTION: Converting {qb_parent} to group; clearing account_type")
                    parent_doc.account_type = None
                    parent_doc.is_group = 1
                    parent_doc.flags.ignore_permissions = True
                    parent_doc.save(ignore_permissions=True)
        else:
            default_parent_name = self._default_parent_name(qb_type, root_type)
            if default_parent_name:
                parent_account = self._find_account(default_parent_name, company)
                if not parent_account:
                    parent_account = self._ensure_group_account(
                        default_parent_name,
                        root_type,
                        company,
                        parent_account=None
                    )
            else:
                parent_account = ""

        parent_account = self._avoid_self_parent(name, company, parent_account)

        # ---------- Unique name handling ----------
        # If we have an account_number, check for name conflicts.
        # If an account with the same name already exists (regardless of parent),
        # we append the account_number to make the name unique.
        final_account_name = name
        if account_number and account_number.strip():
            # Check if any account with this name already exists in the company
            existing_by_name = self._find_account(name, company)
            if existing_by_name:
                # There is already an account with this name – we need a unique name.
                # Append the account number to the name.
                final_account_name = f"{name} - {account_number}"
                print(f"  NOTE: Account name '{name}' already exists. Using '{final_account_name}' instead.")

        # ---------- Determine is_group ----------
        is_root = (parent_account == "")
        is_non_posting = (qb_type == "NonPosting")
        has_children = (name in self.parent_groups)

        is_group = 1 if (is_root or is_non_posting or has_children) else 0

        # Clear account_type for groups
        account_type = None if is_group else acct_type

        # Debug log
        print(
            f"DEBUG: QB={name}, Parent={qb_parent}, Resolved={parent_account or '(root)'}, "
            f"InParentGroups={has_children}, is_group={is_group}"
        )

        result = {
            "doctype": "Account",
            "account_name": final_account_name,   # use the possibly modified name
            "account_number": account_number,
            "account_type": account_type,
            "root_type": root_type,
            "company": company,
            "parent_account": parent_account,
            "is_group": is_group,
            "_qb_parent": qb_parent,
        }

        currency = self._get_account_currency(record, company)
        if currency:
            result["account_currency"] = currency

        return result

    # -----------------------------------------------------------------
    #  Post‑import validation and repair
    # -----------------------------------------------------------------
    def post_import_validation_and_repair(self):
        company = frappe.defaults.get_global_default("company")
        records = self.load_data()

        for record in records:
            qb_name = record.get("name", "").strip()
            qb_parent = record.get("parent", "").strip()
            qb_number = record.get("account_number")
            qb_type = (record.get("account_type") or "").strip() or "Expense"

            _, root_type = self._resolve_account_mapping(record, fallback_qb_type=qb_type)

            if not qb_name:
                continue

            child_account = self._find_account_by_parent(
                qb_name,
                company,
                parent_name=qb_parent,
                account_number=qb_number,
            )
            if not child_account:
                print(f"  ERROR: Account {qb_name} not found in ERPNext during validation.")
                continue

            child_doc = frappe.get_doc("Account", child_account)
            changed = False

            is_root = (child_doc.parent_account is None or child_doc.parent_account == "")
            is_non_posting = (qb_type == "NonPosting")
            should_be_group = is_root or is_non_posting or (qb_name in self.parent_groups)

            if should_be_group and child_doc.is_group == 0:
                print(f"CORRECTION: Converting {qb_name} to group; clearing account_type")
                child_doc.account_type = None
                child_doc.is_group = 1
                changed = True

            if qb_number is not None and child_doc.account_number != qb_number:
                child_doc.account_number = qb_number
                changed = True

            expected_currency = self._get_account_currency(record, company)
            if expected_currency and child_doc.account_currency != expected_currency:
                child_doc.account_currency = expected_currency
                changed = True

            if qb_parent:
                expected_parent = self._find_account(qb_parent, company)
                if not expected_parent:
                    parent_record = self.records_by_name.get(qb_parent)
                    _, parent_root_type = self._resolve_account_mapping(
                        parent_record,
                        fallback_qb_type=qb_type,
                    )
                    expected_parent = self._ensure_group_account(
                        qb_parent,
                        parent_root_type,
                        company,
                        parent_account=None
                    )
                    print(f"  RESOLVED missing parent {qb_parent} for {qb_name}: {expected_parent}")

                expected_parent = self._avoid_self_parent(qb_name, company, expected_parent)
                if child_doc.parent_account != expected_parent:
                    print(
                        f"  CORRECTION: Corrected parent for {qb_name} "
                        f"from {child_doc.parent_account} to {expected_parent}"
                    )
                    child_doc.parent_account = expected_parent
                    changed = True
            else:
                if not child_doc.parent_account:
                    default_parent_name = self._default_parent_name(qb_type, root_type)
                    if default_parent_name:
                        expected_parent = self._find_account(default_parent_name, company)
                        if expected_parent:
                            print(f"  CORRECTION: Setting default parent for {qb_name} to {expected_parent}")
                            child_doc.parent_account = expected_parent
                            changed = True
                        else:
                            print(f"  WARNING: Default parent {default_parent_name} not found for {qb_name}")
                else:
                    print(f"  INFO: Leaving top-level account {qb_name} parent unchanged: {child_doc.parent_account}")

            if changed:
                child_doc.flags.ignore_permissions = True
                child_doc.save(ignore_permissions=True)

        self._set_default_employee_advances_account_type(company)
        frappe.db.commit()
