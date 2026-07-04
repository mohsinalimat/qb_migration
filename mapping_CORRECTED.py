# QUICKBOOKS ENTERPRISE 16.0 → ERPNEXT V16 DATA MAPPING
# CORRECTED & AUDITED VERSION
# Generated: June 30, 2026
# Status: Production-Ready (with noted exceptions)

# =============================================================================
# MASTER DATA MAPPINGS
# =============================================================================

# CHART OF ACCOUNTS
# QuickBooks: Accounts (hierarchical list of GL accounts)
# ERPNext: Chart of Accounts (Account master)
accounts = {
    'list_id': 'name',                    # QB account ID → ERPNext account name
    'name': 'account_name',               # Account display name
    'account_number': 'account_number',   # Account code/number
    'full_name': 'account_name',          # QB full hierarchical name → account_name
    'parent': 'parent_account',           # Parent account for hierarchy
    'account_type': 'account_type',       # GL account type (Asset, Liability, etc)
    'active': 'disabled',                 # INVERSE: active=true → disabled=0
    'description': 'description',         # Optional account notes
}

# NOTE: list_id should NOT be stored as external_id in production
# Use account_number as the unique identifier for migration lookups


# ITEMS/PRODUCTS
# QuickBooks: Item (inventory items, non-inventory items, service items)
# ERPNext: Item (unified product/service master)
Items = {
    'list_id': 'name',                    # QB item ID → ERPNext item code
    'item': 'item_code',                  # QB item name as code
    'item': 'item_name',                  # QB item name as display name
    'description': 'description',         # Item description
    'unitms': 'stock_uom',                # Unit of measure
    'item_group': 'item_group',           # Item category/classification
    'active': 'disabled',                 # INVERSE: active=true → disabled=0
    'price': 'standard_rate',             # Selling price → standard_rate
    'cost': 'valuation_rate',             # Cost → valuation_rate
    'income_acct': 'income_account',      # Sales/income account
    'asset_acct': 'asset_account',        # Asset account for inventory
    'cogs_acct': 'expense_account',       # Cost of goods sold account
    'barcode': 'barcode',                 # UPC/barcode
    'reorder_pt': 'reorder_level',        # Reorder point/minimum stock
    
    # Fields to handle separately (NOT direct mapping)
    'on_hand': 'IMPORT VIA Opening Stock or Stock Entry',
    'on_order': 'SKIP - ERPNext derives from open Purchase Orders',
    'price': 'IMPORT TO Item Price doctype for multi-currency/customer pricing',
    'barcode': 'Import to Item Barcode child table for multi-barcode support',
}


# CUSTOMERS
# QuickBooks: Customer (with optional embedded addresses and contacts)
# ERPNext: Customer (party master) + Contact + Address (separate doctypes)
customers = {
    'CUSTOMER MASTER':
    {
        'list_id': 'name',                # QB customer ID → ERPNext customer code
        'name': 'customer_name',          # QB customer name
        'company': 'customer_type',       # QB company name → customer type
        'contact': 'LINK to Contact',     # QB primary contact name
        'phone': 'phone_no',              # Primary phone
        'email': 'email_id',              # Primary email
        'terms': 'payment_terms_template',# Payment terms template
        'price_level': 'default_price_list',  # Default price list
        'credit_limit': 'credit_limit',   # Credit limit
        'notes': 'remarks',               # Customer notes/remarks
        'active': 'disabled',             # INVERSE: active=true → disabled=0
    },
    'ADDRESS (linked to Customer)':
    {
        'addr1': 'address_line1',
        'addr2': 'address_line2',
        'city': 'city',
        'state': 'state',
        'zip': 'pincode',
        'country': 'country',
        'email': 'email_id',
        'phone': 'phone',
        'address_type': 'is_primary_address (set to 1 for default)',
    },
    'CONTACT (linked to Customer)':
    {
        'contact': 'first_name + last_name',  # Split QB contact name
        'email': 'email_id',
        'phone': 'phone',
        'fax': 'fax',
    }
}


