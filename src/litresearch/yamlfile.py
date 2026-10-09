"""Change YAML settings files without losing the user's comments.

Used for ``config.yaml`` (web server, password) and for each profile's
``settings.yaml``. ruamel.yaml's round-trip mode keeps comments and key
order; the previous file is kept as ``<name>.bak`` and the new one is written
atomically, so a crash never leaves a half-written file.
"""

import io
import shutil
from pathlib import Path

from ruamel.yaml import YAML

from litresearch.backup import atomic_write_text


def deep_merge(base: dict, updates: dict) -> dict:
    """``base`` with ``updates`` applied; nested dicts are merged key by key."""
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def update_yaml(path: Path, updates: dict) -> None:
    """Write ``updates`` into the YAML file at ``path`` (created if missing).

    Nested sections are updated key by key. A ``topics`` list is matched by
    topic name, so fields the caller does not mention (``mesh_terms``,
    ``seed_dois``) survive.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    document = yaml.load(path.read_text(encoding="utf-8")) if path.exists() else None
    document = document if document is not None else {}
    _apply(document, updates)
    buffer = io.StringIO()
    yaml.dump(document, buffer)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    atomic_write_text(path, buffer.getvalue())


def _apply(node, updates: dict) -> None:
    for key, value in updates.items():
        if key == "topics" and isinstance(node.get(key), list):
            node[key] = _merge_topics(node[key], value)
        elif isinstance(value, dict):
            if not isinstance(node.get(key), dict):
                node[key] = {}
            _apply(node[key], value)
        else:
            node[key] = value


def _merge_topics(existing: list, topics: list[dict]) -> list:
    by_name = {t.get("name"): t for t in existing if isinstance(t, dict)}
    merged = []
    for topic in topics:
        node = by_name.get(topic.get("name"))
        if node is None:
            merged.append(dict(topic))
            continue
        for key, value in topic.items():
            node[key] = value
        merged.append(node)
    return merged
