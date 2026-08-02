"""Dependency declarations are complete, and mean what they say.

Three checks, with deliberately different strengths:

  1. every CORE distribution imports — a hard failure, because core means the
     suite and the planning path cannot run without it;
  2. every OPTIONAL distribution is declared and imports only if installed —
     requiring an optional package to import would make it mandatory;
  3. two-way coverage: every direct third-party import anywhere in the repo is
     declared in one of the two files, and every declared distribution is
     actually imported by something.

Check 3 is the one that earns its keep. A new import added to a module fails
here, on the machine that added it, instead of on someone else's.

The import-name to distribution mapping is an explicit table, never inferred.
Google is why: three distributions install into the one `google` namespace
package, so `import google` succeeding proves nothing about which of them is
present.

    python tests/test_dependencies.py
    python tests/test_dependencies.py --optional-env   # require the optional set too
"""

import ast
import importlib
import importlib.metadata as md
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE_FILE = ROOT / "requirements.txt"
OPTIONAL_FILE = ROOT / "requirements-optional.txt"

failures = []
skipped = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def skip(name, why):
    print(f"  SKIP  {name}  <- {why}")
    skipped.append(name)


# Distribution name -> the module name(s) that actually prove it is installed.
# A distribution with several candidates is satisfied by any one of them.
IMPORT_NAMES = {
    "jsonschema": ["jsonschema"],
    "Pillow": ["PIL"],
    "requests": ["requests"],
    "openai": ["openai"],
    "python-dotenv": ["dotenv"],
    "anthropic": ["anthropic"],
    "edge-tts": ["edge_tts"],
    # Namespace packages: `import google` would succeed for any of the three, so
    # each is probed at the submodule that only it provides.
    "google-genai": ["google.genai"],
    "google-api-python-client": ["googleapiclient"],
    "google-auth-oauthlib": ["google_auth_oauthlib"],
    "google-auth": ["google.auth"],
    "replicate": ["replicate"],
    "mutagen": ["mutagen"],
    "pydub": ["pydub"],
    "matplotlib": ["matplotlib"],
    "geopandas": ["geopandas"],
    "torch": ["torch"],
    "whisperx": ["whisperx"],
    "openai-whisper": ["whisper"],
    "ddgs": ["ddgs"],
    "duckduckgo-search": ["duckduckgo_search"],
}

# Top-level import name -> distributions that could provide it. Only needed
# where the two differ or where one name is shared.
PROVIDERS = {
    "PIL": ["Pillow"],
    "dotenv": ["python-dotenv"],
    "edge_tts": ["edge-tts"],
    "googleapiclient": ["google-api-python-client"],
    "google_auth_oauthlib": ["google-auth-oauthlib"],
    "google": ["google-genai", "google-auth", "google-api-python-client"],
    "whisper": ["openai-whisper"],
    "duckduckgo_search": ["duckduckgo-search"],
}

# Imported but deliberately unpinnable. Recorded in requirements-optional.txt as
# a comment, with its origin, so it is declared without being fabricated.
NOT_ON_PYPI = {"creative_feedback_loop"}


def parse_requirements(path: Path) -> list[str]:
    """Distribution names, ignoring comments, blank lines and version ranges."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        out.append(re.split(r"[<>=!~\[]", line, 1)[0].strip())
    return out


def importable(dist: str) -> bool:
    for mod in IMPORT_NAMES.get(dist, [dist]):
        try:
            importlib.import_module(mod)
            return True
        except Exception:
            continue
    return False


def declared_version(dist: str) -> str:
    try:
        return md.version(dist)
    except md.PackageNotFoundError:
        return "not installed"


def repo_imports() -> dict[str, set[str]]:
    """Every direct third-party top-level import, by AST, with its source files."""
    stdlib = set(sys.stdlib_module_names)
    local = {p.stem for p in ROOT.glob("*.py")} | {p.stem for p in (ROOT / "tests").glob("*.py")}
    found: dict[str, set[str]] = {}
    files = sorted(ROOT.glob("*.py")) + sorted((ROOT / "tests").glob("*.py"))
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            for m in mods:
                top = m.split(".")[0]
                if top in stdlib or top in local or top.startswith("_"):
                    continue
                found.setdefault(top, set()).add(f.name)
    return found


def main() -> int:
    require_optional = "--optional-env" in sys.argv

    print("\n1. core dependencies are installed and import")
    if not CORE_FILE.is_file():
        check("requirements.txt exists", False, str(CORE_FILE))
        return 1
    core = parse_requirements(CORE_FILE)
    check("requirements.txt declares something", bool(core))
    for dist in core:
        check(f"core: {dist} imports ({declared_version(dist)})", importable(dist),
              f"pip install -r requirements.txt")

    print("\n2. optional dependencies are declared; imported only if installed")
    if not OPTIONAL_FILE.is_file():
        check("requirements-optional.txt exists", False, str(OPTIONAL_FILE))
        return 1
    optional = parse_requirements(OPTIONAL_FILE)
    check("requirements-optional.txt declares something", bool(optional))
    overlap = sorted(set(core) & set(optional))
    check("no distribution is both core and optional", not overlap, f"{overlap}")
    for dist in optional:
        if importable(dist):
            check(f"optional: {dist} imports ({declared_version(dist)})", True)
        elif require_optional:
            check(f"optional: {dist} imports", False,
                  "--optional-env was requested, so the full set must be present")
        else:
            skip(f"optional: {dist}", "not installed — optional means optional")

    print("\n3. two-way coverage between the code and the declarations")
    declared = set(core) | set(optional)
    imports = repo_imports()

    undeclared = []
    for top, files in sorted(imports.items()):
        if top in NOT_ON_PYPI:
            continue
        candidates = PROVIDERS.get(top, [top])
        if not any(c in declared for c in candidates):
            undeclared.append(f"{top} (in {', '.join(sorted(files)[:3])})")
    check("every third-party import is declared", not undeclared,
          "; ".join(undeclared))

    used_tops = set(imports)
    unused = []
    for dist in sorted(declared):
        names = {n.split(".")[0] for n in IMPORT_NAMES.get(dist, [dist])}
        # A distribution also counts as used when it is one of the providers of
        # an import name the code actually uses.
        provided = {t for t, ds in PROVIDERS.items() if dist in ds}
        if not (names | provided) & used_tops:
            unused.append(dist)
    check("every declared distribution is imported by something", not unused,
          f"declared but unused: {unused}")

    check("the unpinnable import is recorded in the optional file",
          all(n in OPTIONAL_FILE.read_text(encoding="utf-8") for n in NOT_ON_PYPI),
          f"{sorted(NOT_ON_PYPI)} must be named in a comment so the audit above "
          f"does not report it as undeclared")

    print(f"\n{'=' * 62}")
    if skipped:
        print(f"{len(skipped)} optional dependenc(ies) not installed here — expected")
    if failures:
        print(f"FAILED: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all dependency checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
