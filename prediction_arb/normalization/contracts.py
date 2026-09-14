"""Contract normalization helpers (§16, §96).

Adapters produce NormalizedContract objects; this module fills in the
cross-venue `normalized_event` / `normalized_outcome` keys and generates
cross-venue candidate groups by BLOCKING (§96): contracts land in the same
group when they share at least one cheap, deterministic signal — a
settlement window, a category, a threshold + operator + units triple, a
numeric token from the title, or a content word of the title (plural-folded,
stopwords removed).

Blocking is candidate generation only, and it is deliberately
recall-oriented: a false shared group is harmless (the deterministic
matcher rejects — §18, REJECT is final), but a missed grouping hides a
real match forever. Grouping NEVER decides equivalence and can never
promote a pair past the deterministic filter (Principle B, §89): it only
decides which cross-venue pairs the deterministic matcher gets to see.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from ..core.clock import ensure_utc
from ..core.types import NormalizedContract

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s\.\-%]")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[a-z][a-z']*")

# Function words and question scaffolding only. Domain words (prices,
# months, "close", "settle", institution names, ...) are NEVER stopwords
# here: stripping one would delete exactly the blocking signal that
# differently-phrased titles still share.
_STOPWORDS = frozenset("""
a an the and or but nor if then else when whenever what which who whom whose
where why how will would shall should can could may might must do does did
done doing is are was were be been being am of in on at to by for with from
as that this these those there here it its his her their they them he she we
you i me my our your not no so than too very just about above below over
under between into onto upon after before during since until while up down
out off again further once all any both each few more most other some such
only own same per vs versus
""".split())

# Settlement blocking uses 24-hour UTC buckets, and each contract is placed
# in its own bucket plus both neighbours. Any two contracts whose settlement
# times are within 24 hours of each other therefore share at least one
# bucket — comfortably wider than the ±6h the deterministic matcher
# enforces (§18), which is what actually bounds the pairing.
_SETTLEMENT_BUCKET_SECONDS = 86400

# '100k' / '2.5m' scale factors, for numeric tokens with a trailing
# magnitude letter ('Will Bitcoin reach $100k' vs '… $100,000').
_MAGNITUDE_SUFFIX = {"k": Decimal(1000), "m": Decimal(1_000_000)}


def canonical_text(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower().strip()
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t)
    return t


def canonical_number(raw: str) -> str | None:
    """Canonicalize one numeric string: '100,000.00' → '100000',
    '4.50' → '4.5', '1E+4' → '10000'.

    Returns None for anything that is not a plain finite number — such
    input is dropped rather than guessed at: a wrong number key only adds
    harmless candidates, but a silently inconsistent one is how real
    matches get missed.
    """
    s = (str(raw).strip().replace(",", "")
         .replace("$", "").replace("%", ""))
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    out = format(d, "f")  # never exponent notation ('1E+4' → '10000')
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    neg = out.startswith("-")
    out = out.lstrip("-").lstrip("+")
    if "." in out:
        # strip leading zeros of the integer part only ('0.500' → '0.5',
        # never '.5' — that would split one number into two keys)
        int_part, frac = out.split(".", 1)
        out = f"{int_part.lstrip('0') or '0'}.{frac}"
    else:
        out = out.lstrip("0") or "0"
    if neg and out != "0":
        return f"-{out}"
    return out


def numeric_tokens(text: str) -> set[str]:
    """Distinct canonical numbers appearing in a title.

    '4.25%-4.50%' → {'4.25', '4.5'}; '$10,000' → {'10000'};
    '$100k' → {'100000'} (a trailing k/m scales the number instead of
    being dropped, so the abbreviated and spelled-out forms block together).
    """
    out: set[str] = set()
    for m in _NUM.finditer(text or ""):
        canonical = canonical_number(m.group(0))
        if canonical is None:
            continue
        rest = text[m.end():].lstrip()
        if rest[:1].lower() in _MAGNITUDE_SUFFIX:
            scaled = canonical_number(
                Decimal(canonical) * _MAGNITUDE_SUFFIX[rest[:1].lower()])
            if scaled is not None:
                out.add(scaled)
        out.add(canonical)
    return out


def _fold_plural(tok: str) -> str:
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"          # commodities → commodity
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        return tok[:-1]                # rates → rate, raises → raise
    return tok


def content_tokens(text: str) -> set[str]:
    """Distinct content words of a canonical title: stopwords removed,
    simple plural folding so 'raises rates' and 'raise rate' share tokens."""
    return {
        _fold_plural(w)
        for w in _WORD.findall(canonical_text(text))
        if w not in _STOPWORDS
    }


def normalized_event_key(contract: NormalizedContract) -> str:
    """Human-readable record key: canonical event text + settlement date.

    This is a RECORD key (it is stored on the contract and shown in
    reports). It is deliberately NOT the grouping mechanism any more —
    see `blocking_keys` / `group_by_event` (§96): exact-text grouping was
    defect D6, because two venues never phrase the same event identically.
    """
    date = (contract.settlement_time.date().isoformat()
            if contract.settlement_time else "nodate")
    return f"{canonical_text(contract.event_description)}|{date}"


def with_normalized_keys(contract: NormalizedContract) -> NormalizedContract:
    return dataclasses.replace(
        contract,
        normalized_event=normalized_event_key(contract),
        normalized_outcome=canonical_text(contract.outcome),
    )


def blocking_keys(contract: NormalizedContract) -> set[str]:
    """Cheap deterministic blocking keys for one contract (§96).

    Two contracts become match candidates when they share ≥1 key. Families:

    * ``settle:<bucket>`` — 24h UTC settlement buckets, own bucket ±1
      (covers venue-to-venue timestamp disagreement across day boundaries;
      contracts with no settlement time get no settle key — the
      deterministic matcher rejects those pairs anyway, §18);
    * ``cat:<category>`` — normalized category / product code;
    * ``thr:<op>:<number>:<units>`` — exact threshold + operator + units,
      canonicalized so '10000.00' and '1E+4' block together;
    * ``num:<n>`` — every canonical numeric token of the title;
    * ``tok:<word>`` — every plural-folded, stopword-filtered content word.
    """
    keys: set[str] = set()

    if contract.settlement_time is not None:
        bucket = int(ensure_utc(contract.settlement_time).timestamp()) \
            // _SETTLEMENT_BUCKET_SECONDS
        keys.update(f"settle:{b}" for b in
                    (bucket - 1, bucket, bucket + 1))

    if contract.category:
        keys.add(f"cat:{canonical_text(contract.category)}")

    if contract.threshold is not None:
        num = canonical_number(contract.threshold)
        if num is not None:
            keys.add("thr:{}:{}:{}".format(
                (contract.threshold_operator or "").strip().lower(),
                num,
                (contract.units or "").strip().lower()))

    for n in numeric_tokens(contract.event_description):
        keys.add(f"num:{n}")
    for w in content_tokens(contract.event_description):
        keys.add(f"tok:{w}")

    return keys


def group_by_event(contracts: list[NormalizedContract]
                   ) -> dict[str, list[NormalizedContract]]:
    """Group contracts into candidate components over shared blocking keys
    (§96).

    Contracts are connected when they share at least one blocking key;
    groups are the connected components of that graph (union-find). The
    component id is derived from the sorted member identities, so it is
    stable across runs and input orderings.

    Grouping is intentionally coarse and transitive: merging contracts that
    merely share a common year puts a few extra pairs in front of the
    deterministic matcher, which rejects them for free. The alternative —
    a tighter key — is how real cross-venue matches get lost (that was D6).
    """
    n = len(contracts)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    owner: dict[str, int] = {}
    for i, contract in enumerate(contracts):
        for key in blocking_keys(contract):
            first = owner.setdefault(key, i)
            if first != i:
                union(i, first)

    members: dict[int, list[int]] = {}
    for i in range(n):
        members.setdefault(find(i), []).append(i)

    groups: dict[str, list[NormalizedContract]] = {}
    for indices in members.values():
        group = [contracts[i] for i in indices]
        ident = "|".join(sorted(f"{c.venue}:{c.contract_id}" for c in group))
        digest = hashlib.sha256(ident.encode()).hexdigest()[:16]
        groups[f"blk:{digest}"] = group
    return groups
