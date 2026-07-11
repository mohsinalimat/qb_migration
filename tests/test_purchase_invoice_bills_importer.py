import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

frappe = types.ModuleType("frappe")
frappe.defaults = types.SimpleNamespace(get_global_default=lambda *_args, **_kwargs: "Test Company")
frappe.db = types.SimpleNamespace(get_value=lambda *_args, **_kwargs: None)
frappe.utils = types.SimpleNamespace(getdate=lambda value: value, nowdate=lambda: "2020-01-01", now_datetime=lambda: "2020-01-01")
sys.modules.setdefault("frappe", frappe)
sys.modules.setdefault("frappe.utils", frappe.utils)

from qb_migration.qb_migration.migration.importers.bills import PurchaseInvoiceImporter


@pytest.fixture
def importer():
    importer = PurchaseInvoiceImporter.__new__(PurchaseInvoiceImporter)
    importer.resolve_supplier = lambda name: "SUP-001"
    importer.resolve_item = lambda name: "ITEM-001"
    importer._resolve_account = lambda name: "ACC-001"
    importer.resolve_payment_terms_template = lambda terms: None
    importer.resolve_payable_account = lambda supplier, currency: "Creditors"
    importer.normalize_date = lambda value: "2020-07-17"
    importer.resolve_cost_center = lambda name: "CC-001" if name else None
    importer.resolve_project = lambda name: "PRJ-001" if name else None
    return importer


def test_map_record_splits_item_and_expense_lines(monkeypatch, importer):
    record = {
        "txn_id": "123",
        "ref_no": "INV-1",
        "date": "2020-07-17",
        "due_date": "2020-07-27",
        "vend_name": "Acme Supplier",
        "terms": "Net 30",
        "memo": "Imported",
        "lines": [
            {
                "line_no": 1,
                "line_type": "item",
                "item": "Widget",
                "qty": 2,
                "amount": 200,
                "description": "Widget line",
            },
            {
                "line_no": 2,
                "line_type": "expense",
                "account": "Freight",
                "gl_code": "Freight",
                "amount": 50,
                "description": "Freight line",
                "class": "Remodel",
                "customer": "Acme:Project",
            },
        ],
    }

    doc = importer.map_record(record)

    assert doc["items"][0]["item_code"] == "ITEM-001"
    assert doc["items"][0]["qty"] == 2
    assert doc["items"][0]["rate"] == 100.0
    assert doc["taxes"][0]["charge_type"] == "Actual"
    assert doc["taxes"][0]["account_head"] == "ACC-001"
    assert doc["taxes"][0]["tax_amount"] == 50
    assert doc["taxes"][0]["description"] == "Freight line"
