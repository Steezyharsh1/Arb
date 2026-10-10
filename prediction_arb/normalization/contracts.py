"""Contract normalization helpers (§16, §96).

Adapters produce NormalizedContract objects; this module fills in the
cross-venue `normalized_event` / `normalized_outcome` keys and proposes
cross-venue candidate PAIRS by BLOCKING (§96, revised 2026-10-10 — see
`candidate_pairs`).

A pair is proposed only when two cross-venue contracts (a) settle within the
deterministic matcher's own settlement tolerance and (b) share a *specific*
signal: an exact threshold + operator + units triple, a numeric title token,
or a plural-folded content word — and that signal is not so common that it
would link unrelated contracts (block-size cap).

Retrieval is candidate generation only. It never decides equivalence and can
never promote a pair past the deterministic filter (Principle B, §89): it only
decides which cross-venue pairs the deterministic matcher gets to see. A
retrieval match is NOT a match — the deterministic matcher, the LLM and the
certificate decide.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
import unicodedata
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from ..core.clock import ensure_utc
from ..core.types import NormalizedContract
from ..matching.deterministic_matcher import SETTLEMENT_TOLERANCE

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

# Retrieval constants (§96, revised 2026-10-10).
#
# Settlement is a PAIRWISE window, never a bucket: the previous design put each
# contract in 24h buckets ±1 and joined everything in a bucket with union-find,
# so a calendar of clustered expiries chained into one giant component (1,000
# contracts → 1 component; every one of the 333,296 cross-venue pairs proposed
# on the benchmark generator, docs/CANDIDATE_RETRIEVAL.md). The
# window is the deterministic matcher's own tolerance, imported so the two can
# never drift apart: a pair outside it is always rejected (§18), so proposing
# only pairs inside it cannot remove any pair the matcher could pass.
RETRIEVAL_SETTLEMENT_WINDOW = SETTLEMENT_TOLERANCE

# A key is generic at a settlement moment when more than this many contracts
# holding it settle within the window of that moment (a year, "total",
# "will" on a busy expiry). Such a key proposes no pairs at that moment. It is
# counted locally, not across the calendar, so a family-level word that spans
# many dates still identifies its event. Bounds work per contract. The value
# and the sweep that chose it are in docs/CANDIDATE_RETRIEVAL.md.
MAX_LOCAL_DENSITY = 20

# Families that may PROPOSE a pair. `cat:` is excluded: venue taxonomies
# differ (Kalshi series tickers vs. Polymarket topics), so category equality
# across venues is rare, and where it does occur it is generic. Settlement is
# replaced by the pairwise window above.
PROPOSAL_FAMILIES = ("thr", "num", "tok")


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


def blocking_keys(contract: NormalizedContract,
                  families: tuple[str, ...] = PROPOSAL_FAMILIES) -> set[str]:
    """Discriminative blocking keys for one contract (§96).

    Two cross-venue contracts can become candidates only when they share at
    least one key from ``families`` (default: ``PROPOSAL_FAMILIES``) and
    settle within ``RETRIEVAL_SETTLEMENT_WINDOW``. Families:

    * ``thr:<op>:<number>:<units>`` — exact threshold + operator + units,
      canonicalized so '10000.00' and '1E+4' block together;
    * ``num:<n>`` — every canonical numeric token of the title;
    * ``tok:<word>`` — every plural-folded, stopword-filtered content word.

    ``cat:<category>`` is produced only when requested by name (diagnostics).
    There is no settlement key: settlement is a pairwise window in
    ``candidate_pairs``, never a shared bucket.
    """
    keys: set[str] = set()

    if "cat" in families and contract.category:
        keys.add(f"cat:{canonical_text(contract.category)}")

    if "thr" in families and contract.threshold is not None:
        num = canonical_number(contract.threshold)
        if num is not None:
            keys.add("thr:{}:{}:{}".format(
                (contract.threshold_operator or "").strip().lower(),
                num,
                (contract.units or "").strip().lower()))

    if "num" in families:
        for n in numeric_tokens(contract.event_description):
            keys.add(f"num:{n}")
    if "tok" in families:
        for w in content_tokens(contract.event_description):
            keys.add(f"tok:{w}")

    return keys


def candidate_pairs(contracts: list[NormalizedContract],
                    *,
                    families: tuple[str, ...] = PROPOSAL_FAMILIES,
                    max_local_density: int = MAX_LOCAL_DENSITY,
                    window: timedelta = RETRIEVAL_SETTLEMENT_WINDOW,
                    ) -> list[tuple[int, int]]:
    """Propose cross-venue candidate pairs as sorted index pairs ``(i, j)``, ``i < j``.

    A pair ``(i, j)`` is proposed iff all of:

    1. the two contracts are on different venues (same-venue pairs are never
       matched — D15);
    2. both have a settlement time and they differ by at most ``window``;
    3. they share a blocking key from ``families`` (``blocking_keys``), and
       for that key, neither contract is in a *dense* moment: at most
       ``max_local_density`` contracts holding the key settle within
       ``window`` of it.

    Rule 3 is local, not global. A word that is common across the whole
    calendar ("shutdown", "senate") still identifies its event when the
    listings settle at different times; a word that many contracts share at
    the same settlement moment ("2026", "total", "will" on a busy expiry)
    cannot discriminate there and proposes nothing. The density is counted
    inside the same window used by rule 2, so the cap bounds the work per
    contract by ``max_local_density``.

    Nothing here is transitive: two contracts that merely share a neighbour
    are NOT proposed together. Rule 2 is a NECESSARY condition of the
    deterministic settlement check (§18), so it removes no pair the matcher
    could pass. Proposal is not approval: every pair still goes through the
    deterministic matcher, the LLM and the certificate.
    """
    ts: list[float | None] = []
    members: dict[str, list[int]] = {}
    for i, c in enumerate(contracts):
        if c.settlement_time is None:
            ts.append(None)
            continue
        t = ensure_utc(c.settlement_time).timestamp()
        ts.append(t)
        for key in blocking_keys(c, families):
            members.setdefault(key, []).append(i)

    window_s = window.total_seconds()
    seen: set[tuple[int, int]] = set()
    for idx in members.values():
        if len(idx) < 2:
            continue
        idx.sort(key=lambda k: ts[k])  # type: ignore[arg-type,return-value]
        times = [ts[k] for k in idx]   # type: ignore[misc]
        n = len(idx)
        # dense[a]: how many holders of this key settle within `window` of a.
        lo = hi = 0
        dense = [0] * n
        for a in range(n):
            while times[a] - times[lo] > window_s:  # type: ignore[operator]
                lo += 1
            while hi < n and times[hi] - times[a] <= window_s:  # type: ignore[operator]
                hi += 1
            dense[a] = hi - lo
        for a in range(n):
            if dense[a] > max_local_density:
                continue
            i = idx[a]
            for b in range(a + 1, n):
                if times[b] - times[a] > window_s:  # type: ignore[operator]
                    break
                if dense[b] > max_local_density:
                    continue
                j = idx[b]
                if contracts[i].venue == contracts[j].venue:
                    continue
                seen.add((i, j) if i < j else (j, i))
    return sorted(seen)


def component_indices(n: int, pairs: list[tuple[int, int]]) -> list[list[int]]:
    """Connected components over ``n`` nodes and candidate ``pairs`` (union-find).

    Each component is a sorted list of indices. Components are only a COARSER
    GROUPING for the scan (search_event evaluates APPROVED pairs inside a
    group); they are never a claim that their members match. Because pairs are
    not transitive, components stay small unless the candidate graph itself is
    dense.
    """
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    members: dict[int, list[int]] = {}
    for i in range(n):
        members.setdefault(find(i), []).append(i)
    return list(members.values())


def groups_from_pairs(contracts: list[NormalizedContract],
                      pairs: list[tuple[int, int]]
                      ) -> dict[str, list[NormalizedContract]]:
    """Name each candidate component by a stable digest of its members."""
    groups: dict[str, list[NormalizedContract]] = {}
    for indices in component_indices(len(contracts), pairs):
        group = [contracts[i] for i in indices]
        ident = "|".join(sorted(f"{c.venue}:{c.contract_id}" for c in group))
        digest = hashlib.sha256(ident.encode()).hexdigest()[:16]
        groups[f"blk:{digest}"] = group
    return groups


def group_by_event(contracts: list[NormalizedContract]
                   ) -> dict[str, list[NormalizedContract]]:
    """Candidate groups = connected components of ``candidate_pairs`` (§96).

    Kept for the scan (``run_arbitrage_scan``), which asks each group for its
    approved pairs. Group membership implies nothing about equivalence.
    """
    return groups_from_pairs(contracts, candidate_pairs(contracts))
