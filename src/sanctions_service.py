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

# We query the OpenSanctions /match endpoint (not /search) so we get a per-
# candidate similarity `score` (0-1) and can send a structured entity (name +
# country) instead of a bare text query — far fewer false positives.
#
# The schema is the entity type matched against. We default to LegalEntity, the
# FollowTheMoney parent of both Person and Company, so a single query surfaces
# individuals AND organizations without the customer declaring which their board
# holds. This matters for compliance: querying a person's name under the Company
# schema returns NO results, so a wrong per-board type would silently miss a
# sanctioned individual (a false Clear). LegalEntity sidesteps that entirely.
# MATCH_SCHEMA can still narrow to a concrete schema (e.g. "Person") if a
# deployment ever wants type-specific precision.
MATCH_SCHEMA_DEFAULT = "LegalEntity"
# Score thresholds gate the risk level so a weak namesake doesn't get flagged.
# A sanction hit at/above CRITICAL is Critical; anything sanction/PEP-related
# at/above WARNING (but below the critical bar) is a Warning worth review;
# below WARNING is noise and ignored. Tunable per deployment via env.
SCORE_CRITICAL_DEFAULT = 0.85
SCORE_WARNING_DEFAULT = 0.70

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


def _thresholds():
    """Read the (critical, warning) score thresholds from the environment at call
    time so a deployment can retune them without a code change."""
    critical = float(os.getenv("MATCH_SCORE_CRITICAL") or SCORE_CRITICAL_DEFAULT)
    warning = float(os.getenv("MATCH_SCORE_WARNING") or SCORE_WARNING_DEFAULT)
    return critical, warning


def _score(entity):
    return entity.get("score") or 0.0


def _is_sanction(entity):
    datasets = entity.get("datasets") or []
    topics = (entity.get("properties") or {}).get("topics") or []
    return any("sanction" in ds for ds in datasets) or any("sanction" in t for t in topics)


def _is_pep(entity):
    datasets = entity.get("datasets") or []
    topics = (entity.get("properties") or {}).get("topics") or []
    return any("pep" in ds for ds in datasets) or any("pep" in t for t in topics) or "poi" in topics


def _classify(results, critical, warning):
    """Pick the risk level and the driving match from scored /match candidates.

    Returns (level_key, match_entity) or None for Clear. Only candidates scoring
    at/above the warning floor are considered — below that is noise. A sanction
    hit at/above the critical bar is Critical; any remaining sanction/PEP-flagged
    candidate above the floor is a Warning. A strong name match with no
    sanction/PEP signal is a namesake, not a hit → Clear.
    """
    candidates = [e for e in results if _score(e) >= warning]
    if not candidates:
        return None

    sanctioned = [e for e in candidates if _is_sanction(e)]
    top_sanction = max(sanctioned, key=_score, default=None)
    if top_sanction is not None and _score(top_sanction) >= critical:
        return "CRITICAL", top_sanction

    flagged = sanctioned + [e for e in candidates if _is_pep(e)]
    top_flag = max(flagged, key=_score, default=None)
    if top_flag is not None:
        return "WARNING", top_flag

    return None


def _clear_result():
    return {
        "riskLevel": RISK_LEVEL["CLEAR"],
        "details": with_disclaimer("No sanction or PEP matches found in OpenSanctions."),
        "score": None,
        "matchId": None,
        "matchCaption": None,
    }


async def match_vendor(vendor_name, country=None):
    """Screen a vendor via the OpenSanctions /match endpoint.

    Sends a structured entity (schema + name + optional country) and classifies
    the scored candidates into Clear / Warning / Critical. The returned dict also
    carries the driving match's score/id/caption so the caller (audit log) can
    persist a trimmed summary without re-querying.
    """
    critical, warning = _thresholds()

    properties = {"name": [vendor_name]}
    if country:
        # Monday's country column yields a display name (e.g. "Ukraine"); the
        # matcher handles names and ISO codes, so pass it through as-is.
        properties["country"] = [country]

    body = {
        "queries": {
            "vendor": {
                "schema": os.getenv("MATCH_SCHEMA") or MATCH_SCHEMA_DEFAULT,
                "properties": properties,
            }
        }
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{OPENSANCTIONS_BASE_URL}/match/default",
            json=body,
            headers={"Authorization": f"ApiKey {os.getenv('OPENSANCTIONS_API_KEY')}"},
        )
    response.raise_for_status()

    results = (((response.json().get("responses") or {}).get("vendor") or {}).get("results")) or []

    classification = _classify(results, critical, warning)
    if classification is None:
        return _clear_result()

    level, match = classification
    score_pct = round(_score(match) * 100)
    profile_url = f"https://www.opensanctions.org/entities/{match['id']}/"
    label = (
        "Possible direct sanction match"
        if level == "CRITICAL"
        else "Possible PEP or sanction-related flag"
    )
    return {
        "riskLevel": RISK_LEVEL[level],
        "details": with_disclaimer(
            f"{label}: {match.get('caption')} ({score_pct}% match). Profile: {profile_url}"
        ),
        "score": _score(match),
        "matchId": match.get("id"),
        "matchCaption": match.get("caption"),
    }


async def check_vendor_with_retry(vendor_name, country=None, retries=3, delay_seconds=2.0):
    """Wrap match_vendor with retry logic for transient OpenSanctions failures.

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
            return await match_vendor(vendor_name, country)
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
