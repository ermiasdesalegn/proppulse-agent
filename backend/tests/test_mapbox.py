import httpx
import pytest

from app.services.routing import (
    MapboxRoutingService,
    MockRoutingService,
    ResilientRoutingService,
    RoutingError,
    Stop,
    best_order,
    decode_polyline,
    encode_polyline,
)

A, B, C = Stop("a", 9.0147, 38.7636), Stop("b", 8.9950, 38.7860), Stop("c", 9.0235, 38.8690)
TOKEN = "pk.SECRET-TOKEN-VALUE"


class FakeMapbox:
    def __init__(self, durations=None, route_points=None, distance=15400.4, duration=3200.2, status=200, code="Ok"):
        self.requests = []
        self.durations = durations
        self.route_points = route_points or [(9.0147, 38.7636), (8.9950, 38.7860), (9.0235, 38.8690)]
        self.distance, self.duration, self.status, self.code = distance, duration, status, code

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "Not Authorized - Invalid Token"})
        if "directions-matrix" in req.url.path:
            return httpx.Response(200, json={"code": self.code, "durations": self.durations})
        return httpx.Response(200, json={"code": self.code, "routes": [{"geometry": encode_polyline(self.route_points), "distance": self.distance, "duration": self.duration}]})

    def path(self, i):
        return self.requests[i].url.path


def service(fake):
    return MapboxRoutingService(TOKEN, transport=httpx.MockTransport(fake))


# matrix indices: 0=A 1=B 2=C. A->B 100, A->C 900, B->C 120, B->A 100, C->A 900, C->B 120. Best open path: A,B,C (220).
DURATIONS = [[0, 100, 900], [100, 0, 120], [900, 120, 0]]


async def test_orders_stops_by_shortest_total_drive_then_draws_that_route():
    # Rows follow the order the stops are given in: 0=C, 1=A, 2=B. A->B 100 and B->C 120 make A,B,C the cheapest path (220);
    # starting from C or going C->A costs 900.
    given_order = [[0, 900, 900], [900, 0, 100], [120, 100, 0]]
    fake = FakeMapbox(given_order)
    plan = await service(fake).plan_route([C, A, B])  # given in a poor order on purpose
    assert plan.order == ["a", "b", "c"]
    assert plan.distance_meters == 15400 and plan.duration_seconds == 3200
    assert decode_polyline(plan.geometry)[0] == pytest.approx((9.0147, 38.7636), abs=1e-5)
    assert plan.bbox == pytest.approx([38.7636, 8.9950, 38.8690, 9.0235], abs=1e-5), "[min lng, min lat, max lng, max lat]"
    assert set(plan.to_json()) == {"geometry", "distance_meters", "duration_seconds", "bbox"}

    assert fake.path(0).startswith("/directions-matrix/v1/mapbox/driving/") and fake.path(1).startswith("/directions/v5/mapbox/driving/")
    matrix_coords = fake.path(0).rsplit("/", 1)[1]
    assert matrix_coords == "38.869000,9.023500;38.763600,9.014700;38.786000,8.995000", "longitude first, six decimals, in the order given"
    route_coords = fake.path(1).rsplit("/", 1)[1]
    assert route_coords == "38.763600,9.014700;38.786000,8.995000;38.869000,9.023500", "directions use the chosen order"
    assert all(r.url.params["access_token"] == TOKEN for r in fake.requests)
    assert fake.requests[1].url.params["geometries"] == "polyline" and fake.requests[1].url.params["overview"] == "full"


async def test_a_starting_point_is_fixed_first_and_shapes_the_order():
    office = (9.0000, 38.7500)
    # origin(0), then stops 1=A 2=B 3=C. From the office, C is closest, then B, then A.
    durations = [[0, 800, 500, 100], [800, 0, 100, 900], [500, 100, 0, 120], [100, 900, 120, 0]]
    fake = FakeMapbox(durations)
    plan = await service(fake).plan_route([A, B, C], origin=office)
    assert plan.order == ["c", "b", "a"]
    assert fake.path(0).rsplit("/", 1)[1].startswith("38.750000,9.000000;"), "the office is the first point"
    assert fake.path(1).rsplit("/", 1)[1].startswith("38.750000,9.000000;38.869000,9.023500;")


