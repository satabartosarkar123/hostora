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
VALID_CONNECTS = {"swiggy_mcp_instamart", "swiggy_mcp_food", "zomato_mcp"}


@dataclass(frozen=True, slots=True)
class Channel:
    id: str
    label: str
    kind: Literal["quick", "food", "ecommerce"]
    url: str
    eta_minutes: tuple[float, float] = (10.0, 30.0)
    connect: str | None = None


@dataclass(frozen=True, slots=True)
class DataBundle:
    version: str
    rules: dict[str, RuleSet]
    confirm: dict[str, dict[str, str]]
    semantics: Semantics
    traditions: dict[str, Any]  # Fallback patterns for compatibility; functions are learned
    channels: dict[str, Channel]
    concept_map: dict[str, Any]
    sources: dict[str, Any]
    tuning: dict[str, Any]


class DataError(ValueError):
    """Raised when data validation fails."""


def _compute_version(files: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(files):
        h.update(p.read_bytes())
    return h.hexdigest()


DEFAULT_TRADITIONS: dict[str, Any] = {
    "traditions": {
        "interfaith": {"label": "Interfaith", "functions": ["Ceremony", "Reception"]},
    },
    "invite_text": {
        "en": "{host} invites you to {event}. Please reply here: {link} (code {code})",
        "hi": "{host} आपको {event} में आमंत्रित करते हैं। कृपया यहाँ जवाब दें: {link} (कोड {code})",
    },
}


def load_data(data_dir: Path) -> DataBundle:
    rules_path = data_dir / "rules" / "rules.yaml"
    channels_path = data_dir / "commerce" / "channels.yaml"
    concept_map_path = data_dir / "knowledge" / "concept_map.yaml"
    sources_path = data_dir / "knowledge" / "sources.yaml"
    tuning_path = data_dir / "tuning.yaml"

    all_paths = [rules_path, channels_path, concept_map_path, sources_path, tuning_path]
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

    # 2. Commerce channels
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
        eta_raw = ch_def.get("eta_minutes")
        if (
            not isinstance(eta_raw, (list, tuple))
            or len(eta_raw) != 2
            or not isinstance(eta_raw[0], (int, float))
            or not isinstance(eta_raw[1], (int, float))
            or isinstance(eta_raw[0], bool)
            or isinstance(eta_raw[1], bool)
            or float(eta_raw[0]) < 0
            or float(eta_raw[1]) < float(eta_raw[0])
        ):
            raise DataError(
                f"{channels_path.name} ({ch_id}): "
                "eta_minutes must be [min, max] with 0 <= min <= max"
            )
        connect_raw = ch_def.get("connect")
        if connect_raw is not None and (
            not isinstance(connect_raw, str) or connect_raw not in VALID_CONNECTS
        ):
            raise DataError(f"{channels_path.name} ({ch_id}): invalid connect '{connect_raw}'")
        channels[ch_id] = Channel(
            id=ch_id,
            label=str(ch_def.get("label") or ch_id),
            kind=cast(Literal["quick", "food", "ecommerce"], kind),
            url=url,
            eta_minutes=(float(eta_raw[0]), float(eta_raw[1])),
            connect=connect_raw,
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

    # 6. Tuning
    tuning_raw = yaml.safe_load(tuning_path.read_text(encoding="utf-8")) or {}
    if "tuning" not in tuning_raw or not isinstance(tuning_raw["tuning"], dict):
        raise DataError(f"{tuning_path.name}: missing tuning section")

    return DataBundle(
        version=version,
        rules=rules,
        confirm=confirm,
        semantics=semantics,
        traditions=DEFAULT_TRADITIONS,
        channels=channels,
        concept_map=concept_map_raw,
        sources=sources_raw,
        tuning=tuning_raw["tuning"],
    )


@lru_cache
def get_data(data_dir: Path | None = None) -> DataBundle:
    if data_dir is not None:
        return load_data(data_dir)
    try:
        return load_data(get_settings().data_dir)
    except Exception:
        return load_data(Path("data"))


def get_tuning_value(name: str, fallback: float = 0.0) -> float:
    """Retrieve a parameter from data/tuning.yaml."""
    try:
        data = get_data()
        val = data.tuning.get(name, {}).get("value")
        if val is not None:
            return float(val)
    except (DataError, KeyError, ValueError, TypeError):
        return fallback
    return fallback
