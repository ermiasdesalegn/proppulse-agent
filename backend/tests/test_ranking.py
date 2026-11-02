"""'The cheapest', 'the most luxurious', 'the best rental income': asked for by rule, not by similarity."""
import pytest

from tests.agent_helpers import FakeGateway, ext, make_agent, new_buyer, say_to


async def titles(repo, ranking, **kw):
    kw = {"max_budget_etb": None, "min_bedrooms": None, "sub_cities": None, **kw}
    return [m.title for m in await repo.rank_properties(ranking, **kw)]


async def test_each_ranking_orders_by_its_rule(repo):
    assert (await titles(repo, "cheapest"))[0] == "Ayat Square Studios Studio"
    assert (await titles(repo, "priciest"))[:2] == ["Noah Bole Penthouse Penthouse 4BR", "Ayat Hill Villas 4BR Villa"]
    assert (await titles(repo, "highest_yield"))[:2] == ["Gift Kazanchis Suites Studio", "Ayat Square Studios Studio"]
    assert (await titles(repo, "largest"))[:2] == ["Noah Bole Penthouse Penthouse 4BR", "Ayat Hill Villas 4BR Villa"]


async def test_a_ranking_respects_the_buyers_limits(repo):
    assert (await titles(repo, "cheapest", min_bedrooms=3))[0] == "Noah Sarbet Gardens 3BR Apartment"
    assert (await titles(repo, "priciest", max_budget_etb=15_000_000))[0] == "Ayat Garden Residences 3BR Townhouse"
    assert await titles(repo, "cheapest", sub_cities=["Bole"], max_budget_etb=1_000_000) == []
    assert set(await titles(repo, "largest", sub_cities=["Kazanchis"])) == {"Gift Kazanchis Suites Studio", "Noah Kazanchis Plaza 1BR Apartment"}


async def test_only_available_units_are_ranked_and_the_count_is_respected(repo):
    await repo.as_platform()._fetch("update properties set status = 'UNAVAILABLE' where title = 'Ayat Square Studios Studio' returning id")
    assert (await titles(repo, "cheapest"))[0] == "Gift Kazanchis Suites Studio"
    assert len(await repo.rank_properties("cheapest", max_budget_etb=None, min_bedrooms=None, sub_cities=None, count=2)) == 2


async def test_a_ranking_name_is_never_trusted_as_sql(repo):
    with pytest.raises(KeyError):
        await repo.rank_properties("price_etb; drop table properties", max_budget_etb=None, min_bedrooms=None, sub_cities=None)


async def test_the_agent_ranks_without_any_embedding_or_threshold(spy_repo):
    gw = FakeGateway([ext("search", ranking="cheapest", search_query="the cheapest home")])
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "rank-cheapest")
    res = await say_to(agent, buyer, "what is the cheapest thing you have?")
    assert res.state["active_matches"][0]["title"] == "Ayat Square Studios Studio"
    assert [k for k, _ in gw.calls if k == "embed"] == [], "ranking does not need a query vector"
    assert spy_repo.searches == [], "and does not go through the similarity search"
    assert "1. Ayat Square Studios Studio" in res.reply


async def test_a_ranking_still_widens_when_the_buyers_limits_leave_nothing(spy_repo):
    gw = FakeGateway([ext("search", ranking="cheapest", max_budget_etb=3_300_000, preferred_sub_cities=["Bole"])])
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "rank-widen")
    res = await say_to(agent, buyer, "cheapest in Bole under 3.3M")
    assert res.state["search_scope"]["relaxation_step"] == 2 and res.state["active_matches"][0]["title"] == "Ayat Square Studios Studio"


async def test_a_normal_search_after_a_ranking_goes_back_to_similarity(spy_repo, vector_of):
    gw = FakeGateway([ext("search", ranking="cheapest"), ext("search", max_budget_etb=25_000_000, min_bedrooms=3, preferred_sub_cities=["Bole"])],
                     vector=await vector_of("Noah Bole Towers 3BR%"))
    agent, buyer = make_agent(spy_repo, gw), await new_buyer(spy_repo, "rank-then-search")
    await say_to(agent, buyer, "cheapest")
    res = await say_to(agent, buyer, "3 bedrooms in Bole please")
    assert res.state["ranking"] is None and len(spy_repo.searches) == 1 and "Noah Bole Towers 3BR Apartment" in [m["title"] for m in res.state["active_matches"]]