async def test_one_stop_and_no_start_needs_no_network_at_all():
    fake = FakeMapbox()
    plan = await service(fake).plan_route([A])
    assert plan.order == ["a"] and plan.distance_meters == 0 and plan.duration_seconds == 0 and fake.requests == []


async def test_one_stop_from_an_office_skips_the_matrix_and_asks_only_for_directions():
    fake = FakeMapbox(route_points=[(9.0, 38.75), (9.0147, 38.7636)], distance=2100.0, duration=420.0)
    plan = await service(fake).plan_route([A], origin=(9.0, 38.75))
    assert plan.order == ["a"] and plan.distance_meters == 2100 and len(fake.requests) == 1 and "directions/v5" in fake.path(0)


async def test_too_many_points_is_refused_up_front():
    stops = [Stop(str(i), 9.0 + i / 1000, 38.7) for i in range(26)]
    with pytest.raises(ValueError, match="at most 25"):
        await service(FakeMapbox()).plan_route(stops)
    with pytest.raises(ValueError):
        await service(FakeMapbox()).plan_route([])


# ---------------- ordering logic ----------------

def test_best_order_finds_the_cheapest_open_path_and_treats_unreachable_legs_as_infinite():
    assert best_order(DURATIONS, [0, 1, 2], None) == [0, 1, 2]
    assert best_order(DURATIONS, [1, 2], origin=0) == [1, 2]
    blocked = [[0, None, 5], [None, 0, 5], [5, 5, 0]]
    assert best_order(blocked, [0, 1, 2], None) in ([0, 2, 1], [1, 2, 0], [2, 0, 1][::-1], [0, 2, 1])
    assert best_order([[0, None], [None, 0]], [0, 1], None) == [0, 1], "nothing reachable: keep the given order"


def test_best_order_with_many_stops_still_visits_every_stop_once():
    n = 10
    matrix = [[abs(i - j) * 10 for j in range(n)] for i in range(n)]
    order = best_order(matrix, list(range(n)), None)
    assert sorted(order) == list(range(n)) and order == list(range(n)), "points on a line are visited in line order"


def test_polyline_round_trip():
    points = [(9.0147, 38.7636), (8.995, 38.786), (9.0235, 38.869)]
    assert [pytest.approx(p, abs=1e-5) for p in decode_polyline(encode_polyline(points))] == points
    assert decode_polyline("") == []


# ---------------- failures never expose the token ----------------

@pytest.mark.parametrize("kwargs,match", [({"status": 401}, "401"), ({"status": 429}, "429"), ({"code": "NoRoute", "durations": DURATIONS}, "NoRoute")])
async def test_mapbox_errors_become_routing_errors_without_the_token(kwargs, match):
    with pytest.raises(RoutingError, match=match) as exc:
        await service(FakeMapbox(**kwargs)).plan_route([A, B])
    assert TOKEN not in str(exc.value)


async def test_a_network_failure_is_a_routing_error_that_hides_the_token_bearing_url():
    def down(req):
        raise httpx.ConnectError(f"cannot connect to {req.url}", request=req)

    with pytest.raises(RoutingError) as exc:
        await MapboxRoutingService(TOKEN, transport=httpx.MockTransport(down)).plan_route([A, B])
    assert TOKEN not in str(exc.value) and exc.value.__cause__ is None


# ---------------- fallback ----------------

async def test_a_failing_mapbox_falls_back_to_the_local_estimate_so_bookings_continue():
    resilient = ResilientRoutingService(service(FakeMapbox(status=500)), MockRoutingService())
    plan = await resilient.plan_route([A, B, C])
    assert sorted(plan.order) == ["a", "b", "c"] and plan.distance_meters > 0


async def test_a_working_mapbox_is_used_as_is():
    fake = FakeMapbox(DURATIONS)
    plan = await ResilientRoutingService(service(fake), MockRoutingService()).plan_route([A, B, C])
    assert plan.distance_meters == 15400 and len(fake.requests) == 2
