import frappe
from frappe.utils import cint

from ..base_importer import BaseImporter


class InventoryAdjustmentImporter(BaseImporter):
    source_type = "QB_INVENTORY_ADJUSTMENT"
    target_doctype = "Stock Entry"
    json_file = "inventory_adjustments.json"
    json_key = "inventory_adjustments"

    ADJUSTMENT_WAREHOUSE_NAME = "Stock Reconciliation"

    def _get_company(self):
        return frappe.defaults.get_global_default("company") or ""

    def _resolve_item(self, item_name):
        if not item_name:
            return None

        item = frappe.db.get_value("Item", {"item_code": item_name}, "name")
        if not item:
            item = frappe.db.get_value("Item", {"item_name": item_name}, "name")

        if not item:
            return None

        if cint(frappe.db.get_value("Item", item, "is_stock_item")) != 1:
            frappe.db.set_value(
                "Item",
                item,
                {
                    "is_stock_item": 1,
                    "is_purchase_item": 1,
                    "is_sales_item": 1,
                },
            )

        return item

    def _resolve_difference_account(self):
        company = self._get_company()
        if not company:
            return None

        for account_name in ["Stock In Hand", "Temporary Opening", "Stock Adjustment"]:
            account = frappe.db.get_value(
                "Account",
                {"account_name": account_name, "company": company},
                "name",
            )
            if account:
                return account

        return frappe.db.get_value(
            "Account",
            {"company": company, "root_type": "Asset"},
            "name",
        )

    def _resolve_or_create_warehouse(self):
        """Ensure the shared clearing warehouse exists for the current company.

        Idempotent: checks for an existing warehouse before creating one, so
        repeated runs never create duplicates.
        """
        company = self._get_company()
        if not company:
            return None

        existing = frappe.db.get_value(
            "Warehouse",
            {"warehouse_name": self.ADJUSTMENT_WAREHOUSE_NAME, "company": company},
            "name",
        )
        if existing:
            return existing

        warehouse = frappe.get_doc(
            {
                "doctype": "Warehouse",
                "warehouse_name": self.ADJUSTMENT_WAREHOUSE_NAME,
                "company": company,
            }
        )
        warehouse.insert(ignore_permissions=True)
        return warehouse.name

    def _resolve_store_warehouse(self, item_doc, company, clearing_warehouse):
        """Resolve "the corresponding store" for an item.

        QB's inventory_adjustments.json carries no warehouse/location per
        line, so this is a best-effort resolution: the item's configured
        default warehouse for this company, falling back to a warehouse
        named "Stores", falling back to any other company warehouse that
        isn't the clearing warehouse itself (s_warehouse == t_warehouse is
        invalid on a Stock Entry row).
        """
        if item_doc:
            default_warehouse = frappe.db.get_value(
                "Item Default",
                {"parent": item_doc, "company": company},
                "default_warehouse",
            )
            if default_warehouse and default_warehouse != clearing_warehouse:
                return default_warehouse

        store = frappe.db.get_value(
            "Warehouse",
            {"warehouse_name": "Stores", "company": company},
            "name",
        )
        if store and store != clearing_warehouse:
            return store

        return frappe.db.get_value(
            "Warehouse",
            {"company": company, "name": ["!=", clearing_warehouse]},
            "name",
        )

    def _parse_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def get_current_stock_qty(self, item_code, warehouse):
        # Not used by map_record (transfer qty is abs(quantity_difference),
        # not current-stock + delta). Retained unused rather than removed,
        # per "no unrelated refactoring".
        if not item_code:
            return 0

        filters = {
            "item_code": item_code,
            "is_cancelled": 0,
        }

        if warehouse:
            filters["warehouse"] = warehouse

        sle = frappe.get_all(
            "Stock Ledger Entry",
            filters=filters,
            fields=["qty_after_transaction"],
            order_by="posting_date desc, posting_time desc, creation desc",
            limit=1,
        )

        return sle[0].qty_after_transaction if sle else 0

    def _get_latest_sle(self, item_code, warehouse):
        """Return latest stock ledger entry dict or None"""
        if not item_code:
            return None

        filters = {"item_code": item_code, "is_cancelled": 0}
        if warehouse:
            filters["warehouse"] = warehouse

        sle = frappe.get_all(
            "Stock Ledger Entry",
            filters=filters,
            fields=["qty_after_transaction", "valuation_rate", "warehouse", "posting_date", "posting_time"],
            order_by="posting_date desc, posting_time desc, creation desc",
            limit=1,
        )

        if not sle:
            return None

        return sle[0]

    def _ensure_item_valuation_rate(self, item_doc, rate):
        """Backfill Item.valuation_rate when it's still blank/zero.

        For a transfer row (s_warehouse + t_warehouse both set), ERPNext's
        stock ledger values the OUTGOING leg from the *source* warehouse's
        own valuation history. The clearing warehouse has none (it never
        holds real stock), so ERPNext falls back to the Item master's
        valuation_rate field for that leg -- it does NOT fall back to the
        basic_rate we set on the row itself. basic_rate only fixes the
        INCOMING leg (into the store). So an item whose master
        valuation_rate is still 0/blank raises "Valuation Rate ... is
        required" at submit time even when we've computed a perfectly good
        rate here. Patching the Item master closes that gap, the same way
        _resolve_item() already patches is_stock_item/is_purchase_item/etc.
        """
        if not item_doc or not rate:
            return

        current = self._parse_float(frappe.db.get_value("Item", item_doc, "valuation_rate"))
        if not current:
            frappe.db.set_value("Item", item_doc, "valuation_rate", rate)

    def _resolve_basic_rate(self, item_doc, store_warehouse, qty_diff, value_diff):
        """basic_rate for a positive-adjustment line (stock flowing OUT of
        the clearing warehouse INTO the store). The clearing warehouse has
        no valuation history of its own, so this must be set explicitly
        rather than left to auto-calculation. Falls back to the store's
        latest known valuation, then the Item's valuation_rate, when QB's
        value_difference is missing or zero.

        Also backfills the Item master's valuation_rate when it's blank --
        see _ensure_item_valuation_rate() for why that's required in
        addition to setting basic_rate on the row.
        """
        if qty_diff and value_diff:
            rate = abs(value_diff / qty_diff)
            if rate:
                self._ensure_item_valuation_rate(item_doc, rate)
                return rate

        sle = self._get_latest_sle(item_doc, store_warehouse)
        if sle and sle.get("valuation_rate"):
            rate = self._parse_float(sle.get("valuation_rate"))
            self._ensure_item_valuation_rate(item_doc, rate)
            return rate

        return self._parse_float(frappe.db.get_value("Item", item_doc, "valuation_rate"))

    def _build_remarks(self, record):
        txn_id = record.get("txn_id") or ""
        ref_no = record.get("ref_no") or ""
        memo = record.get("memo") or ""

        lines = []
        if txn_id:
            lines.append(f"QB_TXN:{txn_id}")
        if ref_no:
            lines.append(f"Reference: {ref_no}")
        if memo:
            lines.append(memo)

        return "\n".join(lines)

    def _build_transfer_items(self, record, clearing_warehouse, company):
        """Build Stock Entry Detail rows for a Material Transfer between
        each item's store warehouse and the clearing warehouse.

        Direction follows the sign of quantity_difference:
          - negative: store -> clearing (store is losing counted stock)
          - positive: clearing -> store (store is gaining counted stock)
        qty is always the absolute value; sign only decides direction.
        Zero-diff lines are no-ops and skipped.

        Returns (items, skip_reason). skip_reason is set only on a hard
        failure (unresolvable item or store warehouse), matching the
        original importer's whole-record skip behavior.
        """
        items = []

        for line in record.get("lines") or []:
            item_name = line.get("item") or ""
            if not item_name:
                continue

            item_doc = self._resolve_item(item_name)
            if not item_doc:
                return None, f"MISSING_ITEM:{item_name}"

            qty_diff = self._parse_float(line.get("quantity_difference"))
            if not qty_diff:
                continue

            store_warehouse = self._resolve_store_warehouse(item_doc, company, clearing_warehouse)
            if not store_warehouse:
                return None, f"MISSING_STORE_WAREHOUSE:{item_name}"

            qty = abs(qty_diff)

            if qty_diff < 0:
                row = {
                    "item_code": item_doc,
                    "qty": qty,
                    "s_warehouse": store_warehouse,
                    "t_warehouse": clearing_warehouse,
                }
                # basic_rate intentionally omitted: ERPNext pulls valuation
                # from the store's current stock automatically.
            else:
                value_diff = self._parse_float(line.get("value_difference"))
                row = {
                    "item_code": item_doc,
                    "qty": qty,
                    "s_warehouse": clearing_warehouse,
                    "t_warehouse": store_warehouse,
                    "basic_rate": self._resolve_basic_rate(item_doc, store_warehouse, qty_diff, value_diff),
                }

            if line.get("memo"):
                row["description"] = line.get("memo")

            items.append(row)

        return items, None

    def find_existing_target(self, doc_data):
        if not doc_data:
            return None

        remarks = (doc_data or {}).get("remarks") or ""
        marker = "QB_TXN:"
        if marker not in remarks:
            return None

        marker_line = remarks.split("\n", 1)[0].strip()
        if not marker_line.startswith(marker):
            return None

        return frappe.db.get_value(
            "Stock Entry",
            {"remarks": ["like", f"%{marker_line}%"], "docstatus": ["!=", 2]},
            "name",
        )

    def post_insert(self, doc, record):
        if hasattr(doc, "submit"):
            doc.submit()

    def map_record(self, record):
        company = self._get_company()
        if not company:
            return {"_skip": True, "_skip_reason": "MISSING_COMPANY", "ref_no": record.get("ref_no")}

        clearing_warehouse = self._resolve_or_create_warehouse()
        if not clearing_warehouse:
            return {"_skip": True, "_skip_reason": "MISSING_WAREHOUSE", "ref_no": record.get("ref_no")}

        lines = record.get("lines") or []
        if not lines:
            return {"_skip": True, "_skip_reason": "NO_LINES", "ref_no": record.get("ref_no")}

        items, skip_reason = self._build_transfer_items(record, clearing_warehouse, company)
        if skip_reason:
            return {"_skip": True, "_skip_reason": skip_reason, "ref_no": record.get("ref_no")}

        if not items:
            return {"_skip": True, "_skip_reason": "NO_VALID_ITEMS", "ref_no": record.get("ref_no")}

        for idx, row in enumerate(items, start=1):
            row["idx"] = idx

        doc = {
            "doctype": "Stock Entry",
            "stock_entry_type": "Material Transfer",
            "purpose": "Material Transfer",
            "company": company,
            "posting_date": self.normalize_date(record.get("date")),
            "set_posting_time": 1,
            "remarks": self._build_remarks(record),
            "items": items,
        }

        return doc