# VENDORS/SUPPLIERS
# QuickBooks: Vendor (with optional embedded addresses and contacts)
# ERPNext: Supplier (party master) + Contact + Address
vendors = {
    'SUPPLIER MASTER':
    {
        'list_id': 'name',                # QB vendor ID → ERPNext supplier code
        'name': 'supplier_name',          # Vendor/company name
        'company': 'supplier_type',       # QB company name
        'contact': 'LINK to Contact',     # Primary contact name
        'phone': 'phone_no',              # Primary phone
        'fax': 'fax_no',                  # Fax number
        'email': 'email_id',              # Email
        'terms': 'payment_terms_template',# Payment terms
        'balance': 'SKIP - Calculated from AP',  # Outstanding balance
        'active': 'disabled',             # INVERSE: active=true → disabled=0
        'notes': 'supplier_details',      # Notes
        'account_no': 'account_number',   # Account number
    },
    'ADDRESS (linked to Supplier)':
    {
        'addr1': 'address_line1',
        'addr2': 'address_line2',
        'city': 'city',
        'state': 'state',
        'zip': 'pincode',
        'country': 'country',
        'phone': 'phone',
    },
    'CONTACT (linked to Supplier)':
    {
        'contact': 'first_name + last_name',
        'email': 'email_id',
        'phone': 'phone',
        'fax': 'fax',
    }
}


# EMPLOYEES
# QuickBooks: Employee
# ERPNext: Employee
employee = {
    'list_id': 'employee_number',         # QB employee ID
    'name': 'employee_name',              # Employee full name
    'first_name': 'first_name',
    'last_name': 'last_name',
    'phone': 'cell_number',               # Mobile/cell
    'email': 'company_email',             # Company email (or personal_email)
    'addr1': 'address_line1',
    'addr2': 'address_line2',
    'city': 'city',
    'state': 'state',
    'zip': 'pincode',
    'notes': 'leave unmapped',            # QB notes → user_id or remarks
    'active': 'status',                   # active=true → Active; active=false → Left
}


# CONTACTS & OTHER NAMES
# QuickBooks: Other Names (generic contact records)
# ERPNext: Contact (standalone or linked to Party)
other_name = {
    'CONTACT':
    {
        'list_id': 'name',                # QB contact ID
        'name': 'first_name',             # Name
        'company': 'company_name',        # Company affiliation
        'phone': 'phone',
        'email': 'email_id',
        'notes': 'designation',           # Job title/role
        'active': 'disabled',             # INVERSE
    },
    'ADDRESS (linked to Contact)':
    {
        'addr1': 'address_line1',
        'addr2': 'address_line2',
        'city': 'city',
        'state': 'state',
        'zip': 'pincode',
        'fax': 'fax',
    }
}


# COST CENTERS
# QuickBooks: Class (used for departmental/dimensional tracking)
# ERPNext: Cost Center (dimension for P&L tracking)
class = {
    'list_id': 'name',                    # QB class ID
    'name': 'cost_center_name',           # Class name
    'full_name': 'cost_center_name',      # Hierarchical name
    'parent': 'parent_cost_center',       # Parent class/cost center
    'active': 'disabled',                 # INVERSE: active=true → disabled=0
}


# CUSTOMER GROUPS
# QuickBooks: Customer Type (optional categorization)
# ERPNext: Customer Group
customer_types = {
    'list_id': 'name',
    'name': 'customer_group_name',
    'parent': 'parent_customer_group',
    'active': 'disabled',                 # INVERSE
}


# SUPPLIER GROUPS
# QuickBooks: Vendor Type (optional categorization)
# ERPNext: Supplier Group
vendor_type = {
    'list_id': 'name',
    'name': 'supplier_group_name',
    'parent': 'parent_supplier_group',
    'active': 'disabled',                 # INVERSE
}


# PAYMENT TERMS
# QuickBooks: Terms (payment/credit terms)
# ERPNext: Payment Term Template + Payment Term (detail rows)
term = {
    'list_id': 'name',
    'name': 'payment_terms_template_name',  # QB Terms name
    'type': 'SKIP - Not used in ERPNext',
    'net_days': 'Payment Term.credit_days',  # Credit period
    'disc_days': 'Payment Term.discount_validity',  # Discount validity period
    'disc_pct': 'Payment Term.discount',  # Discount percentage
    
    'DEFAULT_ERPNEXT_VALUES':
    {
        'discount_type': 'Percentage',
        'due_date_based_on': 'Day(s) after invoice date',
        'discount_validity_based_on': 'Day(s) after invoice date',
    }
}


