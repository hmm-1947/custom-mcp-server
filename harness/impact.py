"""Impact analysis: what else has to change.

A localized edit is only correct if every dependent site agrees with it. Given
the files (and optionally symbols) a task touches, this module finds:

  * reverse dependencies - modules that import the edited file
  * symbol call sites    - references to the functions/classes it defines
  * sibling artefacts    - schemas, migrations, routes, serializers, tests,
                           fixtures and config keys that usually move together

Results are ranked and recorded on the session, so the harness can keep
reminding the model about candidates it has not looked at yet.
"""

from __future__ import annotations

from pathlib import Path

from . import ripgrep

#: Directory/file-name hints for artefacts that co-change with code.
RELATED_HINTS = (
    ("migration", "database migration - a model/column change usually needs one"),
    ("alembic", "alembic migration directory"),
    ("schema", "request/response schema or serializer"),
    ("serializer", "serializer definition"),
    ("model", "data model definition"),
    ("route", "route/endpoint registration"),
    ("router", "route/endpoint registration"),
    ("controller", "controller/handler layer"),
    ("fixture", "test fixture that encodes the old shape"),
    ("factory", "test factory that encodes the old shape"),
    ("conftest", "shared pytest fixtures"),
    ("openapi", "API contract document"),
    ("swagger", "API contract document"),
    ("proto", "wire contract"),
    ("graphql", "GraphQL schema"),
)

CONFIG_NAMES = (
    ".env.example", "settings.py", "config.py", "config.json", "config.yaml", "config.yml",
    "application.yml", "application.properties", "pubspec.yaml", "package.json",
    "pyproject.toml", "Cargo.toml", "go.mod", "docker-compose.yml", "Dockerfile",
    "requirements.txt", "tsconfig.json",
)

TEST_HINTS = ("test_", "_test.", "spec.", ".spec.", "tests/", "test/")

SOURCE_SUFFIXES = frozenset({
    ".py", ".dart", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".rb", ".php", ".cs", ".swift",
})


def _module_aliases(root: Path, relative: str) -> list[str]:
    """Import spellings that could refer to this file."""
    path = Path(relative)
    stem = path.stem
    aliases = {stem, path.name}
    if stem == "__init__" and path.parent != Path("."):
        aliases.add(path.parent.name)
    dotted = ".".join(path.with_suffix("").parts)
    aliases.add(dotted)
    # ...and the trailing 1-3 segment forms used by relative/package imports
    parts = path.with_suffix("").parts
    for size in (2, 3):
        if len(parts) >= size:
            aliases.add(".".join(parts[-size:]))
            aliases.add("/".join(parts[-size:]))
    return sorted({alias for alias in aliases if alias and alias not in {".", ""}})


def _import_patterns(aliases: list[str]) -> str:
    escaped = "|".join(alias.replace(".", r"\.").replace("/", r"/") for alias in aliases if alias)
    if not escaped:
        return ""
    return (
        rf"(^\s*(from|import)\s+[\w.]*({escaped})|"
        rf"require\(['\"][^'\"]*({escaped})['\"]\)|"
        rf"import\s+[^;\n]*['\"][^'\"]*({escaped})['\"]|"
        rf"package:[\w/]*({escaped}))"
    )


def dependents(root: Path, relative: str, max_results: int = 80) -> list[dict]:
    aliases = _module_aliases(root, relative)
    pattern = _import_patterns(aliases)
    if not pattern:
        return []
    found = ripgrep.search(root, pattern, max_results=max_results, context_lines=0)
    return [hit for hit in found["results"] if hit["file"] != relative]


def symbol_sites(root: Path, symbols: list[str], exclude: str | None = None,
                 max_results: int = 120) -> dict[str, list[dict]]:
    sites: dict[str, list[dict]] = {}
    for symbol in symbols[:25]:
        if len(symbol) < 3 or symbol.startswith("_"):
            continue
        found = ripgrep.search(
            root, rf"\b{symbol}\b", max_results=max_results, context_lines=0,
        )
        hits = [hit for hit in found["results"] if hit["file"] != exclude]
        if hits:
            sites[symbol] = hits
    return sites


