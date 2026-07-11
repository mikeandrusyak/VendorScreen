import httpx
import pytest
import respx

from sanctions_service import (
    DISCLAIMER,
    RISK_LEVEL,
    SanctionsUnavailableError,
    check_vendor_with_retry,
    match_vendor,
    unavailable_result,
    with_disclaimer,
)

MATCH_URL = "https://api.opensanctions.org/match/default"


def _mock(results):
    """Build a /match response with the given candidate list under the single
    query id ("vendor") that match_vendor sends."""
    return httpx.Response(200, json={"responses": {"vendor": {"results": results}}})


def _entity(id, caption, score, *, datasets=None, topics=None):
    return {
        "id": id,
        "caption": caption,
        "score": score,
        "datasets": datasets or [],
        "properties": {"topics": topics or []},
    }


def test_with_disclaimer_appends_notice():
    assert with_disclaimer("hello") == f"hello{DISCLAIMER}"


@respx.mock
async def test_no_matches_is_clear():
    respx.post(MATCH_URL).mock(return_value=_mock([]))

    result = await match_vendor("Totally Clean LLC")

    assert result["riskLevel"] == RISK_LEVEL["CLEAR"]
    assert DISCLAIMER in result["details"]
    assert result["score"] is None
    assert result["matchId"] is None


@respx.mock
async def test_high_score_sanction_is_critical():
    respx.post(MATCH_URL).mock(
        return_value=_mock([_entity("ent-1", "Bad Actor", 0.95, datasets=["us_ofac_sanctions"])])
    )

    result = await match_vendor("Bad Actor")

    assert result["riskLevel"] == RISK_LEVEL["CRITICAL"]
    assert result["matchId"] == "ent-1"
    assert "95% match" in result["details"]
    assert "ent-1" in result["details"]


@respx.mock
async def test_low_score_sanction_is_warning_not_critical():
    # A sanction dataset hit that only weakly matches the name is a Warning to
    # review, not a hard Critical — the score gate is what separates them.
    respx.post(MATCH_URL).mock(
        return_value=_mock(
            [_entity("ent-9", "Weak Namesake", 0.75, datasets=["us_ofac_sanctions"])]
        )
    )

    result = await match_vendor("Weak Namesake")

    assert result["riskLevel"] == RISK_LEVEL["WARNING"]
    assert result["matchId"] == "ent-9"


@respx.mock
async def test_pep_dataset_is_warning():
    respx.post(MATCH_URL).mock(
        return_value=_mock([_entity("ent-2", "Politician", 0.9, datasets=["everypolitician_peps"])])
    )

    result = await match_vendor("Politician")

    assert result["riskLevel"] == RISK_LEVEL["WARNING"]


@respx.mock
async def test_poi_topic_is_warning():
    respx.post(MATCH_URL).mock(
        return_value=_mock(
            [_entity("ent-3", "Person Of Interest", 0.88, datasets=["some_list"], topics=["poi"])]
        )
    )

    result = await match_vendor("Person Of Interest")

    assert result["riskLevel"] == RISK_LEVEL["WARNING"]


@respx.mock
async def test_below_warning_threshold_is_clear():
    # A weak, unflagged candidate is a namesake, not a hit.
    respx.post(MATCH_URL).mock(
        return_value=_mock([_entity("ent-4", "Namesake Corp", 0.4, datasets=["us_ofac_sanctions"])])
    )

    result = await match_vendor("Namesake Corp")

    assert result["riskLevel"] == RISK_LEVEL["CLEAR"]


@respx.mock
async def test_strong_match_without_flags_is_clear():
    # High score but no sanction/PEP signal → still Clear (namesake).
    respx.post(MATCH_URL).mock(
        return_value=_mock([_entity("ent-5", "Common Name Ltd", 0.97, datasets=["some_list"])])
    )

    result = await match_vendor("Common Name Ltd")

    assert result["riskLevel"] == RISK_LEVEL["CLEAR"]


@respx.mock
async def test_country_is_sent_in_query():
    route = respx.post(MATCH_URL).mock(return_value=_mock([]))

    await match_vendor("Acme", country="Ukraine")

    sent = route.calls.last.request
    import json

    body = json.loads(sent.content)
    props = body["queries"]["vendor"]["properties"]
    assert props["name"] == ["Acme"]
    assert props["country"] == ["Ukraine"]


