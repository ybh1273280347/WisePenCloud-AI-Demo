from __future__ import annotations

import argparse
from pathlib import Path

import libcst as cst
from libcst.metadata import ParentNodeProvider, PositionProvider


BUILTIN_TO_TYPING: dict[str, str] = {
    "list": "List",
    "tuple": "Tuple",
    "dict": "Dict",
    "set": "Set",
    "frozenset": "FrozenSet",
    "type": "Type",
}

TYPING_NAMES: set[str] = {
    "Any",
    "Optional",
    "List",
    "Tuple",
    "Dict",
    "Set",
    "FrozenSet",
    "Type",
    "Callable",
    "Iterable",
    "Iterator",
    "Sequence",
    "Mapping",
    "MutableMapping",
    "MutableSequence",
    "Generator",
    "AsyncIterator",
    "AsyncIterable",
    "Awaitable",
    "Coroutine",
    "Literal",
    "Annotated",
    "ClassVar",
    "Final",
    "Protocol",
    "TypedDict",
    "TypeVar",
    "Generic",
    "Self",
    "NoReturn",
    "Never",
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


def is_name(node: cst.CSTNode, value: str) -> bool:
    return isinstance(node, cst.Name) and node.value == value


def is_none_name(node: cst.CSTNode) -> bool:
    return is_name(node, "None")


def is_typing_attr(node: cst.CSTNode, attr: str) -> bool:
    return (
        isinstance(node, cst.Attribute)
        and isinstance(node.value, cst.Name)
        and node.value.value == "typing"
        and isinstance(node.attr, cst.Name)
        and node.attr.value == attr
    )


def is_union_name(node: cst.CSTNode) -> bool:
    return is_name(node, "Union") or is_typing_attr(node, "Union")


def is_optional_name(node: cst.CSTNode) -> bool:
    return is_name(node, "Optional") or is_typing_attr(node, "Optional")


def is_builtin_generic_name(node: cst.CSTNode) -> str | None:
    if isinstance(node, cst.Name):
        return BUILTIN_TO_TYPING.get(node.value)

    return None


def make_typing_name(name: str) -> cst.Name:
    return cst.Name(name)


def make_subscript(value: cst.BaseExpression, slice_items: list[cst.SubscriptElement]) -> cst.Subscript:
    return cst.Subscript(
        value=value,
        slice=slice_items,
    )


def make_single_subscript(value: cst.BaseExpression, inner: cst.BaseExpression) -> cst.Subscript:
    return cst.Subscript(
        value=value,
        slice=[
            cst.SubscriptElement(
                slice=cst.Index(value=inner),
            )
        ],
    )


def split_subscript_elements(node: cst.Subscript) -> list[cst.BaseExpression]:
    values: list[cst.BaseExpression] = []

    for item in node.slice:
        if not isinstance(item.slice, cst.Index):
            continue

        values.append(item.slice.value)

    return values


def build_pipe_union(items: list[cst.BaseExpression]) -> cst.BaseExpression:
    if not items:
        raise ValueError("Cannot build union from empty items.")

    expr = items[0]

    for item in items[1:]:
        expr = cst.BinaryOperation(
            left=expr,
            operator=cst.BitOr(),
            right=item,
        )

    return expr


def flatten_pipe_union(node: cst.BaseExpression) -> list[cst.BaseExpression]:
    if isinstance(node, cst.BinaryOperation) and isinstance(node.operator, cst.BitOr):
        return flatten_pipe_union(node.left) + flatten_pipe_union(node.right)

    return [node]


class AnnotationNormalizer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (
        ParentNodeProvider,
        PositionProvider,
    )

    def __init__(self) -> None:
        self.annotation_depth = 0
        self.required_typing_names: set[str] = set()
        self.has_typing_import = False

    def visit_Annotation(self, node: cst.Annotation) -> None:
        self.annotation_depth += 1

    def leave_Annotation(
        self,
        original_node: cst.Annotation,
        updated_node: cst.Annotation,
    ) -> cst.Annotation:
        self.annotation_depth -= 1
        return updated_node

    def visit_Param(self, node: cst.Param) -> bool:
        return True

    def in_annotation(self) -> bool:
        return self.annotation_depth > 0

    def visit_ImportAlias(self, node: cst.ImportAlias) -> None:
        name = node.name

        if isinstance(name, cst.Name) and name.value == "typing":
            self.has_typing_import = True

    def leave_Subscript(
        self,
        original_node: cst.Subscript,
        updated_node: cst.Subscript,
    ) -> cst.BaseExpression:
        if not self.in_annotation():
            return updated_node

        # list[T] / tuple[T] / dict[K, V] -> List[T] / Tuple[T] / Dict[K, V]
        typing_name = is_builtin_generic_name(updated_node.value)
        if typing_name is not None:
            self.required_typing_names.add(typing_name)
            return updated_node.with_changes(value=make_typing_name(typing_name))

        # typing.List[T] -> List[T]
        for name in TYPING_NAMES:
            if is_typing_attr(updated_node.value, name):
                self.required_typing_names.add(name)
                return updated_node.with_changes(value=make_typing_name(name))

        # Union[A, B] / typing.Union[A, B] -> A | B
        # Union[A, None] -> Optional[A]
        # Union[A, B, None] -> Optional[A | B]
        if is_union_name(updated_node.value):
            values = split_subscript_elements(updated_node)
            non_none_values = [value for value in values if not is_none_name(value)]
            has_none = len(non_none_values) != len(values)

            if not non_none_values:
                return cst.Name("None")

            union_expr = build_pipe_union(non_none_values)

            if has_none:
                self.required_typing_names.add("Optional")
                return make_single_subscript(cst.Name("Optional"), union_expr)

            return union_expr

        # typing.Optional[T] -> Optional[T]
        # Optional[Union[A, B]] 会在内部 Union 先被转成 A | B
        if is_optional_name(updated_node.value):
            self.required_typing_names.add("Optional")
            return updated_node.with_changes(value=cst.Name("Optional"))

        return updated_node

    def leave_BinaryOperation(
        self,
        original_node: cst.BinaryOperation,
        updated_node: cst.BinaryOperation,
    ) -> cst.BaseExpression:
        if not self.in_annotation():
            return updated_node

        if not isinstance(updated_node.operator, cst.BitOr):
            return updated_node

        items = flatten_pipe_union(updated_node)
        non_none_items = [item for item in items if not is_none_name(item)]

        if len(non_none_items) == len(items):
            return updated_node

        if not non_none_items:
            return cst.Name("None")

        self.required_typing_names.add("Optional")

        inner = build_pipe_union(non_none_items)
        return make_single_subscript(cst.Name("Optional"), inner)

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        if not self.required_typing_names:
            return updated_node

        existing_typing_imports: set[str] = set()
        new_body: list[cst.CSTNode] = []
        inserted_or_updated = False

        for stmt in updated_node.body:
            if isinstance(stmt, cst.SimpleStatementLine) and len(stmt.body) == 1:
                small_stmt = stmt.body[0]

                if (
                    isinstance(small_stmt, cst.ImportFrom)
                    and isinstance(small_stmt.module, cst.Name)
                    and small_stmt.module.value == "typing"
                    and not isinstance(small_stmt.names, cst.ImportStar)
                ):
                    aliases = list(small_stmt.names)

                    for alias in aliases:
                        if isinstance(alias.name, cst.Name):
                            existing_typing_imports.add(alias.name.value)

                    missing_names = sorted(self.required_typing_names - existing_typing_imports)

                    if missing_names:
                        aliases.extend(
                            cst.ImportAlias(name=cst.Name(name))
                            for name in missing_names
                        )

                        stmt = stmt.with_changes(
                            body=[
                                small_stmt.with_changes(
                                    names=aliases,
                                )
                            ]
                        )

                    inserted_or_updated = True

            new_body.append(stmt)

        if inserted_or_updated:
            return updated_node.with_changes(body=new_body)

        import_stmt = cst.SimpleStatementLine(
            body=[
                cst.ImportFrom(
                    module=cst.Name("typing"),
                    names=[
                        cst.ImportAlias(name=cst.Name(name))
                        for name in sorted(self.required_typing_names)
                    ],
                )
            ]
        )

        body = list(updated_node.body)
        insert_index = find_import_insert_index(body)

        body.insert(insert_index, import_stmt)

        return updated_node.with_changes(body=body)


def find_import_insert_index(body: list[cst.CSTNode]) -> int:
    index = 0

    # 跳过模块 docstring。
    if body:
        first = body[0]
        if (
            isinstance(first, cst.SimpleStatementLine)
            and len(first.body) == 1
            and isinstance(first.body[0], cst.Expr)
            and isinstance(first.body[0].value, cst.SimpleString)
        ):
            index = 1

    # 跳过 from __future__ import ...
    while index < len(body):
        stmt = body[index]

        if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
            break

        small_stmt = stmt.body[0]

        if (
            isinstance(small_stmt, cst.ImportFrom)
            and isinstance(small_stmt.module, cst.Name)
            and small_stmt.module.value == "__future__"
        ):
            index += 1
            continue

        break

    return index


def iter_python_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix == ".py" else []

    files: list[Path] = []

    for path in root.rglob("*.py"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        files.append(path)

    return sorted(files)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def normalize_file(path: Path, *, dry_run: bool) -> bool:
    source = read_text(path)

    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError as exc:
        print(f"[syntax-error] {path}: {exc}")
        return False

    wrapper = cst.metadata.MetadataWrapper(module)
    transformer = AnnotationNormalizer()
    updated_module = wrapper.visit(transformer)
    updated_source = updated_module.code

    if updated_source == source:
        return False

    print(f"[changed] {path}")

    if not dry_run:
        path.write_text(updated_source, encoding="utf-8")

    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize Python typing annotations to project style."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Python files or directories to rewrite.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changed files without writing them.",
    )

    args = parser.parse_args()

    changed_count = 0

    for raw_path in args.paths:
        root = Path(raw_path)

        for path in iter_python_files(root):
            changed = normalize_file(path, dry_run=args.dry_run)
            if changed:
                changed_count += 1

    print(f"Changed files: {changed_count}")
    return 1 if args.dry_run and changed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())