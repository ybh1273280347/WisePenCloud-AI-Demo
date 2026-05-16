from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path


TYPING_NAMES: set[str] = {
    "Any",
    "Optional",
    "Union",
    "List",
    "Tuple",
    "Dict",
    "Set",
    "FrozenSet",
    "Sequence",
    "MutableSequence",
    "Mapping",
    "MutableMapping",
    "Iterable",
    "Iterator",
    "Generator",
    "Callable",
    "Awaitable",
    "Coroutine",
    "AsyncIterable",
    "AsyncIterator",
    "Type",
    "TypeVar",
    "ParamSpec",
    "TypeVarTuple",
    "Generic",
    "Protocol",
    "TypedDict",
    "Literal",
    "Annotated",
    "Final",
    "ClassVar",
    "NoReturn",
    "Never",
    "Self",
    "Concatenate",
    "TypeAlias",
    "TypeGuard",
    "Unpack",
    "Required",
    "NotRequired",
}

SKIP_DIR_NAMES: set[str] = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    "dist",
    "build",
}


@dataclass(frozen=True)
class MissingTypingImport:
    path: Path
    name: str
    lines: tuple[int, ...]


@dataclass(frozen=True)
class MisplacedImport:
    path: Path
    line: int
    import_text: str


class ImportCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imported_names: set[str] = set()
        self.has_typing_star_import = False

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.split(".", 1)[0]
            self.imported_names.add(bound_name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in {"typing", "typing_extensions"}:
            for alias in node.names:
                if alias.name == "*":
                    self.has_typing_star_import = True
                else:
                    self.imported_names.add(alias.asname or alias.name)
            return

        for alias in node.names:
            if alias.name == "*":
                continue
            self.imported_names.add(alias.asname or alias.name)


class DefinedNameCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.defined_names: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.defined_names.add(node.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defined_names.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.defined_names.add(node.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._collect_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._collect_target(node.target)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._collect_target(node.target)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._collect_target(node.target)
        self.generic_visit(node)

    def _collect_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self.defined_names.add(target.id)
            return

        if isinstance(target, ast.Tuple | ast.List):
            for item in target.elts:
                self._collect_target(item)


class AnnotationNameCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names_by_line: dict[str, set[int]] = {}

    def collect_from_annotation(self, node: ast.AST | None) -> None:
        if node is None:
            return

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            self._collect_from_string_annotation(node.value, node.lineno)
            return

        self.visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        self.names_by_line.setdefault(node.id, set()).add(node.lineno)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # typing.Optional 这种形式只需要 typing 被导入，不需要 Optional 被单独导入。
        self.visit(node.value)

    def _collect_from_string_annotation(self, annotation: str, fallback_line: int) -> None:
        try:
            parsed = ast.parse(annotation, mode="eval")
        except SyntaxError:
            return

        for child in ast.walk(parsed):
            if isinstance(child, ast.Name):
                line = getattr(child, "lineno", fallback_line)
                self.names_by_line.setdefault(child.id, set()).add(line)


class AnnotationCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.name_collector = AnnotationNameCollector()

    @property
    def names_by_line(self) -> dict[str, set[int]]:
        return self.name_collector.names_by_line

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_annotations(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_annotations(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.name_collector.collect_from_annotation(node.annotation)
        if node.value is not None:
            self.visit(node.value)

    def _visit_function_annotations(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = (
            node.args.posonlyargs
            + node.args.args
            + node.args.kwonlyargs
        )

        for arg in args:
            self.name_collector.collect_from_annotation(arg.annotation)

        if node.args.vararg is not None:
            self.name_collector.collect_from_annotation(node.args.vararg.annotation)

        if node.args.kwarg is not None:
            self.name_collector.collect_from_annotation(node.args.kwarg.annotation)

        self.name_collector.collect_from_annotation(node.returns)

        for stmt in node.body:
            self.visit(stmt)


def iter_python_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix == ".py" else []

    files: list[Path] = []

    for path in root.rglob("*.py"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        files.append(path)

    return sorted(files)


def read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def get_source_segment_line(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment is None:
        return "<unknown import>"

    return " ".join(segment.strip().split())


def is_import_node(node: ast.AST) -> bool:
    return isinstance(node, ast.Import | ast.ImportFrom)


def is_allowed_header_node(node: ast.AST) -> bool:
    if is_import_node(node):
        return True

    if isinstance(node, ast.Expr):
        # 模块 docstring。
        return isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)

    return False


def find_first_non_header_line(tree: ast.Module) -> int | None:
    for node in tree.body:
        if is_allowed_header_node(node):
            continue

        return getattr(node, "lineno", None)

    return None


def check_misplaced_imports(path: Path, source: str, tree: ast.Module) -> list[MisplacedImport]:
    first_non_header_line = find_first_non_header_line(tree)
    if first_non_header_line is None:
        return []

    misplaced: list[MisplacedImport] = []

    for node in tree.body:
        if not is_import_node(node):
            continue

        line = getattr(node, "lineno", 0)

        if line > first_non_header_line:
            misplaced.append(
                MisplacedImport(
                    path=path,
                    line=line,
                    import_text=get_source_segment_line(source, node),
                )
            )

    return misplaced


def check_missing_typing_imports(path: Path, tree: ast.Module) -> list[MissingTypingImport]:
    import_collector = ImportCollector()
    import_collector.visit(tree)

    if import_collector.has_typing_star_import:
        return []

    defined_collector = DefinedNameCollector()
    defined_collector.visit(tree)

    annotation_collector = AnnotationCollector()
    annotation_collector.visit(tree)

    imported_or_defined = (
        import_collector.imported_names
        | defined_collector.defined_names
    )

    missing: list[MissingTypingImport] = []

    for name, lines in sorted(annotation_collector.names_by_line.items()):
        if name not in TYPING_NAMES:
            continue

        if name in imported_or_defined:
            continue

        missing.append(
            MissingTypingImport(
                path=path,
                name=name,
                lines=tuple(sorted(lines)),
            )
        )

    return missing


def check_file(path: Path) -> tuple[list[MissingTypingImport], list[MisplacedImport]]:
    source = read_source(path)

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        print(f"[syntax-error] {path}: {exc}", file=sys.stderr)
        return [], []

    missing = check_missing_typing_imports(path, tree)
    misplaced = check_misplaced_imports(path, source, tree)

    return missing, misplaced


def print_missing_typing_imports(items: list[MissingTypingImport]) -> None:
    if not items:
        return

    by_file: dict[Path, list[MissingTypingImport]] = {}

    for item in items:
        by_file.setdefault(item.path, []).append(item)

    print("\n=== Missing typing imports ===")

    for path, file_items in sorted(by_file.items()):
        print(f"\n{path}")

        names = sorted({item.name for item in file_items})
        print(f"  suggested: from typing import {', '.join(names)}")

        for item in sorted(file_items, key=lambda x: x.name):
            lines = ", ".join(str(line) for line in item.lines)
            print(f"  - {item.name}: line {lines}")


def print_misplaced_imports(items: list[MisplacedImport]) -> None:
    if not items:
        return

    by_file: dict[Path, list[MisplacedImport]] = {}

    for item in items:
        by_file.setdefault(item.path, []).append(item)

    print("\n=== Misplaced module-level imports ===")

    for path, file_items in sorted(by_file.items()):
        print(f"\n{path}")

        for item in sorted(file_items, key=lambda x: x.line):
            print(f"  - line {item.line}: {item.import_text}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check missing typing imports and misplaced module-level imports."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Python files or directories to scan.",
    )

    args = parser.parse_args()

    all_missing: list[MissingTypingImport] = []
    all_misplaced: list[MisplacedImport] = []

    for raw_path in args.paths:
        root = Path(raw_path)

        for path in iter_python_files(root):
            missing, misplaced = check_file(path)
            all_missing.extend(missing)
            all_misplaced.extend(misplaced)

    print_missing_typing_imports(all_missing)
    print_misplaced_imports(all_misplaced)

    if not all_missing and not all_misplaced:
        print("OK: no missing typing imports or misplaced module-level imports found.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())