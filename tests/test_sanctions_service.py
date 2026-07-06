import httpx
import respx

from sanctions_service import (
    DISCLAIMER,
    RISK_LEVEL,
    check_vendor,
    check_vendor_with_retry,
    with_disclaimer,
)

SEARCH_URL = "https://api.opensanctions.org/search/default"


def _mock(results):
    return httpx.Response(200, json={"results": results})


def test_with_disclaimer_appends_notice():
    assert with_disclaimer("hello") == f"hello{DISCLAIMER}"


@respx.mock
async def test_no_matches_is_clear():
    respx.get(SEARCH_URL).mock(return_value=_mock([]))

    result = await check_vendor("Totally Clean LLC")

    assert result["riskLevel"] == RISK_LEVEL["CLEAR"]
    assert DISCLAIMER in result["details"]


@respx.mock
async def test_sanctions_dataset_is_critical():
    respx.get(SEARCH_URL).mock(
        return_value=_mock(
            [{"id": "ent-1", "caption": "Bad Actor", "datasets": ["us_ofac_sanctions"]}]
        )
    )

    result = await check_vendor("Bad Actor")

    assert result["riskLevel"] == RISK_LEVEL["CRITICAL"]
    assert "ent-1" in result["details"]


@respx.mock
async def test_pep_dataset_is_warning():
    respx.get(SEARCH_URL).mock(
        return_value=_mock(
            [{"id": "ent-2", "caption": "Politician", "datasets": ["everypolitician_peps"]}]
        )
    )

    result = await check_vendor("Politician")

    assert result["riskLevel"] == RISK_LEVEL["WARNING"]


@respx.mock
async def test_poi_topic_is_warning():
    respx.get(SEARCH_URL).mock(
        return_value=_mock(
            [
                {
                    "id": "ent-3",
                    "caption": "Person Of Interest",
                    "datasets": ["some_list"],
                    "properties": {"topics": ["poi"]},
                }
            ]
        )
    )

    result = await check_vendor("Person Of Interest")

    assert result["riskLevel"] == RISK_LEVEL["WARNING"]


@respx.mock
async def test_non_critical_match_is_clear():
    respx.get(SEARCH_URL).mock(
        return_value=_mock([{"id": "ent-4", "caption": "Namesake Corp", "datasets": ["some_list"]}])
    )

    result = await check_vendor("Namesake Corp")

    assert result["riskLevel"] == RISK_LEVEL["CLEAR"]
    assert "Namesake Corp" in result["details"]


@respx.mock
async def test_retry_recovers_after_429():
    route = respx.get(SEARCH_URL)
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "0"}),
        _mock([]),
    ]

    result = await check_vendor_with_retry("Rate Limited Co", retries=3, delay_seconds=0)

    assert result["riskLevel"] == RISK_LEVEL["CLEAR"]
    assert route.call_count == 2


@respx.mock
async def test_retry_gives_up_and_raises():
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(429, headers={"retry-after": "0"}))

    try:
        await check_vendor_with_retry("Always Limited", retries=2, delay_seconds=0)
    except httpx.HTTPStatusError as err:
        assert err.response.status_code == 429
    else:
        raise AssertionError("expected HTTPStatusError after exhausting retries")
