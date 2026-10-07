"""Same-entry computed fields: a closed expression, evaluated at read time.

Phase 1 stores the expression on the field schema and projects the value
when an entry is read. The result is never written into ``custom_fields``.
Cross-entry aggregates are out of scope and never parse.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from app.exceptions import BadRequestError

_SCALE = 1_000_000
_QUANTUM = Decimal("0.000001")
_NUMBER_TYPES = frozenset({"number"})
_TEXT_TYPES = frozenset({"text"})


class _ParseError(Exception):
    """The expression string is outside the closed grammar."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def expression_source(raw: Any) -> str:
    """Return the authoring string from a raw expression or a stored object."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return str(raw.get("source") or "").strip()
    return ""


def attach_computed_expression(field_key: str, raw: Any) -> Dict[str, Any]:
    """Parse ``raw`` into ``{source, ast}`` or raise a field-specific error."""
    source = expression_source(raw)
    if not source:
        raise BadRequestError(
            message=f"Computed field '{field_key}' requires an expression"
        )
    try:
        ast = _parse(source)
    except _ParseError as exc:
        raise BadRequestError(
            message=(
                f"Computed field '{field_key}' has an invalid expression: "
                f"{exc.detail}"
            )
        ) from exc
    return {"source": source, "ast": ast}


def bind_computed_fields(fields: List[Dict[str, Any]]) -> None:
    """Check references, types, and cycles. Sets ``expression.result_type``."""
    computed = [field for field in fields if str(field.get("type") or "") == "computed"]
    if not computed:
        return
    seen: Dict[str, int] = {}
    for field in fields:
        key = str(field.get("key") or "")
        seen[key] = seen.get(key, 0) + 1
    by_key = {str(field.get("key") or ""): field for field in fields}
    for key, count in seen.items():
        if count > 1:
            raise BadRequestError(message=f"Field '{key}' is declared more than once")

    deps: Dict[str, List[str]] = {}
    for field in computed:
        key = str(field.get("key") or "")
        ast = (field.get("expression") or {}).get("ast") or {}
        deps[key] = [
            ref
            for ref in _field_refs(ast)
            if ref in by_key and _is_computed(by_key[ref])
        ]

    order = _topo(deps)
    if order is None:
        cycle_key = next(iter(deps))
        raise BadRequestError(
            message=f"Computed field '{cycle_key}' has a circular expression"
        )

    for key in order:
        field = by_key[key]
        ast = (field.get("expression") or {}).get("ast") or {}
        result_type = _infer(ast, by_key, key)
        expression = dict(field.get("expression") or {})
        expression["result_type"] = result_type
        field["expression"] = expression
        by_key[key] = field


def project_computed_values(
    custom_fields: Optional[Dict[str, Any]], fields: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Return a copy of ``custom_fields`` with computed keys filled in.

    Stored values for computed keys are ignored. A cycle or a missing
    expression leaves those keys null instead of failing the read.
    """
    out = dict(custom_fields or {})
    computed = [field for field in fields if _is_computed(field)]
    if not computed:
        return out
    by_key = {str(field.get("key") or ""): field for field in fields}
    deps: Dict[str, List[str]] = {}
    for field in computed:
        key = str(field.get("key") or "")
        out.pop(key, None)
        ast = (field.get("expression") or {}).get("ast") or {}
        deps[key] = [
            ref
            for ref in _field_refs(ast)
            if ref in by_key and _is_computed(by_key[ref])
        ]
    order = _topo(deps)
    if order is None:
        for key in deps:
            out[key] = None
        return out
    for key in order:
        field = by_key[key]
        ast = (field.get("expression") or {}).get("ast") or {}
        out[key] = evaluate_expression(ast, out) if ast else None
    return out


