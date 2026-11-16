"""Resolve local repository directories from the project configuration."""

from pathlib import Path


def resolve_config_path(config_path: Path | None, value) -> Path:
    base = config_path.parent if config_path else Path.cwd()
    return (base / Path(str(value or "").strip()).expanduser()).resolve()


def configured_repository_sources(config: dict, config_path: Path | None, *,
                                  indexed_root=None) -> list[tuple[str, Path]]:
    repositories = config.get("repositories")
    if not isinstance(repositories, list):
        return []
    root = Path(str(config.get("repositoryRoot") or ".data/repositories"))
    sources: dict[str, Path] = {}
    for item in repositories:
        if not isinstance(item, dict):
            continue
        repository_id = str(item.get("id") or "").strip()
        if not repository_id or repository_id in sources:
            continue
        local = item.get("localPath")
        source = resolve_config_path(config_path, local or root / repository_id)
        if indexed_root is not None and (not local or not source.exists()):
            indexed = indexed_root(repository_id)
            if indexed is not None and (not local or indexed.exists()):
                source = indexed
        sources[repository_id] = source
    return list(sources.items())
