import frappe

from .journal_entries import JournalEntryImporter


class CCChargesImporter(JournalEntryImporter):
    """Map QuickBooks credit-card charges to an ERPNext Journal Entry.

    This follows the recommended Scenario 2 pattern for CC liability tracking:
    - debit expense account(s) from each line
    - credit the credit-card liability account
    - use the QuickBooks transaction date as the posting date
    """
    source_type = "QB_CC_CHARGE"
    target_doctype = "Journal Entry"
    json_file = "cc_charges.json"
    json_key = "cc_charges"

    def get_source_id(self, record):
        return str(record.get("txn_id") or "")

    def _ensure_fallback_expense_account(self, line, company):
        candidate_name = (line.get("item") or line.get("description") or "CC Charge Expense").strip()
        if not candidate_name:
            candidate_name = "CC Charge Expense"

        account_name = candidate_name.split(":")[-1].strip() or "CC Charge Expense"
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

    def _ensure_credit_card_liability_account(self, qb_account_name, company):
        candidate_name = (qb_account_name or "Credit Card Liability").strip()
        if not candidate_name:
            candidate_name = "Credit Card Liability"

        account_name = candidate_name.split(":")[-1].strip() or "Credit Card Liability"
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
            "parent_account": parent_account,
            "root_type": "Liability",
            "is_group": 0,
        })
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name

    def _resolve_line_account(self, line, record):
        account_name = line.get("account")
        if account_name:
            resolved = self._resolve_account(account_name)
            if resolved:
                return resolved

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

        fallback = record.get("account") or record.get("expense_account")
        if fallback:
            resolved = self._resolve_account(fallback)
            if resolved:
                return resolved

        company = frappe.defaults.get_global_default("company")
        return self._ensure_fallback_expense_account(line, company)

    def find_existing_target(self, doc_data):
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

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        debit_total = 0.0
        accounts = []

        for line in record.get("lines") or []:
            account = self._resolve_line_account(line, record)
            if not account:
                raise ValueError(f"Account not found for line: {line.get('account') or line.get('item') or line.get('description')}")

            amount = float(line.get("amount") or 0)
            if amount <= 0:
                continue

            debit_total += amount
            row = {
                "account": account,
                "debit_in_account_currency": amount,
                "credit_in_account_currency": 0,
                "debit": amount,
                "credit": 0,
                "exchange_rate": 1,
                "user_remark": line.get("description") or record.get("memo") or record.get("ref_no") or "",
            }

            cost_center = self._resolve_cost_center(line.get("class_name"))
            if cost_center:
                row["cost_center"] = cost_center

            accounts.append(row)

        if debit_total <= 0:
            return {"_skip": True, "_skip_reason": "ZERO_AMOUNT", "ref_no": record.get("txn_id", "")}

        credit_account = self._resolve_account(record.get("cc_account"))
        if not credit_account:
            credit_account = self._ensure_credit_card_liability_account(record.get("cc_account"), company)

        if not credit_account:
            raise ValueError(f"Credit card account not found: {record.get('cc_account')}")

        accounts.append({
            "account": credit_account,
            "debit_in_account_currency": 0,
            "credit_in_account_currency": debit_total,
            "debit": 0,
            "credit": debit_total,
            "exchange_rate": 1,
            "user_remark": record.get("memo") or record.get("ref_no") or "",
        })

        posting_date = self.normalize_date(record.get("date"))
        doc = {
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "company": company,
            "posting_date": posting_date,
            "cheque_no": record.get("txn_id") or "",
            "reference_no": record.get("ref_no") or "",
            "reference_date": posting_date,
            "cheque_date": posting_date,
            "user_remark": record.get("memo") or record.get("ref_no") or record.get("payee") or "",
            "accounts": accounts,
        }

        return doc