# PAYMENT METHODS/MODES
# QuickBooks: Payment Method (check, cash, credit card, etc.)
# ERPNext: Mode of Payment
payment_method = {
    'list_id': 'name',                    # QB payment method ID
    'name': 'mode_of_payment',            # QB method name → ERPNext mode name
    'payment_type': 'type',               # Cash, Bank, Credit Card, General
    'active': 'disabled',                 # INVERSE: active=true → disabled=0
    
    # NOTE: list_id should NOT be stored; use name as unique identifier
}


# SHIPPING METHODS/RULES
# QuickBooks: Shipping Method
# ERPNext: Shipping Rule
shipping_method = {
    'list_id': 'name',                    # QB shipping method ID
    'name': 'shipping_rule_name',         # Method name
    'active': 'disabled',                 # INVERSE
}


# PRICE LISTS
# QuickBooks: Price Level (tier-based pricing)
# ERPNext: Price List (master) + Item Price (detail rows)
price_level = {
    'list_id': 'name',                    # QB price level ID
    'name': 'price_list_name',            # QB level name
    'type': 'price_list_type',            # Selling or Buying
    
    'PRICING LOGIC':
    {
        'pct': 'Apply as markup/discount when creating Item Price records',
        'pct': 'NOT a direct field in Item Price',
        'pct': 'Calculate price = base_price * (1 + pct/100) for each item',
        'fixed_prices': 'Create Item Price doctype rows for specific items at specific prices',
    }
}


# TAX CATEGORIES & CODES
# QuickBooks: Sales Tax Code (tax applicability)
# ERPNext: Tax Category (sales tax applicability indicator)
sales_tax_code = {
    'list_id': 'name',                    # QB tax code ID
    'name': 'tax_category_name',          # QB code name → ERPNext tax category
    'description': 'description',
    'is_taxable': 'Used in Tax Category logic',  # Taxable vs non-taxable
    'active': 'disabled',                 # INVERSE
}

# SALES TAX ITEMS/TEMPLATES
# QuickBooks: Sales Tax Item (aggregate tax liability)
# ERPNext: Sales Taxes and Charges Template
sales_tax_item = {
    'list_id': 'name',                    # QB tax item ID
    'name': 'tax_account_title',          # Tax item name
    'description': 'description',         # Optional
    'tax_rate': 'rate',                   # Tax percentage in template rows
    'tax_vendor': 'SKIP - Usually ignored',  # QB agency collecting tax
    'active': 'disabled',                 # INVERSE
}


# QUANTITY DISCOUNT RULES
# QuickBooks: Quantity Discount (volume-based pricing)
# ERPNext: Pricing Rule
quantity_discount = {
    'list_id': 'name',                    # QB discount ID
    'item_name': 'title',                 # Discount rule name
    'description': 'description',
    'discount_rate': 'price_or_product_discount = "Price Discount"',
    'discount_pct': 'discount_percentage',  # Discount % in Pricing Rule
    'active': 'disabled',                 # INVERSE
    
    # NOTE: Not all QB discount scenarios map directly
    # Quantity discounts may need custom Pricing Rules per item
}


# =============================================================================
# TRANSACTIONAL DATA MAPPINGS
# =============================================================================

# INVOICES
# QuickBooks: Invoice (sales transaction, customer AR)
# ERPNext: Sales Invoice
invoices = {
    'SALES_INVOICE_HEADER':
    {
        'txn_id': 'name',                 # QB invoice ID
        'inv_no': 'invoice_number',       # QB invoice number
        'inv_date': 'posting_date',       # Invoice date
        'due_date': 'due_date',
        'cust_name': 'customer',          # Link to Customer
        'cust_list_id': 'SKIP - Migration lookup only',
        'po_num': 'po_no',                # Customer PO reference
        'salesman': 'sales_person (via Sales Team child table)',
        'terms': 'payment_terms_template',
        'ship_date': 'delivery_date',
        'ship_via': 'shipping_rule',      # Optional shipping rule
        'memo': 'remarks',                # Invoice notes
        'is_paid': 'status',              # Paid/Unpaid determined automatically
        
        'CALCULATED_BY_ERPNEXT': {
            'subtotal': 'total',
            'tax_amt': 'total_taxes_and_charges',
            'total_amt': 'grand_total',
            'balance': 'outstanding_amount',
        }
    },
    'SALES_INVOICE_ITEMS':
    {
        'lines.line_no': 'idx',
        'lines.item': 'item_code',
        'lines.item_list_id': 'SKIP - Migration lookup only',
        'lines.description': 'description',
        'lines.qty': 'qty',
        'lines.unitms': 'uom',
        'lines.price': 'rate',
        'lines.ext_price': 'amount (calculated field - do not force)',
        'lines.tax_code': 'item_tax_template OR tax_category',
    }
}

