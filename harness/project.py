"""Project detection and validation-plan derivation.

The harness never hardcodes a fixed command sequence. It reads the repository's
own markers (manifests, lockfiles, script definitions, test layout), checks
which tools actually exist on this machine, and derives a tiered plan:

    syntax  -> cheapest possible proof the file still parses
    static  -> analyzer / type checker / linter
    test    -> the project's own test suite, narrowed to what changed

Steps whose tool is missing are returned as unavailable with a reason instead
of being silently dropped, so an environment gap stays visible rather than
being mistaken for a passing check.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import terminal

TIERS = ("syntax", "static", "test")


@dataclass
class ValidationStep:
    name: str
    command: str
    tier: str
    why: str
    available: bool = True
    unavailable_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


MARKERS = {
    "python": ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile", "tox.ini"),
    "flutter": ("pubspec.yaml",),
    "node": ("package.json",),
    "rust": ("Cargo.toml",),
    "go": ("go.mod",),
    "maven": ("pom.xml",),
    "gradle": ("build.gradle", "build.gradle.kts", "settings.gradle"),
    "cmake": ("CMakeLists.txt",),
    "make": ("Makefile", "makefile", "GNUmakefile"),
}

SUFFIX_KIND = {
    ".py": "python", ".dart": "flutter", ".rs": "rust", ".go": "go",
    ".js": "node", ".jsx": "node", ".ts": "node", ".tsx": "node", ".mjs": "node",
    ".java": "maven", ".kt": "gradle", ".c": "cmake", ".cc": "cmake",
    ".cpp": "cmake", ".h": "cmake", ".hpp": "cmake",
}


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


def python_interpreter(root: Path) -> str:
    """Prefer the project's own virtualenv over the ambient interpreter."""
    for candidate in ("venv/bin/python", ".venv/bin/python", "env/bin/python",
                      "venv/Scripts/python.exe", ".venv/Scripts/python.exe"):
        interpreter = root / candidate
        if interpreter.exists():
            return str(interpreter)
    return "python3" if terminal.tool_path("python3") else "python"


def _package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    return "npm"


def detect(root: Path) -> dict:
    """Identify what kind of project this is from its own files."""
    root = Path(root)
    kinds: list[str] = []
    markers: list[str] = []
    for kind, names in MARKERS.items():
        found = [name for name in names if (root / name).exists()]
        if found:
            kinds.append(kind)
            markers.extend(found)

    profile: dict = {
        "root": str(root),
        "kinds": kinds or ["unknown"],
        "markers": markers,
        "is_git_repo": (root / ".git").exists(),
    }

    if "node" in kinds:
        package = _read_json(root / "package.json")
        profile["node_scripts"] = sorted((package.get("scripts") or {}).keys())
        profile["package_manager"] = _package_manager(root)
    if "python" in kinds:
        profile["python"] = python_interpreter(root)
        profile["test_dirs"] = [
            name for name in ("tests", "test", "src/tests") if (root / name).is_dir()
        ]
    if "flutter" in kinds:
        try:
            pubspec = (root / "pubspec.yaml").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            pubspec = ""
        profile["is_flutter"] = "flutter:" in pubspec or "sdk: flutter" in pubspec
    return profile


#: `python -m pytest` succeeds or fails on whether *that interpreter* can import
#: pytest, which PATH cannot answer. Probed once per interpreter and cached.
_MODULE_CACHE: dict[tuple[str, str], bool] = {}


def python_has_module(interpreter: str, module: str, root: Path) -> bool:
    key = (interpreter, module)
    if key not in _MODULE_CACHE:
        result = terminal.execute(f"{interpreter} -c 'import {module}'", root, timeout=30)
        _MODULE_CACHE[key] = bool(result.ok)
    return _MODULE_CACHE[key]


