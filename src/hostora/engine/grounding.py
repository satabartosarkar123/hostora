"""Runtime grounding: fuse live evidence about an AI-proposed item into a safety decision.

Nothing in this module is a list of foods or dishes. The AI may propose anything. At request
time the application gathers evidence from independent sources (see AGENTS.md section 23):

* ``proposer`` / ``verifier``: language models. The verifier answers without seeing the
  proposer's answer and is sampled several times; ``agreement`` is the share of samples that
  agree (Chain-of-Verification, SelfCheckGPT-style consistency);
* ``knowledge_base``: structured open knowledge fetched live and cached with a TTL, e.g. the
  Open Food Facts ingredient taxonomy and Wikidata ("made from material" / "has part");
* ``web``: retrieved pages (search-grounded extraction); only counted with a citation URL;
* ``product_label``: a real product's declared ingredients and labels (store or Open Food Facts);
* ``host``: the host's own confirmation.

The ``Vocabulary`` that turns ingredient text into rule tags is also built at runtime, from
concept names and synonyms (any language) pulled from those knowledge sources.

Decision (deterministic, fail-closed):

* ``removed`` if any source, however weak, links the item to a forbidden tag;
* ``allowed`` only if enough independent source families fully explain the item's composition
  (a quorum that must include a non-model source) or a product label / the host vouches for it,
  with no contradictions and no unresolved words;
* otherwise ``needs_confirmation``: the host decides, with every reason and citation shown.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from hostora.engine.constraints import RuleSet, expand_tags

Status = Literal["allowed", "needs_confirmation", "removed"]
Source = Literal["proposer", "verifier", "knowledge_base", "web", "product_label", "host"]

CONSUMABLE_CATEGORIES: frozenset[str] = frozenset({"food", "beverage"})
SUBSTANCE_TAGS: frozenset[str] = frozenset(
    {"meat", "fish", "egg", "alcohol", "root_veg", "onion", "garlic", "honey", "dairy"}
)
NON_VEG_TAGS: frozenset[str] = frozenset({"meat", "fish", "egg"})
_DIET_CONFLICTS: Mapping[str, frozenset[str]] = {
    "veg": NON_VEG_TAGS,
    "jain": NON_VEG_TAGS | {"root_veg", "onion", "garlic", "alcohol", "honey"},
    "vegan": NON_VEG_TAGS | {"dairy", "allergen:dairy", "honey"},
    "egg": frozenset({"meat", "fish"}),
}
_DIET_IMPLIES: Mapping[str, frozenset[str]] = {
    "egg": frozenset({"egg"}),
    "nonveg": frozenset({"meat", "fish"}),  # unspecified non-veg: assume the worst
}
_FAMILY: Mapping[str, str] = {
    "proposer": "model",
    "verifier": "model",
    "knowledge_base": "knowledge_base",
    "web": "web",
    "product_label": "product_label",
    "host": "host",
}
_AUTHORITATIVE: frozenset[str] = frozenset({"product_label", "host"})
_NEEDS_CITATION: frozenset[str] = frozenset({"knowledge_base", "web", "product_label"})
_MIN_UNKNOWN_LEN = 3

EntryKind = Literal["term", "negation"]


# --------------------------------------------------------------------------- vocabulary


@dataclass(frozen=True, slots=True)
class _Entry:
    kind: EntryKind
    tags: frozenset[str]
    form: str


def tokenize(text: str) -> list[str]:
    """Case-folded word tokens. Letters, digits and combining marks stay together, so
    Devanagari and other Indic words keep their vowel signs."""
    out: list[str] = []
    buf: list[str] = []
    for ch in unicodedata.normalize("NFKC", text).casefold():
        if unicodedata.category(ch)[0] in "LNM":
            buf.append(ch)
        elif buf:
            out.append("".join(buf))
            buf = []
    if buf:
        out.append("".join(buf))
    return out


def _singulars(token: str) -> list[str]:
    variants = [token]
    if token.isascii():
        if token.endswith("es") and len(token) > 4:
            variants.append(token[:-2])
        if token.endswith("s") and len(token) > 3:
            variants.append(token[:-1])
    return variants


@dataclass(frozen=True, slots=True)
class ScanResult:
    tags: frozenset[str]
    conflicts: frozenset[str]  # tags both negated and asserted in the same text
    matched: tuple[str, ...]
    unknown: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """Surface forms (words or phrases, any script) mapped to rule tags.

    Built at runtime from knowledge-source concepts; an empty tag set means "known, carries no
    restricted tag" (for example rice). Words not in the vocabulary are reported as unknown,
    which blocks automatic approval.
    """

    index: Mapping[tuple[str, ...], _Entry]
    max_len: int

    @classmethod
    def build(
        cls,
        concepts: Iterable[tuple[str, Iterable[str]]],
        negations: Iterable[tuple[str, Iterable[str]]] = (),
    ) -> Vocabulary:
        """``concepts``: (surface form, tags). ``negations``: (surface form, tags it cancels),
        e.g. ("eggless", ["egg"]). Tags from duplicate forms are merged (union)."""
        index: dict[tuple[str, ...], _Entry] = {}

        def add(form: str, kind: EntryKind, tags: Iterable[str]) -> None:
            key = tuple(tokenize(form))
            if not key:
                return
            existing = index.get(key)
            if existing is not None and existing.kind != kind:
                raise ValueError(f"form {form!r} is both {existing.kind} and {kind}")
            merged = frozenset(tags) | (existing.tags if existing else frozenset())
            index[key] = _Entry(kind=kind, tags=merged, form=form)

        for form, tags in concepts:
            add(form, "term", tags)
        for form, tags in negations:
            add(form, "negation", tags)
        return cls(index=index, max_len=max((len(k) for k in index), default=1))

    def scan(self, text: str) -> ScanResult:
        tokens = tokenize(text)
        tags: set[str] = set()
        cancelled: set[str] = set()
        matched: list[str] = []
        unknown: list[str] = []
        i = 0
        while i < len(tokens):
            entry, length = self._match_at(tokens, i)
            if entry is None:
                tok = tokens[i]
                if len(tok) >= _MIN_UNKNOWN_LEN and not tok.isdigit():
                    unknown.append(tok)
                i += 1
                continue
            (tags if entry.kind == "term" else cancelled).update(entry.tags)
            matched.append(entry.form)
            i += length
        expanded = expand_tags(tags)
        return ScanResult(
            tags=frozenset(expanded - cancelled),
            conflicts=frozenset(expanded & cancelled),
            matched=tuple(matched),
            unknown=tuple(unknown),
        )

    def _match_at(self, tokens: list[str], i: int) -> tuple[_Entry | None, int]:
        for length in range(min(self.max_len, len(tokens) - i), 0, -1):
            head = tuple(tokens[i : i + length - 1])
            for last in _singulars(tokens[i + length - 1]):
                entry = self.index.get((*head, last))
                if entry is not None:
                    return entry, length
        return None, 0


# --------------------------------------------------------------------------- evidence


@dataclass(frozen=True, slots=True)
class Evidence:
    """One source's claim about one item."""

    source: Source
    tags: frozenset[str] = frozenset()
    diet: str | None = None  # veg | egg | nonveg | jain | vegan
    ingredients: tuple[str, ...] = ()
    agreement: float = 1.0  # share of sampled answers that agree (models); 1.0 otherwise
    citation: str | None = None  # URL or entity id; required for knowledge_base, web, label
    absent: frozenset[str] = frozenset()  # tags this source says the item does NOT contain