def evaluate_expression(ast: Dict[str, Any], values: Dict[str, Any]) -> Any:
    """Evaluate a compiled AST. Missing numbers and division by zero are null."""
    op = ast.get("op")
    if op == "num":
        return _json_number(_to_scaled(ast.get("value")))
    if op == "str":
        return str(ast.get("value") or "")
    if op == "field":
        return values.get(str(ast.get("key") or ""))
    if op == "neg":
        inner = evaluate_expression(ast.get("arg") or {}, values)
        scaled = _to_scaled(inner)
        if scaled is None:
            return None
        return _json_number(-scaled)
    if op in {"add", "sub", "mul", "div"}:
        left = _to_scaled(evaluate_expression(ast.get("left") or {}, values))
        right = _to_scaled(evaluate_expression(ast.get("right") or {}, values))
        if left is None or right is None:
            return None
        if op == "add":
            return _json_number(left + right)
        if op == "sub":
            return _json_number(left - right)
        if op == "mul":
            return _json_number(_div_round(left * right, _SCALE))
        if right == 0:
            return None
        return _json_number(_div_round(left * _SCALE, right))
    if op == "concat":
        parts: List[str] = []
        for arg in ast.get("args") or []:
            part = evaluate_expression(arg, values)
            if part is None:
                return None
            parts.append(str(part))
        return "".join(parts)
    return None


def assert_stored_query_field(path: str, fields: List[Dict[str, Any]]) -> None:
    """Reject a filter or sort path that names a computed field."""
    key = _custom_field_key(path)
    if not key:
        return
    for field in fields:
        if str(field.get("key") or "") == key and _is_computed(field):
            raise BadRequestError(
                message=f"Cannot filter or sort computed field '{key}'"
            )


def _is_computed(field: Dict[str, Any]) -> bool:
    return str(field.get("type") or "") == "computed"


def _custom_field_key(path: str) -> str:
    text = str(path or "").strip()
    if text.startswith("-"):
        text = text[1:]
    if not text.startswith("custom_fields."):
        return ""
    return text.split(".", 2)[1]


def _field_refs(ast: Dict[str, Any]) -> List[str]:
    op = ast.get("op")
    if op == "field":
        key = str(ast.get("key") or "")
        return [key] if key else []
    if op == "neg":
        return _field_refs(ast.get("arg") or {})
    if op in {"add", "sub", "mul", "div"}:
        return _field_refs(ast.get("left") or {}) + _field_refs(ast.get("right") or {})
    if op == "concat":
        refs: List[str] = []
        for arg in ast.get("args") or []:
            refs.extend(_field_refs(arg))
        return refs
    return []


def _topo(deps: Dict[str, List[str]]) -> Optional[List[str]]:
    """Return keys with dependencies first, or None when the graph cycles."""
    remaining = {key: list(refs) for key, refs in deps.items()}
    ordered: List[str] = []
    ready = [key for key, refs in remaining.items() if not refs]
    while ready:
        key = ready.pop()
        ordered.append(key)
        for other, refs in remaining.items():
            if key in refs:
                refs.remove(key)
                if not refs and other not in ordered and other not in ready:
                    ready.append(other)
    if len(ordered) != len(remaining):
        return None
    return ordered


def _operand_kind(field: Dict[str, Any]) -> Optional[str]:
    if _is_computed(field):
        kind = str((field.get("expression") or {}).get("result_type") or "")
        return kind if kind in {"number", "text"} else None
    composite = field.get("composite")
    base = ""
    if isinstance(composite, dict):
        base = str(composite.get("base") or "")
    kind = base or str(field.get("type") or "")
    if kind in _NUMBER_TYPES:
        return "number"
    if kind in _TEXT_TYPES:
        return "text"
    return None


