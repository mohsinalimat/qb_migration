import frappe
from frappe.utils import flt

from ..base_importer import BaseImporter
from .journal_entries import JournalEntryImporter


class DepositImporter(JournalEntryImporter):
    source_type = "QB_DEPOSIT"
    target_doctype = "Payment Entry"
    json_file = "deposits.json"
    json_key = "deposits"

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("txn_number") or "")

    def _resolve_customer(self, entity_name):
        if not entity_name:
            return None

        candidate = str(entity_name).strip()
        if frappe.db.exists("Customer", candidate):
            return candidate

        simple_name = candidate.split(":")[0].strip()
        if frappe.db.exists("Customer", simple_name):
            return simple_name

        row = frappe.db.sql(
            "select name from `tabCustomer` where lower(customer_name)=lower(%s) limit 1",
            (candidate,),
        )
        if row:
            return row[0][0]

        row = frappe.db.sql(
            "select name from `tabCustomer` where lower(customer_name)=lower(%s) limit 1",
            (simple_name,),
        )
        if row:
            return row[0][0]

        return None

    def _resolve_receivable_account(self, currency=None):
        company = frappe.defaults.get_global_default("company")
        filters = {
            "account_type": "Receivable",
            "root_type": "Asset",
            "company": company,
            "is_group": 0,
        }
        if currency:
            filters["account_currency"] = currency
            account = frappe.db.get_value("Account", filters, "name")
            if account:
                return account
            filters.pop("account_currency", None)

        return frappe.db.get_value("Account", filters, "name")

    def _resolve_mode_of_payment(self, mode):
        if mode:
            existing = frappe.db.get_value("Mode of Payment", {"mode_of_payment": mode}, "name")
            if existing:
                return existing

        existing = frappe.db.get_value("Mode of Payment", {}, "name")
        if existing:
            return existing

        mode_name = mode or "Bank"
        doc = frappe.get_doc({
            "doctype": "Mode of Payment",
            "mode_of_payment": mode_name,
        })
        doc.flags.ignore_permissions = True
        doc.insert()
        frappe.db.commit()
        return doc.name

    def _is_payment_line(self, line):
        txn_type = (line.get("txn_type") or "").strip()
        if txn_type == "ReceivePayment":
            return True
        if txn_type == "Invoice":
            return True
        if txn_type == "Deposit" and line.get("account") == "Undeposited Funds":
            return True
        return False

    def _build_payment_entries(self, record):
        company = frappe.defaults.get_global_default("company")
        bank_account = self._resolve_account(record.get("deposit_to_acct"))
        if not bank_account:
            raise ValueError(
                f"Bank account not found for deposit_to_acct={record.get('deposit_to_acct')}"
            )

        payment_entries = []
        for line in (record.get("lines") or []):
            if not self._is_payment_line(line):
                continue

            amount = flt(line.get("amount", 0))
            if amount <= 0:
                continue

            party = self._resolve_customer(line.get("entity"))
            if not party:
                party = self._ensure_customer(line.get("entity"))
            if not party:
                payment_entries.append({
                    "_skip": True,
                    "_skip_reason": "CUSTOMER_NOT_FOUND",
                    "ref_no": record.get("txn_number"),
                    "_source_id": line.get("txn_line_id") or line.get("txn_id") or record.get("txn_id"),
                })
                continue

            currency = record.get("currency")
            party_account = self._resolve_receivable_account(currency)
            if not party_account:
                party_account = self._resolve_receivable_account()
            if not party_account:
                raise ValueError("Could not resolve receivable account for deposit payment")

            payment_method = line.get("payment_method") or record.get("payment_method") or "Bank"
            posting_date = self.normalize_date(record.get("date") or record.get("txn_date"))
            reference_no = line.get("payment_txn_line_id") or line.get("txn_id") or line.get("txn_line_id") or record.get("txn_number") or ""

            payment_entries.append({
                "doctype": "Payment Entry",
                "payment_type": "Receive",
                "company": company,
                "posting_date": posting_date,
                "reference_no": reference_no,
                "reference_date": posting_date,
                "party_type": "Customer",
                "party": party,
                "party_account": party_account,
                "paid_from": party_account,
                "paid_to": bank_account,
                "mode_of_payment": self._resolve_mode_of_payment(payment_method),
                "paid_amount": amount,
                "received_amount": amount,
                "_source_id": line.get("txn_line_id") or line.get("txn_id") or reference_no,
            })

        return payment_entries

    def _build_cash_back_journal_entry(self, record):
        cash_back = record.get("cash_back") or {}
        amount = flt(cash_back.get("amount", 0))
        if amount <= 0:
            return None

        company = frappe.defaults.get_global_default("company")
        bank_account = self._resolve_account(record.get("deposit_to_acct"))
        if not bank_account:
            raise ValueError(
                f"Bank account not found for deposit_to_acct={record.get('deposit_to_acct')}"
            )

        petty_cash_account = self._resolve_account(cash_back.get("account"))
        if not petty_cash_account:
            raise ValueError(
                f"Petty cash account not found for cash_back={cash_back.get('account')}"
            )

        posting_date = self.normalize_date(record.get("date") or record.get("txn_date"))
        currency = record.get("currency")
        exchange_rate = record.get("exchange_rate")
        base_source_id = record.get("txn_id") or record.get("txn_number") or ""
        source_id = f"{base_source_id}:cashback" if base_source_id else "cashback"

        doc = {
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "company": company,
            "posting_date": posting_date,
            "cheque_no": f"{record.get('txn_number') or record.get('txn_id') or ''}-CB",
            "reference_no": f"{record.get('txn_number') or record.get('txn_id') or ''}-CB",
            "reference_date": posting_date,
            "cheque_date": posting_date,
            "user_remark": f"Cash back for {record.get('memo') or 'deposit'}",
            "accounts": [
                {
                    "account": petty_cash_account,
                    "debit_in_account_currency": amount,
                    "credit_in_account_currency": 0,
                    "debit": amount,
                    "credit": 0,
                    "exchange_rate": 1,
                    "user_remark": cash_back.get("memo") or "Cash back",
                },
                {
                    "account": bank_account,
                    "debit_in_account_currency": 0,
                    "credit_in_account_currency": amount,
                    "debit": 0,
                    "credit": amount,
                    "exchange_rate": 1,
                    "user_remark": cash_back.get("memo") or "Cash back",
                },
            ],
            "_source_id": source_id,
        }

        if currency and exchange_rate:
            doc["multi_currency"] = 1
            doc["currency"] = currency
            doc["exchange_rate"] = exchange_rate

        return doc

    def _build_deposit_journal_entry(self, record):
        deposit_lines = [
            line for line in (record.get("lines") or [])
            if line.get("txn_type") == "Deposit" and flt(line.get("amount", 0)) > 0
            and line.get("account") != "Undeposited Funds"
        ]
        if not deposit_lines:
            return None

        company = frappe.defaults.get_global_default("company")
        bank_account = self._resolve_account(record.get("deposit_to_acct"))
        if not bank_account:
            raise ValueError(
                f"Bank account not found for deposit_to_acct={record.get('deposit_to_acct')}"
            )

        posting_date = self.normalize_date(record.get("date") or record.get("txn_date"))
        currency = record.get("currency")
        exchange_rate = record.get("exchange_rate")
        total_amount = 0.0
        accounts = []

        for line in deposit_lines:
            account = self._resolve_account(line.get("account"))
            if not account:
                raise ValueError(f"Account not found for deposit line: {line.get('account')}")

            amount = flt(line.get("amount", 0))
            if amount <= 0:
                continue

            row = {
                "account": account,
                "debit_in_account_currency": 0,
                "credit_in_account_currency": amount,
                "debit": 0,
                "credit": amount,
                "exchange_rate": 1,
                "user_remark": line.get("memo") or line.get("description", ""),
            }

            cost_center = self._resolve_cost_center(line.get("class_name"))
            if cost_center:
                row["cost_center"] = cost_center

            account_type = frappe.db.get_value("Account", account, "account_type")
            if account_type in ("Receivable", "Payable"):
                candidate = line.get("entity") or record.get("memo")
                party_type, party = self._resolve_party(candidate)
                if party_type and party:
                    row["party_type"] = party_type
                    row["party"] = party

            accounts.append(row)
            total_amount += amount

        if total_amount <= 0:
            return None

        accounts.append({
            "account": bank_account,
            "debit_in_account_currency": total_amount,
            "credit_in_account_currency": 0,
            "debit": total_amount,
            "credit": 0,
            "exchange_rate": 1,
            "user_remark": record.get("memo") or "Deposit to bank",
        })

        doc = {
            "doctype": "Journal Entry",
            "voucher_type": "Bank Entry",
            "company": company,
            "posting_date": posting_date,
            "cheque_no": record.get("txn_number") or record.get("txn_id") or "",
            "reference_no": record.get("txn_number") or record.get("txn_id") or "",
            "reference_date": posting_date,
            "cheque_date": posting_date,
            "user_remark": record.get("memo") or "",
            "accounts": accounts,
            "_source_id": record.get("txn_id") or record.get("txn_number") or "",
        }

        if currency and exchange_rate:
            doc["multi_currency"] = 1
            doc["currency"] = currency
            doc["exchange_rate"] = exchange_rate

        return doc

    def map_record(self, record):
        docs = []

        payment_entries = self._build_payment_entries(record)
        if payment_entries:
            docs.extend(payment_entries)

        deposit_journal = self._build_deposit_journal_entry(record)
        if deposit_journal:
            docs.append(deposit_journal)

        cash_back_journal = self._build_cash_back_journal_entry(record)
        if cash_back_journal:
            docs.append(cash_back_journal)

        if docs:
            return docs

        if flt(record.get("deposit_total", 0)) <= 0:
            return {"_skip": True, "_skip_reason": "ZERO_AMOUNT", "ref_no": record.get("txn_number")}

        return {"_skip": True, "_skip_reason": "NO_PAYMENT_LINES", "ref_no": record.get("txn_number")}

    def run(self, dry_run: bool = False):
        records = self.load_data()
        total = len(records)
        success = failed = skipped = 0

        print(f"\n[{self.source_type}] Starting: {total} records")

        for i, record in enumerate(records):
            source_id = self.get_source_id(record)
            try:
                doc_data = self.map_record(record)
                if doc_data is None:
                    skipped += 1
                    continue

                docs = doc_data if isinstance(doc_data, list) else [doc_data]
                for doc in docs:
                    if isinstance(doc, dict) and doc.get("_skip"):
                        skipped += 1
                        reason = doc.get("_skip_reason", "SKIPPED")
                        ref_no = doc.get("ref_no") or record.get("ref_no") or record.get("txn_number")
                        source_id_line = doc.get("_source_id") or source_id
                        print(f"  SKIP [{source_id_line}] ref_no={ref_no or 'N/A'} reason={reason}")
                        self.log_skip(source_id_line, reason, ref_no)
                        continue

                    source_id_line = doc.pop("_source_id", source_id)
                    if not source_id_line:
                        failed += 1
                        print(f"  FAIL: missing source id for record {i+1}")
                        continue

                    if self.is_imported(source_id_line):
                        skipped += 1
                        continue

                    if dry_run:
                        print(f"  DRY RUN: {source_id_line} → {doc.get('name', doc.get('party', '?'))}")
                        success += 1
                        continue

                    existing_target = self.find_existing_target(doc)
                    if existing_target:
                        print(f"  SUCCESS: {source_id_line} → {existing_target}")
                        self.log_success(source_id_line, existing_target, doc.get("doctype", self.target_doctype))
                        success += 1
                        continue

                    payment = frappe.get_doc(doc)
                    payment.flags.ignore_permissions = True
                    payment.flags.ignore_mandatory = False
                    payment.insert()

                    if hasattr(self, "post_insert"):
                        self.post_insert(payment, record)

                    if self.target_doctype in (
                        "Purchase Invoice",
                        "Sales Invoice",
                        "Payment Entry",
                        "Journal Entry",
                    ):
                        payment.submit()

                    frappe.db.commit()
                    self.log_success(source_id_line, payment.name, payment.doctype)
                    success += 1

            except Exception as exc:
                frappe.db.rollback()
                self.log_failure(source_id, str(exc))
                failed += 1
                print(f"  FAIL [{source_id}]: {exc}")

            if (i + 1) % self.batch_size == 0:
                frappe.db.commit()
                print(f"  Progress: {i + 1}/{total}")

        frappe.db.commit()
        print(f"[{self.source_type}] Done — Success: {success}, Failed: {failed}, Skipped: {skipped}")
        return {"success": success, "failed": failed, "skipped": skipped}

    def find_existing_target(self, doc_data):
        doc_type = doc_data.get("doctype", self.target_doctype)

        if doc_type == "Journal Entry":
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

        if doc_data.get("reference_no"):
            return frappe.db.get_value(
                doc_type,
                {
                    "reference_no": doc_data["reference_no"],
                    "company": doc_data.get("company"),
                    "posting_date": doc_data.get("posting_date"),
                },
                "name",
            )
        return None