@respx.mock
async def test_default_schema_matches_people_and_companies():
    # Default schema must be LegalEntity so a board of individuals is never
    # silently missed (querying a person under "Company" returns nothing).
    route = respx.post(MATCH_URL).mock(return_value=_mock([]))

    await match_vendor("Acme")

    import json

    body = json.loads(route.calls.last.request.content)
    assert body["queries"]["vendor"]["schema"] == "LegalEntity"


@respx.mock
async def test_schema_override_via_env(monkeypatch):
    monkeypatch.setenv("MATCH_SCHEMA", "Person")
    route = respx.post(MATCH_URL).mock(return_value=_mock([]))

    await match_vendor("Jane Doe")

    import json

    body = json.loads(route.calls.last.request.content)
    assert body["queries"]["vendor"]["schema"] == "Person"


@respx.mock
async def test_thresholds_are_env_tunable(monkeypatch):
    # Lowering the critical threshold promotes a mid-score sanction to Critical.
    monkeypatch.setenv("MATCH_SCORE_CRITICAL", "0.70")
    respx.post(MATCH_URL).mock(
        return_value=_mock([_entity("ent-7", "Mid Score Co", 0.72, datasets=["us_ofac_sanctions"])])
    )

    result = await match_vendor("Mid Score Co")

    assert result["riskLevel"] == RISK_LEVEL["CRITICAL"]


@respx.mock
async def test_retry_recovers_after_429():
    route = respx.post(MATCH_URL)
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "0"}),
        _mock([]),
    ]

    result = await check_vendor_with_retry("Rate Limited Co", retries=3, delay_seconds=0)

    assert result["riskLevel"] == RISK_LEVEL["CLEAR"]
    assert route.call_count == 2


@respx.mock
async def test_retry_gives_up_and_raises_unavailable():
    respx.post(MATCH_URL).mock(return_value=httpx.Response(429, headers={"retry-after": "0"}))

    with pytest.raises(SanctionsUnavailableError):
        await check_vendor_with_retry("Always Limited", retries=2, delay_seconds=0)


@respx.mock
async def test_retry_recovers_after_500():
    route = respx.post(MATCH_URL)
    route.side_effect = [httpx.Response(503), _mock([])]

    result = await check_vendor_with_retry("Flaky Server Co", retries=3, delay_seconds=0)

    assert result["riskLevel"] == RISK_LEVEL["CLEAR"]
    assert route.call_count == 2


@respx.mock
async def test_persistent_5xx_raises_unavailable():
    respx.post(MATCH_URL).mock(return_value=httpx.Response(502))

    with pytest.raises(SanctionsUnavailableError):
        await check_vendor_with_retry("Always Down", retries=2, delay_seconds=0)


@respx.mock
async def test_timeout_raises_unavailable():
    respx.post(MATCH_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))

    with pytest.raises(SanctionsUnavailableError):
        await check_vendor_with_retry("Slow Service", retries=2, delay_seconds=0)


@respx.mock
async def test_connection_error_recovers_on_retry():
    route = respx.post(MATCH_URL)
    route.side_effect = [httpx.ConnectError("refused"), _mock([])]

    result = await check_vendor_with_retry("Blip Co", retries=3, delay_seconds=0)

    assert result["riskLevel"] == RISK_LEVEL["CLEAR"]
    assert route.call_count == 2


@respx.mock
async def test_non_retryable_4xx_propagates():
    # A 400/401 is a real error, not a transient outage — surface it as-is.
    respx.post(MATCH_URL).mock(return_value=httpx.Response(401))

    with pytest.raises(httpx.HTTPStatusError):
        await check_vendor_with_retry("Bad Key Co", retries=3, delay_seconds=0)


def test_unavailable_result_is_not_clear():
    result = unavailable_result()
    assert result["riskLevel"] == RISK_LEVEL["UNAVAILABLE"]
    assert result["riskLevel"] != RISK_LEVEL["CLEAR"]
    assert DISCLAIMER in result["details"]