def _infer(ast: Dict[str, Any], by_key: Dict[str, Dict[str, Any]], owner: str) -> str:
    op = ast.get("op")
    if op == "num":
        return "number"
    if op == "str":
        return "text"
    if op == "field":
        return _ref_kind(str(ast.get("key") or ""), by_key, owner, slot="expression")
    if op == "neg":
        kind = _infer(ast.get("arg") or {}, by_key, owner)
        if kind != "number":
            raise BadRequestError(
                message=f"Computed field '{owner}' expression must use numbers"
            )
        return "number"
    if op in {"add", "sub", "mul", "div"}:
        for side in ("left", "right"):
            child = ast.get(side) or {}
            if child.get("op") == "field":
                _ref_kind(str(child.get("key") or ""), by_key, owner, slot="arithmetic")
            elif _infer(child, by_key, owner) != "number":
                raise BadRequestError(
                    message=f"Computed field '{owner}' expression must use numbers"
                )
        return "number"
    if op == "concat":
        args = ast.get("args") or []
        if not args:
            raise BadRequestError(
                message=f"Computed field '{owner}' expression must concatenate text"
            )
        for arg in args:
            if arg.get("op") == "field":
                _ref_kind(str(arg.get("key") or ""), by_key, owner, slot="concat")
            elif _infer(arg, by_key, owner) != "text":
                raise BadRequestError(
                    message=(
                        f"Computed field '{owner}' expression must concatenate text"
                    )
                )
        return "text"
    raise BadRequestError(
        message=f"Computed field '{owner}' has an invalid expression: unsupported"
    )


def _ref_kind(
    ref: str,
    by_key: Dict[str, Dict[str, Any]],
    owner: str,
    *,
    slot: str,
) -> str:
    field = by_key.get(ref)
    if field is None:
        raise BadRequestError(
            message=f"Computed field '{owner}' references unknown field '{ref}'"
        )
    kind = _operand_kind(field)
    if slot == "arithmetic" and kind != "number":
        label = _type_label(field)
        raise BadRequestError(
            message=(
                f"Computed field '{owner}' cannot use {label} field '{ref}' "
                "in arithmetic"
            )
        )
    if slot == "concat" and kind != "text":
        label = _type_label(field)
        raise BadRequestError(
            message=(
                f"Computed field '{owner}' cannot concatenate {label} field '{ref}'"
            )
        )
    if kind is None:
        label = _type_label(field)
        raise BadRequestError(
            message=(
                f"Computed field '{owner}' cannot use {label} field '{ref}' "
                "in an expression"
            )
        )
    return kind


def _type_label(field: Dict[str, Any]) -> str:
    if _is_computed(field):
        kind = str((field.get("expression") or {}).get("result_type") or "computed")
        return f"computed {kind}" if kind != "computed" else "computed"
    return str(field.get("type") or "unknown")


