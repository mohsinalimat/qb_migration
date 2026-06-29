import calendar
import os
import sys
from datetime import date, datetime, timedelta

import frappe

DATE_FORMAT = "%Y-%m-%d"
ENV_VAR_NAME = "QB_MIGRATION_FISCAL_YEAR_START_DATE"


def _parse_date(date_string: str) -> date:
    try:
        return datetime.strptime(date_string.strip(), DATE_FORMAT).date()
    except Exception as exc:
        raise ValueError(f"Invalid date format. Expected YYYY-MM-DD. Received: {date_string}") from exc


def _sanitize_year_start_date(start_date: date) -> date:
    today = date.today()
    if start_date > today:
        raise ValueError("Fiscal year start date cannot be in the future.")
    return start_date


def prompt_first_fiscal_year_start_date() -> date:
    env_value = os.getenv(ENV_VAR_NAME)
    if env_value:
        start_date = _parse_date(env_value)
        return _sanitize_year_start_date(start_date)

    if not sys.stdin or not sys.stdin.isatty():
        raise RuntimeError(
            f"Fiscal year start date must be provided interactively or via the {ENV_VAR_NAME} environment variable."
        )

    while True:
        raw_value = input("Enter the first fiscal year start date (YYYY-MM-DD): ").strip()
        if not raw_value:
            print("Please enter a valid date in YYYY-MM-DD format.")
            continue

        try:
            start_date = _parse_date(raw_value)
            return _sanitize_year_start_date(start_date)
        except ValueError as exc:
            print(f"Invalid fiscal year start date: {exc}")


def _build_year_start_date(base_date: date, year: int) -> date:
    day = min(base_date.day, calendar.monthrange(year, base_date.month)[1])
    return date(year, base_date.month, day)


def _create_fiscal_year_label(year: int) -> str:
    return f"FY {year}"


def _find_existing_fiscal_year(label: str, start_date: date, end_date: date):
    existing_by_dates = frappe.db.exists(
        "Fiscal Year",
        {"year_start_date": start_date, "year_end_date": end_date},
    )
    if existing_by_dates:
        return existing_by_dates

    existing_by_label = frappe.db.exists("Fiscal Year", {"year": label})
    if existing_by_label:
        existing_doc = frappe.get_doc("Fiscal Year", existing_by_label)
        if (
            existing_doc.year_start_date == start_date
            and existing_doc.year_end_date == end_date
        ):
            return existing_by_label

        print(
            f"WARNING: Fiscal Year '{label}' already exists with different dates "
            f"({existing_doc.year_start_date} to {existing_doc.year_end_date}). Skipping creation."
        )
        return existing_by_label

    return None


def _create_fiscal_year(label: str, start_date: date, end_date: date):
    fiscal_year_doc = frappe.get_doc(
        {
            "doctype": "Fiscal Year",
            "year": label,
            "year_start_date": start_date,
            "year_end_date": end_date,
            "is_short_year": 0,
            "disabled": 0,
        }
    )
    fiscal_year_doc.flags.ignore_permissions = True
    fiscal_year_doc.insert(ignore_permissions=True)
    return fiscal_year_doc.name


def ensure_fiscal_years():
    start_date = prompt_first_fiscal_year_start_date()
    today = date.today()
    current_year = today.year

    fiscal_years = []
    for year in range(start_date.year, current_year + 1):
        year_start_date = _build_year_start_date(start_date, year)
        next_year_start_date = _build_year_start_date(start_date, year + 1)
        year_end_date = next_year_start_date - timedelta(days=1)
        fiscal_years.append(
            {
                "label": _create_fiscal_year_label(year),
                "start_date": year_start_date,
                "end_date": year_end_date,
            }
        )

    print(
        f"Preparing fiscal years from {fiscal_years[0]['label']} to {fiscal_years[-1]['label']} "
        f"based on start date {start_date.isoformat()}"
    )

    created_count = 0
    skipped_count = 0

    for fiscal_year in fiscal_years:
        label = fiscal_year["label"]
        start_date = fiscal_year["start_date"]
        end_date = fiscal_year["end_date"]

        existing = _find_existing_fiscal_year(label, start_date, end_date)
        if existing:
            print(
                f"  SKIP: {label} already exists for {start_date} → {end_date}"
            )
            skipped_count += 1
            continue

        created_name = _create_fiscal_year(label, start_date, end_date)
        print(
            f"  CREATED: {label} as {created_name} ({start_date} → {end_date})"
        )
        created_count += 1

    frappe.db.commit()
    print(
        f"Fiscal year preparation complete. Created: {created_count}, Skipped: {skipped_count}."
    )
    return {
        "created": created_count,
        "skipped": skipped_count,
        "requested_start_date": start_date.isoformat(),
    }
