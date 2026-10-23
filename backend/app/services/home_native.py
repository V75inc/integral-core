"""Validate native Home components against the owning App's active capabilities."""

from typing import Any, Dict, List

from app.schemas.home_native import HomeElement


def validate_native_home(home: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    """Native composition is data: no HTML, JavaScript, global plugins or frames."""
    if home.get("renderer") == "extension" or any(
        widget.get("type") == "extension_view" for widget in home.get("widgets", [])
    ):
        raise ValueError(
            "Home uses native components; iframe components are unavailable"
        )
    app = manifest.get("app") or {}
    queries = {row["key"]: row for row in app.get("queries", [])}
    operations = {row["key"]: row for row in app.get("operations", [])}
    roots = (
        [home["content"]]
        if home.get("renderer") == "native"
        else [
            widget["content"]
            for widget in home.get("widgets", [])
            if widget.get("type") == "native_component"
        ]
    )
    seen = set()
    count = 0

    def visit(node: Dict[str, Any], depth: int) -> None:
        nonlocal count
        count += 1
        if count > 128 or depth > 12 or node["id"] in seen:
            raise ValueError(
                "Home components need unique IDs, at most 128 nodes and depth 12"
            )
        seen.add(node["id"])
        if node.get("query_key") and node["query_key"] not in queries:
            raise ValueError("Home query must belong to the owning App")
        query = queries.get(node.get("query_key"))
        if query:

            def schema_at(schema: Dict[str, Any], path: str) -> Dict[str, Any]:
                for part in path.split("."):
                    schema = (schema.get("properties") or {}).get(part) or {}
                return schema

            output = query.get("output_schema") or {}
            if node["type"] == "metric":
                scalar = schema_at(output, node.get("value_path") or "total_estimate")
                if scalar.get("type") not in {"integer", "number", "string"}:
                    raise ValueError("Home metric path must name a declared scalar")
            if node["type"] in {"records", "chart"}:
                rows = schema_at(output, node.get("rows_path") or "items")
                label = schema_at(
                    rows.get("items") or {}, node.get("label_field") or "title"
                )
                if rows.get("type") != "array" or label.get("type") != "string":
                    raise ValueError(
                        "Home records require declared rows and readable labels"
                    )
                if node.get("value_field"):
                    value = schema_at(rows.get("items") or {}, node["value_field"])
                    if value.get("type") not in {"number", "integer"}:
                        raise ValueError("Home chart value must name a declared number")
        key = node.get("operation_key")
        if key:
            operation = operations.get(key)
            if operation is None:
                raise ValueError("Home operation must belong to the owning App")
            properties = (operation.get("input_schema") or {}).get("properties") or {}
            if any(field["name"] not in properties for field in node.get("fields", [])):
                raise ValueError(
                    "Home form fields must match the declared operation input"
                )
        for child in node.get("children", []):
            visit(child, depth + 1)

    for root in roots:
        visit(
            HomeElement.model_validate(root).model_dump(mode="json", exclude_none=True),
            0,
        )


def native_component_catalog() -> List[Dict[str, Any]]:
    """Expose the same authoring schema to developers and resident tools."""
    return [
        {
            "key": "native",
            "name": "Integral native components",
            "schema": HomeElement.model_json_schema(),
        }
    ]