@dataclass(frozen=True, slots=True)
class Semantics:
    """What diet words mean for the decision. Loaded from ``rules.yaml`` (``semantics``) at
    runtime; the defaults below only mirror that file so the engine works standalone."""

    consumable_categories: frozenset[str]
    substance_tags: frozenset[str]
    diet_conflicts: Mapping[str, frozenset[str]]
    diet_implies: Mapping[str, frozenset[str]]


DEFAULT_SEMANTICS = Semantics(
    consumable_categories=CONSUMABLE_CATEGORIES,
    substance_tags=SUBSTANCE_TAGS,
    diet_conflicts=_DIET_CONFLICTS,
    diet_implies=_DIET_IMPLIES,
)


def semantics_from_data(data: Mapping[str, object]) -> Semantics:
    """Build ``Semantics`` from the parsed ``semantics`` block of ``rules.yaml``."""

    def tagset(value: object, name: str) -> frozenset[str]:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"semantics.{name} must be a list of strings")
        return frozenset(value)

    def tagmap(value: object, name: str) -> dict[str, frozenset[str]]:
        if not isinstance(value, Mapping):
            raise ValueError(f"semantics.{name} must be a mapping")
        return {str(k): tagset(v, f"{name}.{k}") for k, v in value.items()}

    return Semantics(
        consumable_categories=tagset(data.get("consumable_categories"), "consumable_categories"),
        substance_tags=tagset(data.get("substance_tags"), "substance_tags"),
        diet_conflicts=tagmap(data.get("diet_conflicts"), "diet_conflicts"),
        diet_implies=tagmap(data.get("diet_implies"), "diet_implies"),
    )


