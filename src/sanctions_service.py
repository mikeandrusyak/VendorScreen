import asyncio
import logging
import os

import httpx

log = logging.getLogger("vendorscreen")

OPENSANCTIONS_BASE_URL = "https://api.opensanctions.org"

# HTTP statuses worth retrying: rate limiting (429) and transient server-side
# failures (5xx). Anything else (e.g. 4xx auth/validation) is a real error and
# is surfaced immediately rather than retried.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Risk level constants matching Monday.com status column labels. UNAVAILABLE is
# written when screening could not be completed (OpenSanctions down) so the
# result is never silently lost — Monday auto-creates the label
# (create_labels_if_missing) and the client can see it needs a re-run.
RISK_LEVEL = {
    "CLEAR": "Clear",
    "WARNING": "Warning",
    "CRITICAL": "Critical",
    "UNAVAILABLE": "Screening Failed",
}


class SanctionsUnavailableError(Exception):
    """OpenSanctions could not be reached after exhausting retries.

    Distinct from a code/logic bug: it means the screening did not run and
    should be retried, not that the vendor is clear or flagged.
    """

# Appended to every details string written to the board. VendorScreen is an
# informational screening tool — it does not make compliance decisions. See
# TERMS_OF_SERVICE.md §2. This disclaimer must remain visible in the output.
DISCLAIMER = (
    " — Informational screening only, not a compliance decision. Results may be "
    "incomplete or inaccurate; verify independently before acting."
)


def with_disclaimer(details):
    return f"{details}{DISCLAIMER}"


def unavailable_result():
    """Result written to the board when screening could not be completed.

    Never returns Clear on failure — that would be a false negative. The client
    sees the check did not run and can re-trigger the automation.
    """
    return {
        "riskLevel": RISK_LEVEL["UNAVAILABLE"],
        "details": with_disclaimer(
            "Screening could not be completed — the OpenSanctions service was "
            "temporarily unavailable. Re-run the automation to try again."
        ),
    }


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
    """Wrap check_vendor with retry logic for transient OpenSanctions failures.

    Retries on rate limiting (429), transient server errors (5xx), and
    network/timeout errors. After exhausting retries on any of these — or on the
    first non-retryable HTTP error — behaves as follows:

    - transient failures (429/5xx/network) → raise SanctionsUnavailableError so
      the caller can mark the record instead of losing the check silently;
    - non-retryable HTTP errors (e.g. 4xx auth/validation) → re-raised as-is,
      since retrying won't help and they indicate a real problem.
    """
    for attempt in range(1, retries + 1):
        try:
            return await check_vendor(vendor_name)
        except httpx.HTTPStatusError as err:
            status = err.response.status_code
            if status not in RETRYABLE_STATUS:
                # Not transient (bad request, auth, etc.) — retrying is pointless.
                raise
            if attempt >= retries:
                log.error(
                    "[sanctions] Giving up after %d attempts (last status %d)",
                    retries,
                    status,
                )
                raise SanctionsUnavailableError(
                    f"OpenSanctions returned {status} after {retries} attempts"
                ) from err
            try:
                retry_after = int(err.response.headers.get("retry-after") or "0")
            except ValueError:
                retry_after = 0
            wait_seconds = retry_after if retry_after else delay_seconds * attempt
            log.warning(
                "[sanctions] HTTP %d. Retrying in %ss (attempt %d/%d)",
                status,
                wait_seconds,
                attempt,
                retries,
            )
            await asyncio.sleep(wait_seconds)
        except httpx.TransportError as err:
            # Timeouts, connection failures, DNS errors — OpenSanctions is
            # unreachable. TransportError covers TimeoutException too.
            if attempt >= retries:
                log.error(
                    "[sanctions] Giving up after %d attempts (network error: %s)",
                    retries,
                    err,
                )
                raise SanctionsUnavailableError(
                    f"OpenSanctions unreachable after {retries} attempts: {err}"
                ) from err
            wait_seconds = delay_seconds * attempt
            log.warning(
                "[sanctions] Network error (%s). Retrying in %ss (attempt %d/%d)",
                err,
                wait_seconds,
                attempt,
                retries,
            )
            await asyncio.sleep(wait_seconds)