def _to_scaled(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = Decimal(text)
        except Exception:  # noqa: BLE001 — a non-numeric string is a missing number
            return None
    elif isinstance(value, (int, float)):
        try:
            number = Decimal(str(value))
        except Exception:  # noqa: BLE001
            return None
    else:
        return None
    if not number.is_finite():
        return None
    quantized = number.quantize(_QUANTUM, rounding=ROUND_HALF_UP)
    return int(quantized * _SCALE)


def _div_round(numer: int, denom: int) -> int:
    """Integer division rounded half away from zero."""
    if denom == 0:
        return 0
    negative = (numer < 0) ^ (denom < 0)
    numer, denom = abs(numer), abs(denom)
    quotient, remainder = divmod(numer, denom)
    if remainder * 2 >= denom:
        quotient += 1
    return -quotient if negative else quotient


def _json_number(scaled: Optional[int]) -> Any:
    if scaled is None:
        return None
    sign = "-" if scaled < 0 else ""
    whole, frac = divmod(abs(scaled), _SCALE)
    if frac == 0:
        return int(f"{sign}{whole}")
    text = f"{frac:06d}".rstrip("0")
    return float(f"{sign}{whole}.{text}")


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = _tokenize(source)
        self.index = 0

    def parse(self) -> Dict[str, Any]:
        ast = self._expr()
        if self._peek()[0] != "eof":
            raise _ParseError("unexpected input after the expression")
        return ast

    def _peek(self) -> tuple:
        if self.index >= len(self.tokens):
            return ("eof", "")
        return self.tokens[self.index]

    def _eat(self, kind: str) -> tuple:
        token = self._peek()
        if token[0] != kind:
            raise _ParseError(f"expected {kind}")
        self.index += 1
        return token

    def _expr(self) -> Dict[str, Any]:
        node = self._term()
        while self._peek()[0] in {"plus", "minus"}:
            op = "add" if self._eat(self._peek()[0])[0] == "plus" else "sub"
            node = {"op": op, "left": node, "right": self._term()}
        return node

    def _term(self) -> Dict[str, Any]:
        node = self._unary()
        while self._peek()[0] in {"star", "slash"}:
            op = "mul" if self._eat(self._peek()[0])[0] == "star" else "div"
            node = {"op": op, "left": node, "right": self._unary()}
        return node

    def _unary(self) -> Dict[str, Any]:
        if self._peek()[0] == "minus":
            self._eat("minus")
            return {"op": "neg", "arg": self._unary()}
        return self._primary()

    def _primary(self) -> Dict[str, Any]:
        kind, value = self._peek()
        if kind == "number":
            self._eat("number")
            return {"op": "num", "value": value}
        if kind == "string":
            self._eat("string")
            return {"op": "str", "value": value}
        if kind == "ident":
            self._eat("ident")
            if value == "concat" and self._peek()[0] == "lparen":
                return self._concat()
            if self._peek()[0] == "lparen":
                raise _ParseError(f"unknown call '{value}'")
            return {"op": "field", "key": value}
        if kind == "lparen":
            self._eat("lparen")
            node = self._expr()
            self._eat("rparen")
            return node
        raise _ParseError("expected a number, field, or concat()")

    def _concat(self) -> Dict[str, Any]:
        self._eat("lparen")
        args: List[Dict[str, Any]] = []
        if self._peek()[0] != "rparen":
            args.append(self._expr())
            while self._peek()[0] == "comma":
                self._eat("comma")
                args.append(self._expr())
        self._eat("rparen")
        if not args:
            raise _ParseError("concat() needs text")
        return {"op": "concat", "args": args}


def _parse(source: str) -> Dict[str, Any]:
    return _Parser(source).parse()


def _tokenize(source: str) -> List[tuple]:
    tokens: List[tuple] = []
    i = 0
    length = len(source)
    while i < length:
        char = source[i]
        if char.isspace():
            i += 1
            continue
        if char == "+":
            tokens.append(("plus", "+"))
            i += 1
            continue
        if char == "-":
            tokens.append(("minus", "-"))
            i += 1
            continue
        if char == "*":
            tokens.append(("star", "*"))
            i += 1
            continue
        if char == "/":
            tokens.append(("slash", "/"))
            i += 1
            continue
        if char == "(":
            tokens.append(("lparen", "("))
            i += 1
            continue
        if char == ")":
            tokens.append(("rparen", ")"))
            i += 1
            continue
        if char == ",":
            tokens.append(("comma", ","))
            i += 1
            continue
        if char == '"':
            i += 1
            chars: List[str] = []
            closed = False
            while i < length:
                if source[i] == "\\":
                    if i + 1 >= length:
                        raise _ParseError("unterminated string")
                    chars.append(source[i + 1])
                    i += 2
                    continue
                if source[i] == '"':
                    closed = True
                    i += 1
                    break
                chars.append(source[i])
                i += 1
            if not closed:
                raise _ParseError("unterminated string")
            tokens.append(("string", "".join(chars)))
            continue
        if char.isdigit():
            start = i
            i += 1
            while i < length and source[i].isdigit():
                i += 1
            if i < length and source[i] == ".":
                if i + 1 >= length or not source[i + 1].isdigit():
                    raise _ParseError("invalid number")
                i += 2
                while i < length and source[i].isdigit():
                    i += 1
            tokens.append(("number", source[start:i]))
            continue
        if char.isalpha() or char == "_":
            start = i
            i += 1
            while i < length and (source[i].isalnum() or source[i] == "_"):
                i += 1
            tokens.append(("ident", source[start:i]))
            continue
        raise _ParseError(f"unexpected character '{char}'")
    tokens.append(("eof", ""))
    return tokens
