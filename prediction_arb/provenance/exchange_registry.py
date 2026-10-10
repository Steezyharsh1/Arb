"""Evidence-backed exchange and contract-qualified frontend registry.

A consumer brand is not necessarily the venue that issues, matches, clears, or
settles a contract.  Multi-exchange frontends therefore receive route-qualified
IDs (for example ``ibkr_kalshi`` and ``ibkr_forecastex``).  Callers must not use
a generic frontend ID to infer provenance.

Every record includes a source and verification date.  ``api_status`` describes
our evidence, not an implementation promise; documented endpoints that could
not be reached from this sandbox remain API_DOCUMENTED_UNTESTED.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExchangeRecord:
    venue_id: str
    display_name: str
    underlying_exchange: str
    contract_issuer: str
    order_matching_system: str
    clearing_entity: str
    settlement_authority: str
    dcm_status: str
    api_status: str
    source: str
    verified_at: str
    independence_confidence: str
    notes: str = ""
    record_kind: str = "exchange"       # exchange / alias / frontend_route / frontend
    route_qualifier: str = ""           # explicit contract route when applicable
    live_test_status: str = "UNAVAILABLE"

    # Backward-compatible names used by the original status and per-market
    # provenance APIs. They expose the same evidence without maintaining a
    # second registry that can drift.
    @property
    def frontend(self) -> str:
        return self.venue_id

    @property
    def matching_engine(self) -> str:
        return self.order_matching_system

    @property
    def liquidity_source(self) -> str:
        return self.order_matching_system

    @property
    def order_routing(self) -> str:
        if self.record_kind == "frontend":
            return "UNKNOWN"
        if self.record_kind == "frontend_route":
            return f"frontend_into_{self.route_qualifier}"
        return "direct" if not self.route_qualifier else self.route_qualifier

    @property
    def confidence(self) -> str:
        value = self.independence_confidence.upper()
        if value == "VERIFIED":
            return "HIGH"
        if value == "UNKNOWN":
            return "LOW"
        return value

    @property
    def version(self) -> int:
        return 2

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(part.strip() for part in self.source.split(";") if part.strip())


# Historical public name retained so status/per-market callers remain import
# compatible while all classification uses the richer ExchangeRecord directly.
ProvenanceRecord = ExchangeRecord


# Critical provenance IDs intentionally describe legal/matching identities,
# not brands. Equal critical IDs mean the same liquidity/contract venue.
EXCHANGE_REGISTRY: dict[str, ExchangeRecord] = {
    # ------------------------------------------------------------------
    # Direct DCMs and exchange aliases
    # ------------------------------------------------------------------
    "kalshi": ExchangeRecord(
        venue_id="kalshi", display_name="Kalshi",
        underlying_exchange="kalshi_dcm",
        contract_issuer="kalshi_dcm",
        order_matching_system="kalshi_clob",
        clearing_entity="kalshi_klear_dco",
        settlement_authority="kalshi_dcm",
        dcm_status="CFTC-designated contract market (VERIFIED)",
        api_status="MARKET_DATA_OBSERVED",
        source="https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations; https://docs.kalshi.com/getting_started/api_environments",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="Official REST/WS documentation was reviewed. Historical "
              "public collection on 2026-08-30 recorded 500 markets and 40 "
              "non-empty books, supporting MARKET_DATA_OBSERVED only. The "
              "2026-10-10 06:13:37 UTC public smoke was unavailable: TLS EOF "
              "before HTTP response on both configured hosts. Full current-"
              "adapter verification is not established."),
    "forecast_ex": ExchangeRecord(
        venue_id="forecast_ex", display_name="ForecastEx via IBKR",
        underlying_exchange="forecastex_dcm",
        contract_issuer="forecastex_dcm",
        order_matching_system="forecastex_paired_bid_book",
        clearing_entity="forecastex_dco",
        settlement_authority="forecastex_dcm",
        dcm_status="CFTC-designated DCM and registered DCO (VERIFIED)",
        api_status="API_DOCUMENTED_UNTESTED",
        source="https://www.cftc.gov/PressRoom/PressReleases/8926-24; https://www.interactivebrokers.com/predictionmarkets/en/home.php",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="Retail access researched here is through IBKR. Opposite bids "
              "pair at $1.00; the separate exchange fee is $0.01 per executed "
              "contract side."),
    "polymarket_us": ExchangeRecord(
        venue_id="polymarket_us", display_name="Polymarket US",
        underlying_exchange="qcx_dcm",
        contract_issuer="qcx_dcm",
        order_matching_system="polymarket_us_clob",
        clearing_entity="qc_clearing",
        settlement_authority="qcx_dcm",
        dcm_status="CFTC-designated contract market (VERIFIED)",
        api_status="API_DOCUMENTED_UNTESTED",
        source="https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/49571; https://docs.polymarket.us/",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="QCX LLC d/b/a Polymarket US is distinct from the international "
              "Polymarket platform. The latest 2026-10-10 06:13:41 UTC public "
              "smoke was UNAVAILABLE (TLS EOF before HTTP response); usable "
              "market data and full adapter verification remain unobserved."),
    "gemini": ExchangeRecord(
        venue_id="gemini", display_name="Gemini Predictions / Titan",
        underlying_exchange="gemini_titan_dcm",
        contract_issuer="gemini_titan_dcm",
        order_matching_system="gemini_titan_clob",
        clearing_entity="",
        settlement_authority="gemini_titan_dcm",
        dcm_status="CFTC-designated contract market (VERIFIED)",
        api_status="API_DOCUMENTED_UNTESTED",
        source="https://www.cftc.gov/csl/26-12/download; https://developer.gemini.com/products/prediction-markets",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="Titan contracts may clear through Gemini Olympus or QC "
              "Clearing. Contract-qualified clearing metadata is required."),
    "gemini_qc": ExchangeRecord(
        venue_id="gemini_qc", display_name="Gemini Titan (QC-cleared route)",
        underlying_exchange="gemini_titan_dcm",
        contract_issuer="gemini_titan_dcm",
        order_matching_system="gemini_titan_clob",
        clearing_entity="qc_clearing",
        settlement_authority="gemini_titan_dcm",
        dcm_status="CFTC-designated contract market (VERIFIED)",
        api_status="API_DOCUMENTED_UNTESTED",
        source="https://www.cftc.gov/csl/25-44/download",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="alias", route_qualifier="qc_clearing"),
    "gemini_olympus": ExchangeRecord(
        venue_id="gemini_olympus",
        display_name="Gemini Titan (Olympus-cleared route)",
        underlying_exchange="gemini_titan_dcm",
        contract_issuer="gemini_titan_dcm",
        order_matching_system="gemini_titan_clob",
        clearing_entity="gemini_olympus",
        settlement_authority="gemini_titan_dcm",
        dcm_status="CFTC-designated contract market (VERIFIED)",
        api_status="API_DOCUMENTED_UNTESTED",
        source="https://www.cftc.gov/csl/26-12/download",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="alias", route_qualifier="gemini_olympus"),
    "cdna": ExchangeRecord(
        venue_id="cdna", display_name="Crypto.com Derivatives North America",
        underlying_exchange="cdna_dcm",
        contract_issuer="cdna_dcm",
        order_matching_system="cdna_matching",
        clearing_entity="cdna_dco",
        settlement_authority="cdna_dcm",
        dcm_status="CFTC-designated DCM and registered DCO (VERIFIED)",
        api_status="NO_VERIFIED_TRADING_API",
        source="https://www.cftc.gov/sites/default/files/filings/orgrules/25/03/rules03102516984.pdf",
        verified_at="2026-09-13", independence_confidence="VERIFIED"),
    "og": ExchangeRecord(
        venue_id="og", display_name="OG Prediction Markets (CDNA route)",
        underlying_exchange="cdna_dcm",
        contract_issuer="cdna_dcm",
        order_matching_system="cdna_matching",
        clearing_entity="cdna_dco",
        settlement_authority="cdna_dcm",
        dcm_status="CDNA product/listing route; not a separate DCM",
        api_status="NO_VERIFIED_TRADING_API",
        source="https://www.fanduel.com/about/news/fanduel-predicts-to-expand-event-contract-offering-through-partnership-with-crypto-com-and-og-prediction-markets",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="The reviewed disclosure identifies OG-listed contracts through "
              "the CDNA partnership; it does not evidence a separate book.",
        record_kind="alias", route_qualifier="cdna"),
    "cme": ExchangeRecord(
        venue_id="cme", display_name="CME Event Contracts",
        underlying_exchange="cme_dcm",
        contract_issuer="cme_dcm",
        order_matching_system="cme_globex",
        clearing_entity="cme_clearing",
        settlement_authority="cme_dcm",
        dcm_status="CFTC-designated contract market and DCO (VERIFIED)",
        api_status="ROUTED_API_DOCUMENTED_UNTESTED",
        source="https://www.cmegroup.com/markets/event-contracts.html; https://www.interactivebrokers.com/predictionmarkets/en/home.php",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="Retail/API path evaluated in Phase 1 is an FCM route, not a "
              "direct CME retail adapter."),
    "railbird": ExchangeRecord(
        venue_id="railbird", display_name="Railbird Exchange",
        underlying_exchange="railbird_dcm",
        contract_issuer="railbird_dcm",
        order_matching_system="railbird_clob",
        clearing_entity="",
        settlement_authority="railbird_dcm",
        dcm_status="CFTC-designated contract market (VERIFIED)",
        api_status="NO_VERIFIED_PUBLIC_API",
        source="https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/50090; https://www.cftc.gov/csl/26-13/download",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="QC Clearing-to-Bitnomial transition is authorized; operational "
              "completion is UNVERIFIED, so clearing is intentionally blank."),
    "dkex": ExchangeRecord(
        venue_id="dkex", display_name="DKeX",
        underlying_exchange="railbird_dcm",
        contract_issuer="railbird_dcm",
        order_matching_system="railbird_clob",
        clearing_entity="",
        settlement_authority="railbird_dcm",
        dcm_status="Railbird DCM operating brand / platform (VERIFIED)",
        api_status="NO_VERIFIED_PUBLIC_API",
        source="https://ir.aboutdraftkings.com/news/news-details/2026/DraftKings-Launches-Proprietary-Exchange-to-Bolster-Differentiated-Predictions-Experience/default.aspx; https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/50090",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="DKeX launched 2026-06-26 using acquired Railbird technology "
              "and CFTC license. It is not independent of Railbird.",
        record_kind="alias", route_qualifier="railbird_dcm"),
    "ludlow": ExchangeRecord(
        venue_id="ludlow", display_name="Ludlow Exchange",
        underlying_exchange="ludlow_dcm",
        contract_issuer="ludlow_dcm",
        order_matching_system="ludlow_clob",
        clearing_entity="",
        settlement_authority="ludlow_dcm",
        dcm_status="CFTC-designated contract market (VERIFIED)",
        api_status="NO_VERIFIED_PUBLIC_API",
        source="https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/59390",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="Official application says a third-party registered DCO clears "
              "contracts but does not identify it in public material reviewed."),
    "novig": ExchangeRecord(
        venue_id="novig", display_name="Novig / Ludlow Exchange",
        underlying_exchange="ludlow_dcm",
        contract_issuer="ludlow_dcm",
        order_matching_system="ludlow_clob",
        clearing_entity="",
        settlement_authority="ludlow_dcm",
        dcm_status="Ludlow consumer platform / affiliate identity (VERIFIED)",
        api_status="NO_VERIFIED_PUBLIC_API",
        source="https://www.cftc.gov/filings/documents/2026/orgdcmlexexhibitg3260122.pdf; https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/59390",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="alias", route_qualifier="ludlow_dcm"),
    "rothera": ExchangeRecord(
        venue_id="rothera", display_name="Rothera Exchange and Clearing",
        underlying_exchange="rothera_dcm",
        contract_issuer="rothera_dcm",
        order_matching_system="rothera_matching",
        clearing_entity="rothera_dco",
        settlement_authority="rothera_dcm",
        dcm_status="CFTC-designated DCM and registered DCO (VERIFIED)",
        api_status="NO_VERIFIED_DIRECT_PUBLIC_API",
        source="https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/40473; https://www.cftc.gov/IndustryOversight/IndustryFilings/ClearingOrganizations/30998",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="Formerly LedgerX LLC / MIAXdx; live contracts are documented "
              "through Robinhood's FCM route."),

    # ------------------------------------------------------------------
    # Single-route, generic multi-route, and route-qualified frontends. These
    # are never new liquidity merely because the app/FCM brand differs.
    # ------------------------------------------------------------------
    "ibkr": ExchangeRecord(
        venue_id="ibkr", display_name="IBKR ForecastTrader (route unknown)",
        underlying_exchange="", contract_issuer="", order_matching_system="",
        clearing_entity="", settlement_authority="",
        dcm_status="Multi-DCM FCM frontend", api_status="API_DOCUMENTED_UNTESTED",
        source="https://www.interactivebrokers.com/predictionmarkets/en/home.php",
        verified_at="2026-09-13", independence_confidence="UNKNOWN",
        notes="Use ibkr_kalshi, ibkr_forecastex, or ibkr_cme only after the "
              "contract's exchange route is identified.", record_kind="frontend"),
    "fanduel": ExchangeRecord(
        venue_id="fanduel", display_name="FanDuel Predicts (route unknown)",
        underlying_exchange="", contract_issuer="", order_matching_system="",
        clearing_entity="", settlement_authority="",
        dcm_status="Multi-DCM frontend", api_status="NO_VERIFIED_PUBLIC_API",
        source="https://www.cmegroup.com/media-room/press-releases/2025/11/12/cme_group_and_fanduelannouncelaunchofnewpredictionmarketsapp.html; https://www.fanduel.com/about/news/fanduel-predicts-to-expand-event-contract-offering-through-partnership-with-crypto-com-and-og-prediction-markets",
        verified_at="2026-09-13", independence_confidence="UNKNOWN",
        notes="Use fanduel_cme or fanduel_cdna only after contract routing is "
              "identified.", record_kind="frontend"),
    "draftkings": ExchangeRecord(
        venue_id="draftkings",
        display_name="DraftKings Predictions (route unknown)",
        underlying_exchange="", contract_issuer="", order_matching_system="",
        clearing_entity="", settlement_authority="",
        dcm_status="Multi-DCM frontend", api_status="NO_VERIFIED_PUBLIC_API",
        source="https://www.draftkings.com/draftkings-debuts-predictions-app-entering-prediction-markets; https://ir.aboutdraftkings.com/news/news-details/2026/DraftKings-Launches-Proprietary-Exchange-to-Bolster-Differentiated-Predictions-Experience/default.aspx",
        verified_at="2026-09-13", independence_confidence="UNKNOWN",
        notes="Use draftkings_cme, draftkings_cdna, or draftkings_dkex only "
              "after contract routing is identified.", record_kind="frontend"),
    "webull": ExchangeRecord(
        venue_id="webull", display_name="Webull Event Contracts (Kalshi route)",
        underlying_exchange="kalshi_dcm", contract_issuer="kalshi_dcm",
        order_matching_system="kalshi_clob", clearing_entity="kalshi_klear_dco",
        settlement_authority="kalshi_dcm", dcm_status="FCM/frontend; not DCM",
        api_status="SAME_VENUE_ROUTE",
        source="https://www.webull.com/news-detail/12263811258663936",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="Only the evidenced Kalshi event-contract route is represented.",
        record_kind="frontend_route", route_qualifier="kalshi"),
    "moomoo": ExchangeRecord(
        venue_id="moomoo", display_name="Moomoo Event Contracts (Kalshi route)",
        underlying_exchange="kalshi_dcm", contract_issuer="kalshi_dcm",
        order_matching_system="kalshi_clob", clearing_entity="kalshi_klear_dco",
        settlement_authority="kalshi_dcm", dcm_status="FCM/frontend; not DCM",
        api_status="SAME_VENUE_ROUTE",
        source="https://www.moomoo.com/us/newsroom/moomoo-launches-prediction-markets",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="No verified event-contract OpenAPI endpoints were found.",
        record_kind="frontend_route", route_qualifier="kalshi"),
    "coinbase": ExchangeRecord(
        venue_id="coinbase", display_name="Coinbase Prediction Markets (Kalshi route)",
        underlying_exchange="kalshi_dcm", contract_issuer="kalshi_dcm",
        order_matching_system="kalshi_clob", clearing_entity="kalshi_klear_dco",
        settlement_authority="kalshi_dcm", dcm_status="FCM/frontend; not DCM",
        api_status="NO_VERIFIED_EVENT_CONTRACT_API",
        source="https://help.coinbase.com/en/coinbase/trading-and-funding/prediction-markets/intro",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="Coinbase states Kalshi operates its prediction markets; launch "
              "flow was sourced from Kalshi.",
        record_kind="frontend_route", route_qualifier="kalshi"),
    "ibkr_kalshi": ExchangeRecord(
        venue_id="ibkr_kalshi", display_name="IBKR ForecastTrader (Kalshi route)",
        underlying_exchange="kalshi_dcm", contract_issuer="kalshi_dcm",
        order_matching_system="kalshi_clob", clearing_entity="kalshi_klear_dco",
        settlement_authority="kalshi_dcm", dcm_status="FCM route; not DCM",
        api_status="API_DOCUMENTED_UNTESTED",
        source="https://www.interactivebrokers.com/predictionmarkets/en/home.php",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="frontend_route", route_qualifier="kalshi"),
    "ibkr_forecastex": ExchangeRecord(
        venue_id="ibkr_forecastex", display_name="IBKR ForecastTrader (ForecastEx route)",
        underlying_exchange="forecastex_dcm", contract_issuer="forecastex_dcm",
        order_matching_system="forecastex_paired_bid_book",
        clearing_entity="forecastex_dco", settlement_authority="forecastex_dcm",
        dcm_status="FCM route; not DCM", api_status="API_DOCUMENTED_UNTESTED",
        source="https://www.interactivebrokers.com/predictionmarkets/en/home.php",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="frontend_route", route_qualifier="forecast_ex"),
    "ibkr_cme": ExchangeRecord(
        venue_id="ibkr_cme", display_name="IBKR ForecastTrader (CME route)",
        underlying_exchange="cme_dcm", contract_issuer="cme_dcm",
        order_matching_system="cme_globex", clearing_entity="cme_clearing",
        settlement_authority="cme_dcm", dcm_status="FCM route; not DCM",
        api_status="API_DOCUMENTED_UNTESTED",
        source="https://www.interactivebrokers.com/predictionmarkets/en/home.php",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="frontend_route", route_qualifier="cme"),
    "robinhood": ExchangeRecord(
        venue_id="robinhood", display_name="Robinhood Prediction Markets (route unknown)",
        underlying_exchange="", contract_issuer="", order_matching_system="",
        clearing_entity="", settlement_authority="", dcm_status="Multi-DCM FCM frontend",
        api_status="NO_PUBLIC_TRADING_API",
        source="https://robinhood.com/us/en/newsroom/the-world-cup-is-now-trading-on-robinhood-and-rothera/",
        verified_at="2026-09-13", independence_confidence="UNKNOWN",
        notes="Use a route-qualified ID; contracts may route to Kalshi, "
              "ForecastEx, Rothera, or CDNA.", record_kind="frontend"),
    "robinhood_kalshi": ExchangeRecord(
        venue_id="robinhood_kalshi", display_name="Robinhood (Kalshi route)",
        underlying_exchange="kalshi_dcm", contract_issuer="kalshi_dcm",
        order_matching_system="kalshi_clob", clearing_entity="kalshi_klear_dco",
        settlement_authority="kalshi_dcm", dcm_status="FCM route; not DCM",
        api_status="NO_PUBLIC_TRADING_API",
        source="https://robinhood.com/us/en/support/articles/prediction-markets/",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="frontend_route", route_qualifier="kalshi"),
    "robinhood_forecastex": ExchangeRecord(
        venue_id="robinhood_forecastex", display_name="Robinhood (ForecastEx route)",
        underlying_exchange="forecastex_dcm", contract_issuer="forecastex_dcm",
        order_matching_system="forecastex_paired_bid_book",
        clearing_entity="forecastex_dco", settlement_authority="forecastex_dcm",
        dcm_status="FCM route; not DCM", api_status="NO_PUBLIC_TRADING_API",
        source="https://robinhood.com/us/en/support/articles/prediction-markets/",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="frontend_route", route_qualifier="forecast_ex"),
    "robinhood_rothera": ExchangeRecord(
        venue_id="robinhood_rothera", display_name="Robinhood (Rothera route)",
        underlying_exchange="rothera_dcm", contract_issuer="rothera_dcm",
        order_matching_system="rothera_matching", clearing_entity="rothera_dco",
        settlement_authority="rothera_dcm", dcm_status="FCM route; not DCM",
        api_status="NO_PUBLIC_TRADING_API",
        source="https://robinhood.com/us/en/newsroom/the-world-cup-is-now-trading-on-robinhood-and-rothera/",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="frontend_route", route_qualifier="rothera"),
    "robinhood_cdna": ExchangeRecord(
        venue_id="robinhood_cdna", display_name="Robinhood (CDNA route)",
        underlying_exchange="cdna_dcm", contract_issuer="cdna_dcm",
        order_matching_system="cdna_matching", clearing_entity="cdna_dco",
        settlement_authority="cdna_dcm", dcm_status="FCM route; not DCM",
        api_status="NO_PUBLIC_TRADING_API",
        source="https://robinhood.com/us/en/support/articles/prediction-markets/",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="frontend_route", route_qualifier="cdna"),
    "fanduel_cme": ExchangeRecord(
        venue_id="fanduel_cme", display_name="FanDuel Predicts (CME route)",
        underlying_exchange="cme_dcm", contract_issuer="cme_dcm",
        order_matching_system="cme_globex", clearing_entity="cme_clearing",
        settlement_authority="cme_dcm", dcm_status="Frontend/FCM route; not DCM",
        api_status="NO_VERIFIED_PUBLIC_API",
        source="https://www.cmegroup.com/media-room/press-releases/2025/11/12/cme_group_and_fanduelannouncelaunchofnewpredictionmarketsapp.html",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="frontend_route", route_qualifier="cme"),
    "fanduel_cdna": ExchangeRecord(
        venue_id="fanduel_cdna", display_name="FanDuel Predicts (OG/CDNA route)",
        underlying_exchange="cdna_dcm", contract_issuer="cdna_dcm",
        order_matching_system="cdna_matching", clearing_entity="cdna_dco",
        settlement_authority="cdna_dcm", dcm_status="Frontend/FCM route; not DCM",
        api_status="NO_VERIFIED_PUBLIC_API",
        source="https://www.fanduel.com/about/news/fanduel-predicts-to-expand-event-contract-offering-through-partnership-with-crypto-com-and-og-prediction-markets",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        notes="FanDuel documents sports/entertainment contracts listed by OG "
              "Prediction Markets and CDNA.",
        record_kind="frontend_route", route_qualifier="cdna"),
    "draftkings_cme": ExchangeRecord(
        venue_id="draftkings_cme", display_name="DraftKings Predictions (CME route)",
        underlying_exchange="cme_dcm", contract_issuer="cme_dcm",
        order_matching_system="cme_globex", clearing_entity="cme_clearing",
        settlement_authority="cme_dcm", dcm_status="Frontend/FCM route; not DCM",
        api_status="NO_VERIFIED_PUBLIC_API",
        source="https://www.draftkings.com/draftkings-debuts-predictions-app-entering-prediction-markets",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="frontend_route", route_qualifier="cme"),
    "draftkings_cdna": ExchangeRecord(
        venue_id="draftkings_cdna", display_name="DraftKings Predictions (CDNA route)",
        underlying_exchange="cdna_dcm", contract_issuer="cdna_dcm",
        order_matching_system="cdna_matching", clearing_entity="cdna_dco",
        settlement_authority="cdna_dcm", dcm_status="Frontend/FCM route; not DCM",
        api_status="NO_VERIFIED_PUBLIC_API",
        source="https://crypto.com/us/company-news/draftkings-predictions-to-expand-event-contract-offering-through-partnership-with-cryptocom-and-og-prediction-markets",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="frontend_route", route_qualifier="cdna"),
    "draftkings_dkex": ExchangeRecord(
        venue_id="draftkings_dkex", display_name="DraftKings Predictions (DKeX route)",
        underlying_exchange="railbird_dcm", contract_issuer="railbird_dcm",
        order_matching_system="railbird_clob", clearing_entity="",
        settlement_authority="railbird_dcm", dcm_status="Affiliated DCM route",
        api_status="NO_VERIFIED_PUBLIC_API",
        source="https://ir.aboutdraftkings.com/news/news-details/2026/DraftKings-Launches-Proprietary-Exchange-to-Bolster-Differentiated-Predictions-Experience/default.aspx",
        verified_at="2026-09-13", independence_confidence="VERIFIED",
        record_kind="frontend_route", route_qualifier="dkex"),
}


def get_exchange(venue_id: str) -> Optional[ExchangeRecord]:
    return EXCHANGE_REGISTRY.get(venue_id)


def get_provenance(venue: str) -> Optional[ProvenanceRecord]:
    """Backward-compatible evidence lookup; unknown venues return None."""
    return get_exchange(venue)


def all_venues() -> list[str]:
    return sorted(EXCHANGE_REGISTRY)