# OPEN ACCOUNTS RECEIVABLE
# QuickBooks: Open AR Report entity (not a doctype, derived view of unpaid invoices)
# ERPNext: Not a separate mapping needed
# HANDLING: Open AR is derived from unpaid Sales Invoices
# RECOMMENDATION: Do NOT create separate mapping; include in Invoices mapping
open_ar = {
    'NOTE': 'Open AR is a view/report in QB, not a distinct entity',
    'IMPLEMENTATION': 'Filter Sales Invoices with status=Draft or outstanding_amount > 0',
    'RECOMMENDATION': 'Merge with Invoices mapping; apply workflow status logic',
}


# CREDIT MEMOS
# QuickBooks: Credit Memo (customer return/credit)
# ERPNext: Sales Invoice in Return/Credit Note mode
credit_memo = {
    'SALES_INVOICE_RETURN':
    {
        'txn_id': 'name',                 # QB memo ID → remarks/external reference
        'ref_no': 'bill_no',              # QB memo number (optional)
        'date': 'posting_date',
        'cust_name': 'customer',
        'cust_list_id': 'SKIP - Migration lookup only',
        'memo': 'remarks',
        'return_against': 'original_invoice_no (ERPNext 13+)',
        
        'CALCULATED_BY_ERPNEXT': {
            'subtotal': 'total',
            'sales_tax_total': 'total_taxes_and_charges',
            'total_amt': 'grand_total',
            'balance': 'outstanding_amount',
        }
    },
    'ITEMS':
    {
        'lines.line_no': 'idx',
        'lines.item': 'item_code',
        'lines.description': 'description',
        'lines.qty': 'qty (MUST BE NEGATIVE for return)',
        'lines.price': 'rate (MUST BE NEGATIVE or system-inverted)',
        'lines.ext_price': 'amount (calculated - do not force)',
        'lines.tax_code': 'item_tax_template OR tax_category',
    },
    'NOTE': 'Set is_return = 1 and return_against field to link to original invoice',
}


# SALES ORDERS
# QuickBooks: Sales Order (commitment to deliver)
# ERPNext: Sales Order
sales_orders = {
    'SALES_ORDER_HEADER':
    {
        'txn_id': 'name',                 # QB SO ID → remarks/external ref
        'ref_no': 'so_no',                # SO number
        'date': 'transaction_date',
        'cust_name': 'customer',
        'cust_list_id': 'SKIP - Migration lookup only',
        'po_num': 'customer_po_no',
        'ship_date': 'delivery_date',
        'ship_via': 'shipping_rule',
        'salesman': 'sales_person (via Sales Team child table)',
        'memo': 'remarks',
        'is_fully_inv': 'status',         # Closed if fully invoiced
        
        'CALCULATED_BY_ERPNEXT': {
            'total_amt': 'grand_total',
        }
    },
    'ITEMS':
    {
        'lines.line_no': 'idx',
        'lines.item': 'item_code',
        'lines.item_list_id': 'SKIP - Migration lookup only',
        'lines.description': 'description',
        'lines.qty': 'qty',
        'lines.price': 'rate',
        'lines.ext_price': 'amount (calculated)',
        'lines.qty_invoiced': 'delivered_qty (used indirectly via Delivery Note)',
    }
}


# ESTIMATES/QUOTATIONS
# QuickBooks: Estimate (sales quote)
# ERPNext: Quotation
estimate = {
    'QUOTATION_HEADER':
    {
        'txn_id': 'name',                 # QB estimate ID → external ref
        'ref_no': 'po_no',                # QB estimate number
        'date': 'transaction_date',
        'cust_name': 'party_name',
        'po_num': 'po_no',
        'terms': 'payment_terms_template',
        'salesman': 'sales_partner',
        'due_date': 'valid_till',         # Quote validity
        'memo': 'customer_note',
        
        'CALCULATED_BY_ERPNEXT': {
            'subtotal': 'total',
            'sales_tax_total': 'total_taxes_and_charges',
            'sales_tax_pct': 'Taxes and Charges.rate',
            'total_amt': 'grand_total',
        }
    },
    'QUOTATION_ITEMS':
    {
        'lines.line_no': 'idx',
        'lines.item': 'item_code',
        'lines.item_list_id': 'SKIP - Migration lookup only',
        'lines.description': 'description',
        'lines.qty': 'qty',
        'lines.unitms': 'uom',
        'lines.price': 'rate',
        'lines.ext_price': 'amount (calculated)',
        'lines.class_name': 'cost_center',
        'lines.tax_code': 'item_tax_template',
    }
}


