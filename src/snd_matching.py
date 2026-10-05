"""Conservative matching of saved requirements to proof filenames."""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import PurePath
import re
import unicodedata

from rapidfuzz import fuzz

from src.preparation import normalize_partner_name
from src.snd_requirements import requirement_record

AUTO_THRESHOLD = 0.88
REVIEW_THRESHOLD = 0.68
AMBIGUITY_MARGIN = 0.08
_NOISE = re.compile(r"\b(?:copy|duplicate|final|proof|evidence|revised)\b", re.I)
_GENERIC_FILE = re.compile(r"^(?:img|image|screenshot|whatsapp|scan|photo|dsc)\b", re.I)
_UNIT = {"stories": "story", "posts": "post", "pieces": "piece", "pcs": "pc"}


def normalize_filename(value: str, *, keep_sequence: bool = False) -> str:
    name = PurePath(str(value)).stem
    text = unicodedata.normalize("NFKD", name.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = _NOISE.sub(" ", text)
    text = re.sub(r"\b\d+\s*(?:x|pax|pcs?|pieces?|posts?|stories?|units?)\b", " ", text)
    tokens = re.sub(r"[^a-z0-9]+", " ", text).split()
    tokens = [_UNIT.get(token, token) for token in tokens]
    if not keep_sequence:
        tokens = [token for token in tokens if not token.isdigit()]
    return " ".join(tokens)


def filename_score(requirement: str, filename: str) -> tuple[float, str]:
    left = normalize_filename(requirement)
    right = normalize_filename(filename)
    if not left or not right or _GENERIC_FILE.match(right):
        return 0.0, "none"
    if left == right:
        return 1.0, "normalized_exact"
    if left.replace(" ", "") == right.replace(" ", ""):
        return 0.98, "squashed_exact"
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if left_tokens == right_tokens:
        return 0.97, "token_exact"
    score = max(
        fuzz.token_sort_ratio(left, right),
        fuzz.ratio(left.replace(" ", ""), right.replace(" ", "")),
    ) / 100
    if left_tokens <= right_tokens or right_tokens <= left_tokens:
        score = min(score, 0.82)
    return round(score, 4), "fuzzy"


def partner_folder_score(partner_name: str, folder_name: str) -> float:
    left = normalize_partner_name(partner_name)
    right = normalize_partner_name(folder_name)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left.replace(" ", "") == right.replace(" ", ""):
        return 0.99
    left_tokens, right_tokens = set(left.split()), set(right.split())
    smaller = min((left_tokens, right_tokens), key=len)
    if smaller <= max((left_tokens, right_tokens), key=len) and len("".join(smaller)) >= 5:
        return 0.92
    return max(fuzz.ratio(left, right), fuzz.token_sort_ratio(left, right)) / 100


def select_partner_folder(partner_name: str, folders: list[dict]) -> tuple[dict | None, str]:
    for predicate in (
        lambda name: name.strip() == partner_name.strip(),
        lambda name: normalize_partner_name(name) == normalize_partner_name(partner_name),
    ):
        matches = [item for item in folders if predicate(item["name"])]
        if len(matches) == 1:
            return matches[0], "matched"
        if len(matches) > 1:
            return None, "needs_review"
    scores = sorted(
        ((partner_folder_score(partner_name, item["name"]), item) for item in folders),
        key=lambda pair: (-pair[0], pair[1]["name"].casefold()),
    )
    if not scores or scores[0][0] < 0.72:
        return None, "unmatched"
    if scores[0][0] < 0.90 or (len(scores) > 1 and scores[0][0] - scores[1][0] < AMBIGUITY_MARGIN):
        return None, "needs_review"
    return scores[0][1], "matched"


def requirement_signature(requirements: list[dict], function: str) -> str:
    meaningful = sorted(
        (str(item["id"]), item["kind"], item["name"], int(item["quantity"]))
        for item in requirements
    )
    return sha256(json.dumps([function, meaningful]).encode()).hexdigest()


def _explicit_quantity(filename: str) -> int | None:
    text = PurePath(filename).stem.casefold()
    for pattern in (r"\b(\d+)\s*(?:pax|pcs?|pieces?|posts?|stories?|units?)\b",
                    r"\b(?:qty|x)\s*(\d+)\b", r"\b(\d+)\s*x\b"):
        match = re.search(pattern, text)
        if match and 0 < int(match.group(1)) <= 10000:
            return int(match.group(1))
    return None


def match_evidence(requirements: list[dict], evidence: list[dict]) -> dict:
    """Assign each file to at most one requirement of its own Supply/Demand kind.

    Duplicate-looking files contribute once. Uncertain or generic filenames
    remain visible but never increase fulfillment. Percentages count units;
    every configured quantity stays in the denominator, including review items.
    """
    assigned: dict[str, list[tuple[dict, float, str]]] = defaultdict(list)
    possible: dict[str, list[tuple[dict, float]]] = defaultdict(list)
    used_ids: set[str] = set()
    for proof in evidence:
        options = sorted(
            ((filename_score(item["name"], proof["name"]), item)
             for item in requirements if item["kind"] == proof["kind"]),
            key=lambda pair: -pair[0][0],
        )
        if not options:
            continue
        (best, method), target = options[0]
        second = options[1][0][0] if len(options) > 1 else 0.0
        if best >= AUTO_THRESHOLD and best - second >= AMBIGUITY_MARGIN:
            assigned[target["id"]].append((proof, best, method))
            used_ids.add(proof["id"])
        elif best >= REVIEW_THRESHOLD:
            possible[target["id"]].append((proof, best))

    rows = []
    for item in requirements:
        seen: set[str] = set()
        matched = []
        review = []
        fulfilled = 0
        for proof, score, method in assigned[item["id"]]:
            fingerprint = normalize_filename(proof["name"], keep_sequence=True).replace(" ", "")
            if fingerprint in seen:
                review.append({"name": proof["name"], "confidence": score, "reason": "possible_duplicate"})
                continue
            seen.add(fingerprint)
            credit = _explicit_quantity(proof["name"]) or 1
            fulfilled += credit
            matched.append({"name": proof["name"], "confidence": score, "method": method,
                            "credited_quantity": credit})
        review += [{"name": proof["name"], "confidence": score, "reason": "possible_match"}
                   for proof, score in possible[item["id"]]]
        required = int(item["quantity"])
        credited = min(fulfilled, required)
        if credited >= required:
            status = "COMPLETED"
        elif review:
            status = "NEEDS_REVIEW"
        elif credited:
            status = "PARTIAL"
        else:
            status = "MISSING"
        rows.append({**requirement_record(item), "fulfilled_quantity": credited, "status": status,
                     "matched_evidence": matched, "possible_evidence": review})
    denominator = sum(int(item["quantity"]) for item in rows)
    numerator = sum(item["fulfilled_quantity"] for item in rows)
    def percent(kind: str | None = None) -> float:
        subset = [item for item in rows if kind is None or item["kind"] == kind]
        total = sum(int(item["quantity"]) for item in subset)
        return round(100 * sum(item["fulfilled_quantity"] for item in subset) / total, 2) if total else 0.0
    statuses = [item["status"] for item in rows]
    overall_status = (
        "NEEDS_REVIEW" if "NEEDS_REVIEW" in statuses else
        "COMPLETED" if statuses and all(status == "COMPLETED" for status in statuses) else
        "PARTIAL" if numerator else "MISSING"
    )
    return {"requirements": rows,
            "unmatched_evidence": [item for item in evidence if item["id"] not in used_ids],
            "supply_percent": percent("supply"), "demand_percent": percent("demand"),
            "overall_percent": percent(), "status": overall_status}
