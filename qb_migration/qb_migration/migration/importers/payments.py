import uuid

import frappe
from frappe.utils import flt, nowdate

from ..base_importer import BaseImporter


class PaymentsImporter(BaseImporter):
    source_type = "QB_PAYMENT"
    target_doctype = "Payment Entry"
    json_file = "payments.json"
    json_key = "payments"

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("ref_no") or "")

    def _parse_customer(self, cust_name: str) -> str | None:
        if not cust_name:
            return None
            # Preserve the full QuickBooks customer string (including any
            # ":Project" or job suffix) so ERPNext `party` matches the JSON.
            return str(cust_name).strip()

    def _resolve_payment_account(self, qb_account_name=None):
        # copy pattern from bill_payments importer: prefer specific account, else Bank/Cash
        company = frappe.defaults.get_global_default("company")
        if qb_account_name:
            leaf = qb_account_name.split(":")[-1].strip()
            account = frappe.db.get_value(
                "Account",
                {"account_name": leaf, "company": company, "is_group": 0},
                "name",
            )
            if account:
                return account

            row = frappe.db.sql(
                "select name, is_group from `tabAccount` where account_name=%s and company=%s limit 1",
                (leaf, company),
            )
            if row:
                name, is_group = row[0]
                if not is_group:
                    return name
                child = frappe.db.sql(
                    "select name from `tabAccount` where parent_account=%s and company=%s and is_group=0 limit 1",
                    (name, company),
                )
                if child:
                    return child[0][0]

        row = frappe.db.sql(
            "select name from `tabAccount` where account_type='Bank' and root_type='Asset' and company=%s and is_group=0 limit 1",
            (company,),
        )
        if row:
            return row[0][0]

        row = frappe.db.sql(
            "select name from `tabAccount` where account_type='Cash' and root_type='Asset' and company=%s and is_group=0 limit 1",
            (company,),
        )
        return row[0][0] if row else None

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

    def _resolve_receivable_account(self, company=None):
        company = company or frappe.defaults.get_global_default("company")
        company_currency = frappe.db.get_value("Company", company, "default_currency")

        filters = {
            "company": company,
            "account_type": "Receivable",
            "root_type": "Asset",
            "is_group": 0,
        }
        if company_currency:
            filters["account_currency"] = company_currency

        account = frappe.db.get_value("Account", filters, ["name", "account_currency"])
        if account:
            name, account_currency = account
            if company_currency and account_currency != company_currency:
                frappe.db.set_value("Account", name, "account_currency", company_currency)
                frappe.db.commit()
            return name

        fallback = frappe.db.get_value(
            "Account",
            {"company": company, "account_type": "Receivable", "root_type": "Asset", "is_group": 0},
            ["name", "account_currency"],
        )
        if fallback:
            name, account_currency = fallback
            if company_currency and account_currency != company_currency:
                frappe.db.set_value("Account", name, "account_currency", company_currency)
                frappe.db.commit()
            return name

        return None

    def resolve_sales_invoice(self, inv_no, customer_name=None, amount=None):
        if inv_no:
            result = frappe.db.get_value("Sales Invoice", {"name": inv_no}, "name")
            if not result:
                result = frappe.db.sql(
                    "select name from `tabSales Invoice` where lower(name)=lower(%s) limit 1",
                    inv_no,
                )
                result = result[0][0] if result else None
            if result:
                return result

        if customer_name and amount is not None:
            result = frappe.db.sql(
                "select name from `tabSales Invoice` where customer=%s and abs(grand_total - %s) < 0.01 order by posting_date desc limit 1",
                (customer_name, amount),
            )
            if result:
                return result[0][0]

        return None

    def _build_references(self, record, payment_amount):
        applied_items = record.get("applied") or []

        if applied_items:
            candidates = [
                {"inv_no": item.get("inv_no") or item.get("ref_no"), "amount": flt(item.get("amount", 0))}
                for item in applied_items
            ]
        else:
            # no applied list: try to use ref_no as invoice
            inv_no = record.get("ref_no") or record.get("inv_no")
            candidates = [{"inv_no": inv_no, "amount": flt(payment_amount)}]

        references = []
        total_allocated = 0.0
        resolved_candidates = 0

        for candidate in candidates:
            inv_ref = candidate["inv_no"]
            applied_amount = candidate["amount"]

            customer = self._parse_customer(record.get("cust_name")) or record.get("cust_name")
            invoice = self.resolve_sales_invoice(inv_ref, customer, applied_amount or payment_amount)
            if not invoice:
                continue

            resolved_candidates += 1
            outstanding = flt(frappe.db.get_value("Sales Invoice", invoice, "outstanding_amount") or 0)
            if outstanding <= 0:
                continue

            allocated = min(applied_amount if applied_amount > 0 else outstanding, outstanding)
            if allocated <= 0:
                continue

            references.append({
                "reference_doctype": "Sales Invoice",
                "reference_name": invoice,
                "allocated_amount": allocated,
            })
            total_allocated += allocated

        return references, total_allocated, resolved_candidates

    def find_existing_target(self, doc_data):
        if doc_data.get("reference_no"):
            return frappe.db.get_value(
                "Payment Entry",
                {
                    "reference_no": doc_data["reference_no"],
                    "party": doc_data.get("party"),
                    "company": doc_data.get("company"),
                },
                "name",
            )
        return None

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        payment_account = self._resolve_payment_account(record.get("account") or record.get("payment_account") or record.get("bank_account"))

        payment_amount = flt(record.get("total_amt", record.get("amount", 0)) or 0)
        if not payment_amount and record.get("applied"):
            payment_amount = sum(flt(item.get("amount", 0)) for item in record.get("applied", []))

        if not payment_amount:
            return {"_skip": True, "_skip_reason": "ZERO_AMOUNT", "ref_no": record.get("ref_no", "")}

        references, effective_amount, resolved_candidates = self._build_references(record, payment_amount)

        if not references and resolved_candidates and (record.get("applied") or record.get("ref_no") or record.get("inv_no")):
            return {"_skip": True, "_skip_reason": "ALREADY_APPLIED_OR_UNLINKED", "ref_no": record.get("ref_no", "")}

        final_amount = effective_amount if references else payment_amount
        if not final_amount:
            return {"_skip": True, "_skip_reason": "ZERO_ALLOCATED", "ref_no": record.get("ref_no", "")}

        # Determine party_account from referenced invoices
        if references:
            debit_to_values = frappe.db.get_values(
                "Sales Invoice",
                [ref["reference_name"] for ref in references],
                "debit_to",
            )
            unique_debit_to = set([dt[0] for dt in debit_to_values if dt[0]])
            if len(unique_debit_to) > 1:
                return {"_skip": True, "_skip_reason": "MULTIPLE_PARTY_ACCOUNTS", "ref_no": record.get("ref_no", "")}

            receivable_account = unique_debit_to.pop() if unique_debit_to else None
        else:
            receivable_account = None

        receivable_account = receivable_account or self._resolve_receivable_account()
        payment_account = payment_account or self._resolve_payment_account()
        if not payment_account or not receivable_account:
            return {"_skip": True, "_skip_reason": "NO_VALID_ACCOUNT", "ref_no": record.get("ref_no", "")}

        party = self._parse_customer(record.get("cust_name")) or record.get("cust_name")
        # prefer invoice customer if available
        if references:
            invoice_customer = frappe.db.get_value("Sales Invoice", references[0]["reference_name"], "customer")
            if invoice_customer:
                party = invoice_customer

        posting_date = self.normalize_date(record.get("date") or record.get("txn_date"))
        reference_no = record.get("ref_no") or record.get("reference_no") or f"QB-PAY-{uuid.uuid4().hex[:8].upper()}"
        reference_date = self.normalize_date(
            record.get("reference_date") or record.get("date") or record.get("txn_date") or nowdate()
        )

        # For Receive-type Payment Entries, leave `paid_from` unset so ERPNext can
        # resolve it automatically from the selected `party` and the configured
        # party accounting defaults, while still setting the correct party account.
        return {
            "doctype": "Payment Entry",
            "payment_type": "Receive",
            "company": company,
            "posting_date": posting_date,
            "mode_of_payment": self._resolve_mode_of_payment(record.get("method") or record.get("payment_method")),
            "party_type": "Customer",
            "party": party,
            "party_account": receivable_account,
            "reference_no": reference_no,
            "reference_date": reference_date,
            "paid_amount": final_amount,
            "received_amount": final_amount,
            "paid_to": payment_account,
            "references": references,
            "remarks": record.get("memo") or "",
        }
