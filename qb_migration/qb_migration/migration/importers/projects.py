import frappe

from ..base_importer import BaseImporter


class ProjectImporter(BaseImporter):
    source_type = "QB_PROJECT_NAME"
    target_doctype = "Project"
    json_file = "project_name.json"
    json_key = "project_names"

    def load_data(self):
        records = super().load_data()
        normalized = []

        for record in records:
            if isinstance(record, dict):
                project_name = record.get("project_name") or record.get("name")
            else:
                project_name = record

            if project_name is None:
                continue

            project_name = str(project_name).strip()
            if project_name:
                normalized.append({"project_name": project_name})

        return normalized

    def get_source_id(self, record):
        if isinstance(record, dict):
            return str(record.get("project_name") or record.get("name") or "")
        return str(record or "")

    def map_record(self, record):
        if isinstance(record, dict):
            project_name = record.get("project_name") or record.get("name")
        else:
            project_name = record

        project_name = str(project_name or "").strip()
        if not project_name:
            return None

        return {
            "doctype": "Project",
            "project_name": project_name,
            "status": "Open",
        }

    def find_existing_target(self, doc_data):
        project_name = (doc_data or {}).get("project_name")
        if not project_name:
            return None

        return frappe.db.get_value("Project", {"project_name": project_name}, "name")
