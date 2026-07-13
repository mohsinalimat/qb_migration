import json

import frappe
from frappe.utils import flt

from ..base_importer import DATA_DIR
from .journal_entries import JournalEntryImporter


class DepositImporter(JournalEntryImporter):
    source_type = "QB_DEPOSIT"
    target_doctype = "Payment Entry"
    json_file = "deposits.json"
    json_key = "deposits"
    _cash_back_source_ids = None

    @classmethod
    def _get_deposit_cash_back_source_ids(cls):
        if cls._cash_back_source_ids is None:
            cls._cash_back_source_ids = set()
            path = DATA_DIR / "deposits_cash_back.json"
            if path.exists():
                try:
                    with path.open(encoding="utf-8") as handle:
                        data = json.load(handle)
                    for record in data.get("deposits", []) or []:
                        source_id = str(record.get("txn_id") or record.get("txn_number") or "")
                        if source_id:
                            cls._cash_back_source_ids.add(source_id)
                except Exception:
                    cls._cash_back_source_ids = set()
        return cls._cash_back_source_ids

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

    def _resolve_payment_reference(self, line, record):
        return (
            line.get("check_number")
            or line.get("payment_txn_line_id")
            or line.get("txn_id")
            or line.get("txn_line_id")
            or record.get("txn_number")
            or ""
        )

    def _resolve_payment_type(self, party_type, line):
        if party_type == "Supplier":
            return "Pay"
        return "Receive"

    def _resolve_party_account(self, party_type, currency=None):
        if party_type == "Supplier":
            return self._resolve_payable_account(currency)
        return self._resolve_receivable_account(currency)

    def _resolve_payable_account(self, currency=None):
        company = frappe.defaults.get_global_default("company")
        filters = {
            "account_type": "Payable",
            "root_type": "Liability",
            "company": company,
            "is_group": 0,
        }
        if currency:
            filters["account_currency"] = currency
            account = frappe.db.get_value("Account", filters, "name")
            if account:
                return account
            filters.pop("account_currency", None)

        account = frappe.db.get_value("Account", filters, "name")
        if account:
            return account

        query = "select name from `tabAccount` where account_type='Payable' and root_type='Liability' and company=%s and is_group=0"
        params = [company]
        if currency:
            query += " and account_currency=%s"
            params.append(currency)
        query += " limit 1"
        row = frappe.db.sql(query, tuple(params))
        return row[0][0] if row else None

    def _is_payment_line(self, line):
        txn_type = (line.get("txn_type") or "").strip()
        if txn_type == "ReceivePayment":
            return True
        if txn_type == "Invoice":
            return True
        # Some deposits reference payment lines using txn_type "Deposit".
        # Treat Deposit lines as payment lines only when they represent an
        # actual payment (Undeposited Funds or a referenced payment). If the
        # deposit line posts to a Profit & Loss (Income/Expense) account
        # (e.g. Interest Income) prefer creating a Journal Entry instead
        # (so cost center requirements are handled there).
        if txn_type == "Deposit":
            # Undeposited funds always indicate a payment line
            if line.get("account") == "Undeposited Funds":
                return True

            # If this line references an existing payment txn, consider it a payment
            if line.get("txn_id") or line.get("txn_line_id") or line.get("payment_txn_line_id"):
                # Check resolved account type; if it's Profit and Loss, treat as non-payment
                try:
                    erp_account = self._resolve_account(line.get("account"))
                    if erp_account:
                        acct_type = frappe.db.get_value("Account", erp_account, "account_type")
                        if acct_type:
                            acct_type_norm = acct_type.strip().lower()
                            # Common profit & loss/account types in ERPNext
                            if acct_type_norm in ("income", "expense", "profit and loss", "income account", "expense account"):
                                return False
                        # If account_type is empty, check root_type for Profit and Loss
                        root_type = frappe.db.get_value("Account", erp_account, "root_type")
                        if root_type and str(root_type).strip().lower() in ("profit and loss", "income", "expense"):
                            return False
                    else:
                        # If we couldn't resolve the account, fallback to checking
                        # the raw account name for common P&L keywords (e.g. Interest Income)
                        raw_acc = (line.get("account") or "").strip().lower()
                        if any(k in raw_acc for k in ("income", "interest", "expense", "profit")):
                            return False
                except Exception:
                    # If resolution fails, fall back to treating as payment
                    pass
                return True
        return False

    def _build_payment_entries(self, record):
        company = frappe.defaults.get_global_default("company")
        company_currency = frappe.db.get_value("Company", company, "default_currency")
        currency, exchange_rate = self._get_currency_details(record, company_currency)
        deposit_to = record.get("deposit_to_acct")
        bank_account = self._resolve_account(deposit_to) if deposit_to else None
        # If deposit_to_acct is missing or cannot be resolved, default to
        # 'Undeposited Funds' so amounts go to the undeposited account.
        if not bank_account:
            bank_account = self._resolve_account("Undeposited Funds")
        if not bank_account:
            raise ValueError(
                f"Bank account not found for deposit_to_acct={deposit_to}"
            )

        payment_entries = []
        record_project = self._resolve_project(record.get("project_name"))
        for line in (record.get("lines") or []):
            if not self._is_payment_line(line):
                continue

            amount = flt(line.get("amount", 0))
            if amount <= 0:
                continue

            entity_raw = line.get("entity")
            party_type, party = self._resolve_party(entity_raw)
            project = record_project or self._resolve_project_from_entity(entity_raw)

            # If the party doc doesn't exist, try to create a Customer with the
            # exact QuickBooks `entity` value as its document name so we
            # preserve the full value (including any ':Project' suffix).
            if not party and entity_raw:
                # Try to create a Customer with `name` == entity_raw. If that
                # fails (e.g. due to invalid characters), fall back to the
                # existing _ensure_customer behaviour.
                try:
                    if not frappe.db.exists("Customer", entity_raw):
                        cust = frappe.get_doc({
                            "doctype": "Customer",
                            "name": entity_raw,
                            "customer_name": entity_raw,
                            "customer_group": "All Customers",
                            "territory": "All Territories",
                        })
                        cust.flags.ignore_permissions = True
                        cust.insert()
                        frappe.db.commit()
                    party_type = "Customer"
                    party = entity_raw
                except Exception:
                    # Fallback: create customer using the safer helper which may
                    # generate a different docname, but preserves the
                    # customer_name field.
                    party = self._ensure_customer(entity_raw)
                    if party:
                        party_type = "Customer"

            if not party and line.get("txn_type") != "Deposit":
                payment_entries.append({
                    "_skip": True,
                    "_skip_reason": "CUSTOMER_NOT_FOUND",
                    "ref_no": record.get("txn_number"),
                    "_source_id": line.get("txn_line_id") or line.get("txn_id") or record.get("txn_id"),
                })
                continue

            payment_type = self._resolve_payment_type(party_type, line)
            party_account = self._resolve_party_account(party_type, currency) if party else None
            if party and not party_account:
                raise ValueError("Could not resolve party account for deposit payment")

            # IMPORTANT: Do NOT use the JSON `account` field to set `paid_from`.
            # Leave `paid_from` unset so ERPNext can determine it automatically
            # based on the selected `party` and party accounting configuration.

            payment_method = line.get("payment_method") or record.get("payment_method") or "Bank"
            posting_date = self.normalize_date(record.get("date") or record.get("txn_date"))
            reference_no = self._resolve_payment_reference(line, record)
            payment_entry = {
                "doctype": "Payment Entry",
                "payment_type": payment_type,
                "company": company,
                "posting_date": posting_date,
                "reference_no": reference_no,
                "reference_date": posting_date,
                "paid_to": bank_account,
                "mode_of_payment": self._resolve_mode_of_payment(payment_method),
                "paid_amount": amount,
                "received_amount": amount,
                "remarks": line.get("memo") or record.get("memo") or "",
                "_source_id": line.get("txn_line_id") or line.get("txn_id") or reference_no,
            }

            if project:
                payment_entry["project"] = project

            if entity_raw and party_type:
                # Preserve the raw entity string as the Payment Entry `party`.
                payment_entry["party_type"] = party_type
                payment_entry["party"] = entity_raw
                if party_account:
                    payment_entry["party_account"] = party_account

            cost_center = self._resolve_cost_center(line.get("class_name"))
            if cost_center:
                payment_entry["cost_center"] = cost_center

            if currency and currency != company_currency and exchange_rate:
                payment_entry["currency"] = currency
                payment_entry["exchange_rate"] = exchange_rate

            payment_entries.append(payment_entry)

        return payment_entries

    def _build_deposit_journal_entry(self, record):
        """Build a Journal Entry for pure deposit transactions without payment lines."""
        deposit_lines = [
            line for line in (record.get("lines") or [])
            if line.get("txn_type") == "Deposit" and flt(line.get("amount", 0)) > 0
            and line.get("account") != "Undeposited Funds"
        ]
        if not deposit_lines:
            return None

        company = frappe.defaults.get_global_default("company")
        deposit_to = record.get("deposit_to_acct")
        bank_account = self._resolve_account(deposit_to) if deposit_to else None
        # Default to 'Undeposited Funds' when deposit_to_acct is missing.
        if not bank_account:
            bank_account = self._resolve_account("Undeposited Funds")
        if not bank_account:
            raise ValueError(
                f"Bank account not found for deposit_to_acct={deposit_to}"
            )

        posting_date = self.normalize_date(record.get("date") or record.get("txn_date"))
        company_currency = frappe.db.get_value("Company", company, "default_currency")
        currency, exchange_rate = self._get_currency_details(record, company_currency)
        total_amount = 0.0
        accounts = []
        project = self._resolve_project(record.get("project_name"))

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
                # Preserve the exact JSON `entity` string when possible so
                # ERPNext `party` matches the QuickBooks value (including
                # any ":Project" suffix). If we can't determine a party
                # type from the raw candidate, fall back to the resolved
                # party value.
                if candidate and party_type:
                    row["party_type"] = party_type
                    row["party"] = candidate
                elif party_type and party:
                    row["party_type"] = party_type
                    row["party"] = party

            if project:
                row["project"] = project

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
            "voucher_type": "Journal Entry",
            "company": company,
            "posting_date": posting_date,
            "reference_no": record.get("txn_number") or record.get("txn_id") or "",
            "reference_date": posting_date,
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
        source_id = self.get_source_id(record)
        if source_id and source_id in self._get_deposit_cash_back_source_ids():
            return {
                "_skip": True,
                "_skip_reason": "HANDLED_BY_DEPOSITS_CASH_BACK_IMPORTER",
                "ref_no": record.get("txn_number"),
                "_source_id": source_id,
            }

        payment_entries = self._build_payment_entries(record)
        if payment_entries:
            return payment_entries

        deposit_journal = self._build_deposit_journal_entry(record)
        if deposit_journal:
            return deposit_journal

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
