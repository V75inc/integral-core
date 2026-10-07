"""Merge linked employee personal fields into onboarding form API payloads."""

from __future__ import annotations

from typing import Any, Dict, List

from app.models.nodes import Entry

# Shared keys between employee and onboarding_form (personal + banking).
_EMPLOYEE_PREFILL_KEYS: List[str] = [
    "phone",
    "address_line",
    "city",
    "country",
    "date_of_birth",
    "personal_email",
    "bank_account_name",
    "bank_name",
    "bank_branch",
    "bank_account_number",
    "bank_account_type",
    "tax_id",
    "national_id",
    "emergency_contact_name",
    "emergency_contact_phone",
]


def _as_single_id(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0] or "").strip() if value else ""
    return str(value or "").strip()


async def enrich_onboarding_form_export(
    entry: Entry, entry_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Fill empty onboarding form fields from the linked employee record."""
    cf = dict(entry_data.get("custom_fields") or {})
    employee_id = _as_single_id(cf.get("employee"))
    if not employee_id:
        return entry_data

    employee = await Entry.get(employee_id)
    if not employee:
        return entry_data

    emp_cf = dict(getattr(employee, "custom_fields", None) or {})
    changed = False
    for key in _EMPLOYEE_PREFILL_KEYS:
        if str(cf.get(key) or "").strip():
            continue
        val = emp_cf.get(key)
        if val not in (None, ""):
            cf[key] = val
            changed = True

    title = str(entry_data.get("title") or "").strip()
    emp_title = str(getattr(employee, "title", "") or "").strip()
    if not title and emp_title:
        entry_data = {**entry_data, "title": emp_title}
        changed = True

    if changed:
        entry_data = {**entry_data, "custom_fields": cf}
    return entry_data


async def materialize_employee_prefill_on_form(entry: Entry) -> bool:
    """Persist empty onboarding form fields from the linked employee (same rules as export enrich)."""
    cf = dict(getattr(entry, "custom_fields", None) or {})
    employee_id = _as_single_id(cf.get("employee"))
    if not employee_id:
        return False

    employee = await Entry.get(employee_id)
    if not employee:
        return False

    emp_cf = dict(getattr(employee, "custom_fields", None) or {})
    patch: Dict[str, Any] = {}
    for key in _EMPLOYEE_PREFILL_KEYS:
        if str(cf.get(key) or "").strip():
            continue
        val = emp_cf.get(key)
        if val not in (None, "", []):
            patch[key] = val

    title = str(getattr(entry, "title", "") or "").strip()
    emp_title = str(getattr(employee, "title", "") or "").strip()
    title_changed = False
    if not title and emp_title:
        entry.title = emp_title
        title_changed = True

    if not patch and not title_changed:
        return False

    if patch:
        entry.custom_fields = {**cf, **patch}
    await entry.save()
    return True
