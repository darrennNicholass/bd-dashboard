"""S&D requirement validation and legacy-safe serialization."""
from __future__ import annotations

from uuid import uuid4

from src.preparation import clean_text

REQUIREMENT_FIELDS = (
    "id", "partner_key", "partner_name", "kind", "name", "quantity",
    "created_at", "updated_at",
)


def requirement_record(item: dict) -> dict:
    """Ignore retired notes/function fields when reading legacy requirements."""
    return {field: item[field] for field in REQUIREMENT_FIELDS if field in item}


def clean_check_result(result: dict) -> dict:
    return {
        **result,
        "requirements": [
            {key: value for key, value in row.items() if key not in {"notes", "function"}}
            for row in result.get("requirements", [])
        ],
    }


def validate_requirements(requirements: list[dict]) -> list[dict]:
    validated = []
    ids = set()
    for item in requirements:
        kind = clean_text(item.get("kind")).lower()
        name = clean_text(item.get("name"))
        raw_quantity = item.get("quantity")
        quantity_text = clean_text(raw_quantity)
        if not name and not quantity_text:
            continue  # An untouched editor placeholder is not a requirement.
        if kind not in {"supply", "demand"} or not name:
            raise ValueError("Jenis dan nama deliverable wajib diisi.")
        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Quantity harus bilangan bulat positif.") from exc
        if quantity < 1 or quantity_text not in {str(quantity), f"{quantity}.0"}:
            raise ValueError("Quantity harus bilangan bulat positif.")
        uid = clean_text(item.get("id")) or str(uuid4())
        if uid in ids:
            raise ValueError("Requirement ID tidak boleh duplikat.")
        ids.add(uid)
        validated.append({"id": uid, "kind": kind, "name": name, "quantity": quantity})
    if not validated:
        raise ValueError("Tambahkan minimal satu requirement Supply atau Demand.")
    return validated
