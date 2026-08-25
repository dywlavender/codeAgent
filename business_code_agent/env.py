"""Small, dependency-free loader for the project's optional ``.env`` file.

The application still accepts ordinary process environment variables. For local
startup, values from ``.env`` intentionally win over same-named process values;
callers embedding the loader can opt out with ``override=False``.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path


_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvFileError(ValueError):
    """Raised when an existing ``.env`` file contains an invalid entry."""


def load_env_file(path: str | Path | None = None, *, override: bool = True) -> list[str]:
    """Load simple ``KEY=value`` entries from *path* into ``os.environ``.

    The file is optional. Blank lines, comments, an optional ``export`` prefix,
    and single/double quoted values are supported. Existing process variables
    are replaced by file values by default, making the project ``.env`` the
    explicit source of truth for local startup. Pass ``override=False`` when
    embedding the loader in a host application that should keep process values.
    The returned list contains only keys that were actually written to the
    process environment.
    """

    env_path = Path(path).expanduser() if path is not None else Path.cwd() / ".env"
    if not env_path.is_file():
        return []

    loaded: list[str] = []
    for line_number, raw_line in enumerate(env_path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not _KEY.fullmatch(key):
            raise EnvFileError(f"invalid entry in {env_path} at line {line_number}")
        value = _parse_value(raw_value.strip(), env_path, line_number)
        if override or key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _parse_value(raw_value: str, path: Path, line_number: int) -> str:
    if not raw_value:
        return ""
    if raw_value[0] in {"'", '"'}:
        quote = raw_value[0]
        if len(raw_value) < 2 or raw_value[-1] != quote:
            raise EnvFileError(f"unterminated quoted value in {path} at line {line_number}")
        if quote == "'":
            return raw_value[1:-1]
        try:
            value = ast.literal_eval(raw_value)
        except (SyntaxError, ValueError) as exc:
            raise EnvFileError(f"invalid quoted value in {path} at line {line_number}") from exc
        if not isinstance(value, str):
            raise EnvFileError(f"invalid quoted value in {path} at line {line_number}")
        return value
    return raw_value