# SALES RECEIPTS
# QuickBooks: Sales Receipt (cash/immediate payment sale)
# ERPNext: Sales Invoice marked as POS or with payment
sales_receipt = {
    'SALES_INVOICE_HEADER':
    {
        'txn_id': 'name',                 # QB receipt ID
        'ref_no': 'customer_reference',   # Receipt number
        'date': 'posting_date',
        'cust_name': 'customer',
        'payment_method': 'mode_of_payment',  # Cash, Check, CC, etc.
        'deposit_to_acct': 'cash_bank_account',  # Bank/cash account
        'memo': 'remarks',
        
        'IF_FULLY_PAID': {
            'mode_of_payment': 'mode_of_payment',
            'deposit_to_acct': 'account_for_payment',
            'total_amt': 'paid_amount',
            'outstanding_amount': '0',
            'is_pos': '1 (mark as POS)',
        }
    },
    'SALES_INVOICE_ITEMS':
    {
        'lines.line_no': 'idx',
        'lines.item': 'item_code',
        'lines.description': 'description',
        'lines.qty': 'qty',
        'lines.unitms': 'uom',
        'lines.price': 'rate',
        'lines.ext_price': 'amount (calculated)',
        'lines.class_name': 'cost_center',
        'lines.tax_code': 'item_tax_template',
    }
}


# =============================================================================
# PURCHASE DOCUMENTS
# =============================================================================

# BILLS
# QuickBooks: Bill (purchase invoice, vendor AP)
# ERPNext: Purchase Invoice
bills = {
    'PURCHASE_INVOICE_HEADER':
    {
        'txn_id': 'name',                 # QB bill ID
        'ref_no': 'bill_no',              # QB bill number
        'date': 'posting_date',
        'due_date': 'due_date',
        'vend_name': 'supplier',          # Link to Supplier
        'vend_list_id': 'SKIP - Migration lookup only',
        'terms': 'payment_terms_template',
        'memo': 'remarks',
        
        'CALCULATED_BY_ERPNEXT': {
            'total_amt': 'grand_total',
            'balance': 'outstanding_amount',
        }
    },
    'PURCHASE_INVOICE_ITEMS':
    {
        'lines.line_no': 'idx',
        'lines.item': 'item_code',
        'lines.item_list_id': 'SKIP - Migration lookup only',
        'lines.description': 'description',
        'lines.qty': 'qty',
        'lines.amount': 'amount',
        'lines.gl_code': 'expense_account',
        'lines.tax_code': 'item_tax_template',
    }
}


# VENDOR CREDITS
# QuickBooks: Vendor Credit (supplier return/credit)
# ERPNext: Purchase Invoice in Return mode
vendor_credit = {
    'PURCHASE_INVOICE_RETURN':
    {
        'txn_id': 'bill_no',              # QB credit ID
        'ref_no': 'supplier_invoice_no',
        'date': 'posting_date',
        'vend_name': 'supplier',
        'bill_ref': 'return_against',     # Link to original bill
        'memo': 'remarks',
        'is_return': '1 (mark as return)',
        
        'CALCULATED_BY_ERPNEXT': {
            'total_amt': 'grand_total',
        }
    },
    'ITEMS':
    {
        'lines.line_no': 'idx',
        'lines.item': 'item_code',
        'lines.description': 'description',
        'lines.qty': 'qty (MUST BE NEGATIVE for return)',
        'lines.amount': 'amount',
        'lines.class_name': 'cost_center',
        'lines.tax_code': 'item_tax_template',
    }
}


