import frappe
import json
from frappe.utils import flt

from .journal_entries import JournalEntryImporter


class ChecksImporter(JournalEntryImporter):
    """
    Map QuickBooks expense checks (checks.json) to ERPNext Journal Entries.
    Each check becomes one Bank Entry – debit expense lines, credit the bank account.
    """
    source_type = "QB_CHECK"
    target_doctype = "Journal Entry"
    json_file = "checks.json"
    json_key = "checks"

    def get_source_id(self, record):
        return str(record.get("txn_id") or "")

    def _ensure_fallback_expense_account(self, line, company):
        candidate_name = (line.get("item") or line.get("description") or "Check Expense").strip()
        if not candidate_name:
            candidate_name = "Check Expense"

        account_name = candidate_name.split(":")[-1].strip() or "Check Expense"
        existing = frappe.db.get_value("Account", {"company": company, "account_name": account_name}, "name")
        if existing:
            return existing

        parent_account = frappe.db.get_value(
            "Account",
            {"company": company, "account_name": "Expenses"},
            "name",
        )
        if not parent_account:
            parent_account = frappe.db.get_value(
                "Account",
                {"company": company, "root_type": "Expense", "is_group": 1},
                "name",
            )

        doc = frappe.get_doc({
            "doctype": "Account",
            "account_name": account_name,
            "company": company,
            "parent_account": parent_account,
            "root_type": "Expense",
            "is_group": 0,
        })
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name

    def _resolve_line_account(self, line, record):
        # Prefer explicit account on the line
        account_name = line.get("account")
        if account_name:
            resolved = self._resolve_account(account_name)
            if resolved:
                return resolved

        # If it's an item line, try to use the item's default expense account
        item_name = line.get("item") or line.get("item_name")
        if item_name:
            company = frappe.defaults.get_global_default("company")
            item_doc = frappe.db.get_value("Item", {"item_code": item_name}, "name")
            if item_doc:
                expense_account = frappe.db.get_value(
                    "Item Default",
                    {"parent": item_doc, "company": company},
                    "expense_account",
                )
                if expense_account:
                    return expense_account

                item = frappe.get_doc("Item", item_doc)
                for default in item.get("item_defaults") or []:
                    if default.get("company") == company and default.get("expense_account"):
                        return default.get("expense_account")

        # Fallback to record-level account fields
        fallback = record.get("account") or record.get("expense_account")
        if fallback:
            resolved = self._resolve_account(fallback)
            if resolved:
                return resolved

        # As a last resort, create/ensure a generic expense account
        company = frappe.defaults.get_global_default("company")
        return self._ensure_fallback_expense_account(line, company)

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")

        # Bank account to credit
        bank_account = self._resolve_account(record.get("bank_account"))
        if not bank_account:
            raise ValueError(
                f"Bank account not found: {record.get('bank_account')} "
                f"(check {record.get('ref_no')})"
            )

        posting_date = self.normalize_date(record.get("date"))
        cheque_no = (record.get("ref_no") or record.get("ref_number", ""))
        # Treat obvious non-numeric placeholders as empty (e.g. 'DRAFT')
        if isinstance(cheque_no, str) and cheque_no.strip().upper() == "DRAFT":
            cheque_no = ""
        accounts = []
        total = 0.0

        for line in record.get("lines") or []:
            account = self._resolve_line_account(line, record)
            if not account:
                continue

            amount = flt(line.get("amount", 0))
            if not amount:
                continue

            row = {
                "account": account,
                "debit_in_account_currency": amount,
                "credit_in_account_currency": 0,
                "debit": amount,
                "credit": 0,
                "exchange_rate": 1,
                "user_remark": line.get("memo") or line.get("description", ""),
            }

            # Resolve cost center (QuickBooks 'class_name') if present
            cost_center = self._resolve_cost_center(line.get("class_name"))
            if cost_center:
                row["cost_center"] = cost_center

            # If account is Receivable/Payable, resolve party
            account_type = frappe.db.get_value("Account", account, "account_type")
            if account_type in ("Receivable", "Payable"):
                candidate = (
                    line.get("entity")
                    or line.get("customer")
                    or line.get("customer_name")
                    or line.get("party")
                    or record.get("payee")
                )
                acct_name_lower = (account or "").lower()
                if candidate and "employee advances" in acct_name_lower:
                    emp = self._ensure_employee(candidate)
                    if emp:
                        party_type, party = "Employee", emp
                    else:
                        party_type, party = None, None
                elif candidate and ("employee" in acct_name_lower or "advance" in acct_name_lower):
                    emp = self._ensure_employee(candidate)
                    if emp:
                        party_type, party = "Employee", emp
                    else:
                        party_type, party = self._resolve_party(candidate)
                else:
                    party_type, party = self._resolve_party(candidate)
                if party_type and party:
                    row["party_type"] = party_type
                    row["party"] = party

            accounts.append(row)
            total += amount

        if total <= 0:
            return {"_skip": True, "_skip_reason": "ZERO_AMOUNT", "ref_no": cheque_no}

        # Single credit to the bank
        accounts.append({
            "account": bank_account,
            "debit_in_account_currency": 0,
            "credit_in_account_currency": total,
            "debit": 0,
            "credit": total,
            "exchange_rate": 1,
            "user_remark": record.get("memo") or f"Payee: {record.get('payee', '')}",
        })

        # Ensure Bank Entry has reference_no & reference_date (some setups require it)
        reference_no = cheque_no or record.get("txn_id") or record.get("ref_no") or ""
        cheque_no_field = cheque_no or reference_no
        doc = {
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "company": company,
            "posting_date": posting_date,
            "cheque_no": cheque_no_field,
            "reference_no": reference_no,
            "reference_date": posting_date,
            "cheque_date": posting_date,
            "user_remark": record.get("memo") or f"Check to {record.get('payee', '')}",
            "accounts": accounts,
        }

        return doc

    def find_existing_target(self, doc_data):
        """Avoid duplicates by looking up existing Journal Entry with same cheque no & date."""
        if doc_data.get("cheque_no"):
            return frappe.db.get_value(
                "Journal Entry",
                {
                    "cheque_no": doc_data["cheque_no"],
                    "company": doc_data.get("company"),
                    "posting_date": doc_data.get("posting_date"),
                },
                "name",
            )
        return None