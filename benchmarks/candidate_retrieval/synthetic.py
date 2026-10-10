"""Seeded synthetic multi-venue contracts for candidate-retrieval benchmarking (§96).

Everything here is reproducible from ``(n_contracts, seed)``. Each generated
contract carries a hidden ``eq_id`` label that the matcher never sees:

* listings of the SAME latent event on two or three venues share an
  ``eq_id`` (genuinely equivalent, worded differently, timestamps disagree by
  a few hours, thresholds written in different styles);
* near-miss variants of those events (threshold one step away, or settlement
  moved by days) get their own ``eq_id`` — they share most words with a real
  match and are the hard negatives the deterministic matcher must reject;
* decoys use generic vocabulary (``will``, ``above``, ``2026``, ``total``,
  ``number``) and settle on the same clustered calendar as real events, so
  they share generic keys with everything.

Settlement times are heavily clustered on a small calendar, as real
prediction-market expiries are. This is deliberate: it is the condition under
which 24-hour buckets overlap and transitive grouping can become giant.

The generator produces realistic *shapes*, not the inherited review's exact
dataset. Numbers from it describe this generator only.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional

from prediction_arb.core.types import (
    MarketMicrostructure,
    MicrostructureType,
    NormalizedContract,
    OrderTypeSupport,
    OutcomeType,
)

# Venue → underlying DCM identity. The three enabled venues in config/venues.yaml
# are INDEPENDENT of each other; this lets the deterministic/certificate path
# treat cross-venue pairs as independent, as in production.
VENUE_UNDERLYING = {
    "kalshi": "kalshi_dcm",
    "polymarket_us": "qcx_dcm",
    "forecast_ex": "forecastex_dcm",
}
VENUES = tuple(VENUE_UNDERLYING)

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]
_CAL_START = datetime(2026, 10, 12, tzinfo=timezone.utc)
_CAL_DAYS = 60
_SETTLE_TIMES = ((16, 0), (21, 0), (23, 59))


def _fmt_usd(v: Decimal) -> str:
    return f"${int(v):,}"


def _fmt_k(v: Decimal) -> str:
    return f"${int(v) // 1000}k"


def _fmt_pct(v: Decimal) -> str:
    return f"{v}%"


def _fmt_pct_words(v: Decimal) -> str:
    return f"{v} percent"


def _fmt_plain(v: Decimal) -> str:
    return f"{v}"


def _fmt_dollars_words(v: Decimal) -> str:
    return f"{v} dollars"


def _fmt_points(v: Decimal) -> str:
    return f"{int(v):,}"


def _fmt_f(v: Decimal) -> str:
    return f"{int(v)}°F"


@dataclass(frozen=True)
class Family:
    """One real-world event template. ``titles`` are venue-style phrasings;
    ``render_a``/``render_b`` are two threshold spellings chosen per listing."""
    name: str
    subjects: tuple[str, ...]
    thresholds: tuple[Decimal, ...]
    units: Optional[str]
    operator: Optional[str]
    op_phrases: tuple[str, ...]
    titles: tuple[str, ...]
    render_a: Callable[[Decimal], str]
    render_b: Callable[[Decimal], str]
    resolution: str
    dated: bool = True


def _range(start: str, stop: str, step: str) -> tuple[Decimal, ...]:
    out, v, s, e, st = [], Decimal(start), Decimal(start), Decimal(stop), Decimal(step)
    while v <= e:
        out.append(v)
        v += st
    return tuple(out)


FAMILIES: tuple[Family, ...] = (
    Family("btc", ("Bitcoin",), _range("50000", "150000", "5000"), "USD", ">",
           ("above", "over", "higher than"),
           ("Will {subj} be {op} {thr} on {mon} {day}, {year}?",
            "{subj} price {op} {thr} at settlement on {mon} {day}?",
            "Will the price of {subj} be {op} {thr} by {mon} {day}?"),
           _fmt_usd, _fmt_k,
           "CF Benchmarks Bitcoin Real-Time Index at settlement"),
    Family("cpi", ("CPI inflation",), _range("2.0", "4.5", "0.1"), "percent", ">=",
           ("at or above", "at least"),
           ("Will US {subj} year-over-year for {month} {year} be {op} {thr}?",
            "Headline CPI YoY {month} {year} {op} {thr}?",
            "Will inflation (CPI) print {op} {thr} for {month} {year}?"),
           _fmt_pct, _fmt_pct_words,
           "Bureau of Labor Statistics CPI release for the reference month"),
    Family("spx", ("S&P 500 close",), _range("4000", "7000", "50"), "points", ">",
           ("above", "over"),
           ("{subj} {op} {thr} on {mon} {day}?",
            "Will the S&P 500 index close {op} {thr} on {mon} {day}, {year}?",
            "Does the S&P 500 finish {op} {thr} on {mon} {day}?"),
           _fmt_points, _fmt_points,
           "Official S&P 500 closing value published by S&P Dow Jones Indices"),
    Family("unemp", ("unemployment rate",), _range("3.5", "5.5", "0.1"), "percent", ">",
           ("above", "higher than"),
           ("Will the US unemployment rate be {op} {thr} in {month} {year}?",
            "US jobless rate {op} {thr} for {month} {year}?",
            "Will the unemployment rate exceed {thr} in {month} {year}?"),
           _fmt_pct, _fmt_pct_words,
           "BLS Employment Situation report for the reference month"),
    Family("gas", ("US average gasoline price",), _range("2.50", "4.50", "0.05"),
           "USD", ">", ("above", "over"),
           ("Will the national average gas price be {op} {thr} per gallon on {mon} {day}?",
            "AAA national average regular gas {op} {thr} on {mon} {day}?",
            "Gas price {op} {thr} on {mon} {day}, {year}?"),
           _fmt_dollars_words, lambda v: f"${v}",
           "AAA national average for regular unleaded gasoline"),
    Family("fedhold", ("Federal Reserve target rate",), _range("3.25", "5.50", "0.25"),
           "percent", "=", ("at", "held at"),
           ("Will the Federal Reserve keep its target rate {op} {thr} after the {month} {year} meeting?",
            "Fed holds rates {op} {thr} at the {month} {year} meeting?",
            "Will the Federal Reserve hold the target range at {thr} in {month} {year}?"),
           _fmt_pct, _fmt_pct_words,
           "Federal Open Market Committee statement for the meeting"),
    Family("storms", ("named storms making US landfall",), _range("1", "6", "1"), "count", ">=",
           ("at least", "of"),
           ("How many named storms make US landfall in the {year} season, {op} {thr}?",
            "US landfalling named storms {year}: {op} {thr}?",
            "Will at least {thr} named storms make US landfall in {year}?"),
           _fmt_plain, _fmt_plain,
           "National Hurricane Center best-track landfall record"),
    Family("nyc_high", ("New York City high temperature",), _range("84", "100", "1"), "F", ">",
           ("above", "over"),
           ("Will the high temperature in New York City be {op} {thr} degrees on {mon} {day}?",
            "NYC daily high {op} {thr}°F on {mon} {day}?",
            "Central Park high temp {op} {thr} on {mon} {day}, {year}?"),
           _fmt_f, _fmt_plain,
           "National Weather Service Central Park daily climate report"),
)

# Threshold-less event families: the only identity signal is the subject words
# plus the settlement moment, so they stress the non-threshold retrieval path.
FAMILIES = FAMILIES + (
    Family("shutdown", ("federal government shutdown",), (None,), None, None,
           ("before", "by"),
           ("Will the {subj} begin {op} {mon} {day}, {year}?",
            "{subj} starts {op} {mon} {day} {year}?",
            "Will a {subj} happen {op} {mon} {day}?"),
           _fmt_plain, _fmt_plain,
           "Enacted appropriations status on the settlement date"),
    Family("senate", ("Republican Senate majority",), (None,), None, None,
           ("after",),
           ("Will Republicans control the Senate {op} the {year} midterms?",
            "{subj} after the {year} midterm elections?",
            "Will the {subj} hold {op} the {year} election?"),
           _fmt_plain, _fmt_plain,
           "Certified Senate control after the general election"),
)

# Generic families — deliberately made of common words and clustered numbers.
GENERIC_NOUNS = ("goals", "points", "votes", "games", "products", "bills",
                 "deals", "tweets", "appearances", "launches", "filings", "fights")
GENERIC_PEOPLE = ("the president", "the press secretary", "the senator",
                  "the CEO", "the coach", "the mayor", "the chair")
GENERIC_EVENTS = ("the press conference", "the debate", "the earnings call",
                  "the State of the Union", "the keynote")
GENERIC_WORDS = ("tariff", "recession", "historic", "record", "crisis", "deal")
TEAMS = ("Lakers", "Celtics", "Knicks", "Warriors", "Bulls", "Heat",
         "Rangers", "Bruins", "Yankees", "Dodgers")
COMPANIES = ("Apple", "Amazon", "Netflix", "Nvidia", "Tesla", "Walmart", "Target")

_CATEGORY_BY_VENUE = {
    "kalshi": {"btc": "KXBTC", "cpi": "KXCPI", "spx": "KXINXU", "unemp": "KXUNRATE",
               "gas": "KXAAAGASW", "fedhold": "KXFEDDECISION", "storms": "KXHURCT",
               "nyc_high": "KXHIGHNY", "generic": "KXMENTION", "sports": "KXNBAGAME",
               "earnings": "KXEARNINGS", "shutdown": "KXGOVSHUT", "senate": "KXSENATE"},
    "polymarket_us": {"btc": "Crypto", "cpi": "Economics", "spx": "Finance",
                      "unemp": "Economics", "gas": "Economics", "fedhold": "Economics",
                      "storms": "Weather", "nyc_high": "Weather", "generic": "Pop Culture",
                      "sports": "Sports", "earnings": "Business",
                      "shutdown": "Politics", "senate": "Politics"},
    "forecast_ex": {"btc": "ECON", "cpi": "ECON", "spx": "ECON", "unemp": "ECON",
                    "gas": "ECON", "fedhold": "ECON", "storms": "WTHR", "nyc_high": "WTHR",
                    "generic": "MISC", "sports": "SPRT", "earnings": "ECON",
                    "shutdown": "ECON", "senate": "POL"},
}


@dataclass
class Listing:
    """One synthetic market listing, exactly as an adapter would produce it,
    plus the hidden benchmark label ``eq_id``."""
    venue: str
    contract_id: str
    eq_id: str
    family: str
    title: str
    settlement: datetime
    threshold: Optional[Decimal]
    operator: Optional[str]
    units: Optional[str]
    category: str
    resolution: str
    payout: Decimal = Decimal("1.00")

    def to_contract(self) -> NormalizedContract:
        return NormalizedContract(
            venue=self.venue,
            underlying_exchange=VENUE_UNDERLYING[self.venue],
            clearing_entity="",
            market_id=self.contract_id,
            contract_id=self.contract_id,
            event_description=self.title,
            normalized_event="",
            outcome_type=OutcomeType.BINARY,
            outcome="YES",
            normalized_outcome="",
            outcome_count=2,
            all_outcomes=("YES", "NO"),
            settlement_time=self.settlement,
            timezone="UTC",
            cutoff_time=self.settlement - timedelta(hours=1),
            resolution_source=self.resolution,
            resolution_method="",
            threshold=self.threshold,
            threshold_operator=self.operator,
            units=self.units,
            payout=self.payout,
            currency="USD",
            early_resolution_rules="",
            cancellation_rules="",
            contract_version="v1",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("1"),
            max_order_size=None,
            jurisdiction_requirements="US",
            category=self.category,
            market_microstructure=MarketMicrostructure(
                type=MicrostructureType.CLOB, has_order_book=True),
            supported_order_types=OrderTypeSupport(limit=True, gtc=True),
            raw=None,
        )


def _calendar(rng: random.Random, skew: float = 1.3
              ) -> tuple[list[datetime], list[float]]:
    """Clustered settlement calendar: ~60 days × 3 expiry times, with a
    Zipf-like weight so a few slots absorb most of the contracts. ``skew``
    controls how concentrated expiries are (1.3 = default; 2.5 = stress)."""
    slots = []
    for d in range(_CAL_DAYS):
        for hh, mm in _SETTLE_TIMES:
            day = _CAL_START + timedelta(days=d)
            slots.append(day.replace(hour=hh, minute=mm))
    weights = [1.0 / (1 + (i % 17)) ** skew for i in range(len(slots))]
    rng.shuffle(weights)
    return slots, weights


def _pick_settlement(rng: random.Random, cal: list[datetime],
                     weights: list[float]) -> datetime:
    return rng.choices(cal, weights=weights, k=1)[0]


def _date_fields(ts: datetime) -> dict[str, str]:
    return {"mon": _MONTHS[ts.month - 1], "month": _MONTHS[ts.month - 1],
            "day": str(ts.day), "year": str(ts.year)}


def _title_for(rng: random.Random, fam: Family, venue: str, thr: Decimal,
               settle: datetime, subject: str) -> tuple[str, str]:
    """Return (title, threshold_text) for one venue's phrasing."""
    template = rng.choice(fam.titles)
    op_word = rng.choice(fam.op_phrases)
    if thr is None:
        thr_text = ""
    else:
        thr_text = (fam.render_a if rng.random() < 0.6 else fam.render_b)(thr)
    fields = _date_fields(settle)
    title = template.format(subj=subject, op=op_word, thr=thr_text, **fields)
    return title, thr_text


