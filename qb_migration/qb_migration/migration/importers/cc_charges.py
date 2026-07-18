import frappe

from .invoices import SalesInvoiceImporter
from .journal_entries import JournalEntryImporter
from .purchase_invoices import PurchaseInvoiceImporter


class CCChargesImporter(JournalEntryImporter, PurchaseInvoiceImporter, SalesInvoiceImporter):
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

    def _resolve_line_type(self, line):
        line_type = line.get("line_type")
        if line_type == "item":
            return "Item"
        elif line_type == "expense":
            return "Expense"
        else:
            return None

    def _resolve_payee_type(self, record):
        payee_type = str(record.get("payee_type") or "").strip().lower()
        if payee_type in {"customer", "cust", "client"}:
            return "Customer"
        if payee_type in {"supplier", "vendor", "vendor_name"}:
            return "Supplier"

        payee_name = record.get("payee")
        if frappe.db.exists("Customer", payee_name):
            return "Customer"
        if frappe.db.exists("Supplier", payee_name):
            return "Supplier"
        return None

    def _resolve_item_code(self, qb_item_name):
        if not qb_item_name:
            return None

        item_doc = frappe.db.get_value("Item", {"item_code": qb_item_name}, "name")
        if item_doc:
            return item_doc

        item_doc = frappe.db.get_value("Item", {"item_name": qb_item_name}, "name")
        if item_doc:
            return item_doc

        return qb_item_name

    def _is_zero_qty(self, qty):
        if qty is None or qty == "":
            return False
        try:
            return float(qty) == 0
        except (TypeError, ValueError):
            return False

    def _get_line_description(self, line, record, fallback=None):
        for key in ("description", "memo", "item_name", "item", "account", "expense_account"):
            value = line.get(key) if isinstance(line, dict) else None
            if value not in (None, ""):
                text = str(value).strip()
                if text:
                    return text

        for key in ("memo", "payee", "ref_no", "txn_id"):
            value = record.get(key)
            if value not in (None, ""):
                text = str(value).strip()
                if text:
                    return text

        return fallback or "Imported from QuickBooks"

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
        line_types = [self._resolve_line_type(line) for line in record.get("lines") or []]
        has_expense_lines = "Expense" in line_types
        has_item_lines = "Item" in line_types

        if has_item_lines:
            payee_name = record.get("payee") or record.get("vendor") or record.get("entity") or ""
            payee_type = self._resolve_payee_type(record)
            if payee_type == "Customer":
                items = []
                total_amount = 0.0
                for line in record.get("lines") or []:
                    if self._resolve_line_type(line) != "Item":
                        continue

                    qty = line.get("qty")
                    if self._is_zero_qty(qty):
                        continue

                    amount = float(line.get("amount") or 0)
                    total_amount += amount
                    items.append({
                        "item_code": self._resolve_item_code(line.get("item") or line.get("item_name") or line.get("description")),
                        "qty": qty or 1,
                        "rate": line.get("rate") or amount,
                        "amount": amount,
                        "description": self._get_line_description(line, record, "Imported item"),
                    })

                if not items:
                    return {"_skip": True, "_skip_reason": "Skipped cc_charges because all line items have qty 0.0", "ref_no": record.get("txn_id", "")}

                return {
                    "doctype": "Sales Invoice",
                    "customer": payee_name,
                    "posting_date": self.normalize_date(record.get("date")),
                    "due_date": self.normalize_date(record.get("due_date") or record.get("date")),
                    "company": company,
                    "remarks": record.get("memo") or record.get("ref_no") or record.get("payee") or "",
                    "items": items,
                    "update_stock": 1,
                    "set_posting_time": 1,
                    "total": total_amount,
                    "grand_total": total_amount,
                    "base_total": total_amount,
                    "base_grand_total": total_amount,
                }

            if payee_type == "Supplier":
                items = []
                taxes = []
                total_amount = 0.0
                for line in record.get("lines") or []:
                    line_type = self._resolve_line_type(line)
                    if line_type == "Item":
                        qty = line.get("qty")
                        if self._is_zero_qty(qty):
                            continue

                        amount = float(line.get("amount") or 0)
                        total_amount += amount
                        items.append({
                            "item_code": self._resolve_item_code(line.get("item") or line.get("item_name") or line.get("description")),
                            "qty": qty or 1,
                            "rate": line.get("rate") or amount,
                            "amount": amount,
                            "expense_account": self._resolve_account(line.get("account") or record.get("account") or record.get("expense_account")),
                            "description": self._get_line_description(line, record, "Imported item"),
                        })
                    elif line_type == "Expense":
                        account = self._resolve_line_account(line, record)
                        amount = float(line.get("amount") or 0)
                        taxes.append({
                            "charge_type": "Actual",
                            "account_head": account,
                            "description": self._get_line_description(line, record, "Imported expense"),
                            "tax_amount": amount,
                            "rate": 0.0,
                            "included_in_print_rate": 0,
                        })

                if not items:
                    return {"_skip": True, "_skip_reason": "NO_ITEM_LINES", "ref_no": record.get("txn_id", "")}

                supplier_name = payee_name
                try:
                    supplier = self.resolve_supplier(supplier_name)
                except ValueError:
                    supplier = supplier_name

                currency = record.get("currency") or "PKR"
                tax_total = sum(float(t.get("tax_amount") or 0) for t in taxes)
                invoice = {
                    "doctype": "Purchase Invoice",
                    "supplier": supplier,
                    "posting_date": self.normalize_date(record.get("date")),
                    "due_date": self.normalize_date(record.get("due_date") or record.get("date")),
                    "bill_no": record.get("ref_no") or record.get("txn_id") or "",
                    "bill_date": self.normalize_date(record.get("date")),
                    "company": company,
                    "currency": currency,
                    "items": items,
                    "taxes": taxes,
                    "is_return": record.get("is_credit", False),
                    "update_stock": 1,
                    "set_posting_time": 1,
                    "total": total_amount,
                    "grand_total": total_amount + tax_total,
                    "base_total": total_amount + tax_total,
                    "base_grand_total": total_amount + tax_total,
                }

                try:
                    invoice["credit_to"] = self.resolve_payable_account(supplier, currency)
                except Exception:
                    invoice["credit_to"] = None

                return invoice

            return {"_skip": True, "_skip_reason": "UNRESOLVED_PAYEE_TYPE", "ref_no": record.get("txn_id", "")}

        debit_total = 0.0
        accounts = []

        for line in record.get("lines") or []:
            if self._resolve_line_type(line) != "Expense":
                continue

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
