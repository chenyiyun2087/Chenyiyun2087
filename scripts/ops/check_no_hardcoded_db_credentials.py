#!/usr/bin/env python3
"""Reject literal database credentials embedded in Python source."""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SENSITIVE_ENV_KEYS = {"CHENYIYUN_DB_PASSWORD", "CHENYIYUN_DB_URL"}
URL_WITH_PASSWORD = re.compile(
    r"(?:mysql(?:\+pymysql)?|postgres(?:ql)?)(?:://)[^\s:/@{}]+:[^\s/@{}]+@",
    re.IGNORECASE,
)


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def scan_source(source: str, filename: str = "<source>") -> list[str]:
    findings: list[str] = [
        f"{filename}:{source.count(chr(10), 0, match.start()) + 1}: "
        "literal database URL contains a password"
        for match in URL_WITH_PASSWORD.finditer(source)
    ]
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        # Secret scanning must still work on a temporarily malformed file.
        fallback = re.compile(
            r'''(?:setdefault|getenv)\(\s*["'](CHENYIYUN_DB_(?:PASSWORD|URL))["']\s*,\s*["']([^"'$\s]+)["']'''
        )
        findings.extend([
            f"{filename}: literal fallback for {match.group(1)}"
            for match in fallback.finditer(source)
        ])
        return findings
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if _is_os_environ(owner) and node.func.attr == "setdefault" and len(node.args) >= 2:
                key = _literal_string(node.args[0])
                value = _literal_string(node.args[1])
                if key in SENSITIVE_ENV_KEYS and value:
                    findings.append(f"{filename}:{node.lineno}: literal default for {key}")
            if isinstance(owner, ast.Name) and owner.id == "os" and node.func.attr == "getenv" and len(node.args) >= 2:
                key = _literal_string(node.args[0])
                value = _literal_string(node.args[1])
                if key in SENSITIVE_ENV_KEYS and value:
                    findings.append(f"{filename}:{node.lineno}: literal getenv fallback for {key}")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value_node = node.value
            value = _literal_string(value_node)
            if not value:
                continue
            for target in targets:
                if isinstance(target, ast.Subscript) and _is_os_environ(target.value):
                    key = _literal_string(target.slice)
                    if key in SENSITIVE_ENV_KEYS:
                        findings.append(f"{filename}:{node.lineno}: literal assignment for {key}")
    return findings


def scan_json_source(source: str, filename: str = "<json>") -> list[str]:
    """Reject non-placeholder password values and credentialed URLs in JSON."""
    findings = [
        f"{filename}:{source.count(chr(10), 0, match.start()) + 1}: "
        "literal database URL contains a password"
        for match in URL_WITH_PASSWORD.finditer(source)
    ]
    try:
        payload = json.loads(source)
    except json.JSONDecodeError:
        return findings

    def visit(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                if (
                    "password" in str(key).lower()
                    and isinstance(item, str)
                    and item.strip()
                    and not item.strip().startswith("${")
                ):
                    findings.append(f"{filename}:{child}: literal password value")
                visit(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(payload, "")
    return findings


def project_python_files() -> list[Path]:
    """Return committed and not-yet-committed Python files visible to Git."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=PROJECT_ROOT,
        capture_output=True, text=True, check=True,
    )
    return [
        PROJECT_ROOT / value
        for value in result.stdout.splitlines()
        if value and not value.startswith("test/")
    ]


def project_json_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.json"],
        cwd=PROJECT_ROOT,
        capture_output=True, text=True, check=True,
    )
    return [
        PROJECT_ROOT / value
        for value in result.stdout.splitlines()
        if value and not value.startswith("test/")
    ]


def main() -> int:
    findings: list[str] = []
    for path in project_python_files():
        findings.extend(scan_source(path.read_text(encoding="utf-8"), str(path.relative_to(PROJECT_ROOT))))
    for path in project_json_files():
        findings.extend(scan_json_source(path.read_text(encoding="utf-8"), str(path.relative_to(PROJECT_ROOT))))
    if findings:
        print("Hard-coded database credentials detected:")
        print("\n".join(f"- {value}" for value in findings))
        return 1
    print("OK: no literal database credentials in Git-visible Python/JSON files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