def defined_symbols(absolute: Path) -> list[str]:
    """Public functions/classes defined in a file, via tree-sitter when possible."""
    try:
        from code_engine.finder import list_classes, list_functions
        from code_engine.languages import LANGUAGES
    except Exception:
        return []
    if absolute.suffix.lower() not in LANGUAGES:
        return []
    try:
        names = [item["name"] for item in list_classes(str(absolute))]
        names += [item["name"] for item in list_functions(str(absolute))]
    except Exception:
        return []
    return [name for name in dict.fromkeys(names) if name and not name.startswith("_")]


def related_artefacts(root: Path, relative: str) -> list[dict]:
    """Sibling files that conventionally co-change with this one."""
    stem = Path(relative).stem.lower()
    candidates: list[dict] = []
    for path in ripgrep.iter_files(root):
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            continue
        if rel == relative:
            continue
        lowered = rel.lower()
        name = path.name
        if name in CONFIG_NAMES:
            candidates.append({"file": rel, "why": "configuration/manifest that may declare this behaviour",
                               "weight": 1})
            continue
        for hint, why in RELATED_HINTS:
            if hint in lowered:
                weight = 3 if stem and stem in lowered else 1
                candidates.append({"file": rel, "why": why, "weight": weight})
                break
        else:
            if stem and stem in lowered and any(marker in lowered for marker in TEST_HINTS):
                candidates.append({"file": rel, "why": "test covering this file", "weight": 4})
    candidates.sort(key=lambda item: (-item["weight"], item["file"]))
    deduped: dict[str, dict] = {}
    for item in candidates:
        deduped.setdefault(item["file"], item)
    return list(deduped.values())[:40]


def analyze(root: Path, relatives: list[str], *, include_symbols: bool = True,
            max_files: int = 12) -> dict:
    """Full impact report for a set of files."""
    root = Path(root)
    report: dict = {"targets": [], "ranked_candidates": [], "summary": ""}
    scores: dict[str, dict] = {}

    def bump(path: str, weight: int, why: str) -> None:
        entry = scores.setdefault(path, {"file": path, "score": 0, "reasons": []})
        entry["score"] += weight
        if why not in entry["reasons"]:
            entry["reasons"].append(why)

    for relative in relatives[:max_files]:
        absolute = root / relative
        target: dict = {"file": relative, "exists": absolute.exists()}

        importers = dependents(root, relative)
        target["dependents"] = importers[:30]
        for hit in importers:
            bump(hit["file"], 5, f"imports {relative}")

        if include_symbols and absolute.suffix.lower() in SOURCE_SUFFIXES:
            symbols = defined_symbols(absolute)
            target["defined_symbols"] = symbols[:30]
            sites = symbol_sites(root, symbols, exclude=relative)
            target["symbol_sites"] = {name: hits[:12] for name, hits in sites.items()}
            for name, hits in sites.items():
                for hit in hits:
                    bump(hit["file"], 3, f"references {name}")

        artefacts = related_artefacts(root, relative)
        target["related_artefacts"] = artefacts[:20]
        for item in artefacts:
            bump(item["file"], item["weight"], item["why"])

        report["targets"].append(target)

    ranked = sorted(scores.values(), key=lambda item: (-item["score"], item["file"]))
    report["ranked_candidates"] = ranked[:40]
    report["summary"] = (
        f"{len(ranked)} file(s) may be affected by changes to "
        f"{', '.join(relatives[:max_files])}. Highest-scoring candidates are the ones to open first."
    )
    report["method"] = (
        "reverse imports (weight 5) + references to defined symbols (weight 3) + "
        "conventional co-changing artefacts such as migrations, schemas, routes, config and tests"
    )
    return report