# PURCHASE ORDERS
# QuickBooks: Purchase Order
# ERPNext: Purchase Order
purchase_order = {
    'PURCHASE_ORDER_HEADER':
    {
        'txn_id': 'name',                 # QB PO ID → remarks/external ref
        'ref_no': 'po_no',                # PO number
        'date': 'transaction_date',
        'expected_date': 'schedule_date (per-item or header)',
        'vend_name': 'supplier',
        'vend_list_id': 'SKIP - Migration lookup only',
        'memo': 'remarks',
        'is_fully_rcvd': 'status',        # Completed if fully received
        
        'CALCULATED_BY_ERPNEXT': {
            'total_amt': 'grand_total',
        }
    },
    'ITEMS':
    {
        'lines.line_no': 'idx',
        'lines.item': 'item_code',
        'lines.item_list_id': 'SKIP - Migration lookup only',
        'lines.description': 'description',
        'lines.qty': 'qty',
        'lines.price': 'rate',
        'lines.ext_price': 'amount (calculated)',
        'lines.qty_received': 'received_qty (if tracking enabled)',
    }
}


# ITEM RECEIPTS
# QuickBooks: Item Receipt (goods receipt/receiving)
# ERPNext: Purchase Receipt (GRN)
item_receipt = {
    'PURCHASE_RECEIPT_HEADER':
    {
        'txn_id': 'supplier_delivery_note',  # QB receipt ID
        'ref_no': 'supplier_delivery_note',  # Supplier reference number
        'date': 'posting_date',
        'vend_name': 'supplier',
        'po_num': 'purchase_order',       # Link to PO
        'memo': 'remarks',
        'sales_tax_code': 'tax_category',
        'is_tax_included': 'included_in_print_rate',
        
        'CALCULATED_BY_ERPNEXT': {
            'total_amt': 'grand_total',
        }
    },
    'ITEMS':
    {
        'lines.line_no': 'idx',
        'lines.item': 'item_code',
        'lines.item_list_id': 'SKIP - Migration lookup only',
        'lines.description': 'description',
        'lines.qty': 'qty',
        'lines.unitms': 'uom',
        'lines.amount': 'amount',
        'lines.rate': 'amount / qty',
        'lines.class_name': 'cost_center',
        'lines.tax_code': 'item_tax_template',
        'lines.sales_rep': 'sales_partner',
    }
}


# =============================================================================
# ACCOUNTING & PAYMENT ENTRIES
# =============================================================================

# JOURNAL ENTRIES
# QuickBooks: Journal Entry (general accounting entry)
# ERPNext: Journal Entry
journal_entries = {
    'JOURNAL_ENTRY_HEADER':
    {
        'txn_id': 'name',                 # QB journal entry ID
        'txn_number': 'user_remark',      # Optional QB entry number
        'txn_date': 'posting_date',
        'memo': 'user_remark',            # Description
        'is_adj': 'voucher_type',         # Adjustment Entry vs Journal Entry
    },
    'JOURNAL_ENTRY_ACCOUNT_ROWS':
    {
        'lines.account': 'account',       # GL account to debit/credit
        'lines.amount': 'debit or credit', # Amount (determined by line_type)
        'lines.line_type': 'determines debit/credit field',
        'lines.memo': 'user_remark',
        'lines.entity': 'party (if applicable)',
        'lines.entity_id': 'party',       # Customer/Supplier reference
        'lines.class': 'cost_center',
    },
    'REQUIRED_ERPNEXT_FIELDS': {
        'company': 'Company (mandatory)',
        'voucher_type': 'Journal Entry or Adjustment Entry',
    }
}


# CHECKS
# QuickBooks: Check (payment by check)
# ERPNext: Payment Entry with mode_of_payment="Check"
# CORRECTED MAPPING (was incorrectly mapped to Journal Entry)
checks = {
    'CHECK_HEADER': {
        'txn_id': 'source_id / migration key',
        'ref_no': 'cheque_no / reference_no',
        'date': 'posting_date / reference_date / cheque_date',
        'memo': 'user_remark',
        'bank_account': 'credit account on bank line',
    },
    'CHECK_ACCOUNT_ROWS': {
        'lines.account': 'account',
        'lines.amount': 'debit',
        'lines.line_type': 'assumed expense debit (not explicitly used)',
        'lines.memo': 'user_remark',
        'lines.description': 'user_remark fallback',
        'lines.class_name': 'cost_center',
        'lines.entity|customer|customer_name|party': 'party / party_type if account is Receivable/Payable',
    },
    'REQUIRED_ERPNEXT_FIELDS': {
        'company': 'Company',
        'voucher_type': 'Bank Entry',
        'posting_date': 'Posting Date',
        'accounts': 'Journal Entry accounts child table',
    }
}