def build_dataset(n_contracts: int = 1000, seed: int = 20261010,
                  calendar_skew: float = 1.3) -> list[Listing]:
    """Deterministically build ``n_contracts`` listings across three venues.

    Composition (targets, scaled from ``n_contracts``):
      ~40 %  genuinely equivalent listings (2–3 venues per latent event);
      ~15 %  near-miss hard negatives derived from those events;
      ~45 %  generic decoys sharing common words and settlement clusters.
    """
    rng = random.Random(seed)
    cal, weights = _calendar(rng, calendar_skew)
    listings: list[Listing] = []
    n_eq_target = int(n_contracts * 0.40)
    n_near_target = int(n_contracts * 0.15)

    eq_events: list[dict] = []
    eq_idx = 0
    while len(listings) < n_eq_target:
        fam = FAMILIES[eq_idx % len(FAMILIES)]
        subject = rng.choice(fam.subjects)
        thr = rng.choice(fam.thresholds)
        settle = _pick_settlement(rng, cal, weights)
        k = 2 if rng.random() < 0.6 else 3
        venues = rng.sample(VENUES, k)
        eq_id = f"eq-{eq_idx:04d}"
        ev = {"fam": fam, "subject": subject, "thr": thr, "settle": settle,
              "eq_id": eq_id, "venues": venues}
        eq_events.append(ev)
        for v in venues:
            # 85 %: venues disagree by ≤3 h; 15 %: disagree by 3–9 h, so some
            # genuinely equivalent pairs sit beyond the ±6 h matcher tolerance.
            if rng.random() < 0.85:
                offset = timedelta(minutes=rng.randint(-180, 180))
            else:
                offset = timedelta(minutes=rng.choice([-1, 1]) * rng.randint(180, 540))
            ts = settle + offset
            title, thr_text = _title_for(rng, fam, v, thr, ts, subject)
            listings.append(Listing(
                venue=v, contract_id=f"{v[:2].upper()}-{eq_id}-{len(listings):04d}",
                eq_id=eq_id, family=fam.name, title=title, settlement=ts,
                threshold=thr, operator=fam.operator, units=fam.units,
                category=_CATEGORY_BY_VENUE[v][fam.name],
                resolution=fam.resolution))
        eq_idx += 1

    # Hard negatives: near-miss variants of real events on the same family.
    near = 0
    while near < n_near_target:
        ev = rng.choice(eq_events)
        fam = ev["fam"]
        v = rng.choice(VENUES)
        if ev["thr"] is not None and rng.random() < 0.5:   # threshold one step away
            steps = fam.thresholds.index(ev["thr"])
            j = steps + rng.choice([-1, 1])
            if not (0 <= j < len(fam.thresholds)):
                continue
            thr, settle = fam.thresholds[j], ev["settle"]
        else:                    # same threshold, settles days later/earlier
            thr = ev["thr"]
            settle = ev["settle"] + timedelta(days=rng.choice([-1, 1]) * rng.randint(2, 20))
        title, thr_text = _title_for(rng, fam, v, thr, settle, ev["subject"])
        listings.append(Listing(
            venue=v, contract_id=f"{v[:2].upper()}-NM-{len(listings):04d}",
            eq_id=f"nm-{len(listings):04d}", family=fam.name, title=title,
            settlement=settle, threshold=thr, operator=fam.operator,
            units=fam.units, category=_CATEGORY_BY_VENUE[v][fam.name],
            resolution=fam.resolution))
        near += 1

    # Decoys: generic wording, clustered settlement, unrelated events.
    while len(listings) < n_contracts:
        v = rng.choice(VENUES)
        kind = rng.random()
        settle = _pick_settlement(rng, cal, weights)
        f = _date_fields(settle)
        if kind < 0.4:
            n = rng.choice(GENERIC_NOUNS)
            thr = Decimal(rng.randint(10, 200))
            title = (f"Will the total number of {n} be above {thr} in "
                     f"{f['month']} {f['year']}?")
            cat, fam_name, op, units, res = (_CATEGORY_BY_VENUE[v]["generic"],
                                             "generic_count", ">", "count",
                                             "Official count per the published league or company record")
        elif kind < 0.7:
            who = rng.choice(GENERIC_PEOPLE)
            word = rng.choice(GENERIC_WORDS)
            ev_name = rng.choice(GENERIC_EVENTS)
            thr = None
            title = (f"Will {who} say '{word}' during {ev_name} on "
                     f"{f['mon']} {f['day']}?")
            cat, fam_name, op, units, res = (_CATEGORY_BY_VENUE[v]["generic"],
                                             "generic_mention", None, None,
                                             "Official transcript of the event")
        elif kind < 0.85:
            team = rng.choice(TEAMS)
            thr = None
            title = f"Will the {team} win their game on {f['mon']} {f['day']}, {f['year']}?"
            cat, fam_name, op, units, res = (_CATEGORY_BY_VENUE[v]["sports"],
                                             "sports", None, None,
                                             "Official league box score")
        else:
            co = rng.choice(COMPANIES)
            thr = Decimal(rng.randint(5, 60))
            title = (f"Will {co} report revenue above {thr} billion for Q"
                     f"{rng.randint(1, 4)} {f['year']}?")
            cat, fam_name, op, units, res = (_CATEGORY_BY_VENUE[v]["earnings"],
                                             "earnings", ">", "USD",
                                             "Company quarterly filing")
        idx = len(listings)
        listings.append(Listing(
            venue=v, contract_id=f"{v[:2].upper()}-DC-{idx:04d}",
            eq_id=f"dc-{idx:04d}", family=fam_name, title=title, settlement=settle,
            threshold=thr, operator=op, units=units, category=cat, resolution=res))

    return listings


def labels_of(listings: list[Listing]) -> dict[str, str]:
    return {f"{x.venue}:{x.contract_id}": x.eq_id for x in listings}
