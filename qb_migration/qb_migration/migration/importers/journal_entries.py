import frappe
import json
from frappe.utils import flt

from ..base_importer import BaseImporter


class JournalEntryImporter(BaseImporter):
    source_type = "QB_JOURNAL"
    target_doctype = "Journal Entry"
    json_file = "journal_entries.json"
    json_key = "journal_entries"

    def get_source_id(self, record):
        return str(record.get("txn_id") or "")

    def _resolve_account(self, qb_account_name):
        if not qb_account_name:
            return None

        company = frappe.defaults.get_global_default("company")
        leaf = qb_account_name.split(":")[-1].strip()

        account = frappe.db.get_value(
            "Account",
            {"account_name": leaf, "company": company, "is_group": 0},
            "name",
        )
        if account:
            return account

        def resolve_group_child(account_name):
            row = frappe.db.sql(
                "select name from `tabAccount` where parent_account=%s and company=%s and is_group=0 limit 1",
                (account_name, company),
            )
            return row[0][0] if row else None

        row = frappe.db.sql(
            "select name, is_group from `tabAccount` where account_name=%s and company=%s limit 1",
            (leaf, company),
        )
        if row:
            name, is_group = row[0]
            if not is_group:
                return name
            return resolve_group_child(name)

        row = frappe.db.sql(
            "select name, is_group from `tabAccount` where lower(account_name)=lower(%s) and company=%s limit 1",
            (leaf, company),
        )
        if row:
            name, is_group = row[0]
            if not is_group:
                return name
            return resolve_group_child(name)

        row = frappe.db.sql(
            "select name, is_group from `tabAccount` where lower(account_name)=lower(%s) and company=%s limit 1",
            (qb_account_name, company),
        )
        if row:
            name, is_group = row[0]
            if not is_group:
                return name
            return resolve_group_child(name)

        return None

    def _get_currency_details(self, record, company_currency):
        currency = (record.get("currency") or "").strip()
        exchange_rate = record.get("exchange_rate")

        if exchange_rate is None or exchange_rate == "":
            exchange_rate = 1.0
        elif isinstance(exchange_rate, str):
            try:
                exchange_rate = float(exchange_rate)
            except ValueError:
                exchange_rate = 1.0

        if currency and currency != company_currency:
            return currency, exchange_rate

        currencies = set()
        for line in record.get("lines", []) or []:
            account_name = line.get("account")
            if not account_name:
                continue

            erpnext_account = self._resolve_account(account_name)
            if not erpnext_account:
                continue

            account_currency = frappe.db.get_value("Account", erpnext_account, "account_currency")
            if account_currency and account_currency != company_currency:
                currencies.add(account_currency)

        if currencies:
            return next(iter(currencies)), 1.0

        return None, None

    def _get_row_currency_values(self, account, debit_val, credit_val, company_currency, transaction_currency, exchange_rate):
        account_currency = frappe.db.get_value("Account", account, "account_currency")

        if transaction_currency and transaction_currency != company_currency and exchange_rate:
            if account_currency == company_currency:
                acct_ccy_debit = debit_val * exchange_rate
                acct_ccy_credit = credit_val * exchange_rate
                base_debit = acct_ccy_debit
                base_credit = acct_ccy_credit
                row_exchange_rate = 1.0
            else:
                acct_ccy_debit = debit_val
                acct_ccy_credit = credit_val
                base_debit = debit_val * exchange_rate
                base_credit = credit_val * exchange_rate
                row_exchange_rate = exchange_rate
        else:
            acct_ccy_debit = debit_val
            acct_ccy_credit = credit_val
            base_debit = debit_val
            base_credit = credit_val
            row_exchange_rate = 1.0

        return base_debit, base_credit, acct_ccy_debit, acct_ccy_credit, row_exchange_rate

    def _resolve_party(self, entity):
        if not entity:
            return None, None
        # Prefer Customer, then Supplier, then Employee
        for doctype in ("Customer", "Supplier", "Employee"):
            if frappe.db.exists(doctype, entity):
                return doctype, entity

        # Try to create Customer or Employee if they don't exist
        # Prefer creating Customer for party-like names
        customer = self._ensure_customer(entity)
        if customer:
            return "Customer", customer

        employee = self._ensure_employee(entity)
        if employee:
            return "Employee", employee

        return None, None

    def _ensure_customer(self, customer_name):
        if not customer_name:
            return None

        if frappe.db.exists("Customer", customer_name):
            return customer_name

        try:
            from .customers import CustomerImporter

            importer = CustomerImporter()
            group = importer._get_or_create_safe_leaf_group()

            customer = frappe.get_doc(
                {
                    "doctype": "Customer",
                    "customer_name": customer_name,
                    "customer_group": group,
                    "territory": "All Territories",
                }
            )
            customer.flags.ignore_permissions = True
            customer.insert()
            frappe.db.commit()
            return customer.name
        except Exception:
            frappe.db.rollback()
            return None

    def _ensure_employee(self, employee_name):
        if frappe.db.exists("Employee", employee_name):
            return employee_name

        try:
            employee = frappe.get_doc(
                {
                    "doctype": "Employee",
                    "employee_name": employee_name,
                    "status": "Active",
                }
            )
            employee.flags.ignore_permissions = True
            employee.insert()
            frappe.db.commit()
            return employee.name
        except Exception:
            frappe.db.rollback()
            return None

    def _resolve_cost_center(self, cc_name):
        """Resolve a QuickBooks class/cost center name to an ERPNext Cost Center name.

        Returns the Cost Center `name` if found, otherwise `None`.
        """
        if not cc_name:
            return None

        company = frappe.defaults.get_global_default("company")
        leaf = cc_name.split(":")[-1].strip()

        # Try exact cost_center_name match
        name = frappe.db.get_value("Cost Center", {"cost_center_name": leaf, "company": company}, "name")
        if name:
            return name

        # Try case-insensitive match
        row = frappe.db.sql(
            "select name from `tabCost Center` where lower(cost_center_name)=lower(%s) and company=%s limit 1",
            (leaf, company),
        )
        if row:
            return row[0][0]

        # Try matching by document name (some installs store the same value in name)
        if frappe.db.exists("Cost Center", leaf):
            return leaf

        # Try full name case-insensitive
        row = frappe.db.sql(
            "select name from `tabCost Center` where lower(cost_center_name)=lower(%s) and company=%s limit 1",
            (cc_name, company),
        )
        if row:
            return row[0][0]

        return None

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        company_currency = frappe.db.get_value("Company", company, "default_currency")
        accounts = []

        currency, exchange_rate = self._get_currency_details(record, company_currency)

        is_foreign_currency = bool(currency and currency != company_currency)
        is_multi_currency = is_foreign_currency and bool(exchange_rate)

        for line in record.get("lines", []):
            acct_name = line.get("account", "")
            erpnext_account = self._resolve_account(acct_name)
            if not erpnext_account:
                raise ValueError(f"Account not found: {acct_name}")

            # Get the actual currency of this account
            account_currency = frappe.db.get_value("Account", erpnext_account, "account_currency")
            amount = line.get("amount", 0) or 0
            line_type = (line.get("line_type") or "").strip().lower()

            if line_type == "debit":
                debit_val = amount
                credit_val = 0
            elif line_type == "credit":
                debit_val = 0
                credit_val = amount
            else:
                debit_val = line.get("debit", 0) or 0
                credit_val = line.get("credit", 0) or 0

            base_debit, base_credit, acct_ccy_debit, acct_ccy_credit, row_exchange_rate = self._get_row_currency_values(
                erpnext_account,
                debit_val,
                credit_val,
                company_currency,
                currency,
                exchange_rate,
            )

            party_type = party = None
            account_type = frappe.db.get_value("Account", erpnext_account, "account_type")
            if account_type in ("Receivable", "Payable"):
                # Try common fields for party/entity, falling back to record payee
                candidate = (
                    line.get("entity")
                    or line.get("entity_id")
                    or line.get("customer")
                    or line.get("customer_name")
                    or line.get("party")
                    or record.get("payee")
                    or record.get("entity")
                    or record.get("entity_id")
                )
                # If the account suggests employee advances, prefer/reserve Employee party first
                acct_name_lower = (erpnext_account or "").lower()
                if candidate and ("employee" in acct_name_lower or "advance" in acct_name_lower):
                    emp = self._ensure_employee(candidate)
                    if emp:
                        party_type, party = "Employee", emp
                    else:
                        party_type, party = self._resolve_party(candidate)
                else:
                    party_type, party = self._resolve_party(candidate)

            cost_center = None
            for key in ("class", "cost_center", "cost_center_name"):
                raw_value = line.get(key)
                if raw_value:
                    cost_center = self._resolve_cost_center(raw_value)
                    if cost_center:
                        break

            row_data = {
                "account": erpnext_account,
                "debit": base_debit,
                "credit": base_credit,
                "debit_in_account_currency": acct_ccy_debit,
                "credit_in_account_currency": acct_ccy_credit,
                "exchange_rate": row_exchange_rate,
                "user_remark": line.get("memo", ""),
            }
            if cost_center:
                row_data["cost_center"] = cost_center
            if party_type and party:
                row_data["party_type"] = party_type
                row_data["party"] = party

            accounts.append(row_data)

        remark_parts = []
        if record.get("memo"):
            remark_parts.append(str(record.get("memo")))
        if record.get("txn_number"):
            remark_parts.append(f"QB Ref: {record.get('txn_number')}")
        if record.get("ref_no"):
            remark_parts.append(f"Ref No: {record.get('ref_no')}")
        if record.get("is_adjustment"):
            remark_parts.append("Adjustment Entry")

        user_remark = " | ".join([part for part in remark_parts if part])

        doc = {
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "posting_date": self.normalize_date(record.get("date") or record.get("txn_date")),
            "company": company,
            "user_remark": user_remark or record.get("memo", ""),
            "accounts": accounts,
        }

        if is_multi_currency:
            doc["multi_currency"] = 1
            doc["currency"] = currency
            doc["exchange_rate"] = exchange_rate

        return doc


