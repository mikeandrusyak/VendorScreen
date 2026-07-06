import asyncio
import logging
import os

import httpx

log = logging.getLogger("vendorscreen")

OPENSANCTIONS_BASE_URL = "https://api.opensanctions.org"

# Risk level constants matching Monday.com status column labels
RISK_LEVEL = {
    "CLEAR": "Clear",
    "WARNING": "Warning",
    "CRITICAL": "Critical",
}

# Appended to every details string written to the board. VendorScreen AI is an
# informational screening tool — it does not make compliance decisions. See
# TERMS_OF_SERVICE.md §2. This disclaimer must remain visible in the output.
DISCLAIMER = (
    " — Informational screening only, not a compliance decision. Results may be "
    "incomplete or inaccurate; verify independently before acting."
)


def with_disclaimer(details):
    return f"{details}{DISCLAIMER}"


async def check_vendor(vendor_name):
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{OPENSANCTIONS_BASE_URL}/search/default",
            params={"q": vendor_name, "limit": 5},
            headers={"Authorization": f"ApiKey {os.getenv('OPENSANCTIONS_API_KEY')}"},
        )
    response.raise_for_status()

    results = response.json().get("results") or []

    if not results:
        return {
            "riskLevel": RISK_LEVEL["CLEAR"],
            "details": with_disclaimer("No matches found in OpenSanctions."),
        }

    # Check for active sanctions first (Critical), then PEP/minor flags (Warning)
    has_critical = any(
        "sanctions" in ds for entity in results for ds in (entity.get("datasets") or [])
    )

    if has_critical:
        match = results[0]
        profile_url = f"https://www.opensanctions.org/entities/{match['id']}/"
        return {
            "riskLevel": RISK_LEVEL["CRITICAL"],
            "details": with_disclaimer(
                f"Possible direct sanction match: {match.get('caption')}. Profile: {profile_url}"
            ),
        }

    has_warning = any(
        any("pep" in ds for ds in (entity.get("datasets") or []))
        or "poi" in ((entity.get("properties") or {}).get("topics") or [])
        for entity in results
    )

    if has_warning:
        match = results[0]
        profile_url = f"https://www.opensanctions.org/entities/{match['id']}/"
        return {
            "riskLevel": RISK_LEVEL["WARNING"],
            "details": with_disclaimer(
                f"Possible PEP or minor flag: {match.get('caption')}. Profile: {profile_url}"
            ),
        }

    return {
        "riskLevel": RISK_LEVEL["CLEAR"],
        "details": with_disclaimer(
            "No active sanctions or PEP flags. "
            f"Possible non-critical match: {results[0].get('caption')}."
        ),
    }


async def check_vendor_with_retry(vendor_name, retries=3, delay_seconds=2.0):
    """Wrap check_vendor with retry logic for 429 rate limiting."""
    for attempt in range(1, retries + 1):
        try:
            return await check_vendor(vendor_name)
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 429 and attempt < retries:
                try:
                    retry_after = int(err.response.headers.get("retry-after") or "0")
                except ValueError:
                    retry_after = 0
                wait_seconds = retry_after if retry_after else delay_seconds * attempt
                log.warning(
                    "[sanctions] Rate limited. Retrying in %ss (attempt %d/%d)",
                    wait_seconds,
                    attempt,
                    retries,
                )
                await asyncio.sleep(wait_seconds)
            else:
                raise
