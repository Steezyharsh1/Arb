"""Evidence-backed derivatives clearing organization registry.

Registration evidence and an exchange's *current operational route* are different
claims.  This registry records the legal DCO identities only; exchange records must
still describe which DCO clears a particular contract route.  Missing or transitional
route evidence remains UNKNOWN and must never be inferred from common ownership.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ClearingRecord:
    clearing_id: str
    legal_name: str
    registration: str
    registration_status: str
    source: str
    verified_at: str
    notes: str = ""


CLEARING_REGISTRY: dict[str, ClearingRecord] = {
    "kalshi_klear_dco": ClearingRecord(
        clearing_id="kalshi_klear_dco",
        legal_name="Kalshi Klear LLC",
        registration="CFTC-registered DCO",
        registration_status="VERIFIED",
        source="https://www.cftc.gov/media/13696/KKL%20-%2004-06-2026-%20Binary%20Option%20Event%20Contracts/download",
        verified_at="2026-09-13",
        notes="Kalshi event contracts are cleared through Klear; no separate "
              "event-contract clearing fee is identified in the cited filing."),
    "forecastex_dco": ClearingRecord(
        clearing_id="forecastex_dco",
        legal_name="ForecastEx LLC",
        registration="CFTC-registered DCO",
        registration_status="VERIFIED",
        source="https://www.cftc.gov/PressRoom/PressReleases/8926-24",
        verified_at="2026-09-13",
        notes="ForecastEx is registered as both DCM and DCO."),
    "qc_clearing": ClearingRecord(
        clearing_id="qc_clearing",
        legal_name="QC Clearing LLC d/b/a Polymarket Clearing",
        registration="CFTC-registered DCO",
        registration_status="VERIFIED",
        source="https://www.cftc.gov/IndustryOversight/IndustryFilings/ClearingOrganizationRules/57739",
        verified_at="2026-09-13"),
    "gemini_olympus": ClearingRecord(
        clearing_id="gemini_olympus",
        legal_name="Gemini Olympus, LLC",
        registration="CFTC-registered DCO",
        registration_status="VERIFIED",
        source="https://www.cftc.gov/IndustryOversight/IndustryFilings/ClearingOrganizations/59189",
        verified_at="2026-09-13",
        notes="Registered 2026-04-29. Gemini Titan contracts may clear through "
              "Olympus or QC Clearing, so contract-route evidence is required."),
    "cdna_dco": ClearingRecord(
        clearing_id="cdna_dco",
        legal_name="North American Derivatives Exchange, Inc. d/b/a Crypto.com | Derivatives North America",
        registration="CFTC-registered DCO",
        registration_status="VERIFIED",
        source="https://www.cftc.gov/sites/default/files/filings/orgrules/25/03/rules03102516984.pdf",
        verified_at="2026-09-13",
        notes="The same legal entity is also the CDNA designated contract market."),
    "cme_clearing": ClearingRecord(
        clearing_id="cme_clearing",
        legal_name="Chicago Mercantile Exchange Inc. (CME Clearing)",
        registration="CFTC-registered DCO",
        registration_status="VERIFIED",
        source="https://www.cftc.gov/IndustryOversight/IndustryFilings/ClearingOrganizations",
        verified_at="2026-09-13"),
    "bitnomial_clearing": ClearingRecord(
        clearing_id="bitnomial_clearing",
        legal_name="Bitnomial Clearinghouse, LLC",
        registration="CFTC-registered DCO",
        registration_status="VERIFIED",
        source="https://www.cftc.gov/csl/26-13/download",
        verified_at="2026-09-13",
        notes="CFTC Letter 26-13 authorizes Railbird's QC Clearing-to-Bitnomial "
              "transition. Completion of the operational cutover was not verified."),
    "rothera_dco": ClearingRecord(
        clearing_id="rothera_dco",
        legal_name="Rothera Exchange and Clearing LLC",
        registration="CFTC-registered DCO",
        registration_status="VERIFIED",
        source="https://www.cftc.gov/IndustryOversight/IndustryFilings/ClearingOrganizations/30998",
        verified_at="2026-09-13",
        notes="Formerly LedgerX LLC / MIAX Derivatives Exchange."),
}


def get_clearing(clearing_id: str) -> Optional[ClearingRecord]:
    return CLEARING_REGISTRY.get(clearing_id)
