import json
from pathlib import Path

import frappe

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


_customer_currency_lookup = None


def _load_customer_currency_lookup():
    global _customer_currency_lookup
    if _customer_currency_lookup is not None:
        return _customer_currency_lookup

    _customer_currency_lookup = {}
    try:
        path = DATA_DIR / "customers.json"
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return _customer_currency_lookup

    for record in payload.get("customers") or []:
        if not isinstance(record, dict):
            continue

        customer_name = (record.get("name") or "").strip()
        currency = (record.get("currency") or "").strip()
        if customer_name and currency:
            _customer_currency_lookup[customer_name] = currency

    return _customer_currency_lookup


def get_customer_currency(customer_name):
    if not customer_name:
        return None

    customer_name = str(customer_name).strip()
    if not customer_name:
        return None

    currency = frappe.db.get_value("Customer", {"customer_name": customer_name}, "default_currency")
    if currency:
        return currency

    return _load_customer_currency_lookup().get(customer_name)


def infer_composite_customer_currency(customer_name):
    if not customer_name or ":" not in str(customer_name):
        return None

    parts = [part.strip() for part in str(customer_name).split(":") if part and part.strip()]
    if len(parts) < 2:
        return None

    matched_currencies = []
    for part in parts:
        currency = get_customer_currency(part)
        if currency:
            matched_currencies.append(currency)

    if not matched_currencies:
        return None

    unique_currencies = list(dict.fromkeys(matched_currencies))
    if len(unique_currencies) == 1:
        return unique_currencies[0]

    return None


def ensure_customer_currency(customer_name, customer_doc, save=False):
    if not customer_name or not customer_doc:
        return None

    inferred_currency = infer_composite_customer_currency(customer_name)
    if not inferred_currency:
        return None

    existing_currency = getattr(customer_doc, "default_currency", None)
    if existing_currency == inferred_currency:
        return inferred_currency

    customer_doc.default_currency = inferred_currency
    if save:
        customer_doc.flags.ignore_permissions = True
        customer_doc.save(ignore_permissions=True)

    return inferred_currency
