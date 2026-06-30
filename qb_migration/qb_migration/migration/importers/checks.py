import frappe
import json
from frappe.utils import flt

from ..base_importer import BaseImporter


class ChecksImporter(BaseImporter):
    """
    Map QuickBooks expense checks (checks.json) to ERPNext Journal Entries.
    Each check becomes one Bank Entry – debit expense lines, credit the bank account.
    """
    source_type = "QB_CHECK"
    target_doctype = "Payment Entry"
    json_file = "checks.json"
    json_key = "checks"

    def get_source_id(self, record):
        return str(record.get("txn_id") or "")

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        company_currency = frappe.db.get_value("Company", company, "default_currency")
        currency, exchange_rate = self._get_currency_details(record, company_currency)
        is_multi_currency = bool(currency and currency != company_currency and exchange_rate)

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
            account = self._resolve_account(line.get("account"))
            if not account:
                continue

            amount = flt(line.get("amount", 0))
            if not amount:
                continue

            base_debit, base_credit, acct_ccy_debit, acct_ccy_credit, row_exchange_rate = self._get_row_currency_values(
                account,
                amount,
                0,
                company_currency,
                currency,
                exchange_rate,
            )

            row = {
                "account": account,
                "debit_in_account_currency": acct_ccy_debit,
                "credit_in_account_currency": acct_ccy_credit,
                "debit": base_debit,
                "credit": base_credit,
                "exchange_rate": row_exchange_rate,
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
                if candidate and ("employee" in acct_name_lower or "advance" in acct_name_lower):
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

        base_debit, base_credit, acct_ccy_debit, acct_ccy_credit, row_exchange_rate = self._get_row_currency_values(
            bank_account,
            0,
            total,
            company_currency,
            currency,
            exchange_rate,
        )

        # Single credit to the bank
        accounts.append({
            "account": bank_account,
            "debit_in_account_currency": acct_ccy_debit,
            "credit_in_account_currency": acct_ccy_credit,
            "debit": base_debit,
            "credit": base_credit,
            "exchange_rate": row_exchange_rate,
            "user_remark": record.get("memo") or f"Payee: {record.get('payee', '')}",
        })

        # Ensure Bank Entry has reference_no & reference_date (some setups require it)
        reference_no = cheque_no or record.get("txn_id") or record.get("ref_no") or ""
        cheque_no_field = cheque_no or reference_no
        doc = {
            "doctype": "Payment Entry",
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

        if is_multi_currency:
            doc["multi_currency"] = 1
            doc["currency"] = currency
            doc["exchange_rate"] = exchange_rate

        # Debug helper: print generated doc for a failing txn so we can inspect party/account mapping
        if str(record.get("txn_id")) == "DD3-933784469":
            try:
                print("DEBUG_GENERATED_DOC for DD3-933784469:")
                print(json.dumps(doc, default=str))
            except Exception:
                print("DEBUG: failed to dump doc for", record.get("txn_id"))

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