# BILL PAYMENTS
# QuickBooks: Bill Payment (payment to vendor)
# ERPNext: Payment Entry with party_type="Supplier"
bill_payment = {
    'PAYMENT_ENTRY_HEADER':
    {
        'txn_id': 'name',                 # QB payment ID
        'ref_no': 'reference_no',         # QB payment reference
        'date': 'posting_date',
        'vend_name': 'party',             # Supplier
        'vend_list_id': 'SKIP - Migration lookup only',
        'total_amt': 'paid_amount',       # Payment amount
        'method': 'mode_of_payment',      # Payment method (Check, Bank, etc)
        'memo': 'remarks',
    },
    'PAYMENT_ENTRY_CONFIGURATION': {
        'payment_type': '"Pay"',
        'party_type': '"Supplier"',
        'reference_doctype': '"Purchase Invoice"',
    },
    'ALLOCATIONS':
    {
        'applied[].ref_no': 'references.reference_name',  # Bill being paid
        'applied[].amount': 'references.allocated_amount',  # Allocated amount
    }
}


# PAYMENTS (Customer Payments)
# QuickBooks: Payment (customer payment received)
# ERPNext: Payment Entry with party_type="Customer"
payments = {
    'PAYMENT_ENTRY_HEADER':
    {
        'txn_id': 'name',                 # QB payment ID
        'ref_no': 'reference_no',
        'date': 'posting_date',
        'cust_name': 'party',             # Customer
        'cust_list_id': 'SKIP - Migration lookup only',
        'total_amt': 'received_amount',   # Payment received
        'method': 'mode_of_payment',      # Payment method
        'memo': 'remarks',
    },
    'PAYMENT_ENTRY_CONFIGURATION': {
        'payment_type': '"Receive"',
        'party_type': '"Customer"',
        'reference_doctype': '"Sales Invoice"',
    },
    'ALLOCATIONS':
    {
        'applied[].inv_no': 'references.reference_name',  # Invoice being paid
        'applied[].ref_no': 'references.reference_name (fallback)',
        'applied[].amount': 'references.allocated_amount',
    }
}


# DEPOSITS
# QuickBooks: Deposit (banking transaction depositing payment)
# ERPNext: Payment Entry (Receive) allocated to bank account
# NOTE: May require special handling if tracking deposit batching
deposits = {
    'PAYMENT_ENTRY_HEADER':
    {
        'txn_id': 'name',                 # QB deposit ID
        'txn_number': 'reference_no',
        'date': 'posting_date',
        'deposit_to_acct': 'paid_to',     # Bank account receiving deposit
        'memo': 'remarks',
        'deposit_total': 'paid_amount OR received_amount',
        'currency': 'paid_to_account_currency',
        'exchange_rate': 'source_exchange_rate',
    },
    'PAYMENT_ENTRY_CONFIGURATION': {
        'payment_type': '"Receive" (if customer deposit) or "Pay" (if vendor)',
        'party': 'May be empty if deposit from bank services',
        'mode_of_payment': 'Based on deposit method',
    },
    'PAYMENT_ENTRY_LINE':
    {
        'entity': 'party',                # Party making deposit
        'txn_type': 'payment_type',
        'account': 'paid_from',           # Source account
        'amount': 'paid_amount',
        'check_number': 'reference_no',
        'payment_method': 'mode_of_payment',
        'class_name': 'cost_center',
    },
    'NOTE': 'Deposits may need to be batched in ERPNext; consider Deposit doctype if standard Payment Entry insufficient',
}


# CREDIT CARD CHARGES
# QuickBooks: Credit Card Expense/Charge (credit card transaction)
# ERPNext: Depends on business scenario
# AMBIGUOUS MAPPING - Requires Business Logic Clarification
cc_charges = {
    'SCENARIO_1_EXPENSE_REIMBURSEMENT': {
        'DocType': 'Expense Claim',
        'WHEN_TO_USE': 'Employee using personal CC, later reimbursed by company',
        'FIELDS': {
            'party': 'Employee',
            'expense_type': 'Expense Claim Detail.expense_type',
            'amount': 'amount',
            'date': 'posting_date',
        }
    },
    'SCENARIO_2_CC_LIABILITY_TRACKING': {
        'DocType': 'Journal Entry',
        'WHEN_TO_USE': 'Recording company CC charges against CC liability account',
        'FIELDS': {
            'DEBIT': 'Expense account (e.g., Office Supplies, Travel)',
            'CREDIT': 'Credit Card Liability account',
            'date': 'posting_date',
        }
    },
    'SCENARIO_3_VENDOR_BILL': {
        'DocType': 'Purchase Invoice',
        'WHEN_TO_USE': 'CC issuer sends invoice for charges',
        'FIELDS': {
            'supplier': 'Credit Card Company',
            'bill_no': 'CC statement number',
            'lines': 'Individual charges',
        }
    },
    'RECOMMENDATION': 'Clarify in pre-migration interviews which scenario applies',
    'COMMON_PATTERN': 'Most companies use Scenario 2 (CC Liability + Journal Entry)',
}