def _module_step(name, command, tier, why, interpreter: str, module: str, root: Path) -> ValidationStep:
    if python_has_module(interpreter, module, root):
        return ValidationStep(name=name, command=command, tier=tier, why=why)
    return ValidationStep(
        name=name, command=command, tier=tier, why=why, available=False,
        unavailable_reason=(
            f"{interpreter} cannot import '{module}', so this check would report an "
            "environment failure rather than anything about the code"
        ),
    )


def _step(name, command, tier, why, tool: str | None = None) -> ValidationStep:
    if tool is not None and terminal.tool_path(tool) is None:
        return ValidationStep(
            name=name, command=command, tier=tier, why=why, available=False,
            unavailable_reason=f"'{tool}' is not on PATH on this machine",
        )
    return ValidationStep(name=name, command=command, tier=tier, why=why)


def _quote(paths: list[str]) -> str:
    return " ".join(f"'{path}'" for path in paths)


def derive_plan(
    root: Path,
    edited_files: list[str] | None = None,
    *,
    depth: str = "standard",
    profile: dict | None = None,
) -> list[ValidationStep]:
    """Build a validation plan for this project, narrowed to what changed.

    depth: "quick" = syntax only, "standard" = syntax + static,
           "full" = syntax + static + tests.
    """
    root = Path(root)
    profile = profile or detect(root)
    edited = [path for path in (edited_files or []) if path]
    kinds = set(profile["kinds"])
    # An edit in a language whose manifest lives elsewhere still deserves the
    # matching checks, so infer extra kinds from the edited files themselves.
    for path in edited:
        kind = SUFFIX_KIND.get(Path(path).suffix.lower())
        if kind and any((root / marker).exists() for marker in MARKERS.get(kind, ())):
            kinds.add(kind)

    wanted_tiers = {"quick": {"syntax"}, "standard": {"syntax", "static"}}.get(depth, set(TIERS))
    steps: list[ValidationStep] = []

    if "python" in kinds:
        interpreter = profile.get("python") or python_interpreter(root)
        python_files = [path for path in edited if path.endswith(".py")]
        if python_files:
            steps.append(ValidationStep(
                name="python syntax",
                command=f"{interpreter} -m py_compile {_quote(python_files)}",
                tier="syntax",
                why="cheapest proof the edited modules still parse",
            ))
        if terminal.tool_path("ruff"):
            steps.append(_step("ruff", f"ruff check {_quote(python_files) or '.'}", "static",
                               "fast lint: undefined names, unused imports, obvious breakage", "ruff"))
        if terminal.tool_path("mypy"):
            steps.append(_step("mypy", f"mypy {_quote(python_files) or '.'}", "static",
                               "type errors introduced by the change", "mypy"))
        test_target = _python_test_target(root, edited, profile)
        pytest_step = _module_step(
            "pytest", f"{interpreter} -m pytest -q {test_target}".strip(), "test",
            "the project's own tests are the real contract", interpreter, "pytest", root,
        )
        # If pytest is missing, fall back to the stdlib runner - but only when the
        # suite is actually written for it. Running `unittest discover` over
        # pytest-style bare functions collects nothing and exits 0, which would
        # be a false pass: worse than an honest "skipped".
        unittest_dir = _unittest_dir(root, profile) if not pytest_step.available else None
        if unittest_dir:
            steps.append(ValidationStep(
                name="unittest",
                command=f"{interpreter} -m unittest discover -s {unittest_dir} -t . -v",
                tier="test",
                why="pytest is unavailable here, but this suite uses unittest.TestCase",
            ))
        else:
            steps.append(pytest_step)

    if "flutter" in kinds:
        driver = "flutter" if profile.get("is_flutter", True) else "dart"
        steps.append(_step(f"{driver} analyze", f"{driver} analyze", "static",
                           "the Dart analyzer is the authoritative syntax + type check", driver))
        steps.append(_step("dart format check", "dart format --output=none --set-exit-if-changed .", "static",
                           "confirms the edit preserved project formatting", "dart"))
        steps.append(_step(f"{driver} test", f"{driver} test", "test", "widget/unit tests", driver))

    if "node" in kinds:
        manager = profile.get("package_manager") or _package_manager(root)
        runner = "npm run" if manager == "npm" else f"{manager} run"
        scripts = set(profile.get("node_scripts") or [])
        if (root / "tsconfig.json").exists():
            steps.append(_step("tsc", "npx --no-install tsc --noEmit", "syntax",
                               "the TypeScript compiler is the syntax + type gate", "npx"))
        for script, tier, why in (
            ("typecheck", "static", "project-defined type check"),
            ("lint", "static", "project-defined lint rules"),
            ("test", "test", "project-defined test suite"),
            ("build", "test", "the build is the last proof nothing broke"),
        ):
            if script in scripts:
                steps.append(_step(f"{manager} {script}", f"{runner} {script}", tier, why, manager))

    if "rust" in kinds:
        steps.append(_step("cargo check", "cargo check --all-targets", "syntax",
                           "type-checks the crate without producing binaries", "cargo"))
        steps.append(_step("cargo clippy", "cargo clippy --all-targets", "static", "lint", "cargo"))
        steps.append(_step("cargo test", "cargo test", "test", "test suite", "cargo"))

    if "go" in kinds:
        steps.append(_step("go build", "go build ./...", "syntax", "compiles every package", "go"))
        steps.append(_step("go vet", "go vet ./...", "static", "static analysis", "go"))
        steps.append(_step("go test", "go test ./...", "test", "test suite", "go"))

    if "gradle" in kinds:
        wrapper = "./gradlew" if (root / "gradlew").exists() else "gradle"
        probe = None if wrapper.startswith("./") else "gradle"
        steps.append(_step("gradle compile", f"{wrapper} compileJava --console=plain -q", "syntax",
                           "compiles JVM sources", probe))
        steps.append(_step("gradle test", f"{wrapper} test --console=plain -q", "test", "test suite", probe))
    elif "maven" in kinds:
        wrapper = "./mvnw" if (root / "mvnw").exists() else "mvn"
        probe = None if wrapper.startswith("./") else "mvn"
        steps.append(_step("maven compile", f"{wrapper} -q -DskipTests compile", "syntax",
                           "compiles JVM sources", probe))
        steps.append(_step("maven test", f"{wrapper} -q test", "test", "test suite", probe))

    if "cmake" in kinds and (root / "build").is_dir():
        steps.append(_step("cmake build", "cmake --build build", "syntax", "compiles the project", "cmake"))
    elif "make" in kinds:
        steps.append(_step("make dry-run", "make -n", "syntax",
                           "confirms the makefile still resolves before a real build", "make"))

    selected = [step for step in steps if step.tier in wanted_tiers]
    if not selected and steps:
        selected = [step for step in steps if step.tier == "syntax"] or steps[:1]
    return selected


def _unittest_dir(root: Path, profile: dict) -> str | None:
    """The test directory, if its files are written against unittest.TestCase."""
    for name in (profile.get("test_dirs") or ["tests", "test"]):
        directory = root / name
        if not directory.is_dir():
            continue
        for candidate in directory.rglob("*.py"):
            try:
                body = candidate.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "TestCase" in body and "unittest" in body:
                return name
    return None


def _python_test_target(root: Path, edited: list[str], profile: dict) -> str:
    """Narrow pytest to tests related to the edited files when possible."""
    candidates: list[str] = []
    for path in edited:
        stem = Path(path).stem
        if not stem or stem.startswith("_"):
            continue
        for test_dir in (profile.get("test_dirs") or ["tests"]):
            for name in (f"test_{stem}.py", f"{stem}_test.py"):
                candidate = root / test_dir / name
                if candidate.exists():
                    candidates.append(str(candidate.relative_to(root)))
    return _quote(sorted(set(candidates)))


def plan_to_dicts(steps: list[ValidationStep]) -> list[dict]:
    return [step.to_dict() for step in steps]
