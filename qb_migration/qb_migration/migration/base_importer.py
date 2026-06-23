import json
import traceback
from pathlib import Path

import frappe
from frappe.utils import getdate, nowdate, now_datetime

DATA_DIR = Path(__file__).parent.parent.parent / "data"


class BaseImporter:
    source_type: str = ""
    target_doctype: str = ""
    json_file: str = ""
    json_key: str = ""
    batch_size: int = 100
    allow_missing_file: bool = False

    def load_data(self):
        path = DATA_DIR / self.json_file
        if not path.exists():
            if self.allow_missing_file:
                print(f"  WARN: optional migration file missing: {path}, skipping stage.")
                return []
            raise FileNotFoundError(f"Missing migration file: {path}")

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        records = data.get(self.json_key)
        if records is None:
            raise ValueError(f"Missing JSON key '{self.json_key}' in {self.json_file}")
        return records

    def is_imported(self, source_id: str) -> bool:
        existing = frappe.db.get_value(
            "Migration Log",
            {
                "source_id": str(source_id),
                "source_type": self.source_type,
                "status": "Success",
            },
            ["name", "target_doctype", "target_name"],
            as_dict=True,
        )

        if not existing:
            return False

        target_doctype = existing.target_doctype
        target_name = existing.target_name
        if target_doctype and target_name and frappe.db.exists(target_doctype, target_name):
            return True

        frappe.delete_doc("Migration Log", existing.name, force=True)
        return False

    def log_success(self, source_id: str, target_name: str, target_doctype: str | None = None):
        target_doctype = target_doctype or self.target_doctype
        existing = frappe.db.get_value(
            "Migration Log",
            {"source_id": str(source_id), "source_type": self.source_type},
            "name",
        )
        if existing:
            frappe.delete_doc("Migration Log", existing, force=True)

        frappe.get_doc({
            "doctype": "Migration Log",
            "source_id": str(source_id),
            "source_type": self.source_type,
            "target_doctype": target_doctype,
            "target_name": target_name,
            "status": "Success",
            "imported_at": now_datetime(),
        }).insert(ignore_permissions=True)

    def log_failure(self, source_id: str, error: str):
        existing = frappe.db.get_value(
            "Migration Log",
            {"source_id": str(source_id), "source_type": self.source_type},
            "name",
        )
        if existing:
            frappe.delete_doc("Migration Log", existing, force=True)

        frappe.get_doc({
            "doctype": "Migration Log",
            "source_id": str(source_id),
            "source_type": self.source_type,
            "target_doctype": self.target_doctype,
            "status": "Failed",
            "error_msg": str(error)[:2000],
            "imported_at": now_datetime(),
        }).insert(ignore_permissions=True)

    def log_skip(self, source_id: str, reason: str, ref_no: str | None = None):
        existing = frappe.db.get_value(
            "Migration Log",
            {"source_id": str(source_id), "source_type": self.source_type},
            "name",
        )
        if existing:
            frappe.delete_doc("Migration Log", existing, force=True)

        skip_msg = reason
        if ref_no:
            skip_msg = f"{reason} ref_no={ref_no}"

        frappe.get_doc({
            "doctype": "Migration Log",
            "source_id": str(source_id),
            "source_type": self.source_type,
            "target_doctype": self.target_doctype,
            "status": "Skipped",
            "error_msg": skip_msg[:2000],
            "imported_at": now_datetime(),
        }).insert(ignore_permissions=True)

    def map_record(self, record: dict) -> dict:
        raise NotImplementedError("Subclasses must implement map_record")

    def find_existing_target(self, doc_data: dict) -> str | None:
        return None

    def get_source_id(self, record: dict) -> str:
        return str(record.get("list_id") or record.get("txn_id") or record.get("name") or record.get("ref_number") or "")

    def normalize_date(self, date_value):
        if not date_value:
            return nowdate()

        try:
            parsed = getdate(date_value)
        except Exception:
            return nowdate()

        fiscal_year = frappe.db.sql(
            "select name from `tabFiscal Year` where %s between year_start_date and year_end_date and docstatus<2 limit 1",
            parsed,
        )
        if fiscal_year:
            return parsed

        active_start = frappe.db.get_value("Fiscal Year", {"disabled": 0}, "year_start_date")
        return active_start or nowdate()

    def run(self, dry_run: bool = False):
        records = self.load_data()
        total = len(records)
        success = failed = skipped = 0

        print(f"\n[{self.source_type}] Starting: {total} records")

        for i, record in enumerate(records):
            source_id = self.get_source_id(record)
            if not source_id:
                failed += 1
                print(f"  FAIL: missing source id for record {i+1}")
                continue

            if self.is_imported(source_id):
                skipped += 1
                continue

            try:
                doc_data = self.map_record(record)
                if doc_data is None:
                    skipped += 1
                    continue

                if isinstance(doc_data, dict) and doc_data.get("_skip"):
                    skipped += 1
                    reason = doc_data.get("_skip_reason", "SKIPPED")
                    ref_no = doc_data.get("ref_no") or doc_data.get("reference_no") or record.get("ref_no") or record.get("ref_number")
                    print(f"  SKIP [{source_id}] ref_no={ref_no or 'N/A'} reason={reason}")
                    self.log_skip(source_id, reason, ref_no)
                    continue

                if dry_run:
                    print(f"  DRY RUN: {source_id} → {doc_data.get('name', doc_data.get('item_code', '?'))}")
                    success += 1
                    continue

                existing_target = self.find_existing_target(doc_data)
                if existing_target:
                    print(f"  SUCCESS: {source_id} → {existing_target}")
                    self.log_success(source_id, existing_target, doc_data.get("doctype", self.target_doctype))
                    success += 1
                    continue

                doc = frappe.get_doc(doc_data)
                doc.flags.ignore_permissions = True
                doc.flags.ignore_mandatory = False
                doc.insert()

                if hasattr(self, "post_insert"):
                    self.post_insert(doc, record)

                if self.target_doctype in (
                    "Purchase Invoice",
                    "Sales Invoice",
                    "Payment Entry",
                    "Journal Entry",
                ):
                    doc.submit()

                frappe.db.commit()
                self.log_success(source_id, doc.name, getattr(doc, "doctype", self.target_doctype))
                success += 1

            except Exception as exc:
                frappe.db.rollback()
                self.log_failure(source_id, traceback.format_exc())
                failed += 1
                print(f"  FAIL [{source_id}]: {exc}")

            if (i + 1) % self.batch_size == 0:
                frappe.db.commit()
                print(f"  Progress: {i + 1}/{total}")

        frappe.db.commit()
        print(f"[{self.source_type}] Done — Success: {success}, Failed: {failed}, Skipped: {skipped}")
        return {"success": success, "failed": failed, "skipped": skipped}