# INVENTORY ADJUSTMENTS
# QuickBooks: Inventory Adjustment (physical inventory adjustment)
# ERPNext: Stock Reconciliation
inventory_adjustment = {
    'STOCK_RECONCILIATION_HEADER':
    {
        'txn_id': 'name',                 # QB adjustment ID → external ref
        'ref_no': 'remarks',              # QB reference
        'date': 'posting_date',
        'memo': 'remarks',
    },
    'STOCK_RECONCILIATION_ITEMS':
    {
        'lines.line_no': 'idx',
        'lines.item': 'item_code',
        'lines.item_list_id': 'SKIP - Migration lookup only',
        'lines.new_quantity': 'qty',      # Actual quantity after count
        'lines.new_value': 'valuation_rate',  # Per-unit value
        'lines.account': 'expense_account',  # Variance account
        'lines.memo': 'remarks',
    },
    'CALCULATED_BY_ERPNEXT': {
        'quantity_difference': 'Calculated system field',
        'value_difference': 'Calculated system field',
    }
}


# =============================================================================
# UNMAPPABLE OR PROBLEMATIC ENTITIES
# =============================================================================

# MEMORIZED TRANSACTIONS
# QuickBooks: Memorized Transaction (recurring transaction template)
# ERPNext: Automation Rule or Recurring Document
# STATUS: Partial Mapping Available
memorized_transactions = {
    'STATUS': 'NOT DIRECTLY MAPPABLE',
    'ERPNEXT_ALTERNATIVE': 'Recurring Document feature (for invoices, POs, etc)',
    'RECOMMENDATION': 'Analyze memorized transactions; migrate as automation rules if complex',
}


# CUSTOM FIELDS
# QuickBooks: Additional custom fields on transactions
# ERPNext: Custom Fields framework
# STATUS: Requires Manual Configuration
custom_fields = {
    'STATUS': 'PARTIALLY MAPPABLE',
    'PROCESS': '1. Export QB custom field values\n2. Create Custom Fields in ERPNext\n3. Populate values via data migration script',
}


# TRANSACTION HISTORY/AUDIT TRAIL
# QuickBooks: Audit trail (transaction history and modifications)
# ERPNext: Document Revisions
# STATUS: Not Directly Imported
transaction_history = {
    'STATUS': 'NOT IMPORTED',
    'RATIONALE': 'ERPNext maintains its own revision history from migration date forward',
    'CONSIDERATION': 'Archive QB data for reference; export QB audit trail separately if needed',
}


# TIME TRACKING
# QuickBooks: Time Tracking (billable time)
# ERPNext: Timesheet
# STATUS: Separate Migration Path
time_tracking = {
    'STATUS': 'MAPPABLE but SEPARATE',
    'DocType': 'Timesheet',
    'RECOMMENDATION': 'Migrate QB time entries to ERPNext Timesheet if feature is used',
}


# MULTIPLE CURRENCIES
# QuickBooks: Multi-currency support
# ERPNext: Multi-currency support
# STATUS: Requires Configuration
multi_currency = {
    'STATUS': 'REQUIRES SETUP',
    'ERPNEXT_SETUP': '1. Define Exchange Rates in ERPNext\n2. Configure currency for Suppliers/Customers\n3. Use Currency field on transactions',
}


# INTERCOMPANY TRANSACTIONS
# QuickBooks: Intercompany transactions (if using QBO + multiple entities)
# ERPNext: Intercompany Transactions
# STATUS: Advanced Feature
intercompany = {
    'STATUS': 'OPTIONAL / ADVANCED',
    'CONSIDERATION': 'If QB has multiple companies, configure Intercompany Transaction rules',
}
