import frappe

from ..base_importer import BaseImporter


class EmployeeImporter(BaseImporter):
    source_type = "QB_EMPLOYEE"
    target_doctype = "Employee"
    json_file = "employees.json"
    json_key = "employees"

    def map_record(self, record):
        doc = {
            "doctype": "Employee",
            "employee_name": record.get("name", ""),
            "employee_number": record.get("list_id", ""),
            "first_name": record.get("first_name", ""),
            "last_name": record.get("last_name", ""),
            "cell_number": record.get("phone", ""),
            "company_email": record.get("email", ""),
            "address_line1": record.get("addr1", ""),
            "address_line2": record.get("addr2", ""),
            "city": record.get("city", ""),
            "state": record.get("state", ""),
            "pincode": record.get("zip", ""),
            "status": "Active" if record.get("active") else "Left",
        }

        return doc
