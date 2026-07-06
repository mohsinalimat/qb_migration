import frappe
from frappe.utils import flt

from ..base_importer import BaseImporter


class BillPaymentImporter(BaseImporter):
    source_type = "QB_BILL_PAYMENT"
    target_doctype = "Payment Entry"
    json_file = "bill_payments.json"
    json_key = "bill_payments"

    def get_source_id(self, record):
        return str(record.get("txn_id") or record.get("ref_no") or "")

    def _resolve_account(self, qb_account_name):
        if not qb_account_name:
            return None
        leaf = qb_account_name.split(":")[-1].strip()
        company = frappe.defaults.get_global_default("company")
        return frappe.db.get_value(
            "Account", {"account_name": leaf, "company": company}, "name"
        )

    def _resolve_payable_account(self, currency=None):
        """
        Fallback: return the first payable account found, optionally matching currency.
        Used only when no invoice references exist to determine the correct account.
        """
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

    def _resolve_payment_account(self, qb_account_name=None):
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

    def resolve_purchase_invoice(self, bill_no, supplier_name=None, amount=None):
        if bill_no:
            result = frappe.db.get_value("Purchase Invoice", {"bill_no": bill_no}, "name")
            if not result:
                result = frappe.db.sql(
                    "select name from `tabPurchase Invoice` where lower(bill_no)=lower(%s) limit 1",
                    bill_no,
                )
                result = result[0][0] if result else None
            if result:
                return result

        if supplier_name and amount is not None:
            result = frappe.db.sql(
                "select name from `tabPurchase Invoice` where supplier=%s and abs(grand_total - %s) < 0.01 order by bill_date desc limit 1",
                (supplier_name, amount),
            )
            if result:
                return result[0][0]

        return None

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

    def _build_references(self, record, payment_amount):
        """
        Build Payment Entry references from QB applied list.

        For each applied invoice:
          - resolve the ERPNext Purchase Invoice
          - fetch its current outstanding_amount
          - cap the allocated amount to min(applied_amount, outstanding)
          - skip invoices that are already fully paid

        Returns (references, effective_payment_amount, resolved_candidates) where
        effective_payment_amount is the sum of all allocated amounts
        (may differ from QB total when invoices have partial outstanding),
        and resolved_candidates is the number of invoice candidates that were
        matched to ERPNext Purchase Invoices.
        """
        applied_items = record.get("applied") or []

        # If QB provides a split applied list, use it; otherwise treat the
        # whole payment as applying to the single bill_no.
        if applied_items:
            candidates = []
            for item in applied_items:
                ref_no = (item.get("ref_no") or "").strip()
                if not ref_no:
                    # Blank or missing invoice reference should not force
                    # a weak supplier/amount match that can incorrectly mark
                    # the payment as already applied.
                    continue
                candidates.append({"ref_no": ref_no, "amount": flt(item.get("amount", 0))})
        else:
            ref_no = record.get("ref_no")
            candidates = [{"ref_no": ref_no, "amount": flt(payment_amount)}]

        references = []
        total_allocated = 0.0
        resolved_candidates = 0

        for candidate in candidates:
            ref_no = candidate["ref_no"]
            applied_amount = candidate["amount"]

            invoice = self.resolve_purchase_invoice(
                ref_no,
                record.get("vend_name"),
                applied_amount or payment_amount,
            )
            if not invoice:
                continue

            resolved_candidates += 1
            outstanding = flt(
                frappe.db.get_value("Purchase Invoice", invoice, "outstanding_amount") or 0
            )

            if outstanding <= 0:
                # Invoice already fully paid — skip this line
                continue

            # Never allocate more than what the invoice can absorb
            allocated = min(applied_amount if applied_amount > 0 else outstanding, outstanding)
            if allocated <= 0:
                continue

            references.append({
                "reference_doctype": "Purchase Invoice",
                "reference_name": invoice,
                "allocated_amount": allocated,
            })
            total_allocated += allocated

        return references, total_allocated, resolved_candidates

    def map_record(self, record):
        company = frappe.defaults.get_global_default("company")
        payment_account = self._resolve_payment_account()

        payment_amount = flt(record.get("total_amt", record.get("amount", 0)) or 0)
        if not payment_amount and record.get("applied"):
            payment_amount = sum(flt(item.get("amount", 0)) for item in record.get("applied", []))

        if not payment_amount:
            return {
                "_skip": True,
                "_skip_reason": "ZERO_AMOUNT",
                "ref_no": record.get("ref_no", ""),
            }

        references, effective_amount, resolved_candidates = self._build_references(record, payment_amount)

        # If every linked invoice was already paid, skip the whole payment.
        if not references and resolved_candidates and (record.get("applied") or record.get("bill_no") or record.get("ref_no")):
            return {
                "_skip": True,
                "_skip_reason": "ALREADY_PAID",
                "ref_no": record.get("ref_no", ""),
            }

        # Use effective_amount when we have references; fall back to QB total
        # for unlinked payments (no matching invoice found).
        final_amount = effective_amount if references else payment_amount

        if not final_amount:
            return {
                "_skip": True,
                "_skip_reason": "ZERO_ALLOCATED",
                "ref_no": record.get("ref_no", ""),
            }

        # --- Determine the correct party_account from the linked invoices ---
        if references:
            # Fetch credit_to for each referenced invoice
            credit_to_values = frappe.db.get_values(
                "Purchase Invoice",
                [ref["reference_name"] for ref in references],
                "credit_to",
            )
            # Extract unique non-empty credit_to accounts
            unique_credit_to = set([ct[0] for ct in credit_to_values if ct[0]])

            if len(unique_credit_to) > 1:
                # Multiple different creditor accounts – cannot process in one Payment Entry
                return {
                    "_skip": True,
                    "_skip_reason": "MULTIPLE_PARTY_ACCOUNTS",
                    "ref_no": record.get("ref_no", ""),
                }

            payable_account = unique_credit_to.pop() if unique_credit_to else self._resolve_payable_account(record.get("currency"))
        else:
            # No references – fall back to generic payable account (unlinked payment)
            payable_account = self._resolve_payable_account(record.get("currency"))
        # --------------------------------------------------------------------

        payment_account = payment_account or self._resolve_payment_account()
        if not payable_account or not payment_account:
            return {
                "_skip": True,
                "_skip_reason": "NO_VALID_ACCOUNT",
                "ref_no": record.get("ref_no", ""),
            }

        # Prefer supplier from the resolved invoice; fall back to QB record.
        supplier = record.get("vend_name")
        if references:
            invoice_supplier = frappe.db.get_value(
                "Purchase Invoice", references[0]["reference_name"], "supplier"
            )
            if invoice_supplier:
                supplier = invoice_supplier

        return {
            "doctype": "Payment Entry",
            "payment_type": "Pay",
            "company": company,
            "posting_date": self.normalize_date(record.get("date") or record.get("txn_date")),
            "mode_of_payment": self._resolve_mode_of_payment(None),
            "party_type": "Supplier",
            "party": supplier,
            "party_account": payable_account,          # now correctly matches the invoices' credit_to
            "reference_no": record.get("ref_no", ""),
            "reference_date": self.normalize_date(record.get("date") or record.get("txn_date")),
            "paid_amount": final_amount,
            "received_amount": final_amount,
            "paid_from": payment_account,
            "paid_to": payable_account,
            "references": references,
        }