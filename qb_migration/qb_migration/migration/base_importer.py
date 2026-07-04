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

    def format_detailed_log_entry(
        self,
        status: str,
        source_id: str,
        reason: str,
        ref_no: str | None = None,
        details: dict | None = None,
    ) -> str:
        parts = [f"[{status}]", f"stage={self.source_type or 'unknown'}", f"source_id={source_id or 'N/A'}"]
        if ref_no:
            parts.append(f"ref_no={ref_no}")
        if details:
            parts.append(f"details={json.dumps(details, default=str)}")
        parts.append(f"reason={reason or 'N/A'}")
        return " | ".join(parts)

    def get_detailed_log_path(self) -> Path:
        log_path = frappe.flags.get("qb_migration_detailed_log_path")
        if log_path:
            return Path(log_path)

        app_dir = Path(__file__).resolve().parents[3]
        log_dir = app_dir / "logs"

        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = now_datetime().strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"qb_migration_failed_skipped_{timestamp}.log"
        frappe.flags["qb_migration_detailed_log_path"] = str(log_path)
        return log_path

    def append_detailed_log(
        self,
        status: str,
        source_id: str,
        reason: str,
        ref_no: str | None = None,
        details: dict | None = None,
    ) -> str:
        log_path = self.get_detailed_log_path()
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(self.format_detailed_log_entry(status, source_id, reason, ref_no, details) + "\n")
        return str(log_path)

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
        self.append_detailed_log("Failed", source_id, str(error)[:2000])

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
        self.append_detailed_log("Skipped", source_id, skip_msg[:2000], ref_no=ref_no)

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
            return getdate(date_value)
        except Exception:
            return nowdate()

    def run(self, dry_run: bool = False):
        records = self.load_data()
        total = len(records)
        success = failed = skipped = 0

        print(f"\n[{self.source_type}] Starting: {total} records")

        for i, record in enumerate(records):
            source_id = self.get_source_id(record)
            if not source_id:
                failed += 1
                self.append_detailed_log(
                    "Failed",
                    source_id or f"record_{i + 1}",
                    "Missing source id",
                    details={"record_index": i + 1},
                )
                print(f"  FAIL: missing source id for record {i+1}")
                continue

            if self.is_imported(source_id):
                skipped += 1
                self.append_detailed_log(
                    "Skipped",
                    source_id,
                    "Already imported",
                    details={"record_index": i + 1},
                )
                continue

            try:
                doc_data = self.map_record(record)
                if doc_data is None:
                    skipped += 1
                    self.append_detailed_log(
                        "Skipped",
                        source_id,
                        "Mapper returned no document data",
                        details={"record_index": i + 1},
                    )
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
                    "Purchase Receipt",
                    "Purchase Order",
                    "Sales Order",
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