@dataclass(frozen=True, slots=True)
class ProposedItem:
    name: str
    category: str  # food | beverage | decor | lighting | tableware | supply


@dataclass(frozen=True, slots=True)
class GroundingPolicy:
    quorum: int = 2  # distinct source families that must fully explain the item
    min_agreement: float = 0.67  # set from calibration (engine.calibration)
    require_non_model: bool = True


@dataclass(frozen=True, slots=True)
class GroundedItem:
    name: str
    category: str
    status: Status
    tags: frozenset[str]
    reasons: tuple[str, ...]
    matched: tuple[str, ...]
    unknown: tuple[str, ...]
    supporting: tuple[str, ...] = field(default=())  # source families that explained it
    citations: tuple[str, ...] = field(default=())


def ground(
    item: ProposedItem,
    evidence: Sequence[Evidence],
    vocabulary: Vocabulary,
    rule_ids: Sequence[str],
    rules: Mapping[str, RuleSet],
    confirm: Mapping[str, Mapping[str, str]] | None = None,
    policy: GroundingPolicy | None = None,
    semantics: Semantics | None = None,
) -> GroundedItem:
    """Decide whether ``item`` may enter a plan under ``rule_ids``.

    ``confirm``: rule id -> tag -> message for tags that need the host's confirmation (e.g.
    halal-certified meat). Only ``host`` or ``product_label`` evidence carrying
    ``confirmed:<rule id>`` satisfies it.

    A tag that comes ONLY from words in the item's name (for example "egg" in "egg-free cake")
    is dropped if a quorum of valid independent sources, including a non-model one, or one
    authoritative source, says the item does not contain it, and no source's ingredients or
    tags assert it. Any source asserting a tag always wins over absence claims.
    """
    policy = policy or GroundingPolicy()
    sem = semantics or DEFAULT_SEMANTICS
    forbidden: set[str] = set()
    for rule_id in rule_ids:
        rule = rules.get(rule_id)
        if rule is None:
            raise KeyError(f"Unknown rule set: {rule_id}")
        forbidden.update(rule.forbid)

    reasons: list[str] = []
    name_scan = vocabulary.scan(item.name)
    name_tags: set[str] = set(name_scan.tags)
    tags: set[str] = set()  # tags asserted by evidence (ingredients, labels, models)
    absent_support: dict[str, set[str]] = {}
    matched: list[str] = list(name_scan.matched)
    conflicts: set[str] = set(name_scan.conflicts)
    unknown: list[str] = list(name_scan.unknown)
    confirmed: set[str] = set()
    supporting: set[str] = set()
    citations: list[str] = []
    authoritative = False

    for ev in evidence:
        family = _FAMILY[ev.source]
        claimed = {t for t in ev.tags if t.startswith("confirmed:")}
        if ev.source in _AUTHORITATIVE:
            confirmed.update(claimed)
        tags.update(ev.tags - claimed)  # negative evidence always counts (fail closed)
        tags.update(sem.diet_implies.get(ev.diet or "", frozenset()))

        ev_unknown: list[str] = []
        for ingredient in ev.ingredients:
            scan = vocabulary.scan(ingredient)
            tags.update(scan.tags)
            matched.extend(scan.matched)
            conflicts.update(scan.conflicts)
            ev_unknown.extend(scan.unknown)
        unknown.extend(ev_unknown)

        if ev.source in _NEEDS_CITATION and not ev.citation:
            reasons.append(f"{ev.source} evidence without a citation was not counted as support")
            continue
        if ev.citation:
            citations.append(ev.citation)
        if family == "model" and ev.agreement < policy.min_agreement:
            reasons.append(f"{ev.source} answers disagreed ({ev.agreement:.0%} agreement)")
            continue
        for t in ev.absent:
            absent_support.setdefault(t, set()).add(
                "authoritative" if ev.source in _AUTHORITATIVE else family
            )
        explains = bool(ev.ingredients) and not ev_unknown
        if ev.source in _AUTHORITATIVE and (explains or "confirmed:composition" in claimed):
            authoritative = True
            supporting.add(family)
        elif explains:
            supporting.add(family)

    asserted = expand_tags(tags)
    for t in sorted(name_tags - asserted):
        fams = absent_support.get(t, set())
        enough = "authoritative" in fams or (len(fams) >= policy.quorum and bool(fams - {"model"}))
        if enough:
            name_tags.discard(t)
            reasons.append(f"name mentions {t}, but independent sources confirm it is absent")
    all_tags = frozenset(expand_tags(name_tags | tags))

    category = item.category
    if category not in sem.consumable_categories and all_tags & sem.substance_tags:
        category = "food"
        reasons.append("looks like food or drink, so diet rules apply")
    consumable = category in sem.consumable_categories

    for ev in evidence:
        clash = sem.diet_conflicts.get(ev.diet or "", frozenset()) & all_tags
        if clash:
            conflicts.update(clash)
            shown = ", ".join(sorted(clash))
            reasons.append(f"{ev.source} said {ev.diet} but evidence shows {shown}")

    def result(status: Status, extra: Sequence[str] = ()) -> GroundedItem:
        return GroundedItem(
            name=item.name,
            category=category,
            status=status,
            tags=all_tags,
            reasons=(*extra, *reasons),
            matched=tuple(matched),
            unknown=tuple(dict.fromkeys(unknown)),
            supporting=tuple(sorted(supporting)),
            citations=tuple(dict.fromkeys(citations)),
        )

    if consumable and forbidden:
        hits = sorted(all_tags & forbidden)
        if hits:
            return result("removed", [f"forbidden:{t}" for t in hits])

    needs: list[str] = []
    if consumable and rule_ids and not authoritative:
        grounded_families = supporting - {"model"}
        if len(supporting) < policy.quorum or (policy.require_non_model and not grounded_families):
            needs.append(
                f"composition confirmed by {len(supporting)} independent source(s); "
                f"{policy.quorum} needed, including a non-AI source"
            )
        if name_scan.unknown and not grounded_families:
            needs.append(f"unrecognised words: {', '.join(name_scan.unknown)}")
    if conflicts:
        needs.append(f"sources disagree about: {', '.join(sorted(conflicts))}")
    if consumable and confirm:
        for rule_id in rule_ids:
            for tag, message in confirm.get(rule_id, {}).items():
                if tag in all_tags and f"confirmed:{rule_id}" not in confirmed:
                    needs.append(message)

    return result("needs_confirmation" if needs else "allowed", needs)


