import frappe


def add_unique_index():
    try:
        frappe.db.add_unique("Migration Log", ["source_id", "source_type"])
    except Exception:
        frappe.db.commit()
