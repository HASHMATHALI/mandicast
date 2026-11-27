"""The documentation matches what exists (Phase 9 exit criterion, docs/08): generated schema reference, API
operations, CLI commands, file paths, links and cited test names."""

from __future__ import annotations

import json
import re
import subprocess
import sys

from sqlalchemy import text

from mandicast.config import PROJECT_ROOT
from mandicast.db import schema_doc

DOCS = sorted((PROJECT_ROOT / "docs").glob("*.md")) + [
    PROJECT_ROOT / "README.md", PROJECT_ROOT / "PROGRESS.md", PROJECT_ROOT / "dashboard" / "README.md"]

# Runtime folders and files that are git-ignored, so a clone does not have them.
RUNTIME = ("data/", "models/", "mlruns", "experiments/mlflow.db", "experiments/mlruns", "backups/", "logs/",
           "dashboard/node_modules", "dashboard/dist", "dashboard/test-results", "dashboard/playwright-report", ".env")
REPO_PATH = re.compile(r"^(?:src|docs|scripts|tests|dashboard|docker|migrations|experiments|reports|config|\.github)/"
                       r"[\w./-]*$")


def _text(path):
    return path.read_text(encoding="utf-8")


def _code(doc: str) -> list[str]:
    """Inline code spans and the lines of fenced code blocks."""
    fenced = re.findall(r"```[^\n]*\n(.*?)```", doc, flags=re.S)
    inline = re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", doc, flags=re.S))
    return inline + [line for block in fenced for line in block.splitlines()]


def test_schema_reference_matches_the_migrated_database(engine):
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 1
        generated = schema_doc.render(conn)
    assert generated == schema_doc.committed(), "run: python -m mandicast.db.schema_doc"


def test_api_specification_lists_every_operation():
    spec = json.loads(_text(PROJECT_ROOT / "dashboard" / "src" / "api" / "openapi.json"))
    actual = {f"{m.upper()} {re.sub(r'{[^}]+}', '{}', p)}" for p, ops in spec["paths"].items() for m in ops
              if m in {"get", "post", "put", "patch", "delete"}}
    doc = _text(PROJECT_ROOT / "docs" / "07_api_specification.md")
    documented = {f"{m} {re.sub(r'{[^}]+}', '{}', p)}"
                  for m, p in re.findall(r"`(GET|POST|PUT|PATCH|DELETE) (/v1/[^`\s?]*)", doc)}
    assert actual - documented == set(), "operations missing from docs/07"
    assert documented - actual == set(), "docs/07 lists operations the API does not have"


def test_documented_cli_commands_exist():
    out = subprocess.run([sys.executable, "-m", "mandicast", "--help"], capture_output=True, text=True,
                         cwd=PROJECT_ROOT, check=True).stdout
    commands = set(re.search(r"\{([a-z,-]+)\}", out).group(1).split(","))
    pattern = re.compile(r"(?:-m mandicast|^mandicast|run --rm worker) ([a-z][a-z0-9-]*)")
    missing = {(p.name, m) for p in DOCS for span in _code(_text(p)) for m in pattern.findall(span.strip())
               if m not in commands}
    assert missing == set()


def test_documented_paths_and_links_exist():
    missing = []
    for p in DOCS:
        doc = _text(p)
        for span in _code(doc):
            for token in span.split():
                token = token.strip("`'\",;:()")
                token = token.split("#")[0].split(":")[0]
                if not REPO_PATH.match(token) or any(c in token for c in "<*…") or token.startswith(RUNTIME):
                    continue
                short_doc = re.fullmatch(r"docs/(\d\d)", token)  # "docs/04" = docs/04_<name>.md
                if short_doc and list((PROJECT_ROOT / "docs").glob(f"{short_doc.group(1)}_*.md")):
                    continue
                # Dashboard documents write its paths relative to dashboard/ ("src/lib/chart.ts").
                if not (PROJECT_ROOT / token).exists() and not (PROJECT_ROOT / "dashboard" / token).exists():
                    missing.append(f"{p.name}: {token}")
        for target in re.findall(r"\]\(([^)\s]+)\)", doc):
            target = target.split("#")[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (p.parent / target).exists():
                missing.append(f"{p.name}: link {target}")
    assert missing == []


def test_cited_test_names_exist():
    defined = set()
    for f in (PROJECT_ROOT / "tests").glob("test_*.py"):
        defined |= set(re.findall(r"^(?:async )?def (test_\w+)", _text(f), flags=re.M))
    # "test_backfill_then_daily…" cites a test by the start of its name.
    cited = {(p.name, name, bool(more)) for p in DOCS for span in _code(_text(p))
             for name, more in re.findall(r"\b(test_[a-z0-9_]+)\b(?!\.py)(…)?", span)}
    unknown = {(doc, name) for doc, name, prefix in cited
               if not (name in defined or (prefix and any(d.startswith(name) for d in defined)))}
    assert unknown == set()
