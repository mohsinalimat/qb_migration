from qb_migration.qb_migration.migration.base_importer import BaseImporter


def test_base_importer_maps_project_name_to_project_when_target_has_project_field():
    importer = BaseImporter()
    importer.target_doctype = "Task"

    record = {"project_name": "Remodel Bathroom"}
    normalized = importer.normalize_record(record)

    assert normalized == {"project": "Remodel Bathroom"}