# --------------------------------------------------------------------------- helpers


def quote_supported(
    ingredients: Sequence[str], quotes: Sequence[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split extracted ingredients into those that literally appear (token-wise) in at least one
    retrieved quote and those that do not. Only supported ones may be used as ``web`` evidence;
    unsupported ones are dropped as possible hallucinations."""
    quote_tokens = [tokenize(q) for q in quotes]

    def appears(ingredient: str) -> bool:
        needle = tokenize(ingredient)
        if not needle:
            return False
        for hay in quote_tokens:
            for i in range(len(hay) - len(needle) + 1):
                window = hay[i : i + len(needle)]
                if all(w == n or n in _singulars(w) for w, n in zip(window, needle, strict=True)):
                    return True
        return False

    supported = tuple(i for i in ingredients if appears(i))
    unsupported = tuple(i for i in ingredients if not appears(i))
    return supported, unsupported


def agreement(samples: Sequence[frozenset[str]]) -> float:
    """Share of independent model answers whose restricted-tag set equals the most common one.
    1.0 means every sample agrees; with no samples, 0.0."""
    if not samples:
        return 0.0
    counts: dict[frozenset[str], int] = {}
    for s in samples:
        counts[s] = counts.get(s, 0) + 1
    return max(counts.values()) / len(samples)
