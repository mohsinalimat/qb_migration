from pathlib import Path

import frappe
from frappe.utils import now_datetime

from .importers.accounts import AccountImporter
from .importers.payment_methods import PaymentMethodsImporter
from .importers.terms import TermsImporter
# from .importers.item_groups import ItemGroupImporter
from .importers.price_levels import PriceLevelsImporter
from .importers.customer_types import CustomerTypesImporter
from .importers.vendor_types import VendorTypesImporter
from .importers.customers import CustomerImporter
from .importers.vendors import SupplierImporter
from .importers.employees import EmployeeImporter
from .importers.items import ItemImporter
from .importers.inventory_adjustments import InventoryAdjustmentImporter
from .importers.purchase_orders import PurchaseOrderImporter
from .importers.sales_orders import SalesOrderImporter
from .importers.estimates import EstimateImporter
from .importers.bills import PurchaseInvoiceImporter
from .importers.invoices import SalesInvoiceImporter
from .importers.sales_receipts import SalesReceiptImporter
from .importers.credit_memos import CreditMemoImporter
from .importers.bill_payments import BillPaymentImporter
from .importers.payments import PaymentsImporter
from .importers.sales_tax_items import SalesTaxItemsImporter
from .importers.sales_tax_codes import SalesTaxCodesImporter
from .importers.deposits import DepositImporter
from .importers.transfers import TransfersImporter
from .importers.cc_charges import CCChargesImporter
from .importers.journal_entries import JournalEntryImporter
from .importers.checks import ChecksImporter
from .importers.vendor_credits import VendorCreditImporter
from .importers.item_receipts import ItemReceiptImporter
from .importers.quantity_discounts import QuantityDiscountImporter
from .importers.other_names import OtherNamesImporter
from .fiscal_years import ensure_fiscal_years


def _prepare_detailed_log_file() -> Path:
    app_dir = Path(__file__).resolve().parents[3]
    log_dir = app_dir / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now_datetime().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"qb_migration_failed_skipped_{timestamp}.log"
    frappe.flags["qb_migration_detailed_log_path"] = str(log_path)

    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"QB Migration detailed failed/skipped log\n")
        handle.write(f"Started: {now_datetime()}\n")
        handle.write("=" * 80 + "\n")

    return log_path


PIPELINE = [
    ("accounts", AccountImporter),
    ("payment_methods", PaymentMethodsImporter),
    ("terms", TermsImporter),
    ("items", ItemImporter),
    ("inventory_adjustments", InventoryAdjustmentImporter),
    ("price_levels", PriceLevelsImporter),
    ("customer_types", CustomerTypesImporter),
    ("vendor_types", VendorTypesImporter),
    ("customers", CustomerImporter),
    ("vendors", SupplierImporter),
    ("employees", EmployeeImporter),
    ("purchase_orders", PurchaseOrderImporter),
    ("sales_orders", SalesOrderImporter),
    ("estimates", EstimateImporter),
    ("bills", PurchaseInvoiceImporter),
    ("invoices", SalesInvoiceImporter),
    ("sales_receipts", SalesReceiptImporter),
    ("credit_memos", CreditMemoImporter),
    ("bill_payments", BillPaymentImporter),
    ("payments", PaymentsImporter),
    ("sales_tax_items", SalesTaxItemsImporter),
    ("sales_tax_codes", SalesTaxCodesImporter),
    ("deposits", DepositImporter),
    ("transfers", TransfersImporter),
    ("checks", ChecksImporter),
    ("cc_charges", CCChargesImporter),
    ("journal_entries", JournalEntryImporter),
    ("vendor_credits", VendorCreditImporter),
    ("item_receipts", ItemReceiptImporter),
    ("quantity_discounts", QuantityDiscountImporter),
    ("other_names", OtherNamesImporter),
]


def run_migration(stages=None, dry_run=False):
    """Run a migration pipeline from JSON files.

    Execute via bench:
        bench --site <site> execute qb_migration.qb_migration.migration.runner.run_migration
    """
    frappe.flags.in_migrate = True
    frappe.set_user("Administrator")
    log_path = _prepare_detailed_log_file()
    print(f"\n=== Detailed failed/skipped log ===")
    print(f"Log file: {log_path}")
    print("=== Fiscal Year Preparation ===")
    ensure_fiscal_years()
    results = {}

    for stage_name, ImporterClass in PIPELINE:
        if stages and stage_name not in stages:
            continue

        print(f"\n{'=' * 50}\nRunning stage: {stage_name}\n{'=' * 50}")
        importer = ImporterClass()
        results[stage_name] = importer.run(dry_run=dry_run)

    print("\n\n=== MIGRATION SUMMARY ===")
    print(f"Detailed failed/skipped log: {log_path}")
    for stage, result in results.items():
        print(f"  {stage:25s}: ✓ {result['success']:5d}  ✗ {result['failed']:5d}  ↷ {result['skipped']:5d}")

    return results
