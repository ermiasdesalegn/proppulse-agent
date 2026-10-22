from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Protocol, Sequence

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
