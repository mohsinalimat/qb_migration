import frappe
from frappe.utils import flt

from .journal_entries import JournalEntryImporter


def build_deposit_cash_back_journal_entry(
    record,
    company,
    bank_account,
    petty_cash_account,
    posting_date,
    currency=None,
    exchange_rate=None,
    source_id=None,
    line_rows=None,
):
    cash_back = record.get("cash_back") or {}
    lines = line_rows if line_rows is not None else (record.get("lines") or [])

    accounts = []
    deposit_total = flt(record.get("deposit_total", 0))
    if deposit_total > 0:
        accounts.append(
            {
                "account": bank_account,
                "debit": deposit_total,
                "credit": 0,
                "debit_in_account_currency": deposit_total,
                "credit_in_account_currency": 0,
                "exchange_rate": 1,
                "user_remark": record.get("memo") or "Deposit to bank",
            }
        )

    cash_back_amount = flt(cash_back.get("amount", 0))
    if cash_back_amount > 0:
        accounts.append(
            {
                "account": petty_cash_account,
                "debit": cash_back_amount,
                "credit": 0,
                "debit_in_account_currency": cash_back_amount,
                "credit_in_account_currency": 0,
                "exchange_rate": 1,
                "user_remark": cash_back.get("memo") or "Cash back",
            }
        )

    for line in lines:
        amount = flt(line.get("amount", 0))
        if amount <= 0:
            continue

        row = {
            "account": line.get("account"),
            "debit": 0,
            "credit": amount,
            "debit_in_account_currency": 0,
            "credit_in_account_currency": amount,
            "exchange_rate": 1,
            "user_remark": line.get("memo") or line.get("description") or "",
        }
        # Preserve any resolved `project` coming from the importer
        if line.get("project"):
            row["project"] = line.get("project")

        cost_center_name = line.get("cost_center")
        if cost_center_name:
            row["cost_center"] = cost_center_name

        if line.get("party_type") and line.get("party"):
            row["party_type"] = line["party_type"]
            row["party"] = line["party"]

        if row.get("account"):
            accounts.append(row)

    if not accounts:
        return None

    doc = {
        "doctype": "Journal Entry",
        "voucher_type": "Journal Entry",
        "company": company,
        "posting_date": posting_date,
        "user_remark": record.get("memo") or "Deposit cash back",
        "accounts": accounts,
    }

    if source_id:
        doc["_source_id"] = source_id

    if currency and exchange_rate:
        doc["multi_currency"] = 1
        doc["currency"] = currency
        doc["exchange_rate"] = exchange_rate

    return doc


class DepositCashBackImporter(JournalEntryImporter):
    source_type = "QB_DEPOSIT_CASH_BACK"
    target_doctype = "Journal Entry"
    json_file = "deposits_cash_back.json"
    json_key = "deposits"

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("txn_number") or "")

    def _resolve_project(self, project_name):
        if not project_name:
            return None

        project_name = str(project_name).strip()
        if not project_name:
            return None

        project = frappe.db.get_value("Project", {"project_name": project_name}, "name")
        if project:
            return project

        result = frappe.db.sql(
            "select name from `tabProject` where lower(project_name)=lower(%s) limit 1",
            (project_name,),
        )
        return result[0][0] if result else None

    def _resolve_project_from_entity(self, entity):
        if not entity:
            return None

        entity_value = str(entity).strip()
        if ":" not in entity_value:
            return None

        project_name = entity_value.split(":")[-1].strip()
        if not project_name:
            return None

        return self._resolve_project(project_name)

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        if not company:
            raise ValueError("Company not configured for migration")

        company_currency = frappe.db.get_value("Company", company, "default_currency")
        currency, exchange_rate = self._get_currency_details(record, company_currency)

        bank_account = self._resolve_account(record.get("deposit_to_acct"))
        if not bank_account:
            raise ValueError(
                f"Bank account not found for deposit_to_acct={record.get('deposit_to_acct')}"
            )

        cash_back = record.get("cash_back") or {}
        petty_cash_account = None
        if flt(cash_back.get("amount", 0)) > 0:
            petty_cash_account = self._resolve_account(cash_back.get("account"))
            if not petty_cash_account:
                raise ValueError(
                    f"Petty cash account not found for cash_back={cash_back.get('account')}"
                )

        posting_date = self.normalize_date(record.get("date") or record.get("txn_date"))
        source_id = self.get_source_id(record)
        record_project = self._resolve_project(record.get("project_name"))
        line_rows = []

        for line in record.get("lines") or []:
            account = self._resolve_account(line.get("account"))
            if not account:
                raise ValueError(f"Account not found for deposit line: {line.get('account')}")

            resolved_line = {
                "account": account,
                "amount": flt(line.get("amount", 0)),
                "memo": line.get("memo") or line.get("description") or "",
                "cost_center": self._resolve_cost_center(
                    line.get("class_name") or line.get("class") or line.get("cost_center")
                ),
            }

            account_type = frappe.db.get_value("Account", account, "account_type")
            candidate = line.get("entity") or line.get("entity_id") or line.get("party")
            if account_type in ("Receivable", "Payable") and candidate:
                party_type, party = self._resolve_party(candidate)
                if party_type and party:
                    resolved_line["party_type"] = party_type
                    resolved_line["party"] = party

            project = self._resolve_project(
                line.get("project_name")
                or line.get("project")
                or line.get("job")
            ) or record_project
            if not project:
                project = self._resolve_project_from_entity(
                    line.get("entity")
                    or line.get("entity_id")
                    or line.get("entity_name")
                    or line.get("customer")
                    or line.get("customer_name")
                    or line.get("party")
                    or record.get("payee")
                )
            if project:
                resolved_line["project"] = project

            line_rows.append(resolved_line)

        return build_deposit_cash_back_journal_entry(
            record,
            company=company,
            bank_account=bank_account,
            petty_cash_account=petty_cash_account or bank_account,
            posting_date=posting_date,
            currency=currency,
            exchange_rate=exchange_rate,
            source_id=source_id,
            line_rows=line_rows,
        )
