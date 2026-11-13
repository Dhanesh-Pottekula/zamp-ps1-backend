"""
Fail when any file under app/ breaks the one-way import rules (BUILD_SPEC section 0).

- adapters import only app.config, app.adapters._types and third-party libraries;
- entities, including the stage modules under app/entities/stages/, import only app.adapters (its package),
  app.entities._types, app.config, the standard library, pydantic and sqlalchemy; a sibling entity or stage module may
  be imported only for typing (inside `if TYPE_CHECKING:`), except by the registry in app/entities/__init__.py;
- services import only app.entities (its package) and their own router package;
- controllers import only their own router's services, models and config;
- router models and config import only app.entities (its package), app.routers.config, fastapi and pydantic;
- a third-party library other than pydantic, sqlalchemy and fastapi appears only in adapters.

Exit status 1 lists every offending import.
"""

import ast
import sys
from pathlib import Path
from typing import Final, Literal

type Layer = Literal["adapter", "entity", "service", "controller", "router", "config", "wiring"]


class _Rules:
    """Layer detection and the allowed import table."""

    ROOT: Final = Path(__file__).resolve().parents[1]
    STDLIB: Final = frozenset(sys.stdlib_module_names)
    WEB: Final = frozenset({"fastapi", "pydantic", "starlette"})

    @staticmethod
    def layer(path: Path) -> tuple[Layer, str | None]:
        """Return the layer of an app file and, for router files, the router name."""
        parts = path.relative_to(_Rules.ROOT / "app").parts
        if parts[0] == "adapters":
            return "adapter", None

        if parts[0] == "entities":
            return "entity", None

        if parts[0] == "config":
            return "config", None

        if parts[0] == "routers" and len(parts) > 2:
            if parts[2] == "services":
                return "service", parts[1]

            return ("controller" if parts[2] == "controllers" else "router"), parts[1]

        return "wiring", None

    @staticmethod
    def allowed(module: str, path: Path, layer: Layer, router: str | None, *, typing_only: bool) -> bool:
        """Return whether one imported module is allowed in this file."""
        top = module.split(".", maxsplit=1)[0]
        relative = path.relative_to(_Rules.ROOT).as_posix()
        if top in _Rules.STDLIB:
            return True

        if top != "app":
            return _Rules.third_party(top, layer)

        own = f"app.routers.{router}"
        match layer:
            case "adapter":
                inside_package = relative == "app/adapters/__init__.py" and module.startswith("app.adapters.")
                return module == "app.config" or module.startswith("app.adapters._types") or inside_package
            case "entity":
                sibling = module.startswith("app.entities.") and not module.startswith("app.entities._types")
                registry = relative == "app/entities/__init__.py"
                return (
                    module in {"app.adapters", "app.config"}
                    or module.startswith("app.entities._types")
                    or (sibling and (typing_only or registry))
                )
            case "service":
                return module == "app.entities" or module.startswith(own)
            case "controller":
                return module.startswith(
                    (f"{own}.services", f"{own}.{router}_models", f"{own}.config", f"{own}.controllers.")
                )
            case "router":
                return module in {"app.entities", "app.routers.config"} or module.startswith(own)
            case "config":
                return module.startswith("app.config")
            case "wiring":
                return True

    @staticmethod
    def third_party(top: str, layer: Layer) -> bool:
        """Return whether a third-party package may be imported by this layer."""
        match layer:
            case "adapter":
                return True
            case "entity":
                return top in {"pydantic", "sqlalchemy"}
            case "config":
                return top in {"pydantic", "pydantic_settings"}
            case _:
                return top in _Rules.WEB


class _Scanner(ast.NodeVisitor):
    """Collect (line, module, typing_only) for every import in one file."""

    def __init__(self) -> None:
        """Start with no imports and outside any TYPE_CHECKING block."""
        self.imports: list[tuple[int, str, bool]] = []
        self._typing_depth = 0

    def visit_If(self, node: ast.If) -> None:
        """Mark imports inside `if TYPE_CHECKING:` as typing-only."""
        guarded = isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        self._typing_depth += guarded
        for child in node.body:
            self.visit(child)

        self._typing_depth -= guarded
        for child in node.orelse:
            self.visit(child)

    def visit_Import(self, node: ast.Import) -> None:
        """Record plain imports."""
        self.imports.extend((node.lineno, alias.name, self._typing_depth > 0) for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record from-imports (relative imports are offenders by construction)."""
        module = node.module or ""
        self.imports.append((node.lineno, "." * node.level + module, self._typing_depth > 0))


def main() -> int:
    """Scan every Python file under app/ and print offenders."""
    offenders: list[str] = []
    for path in sorted((_Rules.ROOT / "app").rglob("*.py")):
        scanner = _Scanner()
        scanner.visit(ast.parse(path.read_text("utf-8"), filename=str(path)))
        layer, router = _Rules.layer(path)
        for line, module, typing_only in scanner.imports:
            if module.startswith(".") or not _Rules.allowed(module, path, layer, router, typing_only=typing_only):
                offenders.append(f"{path.relative_to(_Rules.ROOT)}:{line}: {layer} imports {module}")

    if offenders:
        print("Import rule violations:")
        print("\n".join(f"  {offender}" for offender in offenders))
        return 1

    print("Import rules: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
