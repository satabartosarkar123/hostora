from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from hostora.config import get_settings
from hostora.engine.constraints import RuleSet
from hostora.engine.grounding import Semantics, semantics_from_data
from hostora.schemas.common import RuleId

VALID_KINDS = {"quick", "food", "ecommerce"}


@dataclass(frozen=True, slots=True)
class Channel:
    id: str
    label: str
    kind: Literal["quick", "food", "ecommerce"]
    url: str


@dataclass(frozen=True, slots=True)
class DataBundle:
    version: str
    rules: dict[str, RuleSet]
    confirm: dict[str, dict[str, str]]
    semantics: Semantics
    traditions: dict[str, Any]  # Any: free-form YAML shown in the wizard
    channels: dict[str, Channel]
    concept_map: dict[str, Any]
    sources: dict[str, Any]


class DataError(ValueError):
    """Raised when data validation fails."""


def _compute_version(files: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(files):
        h.update(p.read_bytes())
    return h.hexdigest()


def load_data(data_dir: Path) -> DataBundle:
    rules_path = data_dir / "rules" / "rules.yaml"
    traditions_path = data_dir / "templates" / "traditions.yaml"
    channels_path = data_dir / "commerce" / "channels.yaml"
    concept_map_path = data_dir / "knowledge" / "concept_map.yaml"
    sources_path = data_dir / "knowledge" / "sources.yaml"

    all_paths = [rules_path, traditions_path, channels_path, concept_map_path, sources_path]
    for p in all_paths:
        if not p.exists():
            raise DataError(f"Missing data file: {p}")

    version = _compute_version(all_paths)

    # 1. Rules with confirmation
    rules_raw = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    rules_dict = rules_raw.get("rules") or {}
    expected_rule_ids = {r.value for r in RuleId}
    rules: dict[str, RuleSet] = {}
    confirm: dict[str, dict[str, str]] = {}
    for rule_id, rdef in rules_dict.items():
        if rule_id not in expected_rule_ids:
            raise DataError(f"{rules_path.name}: unknown rule id '{rule_id}'")
        if rule_id in rules:
            raise DataError(f"{rules_path.name}: duplicate rule id '{rule_id}'")
        rules[rule_id] = RuleSet(
            id=rule_id,
            label=str(rdef.get("label") or rule_id),
            forbid=tuple(str(x) for x in (rdef.get("forbid") or [])),
        )
        if "confirm" in rdef and isinstance(rdef["confirm"], dict):
            confirm[rule_id] = {str(k): str(v) for k, v in rdef["confirm"].items()}

    missing_rules = expected_rule_ids - set(rules)
    if missing_rules:
        raise DataError(f"{rules_path.name}: missing RuleId definitions: {sorted(missing_rules)}")

    semantics_raw = rules_raw.get("semantics")
    if not isinstance(semantics_raw, dict):
        raise DataError(f"{rules_path.name}: missing or invalid semantics block")
    try:
        semantics = semantics_from_data(semantics_raw)
    except ValueError as e:
        raise DataError(f"{rules_path.name}: {e}") from e

    # 2. Traditions
    traditions_raw = yaml.safe_load(traditions_path.read_text(encoding="utf-8")) or {}

    # 3. Commerce channels
    channels_raw = yaml.safe_load(channels_path.read_text(encoding="utf-8")) or {}
    channels_dict = channels_raw.get("channels") or {}
    channels: dict[str, Channel] = {}
    for ch_id, ch_def in channels_dict.items():
        if ch_id in channels:
            raise DataError(f"{channels_path.name}: duplicate channel id '{ch_id}'")
        kind = str(ch_def.get("kind") or "")
        if kind not in VALID_KINDS:
            raise DataError(f"{channels_path.name} ({ch_id}): invalid kind '{kind}'")
        url = str(ch_def.get("url") or ch_def.get("search") or "")
        if not url.startswith("https://") or "{q}" not in url:
            msg = (
                f"{channels_path.name} ({ch_id}): "
                "channel URL must start with https:// and include '{q}'"
            )
            raise DataError(msg)
        channels[ch_id] = Channel(
            id=ch_id,
            label=str(ch_def.get("label") or ch_id),
            kind=cast(Literal["quick", "food", "ecommerce"], kind),
            url=url,
        )

    # 4. Concept Map
    concept_map_raw = yaml.safe_load(concept_map_path.read_text(encoding="utf-8")) or {}
    if not isinstance(concept_map_raw.get("open_food_facts_parents"), dict):
        raise DataError(f"{concept_map_path.name}: missing open_food_facts_parents mapping")
    if not isinstance(concept_map_raw.get("open_food_facts_properties"), dict):
        raise DataError(f"{concept_map_path.name}: missing open_food_facts_properties mapping")

    # 5. Sources
    sources_raw = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
    if "open_food_facts" not in sources_raw:
        raise DataError(f"{sources_path.name}: missing open_food_facts config")
    if "wikidata" not in sources_raw:
        raise DataError(f"{sources_path.name}: missing wikidata config")
    if "policy" not in sources_raw:
        raise DataError(f"{sources_path.name}: missing policy config")

    return DataBundle(
        version=version,
        rules=rules,
        confirm=confirm,
        semantics=semantics,
        traditions=traditions_raw,
        channels=channels,
        concept_map=concept_map_raw,
        sources=sources_raw,
    )


@lru_cache
def get_data(data_dir: Path | None = None) -> DataBundle:
    if data_dir is not None:
        return load_data(data_dir)
    try:
        return load_data(get_settings().data_dir)
    except Exception:
        return load_data(Path("data"))
