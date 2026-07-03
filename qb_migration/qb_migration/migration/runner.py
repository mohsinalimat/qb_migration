import frappe

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

PIPELINE = [
    # ("accounts", AccountImporter),
    # ("payment_methods", PaymentMethodsImporter),
    # ("terms", TermsImporter),
    # # ("item_groups", ItemGroupImporter),
    # ("items", ItemImporter),
    # ("price_levels", PriceLevelsImporter),
    # ("customer_types", CustomerTypesImporter),
    # ("vendor_types", VendorTypesImporter),
    # ("customers", CustomerImporter),
    # ("vendors", SupplierImporter),
    # ("employees", EmployeeImporter),
    # ("purchase_orders", PurchaseOrderImporter),
    # ("sales_orders", SalesOrderImporter),
    # ("estimates", EstimateImporter),
    # ("bills", PurchaseInvoiceImporter),
    # ("invoices", SalesInvoiceImporter),
    # ("sales_receipts", SalesReceiptImporter),
    # ("credit_memos", CreditMemoImporter),
    # ("bill_payments", BillPaymentImporter),
    # ("payments", PaymentsImporter),
    ("sales_tax_items", SalesTaxItemsImporter),
    # ("deposits", DepositImporter),
    # ("transfers", TransfersImporter),
    # ("checks", ChecksImporter),
    # ("cc_charges", CCChargesImporter),
    # ("journal_entries", JournalEntryImporter),
    # ("vendor_credits", VendorCreditImporter),
    # ("item_receipts", ItemReceiptImporter),
    # ("quantity_discounts", QuantityDiscountImporter),
    # ("other_names", OtherNamesImporter),
]


def run_migration(stages=None, dry_run=False):
    """Run a migration pipeline from JSON files.

    Execute via bench:
        bench --site <site> execute qb_migration.qb_migration.migration.runner.run_migration
    """
    frappe.flags.in_migrate = True
    frappe.set_user("Administrator")
    print("\n=== Fiscal Year Preparation ===")
    ensure_fiscal_years()
    results = {}

    for stage_name, ImporterClass in PIPELINE:
        if stages and stage_name not in stages:
            continue

        print(f"\n{'=' * 50}\nRunning stage: {stage_name}\n{'=' * 50}")
        importer = ImporterClass()
        results[stage_name] = importer.run(dry_run=dry_run)

    print("\n\n=== MIGRATION SUMMARY ===")
    for stage, result in results.items():
        print(f"  {stage:25s}: ✓ {result['success']:5d}  ✗ {result['failed']:5d}  ↷ {result['skipped']:5d}")

    return results
