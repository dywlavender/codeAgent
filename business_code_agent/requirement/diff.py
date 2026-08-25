from __future__ import annotations

from difflib import SequenceMatcher


def diff_rules(previous: list[str], current: list[str]) -> dict:
    removed = list(previous)
    added = list(current)
    changed = []
    pairs = []
    for old in previous:
        for new in current:
            ratio = SequenceMatcher(None, old, new).ratio()
            if ratio >= 0.45 and old != new:
                pairs.append((ratio, old, new))
    for _, old, new in sorted(pairs, reverse=True):
        if old in removed and new in added:
            changed.append({"old": old, "new": new})
            removed.remove(old)
            added.remove(new)
    return {"addedRules": added, "removedRules": removed, "changedRules": changed}
