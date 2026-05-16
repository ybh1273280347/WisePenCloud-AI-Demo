from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Mapping

if TYPE_CHECKING:
    from chat.application.math_reasoning.compute.models import MathComputeResult


def stringify(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(stringify(item) for item in value) + "]"
    if isinstance(value, dict):
        items = [f"{stringify(k)}: {stringify(v)}" for k, v in value.items()]
        return "{" + ", ".join(items) + "}"
    return str(value)


def latex_or_unknown(value: Any) -> str:
    if value is None:
        return "unknown"
    try:
        import sympy as sp
        return sp.latex(value)
    except Exception:
        return "unknown"


def format_input_block(inputs: Mapping[str, Any]) -> List[str]:
    lines: List[str] = []
    for key, value in inputs.items():
        if value is None:
            continue
        lines.append(f"- {key}: {stringify(value)}")
    if not lines:
        lines.append("- none")
    return lines


def format_math_result(result: "MathComputeResult") -> str:
    lines: List[str] = []
    lines.append(f"Task: {result.task}")
    if result.exact_result is not None:
        lines.append(f"Exact: {stringify(result.exact_result)}")
    if result.latex_result is not None:
        lines.append(f"LaTeX: {result.latex_result}")
    if result.numeric_result is not None:
        lines.append(f"Numeric: {result.numeric_result}")
    if result.notes:
        lines.append(f"Notes: {'; '.join(result.notes)}")
    return "\n".join(lines)
