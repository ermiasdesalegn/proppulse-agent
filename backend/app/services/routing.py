from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import permutations
from math import asin, cos, radians, sin, sqrt
from typing import Protocol, Sequence

import httpx

log = logging.getLogger(__name__)

ROAD_FACTOR = 1.3  # straight-line to road distance
AVG_SPEED_KMH = 25.0  # Addis Ababa city traffic


@dataclass(frozen=True)
class Stop:
    property_id: str
    lat: float
    lng: float


@dataclass(frozen=True)
class RoutePlan:
    order: list[str]
    geometry: str
    distance_meters: int
    duration_seconds: int
    bbox: list[float]  # [min_lng, min_lat, max_lng, max_lat]

    def to_json(self) -> dict:
        """Shape stored in tours_and_appointments.mapbox_route_json."""
        return {
            "geometry": self.geometry,
            "distance_meters": self.distance_meters,
            "duration_seconds": self.duration_seconds,
            "bbox": self.bbox,
        }


class RoutingService(Protocol):
    async def plan_route(self, stops: Sequence[Stop], *, origin: tuple[float, float] | None = None) -> RoutePlan:
        """Order the stops for the shortest drive. origin is (lat, lng) of the starting point."""


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lat1, lng1), (lat2, lng2) = a, b
    dlat, dlng = radians(lat2 - lat1), radians(lng2 - lng1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(h))


def encode_polyline(points: Sequence[tuple[float, float]], precision: int = 5) -> str:
    """Google/Mapbox encoded polyline of (lat, lng) points."""
    factor, out, prev_lat, prev_lng = 10**precision, [], 0, 0
    for lat, lng in points:
        ilat, ilng = round(lat * factor), round(lng * factor)
        for delta in (ilat - prev_lat, ilng - prev_lng):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_lat, prev_lng = ilat, ilng
    return "".join(out)


