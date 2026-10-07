"""Architectural absence: the code has no way to read project sources, repos, documents or indexes.

These tests parse the package source and assert capabilities are ABSENT, rather than trusting
prompts or conventions. They are the primary restricted-project isolation control; the deny-list is backup.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import typer

import opportunity_operator
from opportunity_operator import cli
from opportunity_operator.config import Settings
from opportunity_operator.ports.search import SearchProvider, SearchQuery

SRC = Path(opportunity_operator.__file__).parent
REPO = SRC.parent.parent
FILES = sorted(SRC.rglob("*.py"))


def rel(p: Path) -> str:
    return p.relative_to(SRC).as_posix()


def parse(p: Path) -> ast.Module:
    return ast.parse(p.read_text(), filename=str(p))


FORBIDDEN_IMPORTS = {
    "subprocess", "shutil", "glob", "fnmatch", "ctypes", "pty", "multiprocessing", "pickle", "marshal", "shelve",
    "requests", "ftplib", "smtplib", "imaplib", "poplib", "telnetlib", "paramiko", "webbrowser", "zipfile", "tarfile",
    "git", "dulwich", "pygit2", "chromadb", "faiss", "llama_index", "langchain", "lancedb", "qdrant_client",
    "weaviate", "pinecone", "sentence_transformers", "whoosh", "watchdog",
}
IMPORT_ALLOW = {"socket": {"adapters/http_fetcher.py"}, "tempfile": {"safe_fs.py"}}
FS_ATTRS = {"read_text", "read_bytes", "write_text", "write_bytes", "open", "iterdir", "glob", "rglob", "touch",
            "mkdir", "unlink", "rename", "rmdir", "symlink_to", "hardlink_to", "readlink", "walk", "listdir",
            "scandir", "system", "popen", "remove", "makedirs", "chmod", "fdopen", "copyfile", "copytree", "move"}
FS_ALLOW = {  # file -> attributes it may use
    "safe_fs.py": FS_ATTRS,
    "store/migrator.py": {"read_text", "iterdir"},     # bundled package resources only
    "seeds/loader.py": {"read_text"},                  # bundled package resource only
    "audit.py": {"read_bytes"},                        # paths come from DataDir.walk_files()
}
BANNED_BUILTINS = {"open", "eval", "exec", "compile", "__import__"}


def _roots(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name.split(".")[0] for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [node.module.split(".")[0]]
    return []


def test_package_is_discovered():
    assert len(FILES) > 20 and "safe_fs.py" in {rel(p) for p in FILES}


def import_violations(tree: ast.AST, relname: str) -> list[str]:
    bad = []
    for node in ast.walk(tree):
        for root in _roots(node):
            if root in FORBIDDEN_IMPORTS:
                bad.append(root)
            if root in IMPORT_ALLOW and relname not in IMPORT_ALLOW[root]:
                bad.append(root)
    return bad


def fs_violations(tree: ast.AST, relname: str) -> list[str]:
    allowed = FS_ALLOW.get(relname, set())
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in BANNED_BUILTINS:
            bad.append(f.id)
        elif isinstance(f, ast.Attribute):
            if f.attr in FS_ATTRS and f.attr not in allowed:
                bad.append(f.attr)
            if f.attr in {"import_module", "spec_from_file_location", "load_module", "exec_module"}:
                bad.append(f.attr)
    return bad


def test_no_forbidden_imports():
    assert [(rel(p), v) for p in FILES for v in import_violations(parse(p), rel(p))] == []


def test_no_filesystem_or_process_surface_outside_safe_fs():
    assert [(rel(p), v) for p in FILES for v in fs_violations(parse(p), rel(p))] == []


def test_the_detectors_actually_fire_on_violating_code():
    """Positive controls: if these stop failing, the scans above have lost their teeth."""
    cases_fs = {
        "open('/etc/passwd').read()": "open", "from pathlib import Path\nPath('x').read_text()": "read_text",
        "import os\nos.walk('/')": "walk", "import os\nos.listdir('.')": "listdir", "Path('.').iterdir()": "iterdir",
        "import os\nos.system('ls')": "system", "exec('1')": "exec", "__import__('os')": "__import__",
        "import importlib\nimportlib.import_module('x')": "import_module", "p.rglob('*.py')": "rglob",
    }
    for src, expected in cases_fs.items():
        assert expected in fs_violations(ast.parse(src), "orchestrator/stage.py"), src
    for src, expected in {"import subprocess": "subprocess", "from shutil import copytree": "shutil", "import git": "git",
                          "import chromadb": "chromadb", "import socket": "socket", "import tempfile": "tempfile"}.items():
        assert expected in import_violations(ast.parse(src), "orchestrator/stage.py"), src
    assert fs_violations(ast.parse("Path('x').read_text()"), "safe_fs.py") == []  # the one allowed place


def test_sqlite_files_are_opened_only_by_the_db_layer_and_the_read_only_audit():
    allowed = {"store/db.py", "audit.py"}
    found = {rel(p) for p in FILES for n in ast.walk(parse(p))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "connect"
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "sqlite3"}
    assert found == allowed


def _calls(name: str) -> set[str]:
    return {rel(p) for p in FILES for n in ast.walk(parse(p))
            if isinstance(n, ast.Call) and ((isinstance(n.func, ast.Attribute) and n.func.attr == name)
                                             or (isinstance(n.func, ast.Name) and n.func.id == name))}


def test_models_search_and_network_are_reached_only_through_guarded_wrappers():
    assert _calls("structured") == {"policy/guarded.py"}
    assert _calls("post_json") == {"policy/guarded.py"}
    assert {f for f in _calls("search")} == {"policy/guarded.py"}


def test_prompt_minting_is_confined_to_the_context_builder():
    users = {rel(p) for p in FILES if any(isinstance(n, ast.Name) and n.id in {"_MINT", "_mint_prompt"} for n in ast.walk(parse(p)))
             or "_mint_prompt" in p.read_text() or "_MINT" in p.read_text()}
    assert users == {"policy/prompt.py", "policy/context_builder.py"}


def _connect_roles() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for p in FILES:
        for n in ast.walk(parse(p)):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "connect" and len(n.args) >= 2:
                a = n.args[1]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.setdefault(a.value, set()).add(rel(p))
    return out


def test_privileged_database_roles_are_opened_only_where_designed():
    roles = _connect_roles()
    assert roles["owner"] == {"owner.py"}
    assert roles["context"] == {"app.py"}
    assert roles["migrator"] == {"store/migrator.py"}
    assert roles["agent"] <= {"app.py", "cli.py", "store/migrator.py"} and roles["system"] == {"app.py"}


def test_pipeline_modules_cannot_import_owner_authority_or_raw_adapters():
    owner_importers, adapter_importers = set(), set()
    for p in FILES:
        for n in ast.walk(parse(p)):
            if isinstance(n, ast.ImportFrom):
                names = {a.name for a in n.names}
                mod = n.module or ""
                if mod.endswith("owner") or "owner" in names and n.level:
                    owner_importers.add(rel(p))
                if mod.split(".")[-1] == "adapters" or ".adapters." in f".{mod}." or mod.startswith("adapters"):
                    adapter_importers.add(rel(p))
    assert owner_importers == {"cli.py", "seeds/loader.py"}
    assert adapter_importers <= {"cli.py"}  # composition happens at the edge, never inside the pipeline


def test_cli_surface_is_pinned_and_has_no_import_or_path_arguments():
    group = typer.main.get_command(cli.app)
    names = set(group.commands)  # type: ignore[attr-defined]
    assert names == set(cli.ALLOWED_COMMANDS)
    banned_words = ("import", "ingest", "index", "scan", "crawl", "load", "read", "mount", "attach", "clone", "watch-dir")
    assert not [n for n in names if any(w in n for w in banned_words)]
    for cmd in group.commands.values():  # type: ignore[attr-defined]
        for param in cmd.params:
            assert type(param.type).__name__ not in {"Path", "File"}, (cmd.name, param.name)  # click is vendored by typer
            assert param.name not in {"path", "file", "directory", "repo", "source", "src", "glob", "url"}, (cmd.name, param.name)


def test_settings_have_no_field_that_can_point_at_project_material():
    assert set(Settings.model_fields) == {
        "data_dir", "egress_mode", "api_allow_hosts", "extra_allow_hosts", "max_fetch_bytes", "min_host_interval_s",
        "min_quote_chars", "max_llm_calls", "max_tokens_in", "max_tokens_out", "max_cost_usd", "max_fetches",
        "max_searches", "confidential_to_cloud_llm",
    }
    assert Settings.model_config["extra"] == "forbid"
    try:
        Settings(repo_path="/somewhere")  # type: ignore[call-arg]
    except Exception:
        pass
    else:
        raise AssertionError("unknown settings keys must be rejected")


def test_search_port_is_query_only_with_no_local_corpus_variant():
    import inspect

    assert list(inspect.signature(SearchProvider.search).parameters) == ["self", "query"]
    assert set(SearchQuery.__dataclass_fields__) == {"text", "max_results"}


def test_declared_dependencies_contain_no_retrieval_or_repository_tooling():
    deps = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["dependencies"]
    joined = " ".join(deps).lower()
    for banned in ("git", "chroma", "faiss", "llama", "langchain", "qdrant", "pinecone", "weaviate", "lancedb", "sentence", "unstructured"):
        assert banned not in joined, banned
