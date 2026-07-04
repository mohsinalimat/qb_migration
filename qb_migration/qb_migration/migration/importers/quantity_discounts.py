import frappe

from ..base_importer import BaseImporter


class QuantityDiscountImporter(BaseImporter):
    source_type = "QB_QUANTITY_DISCOUNT"
    target_doctype = "Pricing Rule"
    json_file = "quantity_discounts.json"
    json_key = "quantity_discounts"

    def map_record(self, record):
        discount_pct = float(record.get("discount_pct") or 0)
        discount_rate = float(record.get("discount_rate") or 0)

        # Prefer percentage discounts when QuickBooks provides both values,
        # and fall back to a fixed amount discount when only a rate exists.
        if discount_pct > 0:
            discount_type = "Percentage"
            discount_percentage = discount_pct
            discount_amount = 0.0
        elif discount_rate > 0:
            discount_type = "Price Discount"
            discount_percentage = 0.0
            discount_amount = discount_rate
        else:
            discount_type = "Percentage"
            discount_percentage = 0.0
            discount_amount = 0.0

        doc = {
            "doctype": "Pricing Rule",
            "title": record.get("item_name") or record.get("description") or "Pricing Rule",
            "description": record.get("description", ""),
            "disabled": 0 if record.get("active") else 1,
            "discount_type": discount_type,
            "discount_percentage": discount_percentage,
            "discount_amount": discount_amount,
            "apply_on": "Transaction",
            "selling": 1,
            "buying": 0,
            "priority": 1,
        }

        return doc