class RoutingError(Exception):
    pass


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Inverse of encode_polyline: (lat, lng) pairs."""
    factor, points, index, lat, lng = 10**precision, [], 0, 0, 0
    while index < len(encoded):
        for axis in (0, 1):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lng += delta
        points.append((lat / factor, lng / factor))
    return points


def _bbox(points: Sequence[tuple[float, float]]) -> list[float]:
    lats, lngs = [p[0] for p in points], [p[1] for p in points]
    return [min(lngs), min(lats), max(lngs), max(lats)]


def best_order(durations: list[list[float | None]], stops: list[int], origin: int | None) -> list[int]:
    """Visiting order of `stops` (indices into the matrix) with the least total driving time. Exhaustive up to 8 stops."""
    inf = float("inf")
    cost = lambda a, b: durations[a][b] if durations[a][b] is not None else inf  # noqa: E731
    if len(stops) > 8:  # too many to try every order: always go to the nearest unvisited stop
        order, here, left = [], origin if origin is not None else stops[0], list(stops)
        if origin is None:
            order.append(left.pop(0))
            here = order[0]
        while left:
            nxt = min(left, key=lambda s: cost(here, s))
            left.remove(nxt)
            order.append(nxt)
            here = nxt
        return order
    best, best_cost = list(stops), inf
    for perm in permutations(stops):
        legs = list(zip(([origin] if origin is not None else []) + list(perm[:-1]), perm if origin is not None else perm[1:]))
        total = sum(cost(a, b) for a, b in legs)
        if total < best_cost:
            best, best_cost = list(perm), total
    return best


class MapboxRoutingService:
    """Orders the stops with the Matrix API (shortest total drive), then draws the route with the Directions API."""

    BASE = "https://api.mapbox.com"
    MAX_POINTS = 25  # the Matrix API's limit for the driving profile

    def __init__(self, token: str, *, transport: httpx.AsyncBaseTransport | None = None, profile: str = "driving"):
        self._token, self._transport, self._profile = token, transport, profile

    async def _get(self, path: str, **params: str) -> dict:
        url = f"{self.BASE}{path}"
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=20) as client:
                res = await client.get(url, params={**params, "access_token": self._token})
        except httpx.HTTPError as exc:
            # Never chain the original error: its message contains the URL, and the URL contains the token.
            raise RoutingError(f"could not reach Mapbox: {type(exc).__name__}") from None
        if res.status_code != 200:
            raise RoutingError(f"Mapbox returned {res.status_code}")
        body = res.json()
        if body.get("code") != "Ok":
            raise RoutingError(f"Mapbox said {body.get('code')}: {str(body.get('message', ''))[:120]}")
        return body

    @staticmethod
    def _coords(points: Sequence[tuple[float, float]]) -> str:
        return ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)

    async def plan_route(self, stops: Sequence[Stop], *, origin: tuple[float, float] | None = None) -> RoutePlan:
        if not stops:
            raise ValueError("at least one stop is required")
        points = ([origin] if origin is not None else []) + [(s.lat, s.lng) for s in stops]
        if len(points) > self.MAX_POINTS:
            raise ValueError(f"at most {self.MAX_POINTS} points can be routed at once")
        if len(points) == 1:  # one stop and nowhere to start from: there is no drive to plan
            return RoutePlan([stops[0].property_id], encode_polyline(points), 0, 0, _bbox(points))

        offset = 1 if origin is not None else 0
        if len(stops) == 1:
            order = [0]
        else:
            matrix = await self._get(f"/directions-matrix/v1/mapbox/{self._profile}/{self._coords(points)}", annotations="duration")
            order = [i - offset for i in best_order(matrix["durations"], [offset + i for i in range(len(stops))], 0 if origin is not None else None)]
        ordered_points = ([origin] if origin is not None else []) + [points[offset + i] for i in order]
        route = (await self._get(f"/directions/v5/mapbox/{self._profile}/{self._coords(ordered_points)}", geometries="polyline", overview="full"))["routes"][0]
        return RoutePlan(
            order=[stops[i].property_id for i in order],
            geometry=route["geometry"],
            distance_meters=round(route["distance"]),
            duration_seconds=round(route["duration"]),
            bbox=_bbox(decode_polyline(route["geometry"])),
        )


class ResilientRoutingService:
    """Use the primary service; if it fails for any reason, estimate with the fallback so a booking never fails on routing."""

    def __init__(self, primary: RoutingService, fallback: RoutingService):
        self._primary, self._fallback = primary, fallback

    async def plan_route(self, stops: Sequence[Stop], *, origin: tuple[float, float] | None = None) -> RoutePlan:
        try:
            return await self._primary.plan_route(stops, origin=origin)
        except Exception as exc:
            log.warning("routing service failed (%s); using the local estimate", type(exc).__name__)
            return await self._fallback.plan_route(stops, origin=origin)


class MockRoutingService:
    """Nearest-neighbour ordering on straight-line distance. Same output shape as the Mapbox implementation."""

    async def plan_route(self, stops: Sequence[Stop], *, origin: tuple[float, float] | None = None) -> RoutePlan:
        if not stops:
            raise ValueError("at least one stop is required")
        remaining = list(stops)
        here = origin if origin is not None else (remaining[0].lat, remaining[0].lng)
        path = [here] if origin is not None else []
        ordered: list[Stop] = []
        while remaining:
            nxt = min(remaining, key=lambda s: haversine_m(here, (s.lat, s.lng)))
            remaining.remove(nxt)
            ordered.append(nxt)
            here = (nxt.lat, nxt.lng)
            path.append(here)

        straight = sum(haversine_m(a, b) for a, b in zip(path, path[1:]))
        meters = round(straight * ROAD_FACTOR)
        seconds = round(meters / 1000 / AVG_SPEED_KMH * 3600)
        lats, lngs = [p[0] for p in path], [p[1] for p in path]
        return RoutePlan(
            order=[s.property_id for s in ordered],
            geometry=encode_polyline(path),
            distance_meters=meters,
            duration_seconds=seconds,
            bbox=[min(lngs), min(lats), max(lngs), max(lats)],
        )
