"""A 3D model of a unit, built from its room layout.

The layout is a list of rooms with their position and size in metres. From it this module builds two things, with no model call and no
randomness (the same layout always gives the same files):

 - a binary glTF (.glb) file, which opens in any 3D viewer, on a phone, and in Blender;
 - a picture of the model seen from above at an angle, for chat.

Reading a layout from a floor-plan image is the only step that uses the language model (see `layout_from_plan`); everything after that is code,
so the model can read a plan wrongly, but it can never invent a room in the picture that is not in the saved layout.
"""
from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass, field
from io import BytesIO

from pydantic import BaseModel, Field, model_validator

WALL_THICKNESS = 0.15
SLAB_THICKNESS = 0.12
MAX_ROOMS = 40

KIND_WORDS: list[tuple[str, re.Pattern[str]]] = [
    ("bath", re.compile(r"bath|wc|toilet|shower|lavatory|restroom|ሽንት", re.I)),
    ("kitchen", re.compile(r"kitchen|pantry|ወጥ", re.I)),
    ("bedroom", re.compile(r"bed|master|maid|guest|ማደሪያ|መኝታ", re.I)),
    ("living", re.compile(r"living|lounge|salon|sitting|dining|reception|ሳሎን", re.I)),
    ("balcony", re.compile(r"balcon|terrace|veranda|garden|ጓሮ|በረንዳ", re.I)),
    ("corridor", re.compile(r"corridor|circulation|hall|lobby|stair|lift|elevator|ramp|entrance|ኮሪደር", re.I)),
    ("shop", re.compile(r"shop|office|store|retail|showroom|ሱቅ", re.I)),
]
# floor colour and the colour of the room's name tag, as (r, g, b)
FLOORS = {
    "bath": (150, 196, 214), "kitchen": (232, 196, 140), "bedroom": (184, 168, 214), "living": (170, 208, 170),
    "balcony": (140, 190, 120), "corridor": (214, 214, 208), "shop": (236, 170, 150), "other": (206, 200, 190),
}
WALL = (246, 244, 240)


def room_kind(name: str) -> str:
    for kind, pattern in KIND_WORDS:
        if pattern.search(name):
            return kind
    return "other"


class Room(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    x: float = Field(ge=0, le=200, description="metres from the left edge of the plan")
    y: float = Field(ge=0, le=200, description="metres from the top edge of the plan")
    w: float = Field(gt=0.4, le=100, description="width in metres")
    d: float = Field(gt=0.4, le=100, description="depth in metres")

    @property
    def area(self) -> float:
        return self.w * self.d


class Layout(BaseModel):
    title: str = Field(default="", max_length=80)
    width_m: float = Field(default=10, gt=1, le=200)
    depth_m: float = Field(default=10, gt=1, le=200)
    wall_height_m: float = Field(default=2.8, ge=2.0, le=6.0)
    rooms: list[Room] = Field(min_length=1, max_length=MAX_ROOMS)

    @model_validator(mode="after")
    def _cover_every_room(self) -> Layout:
        # The outline always contains every room, so a slightly wrong overall size cannot cut a room off.
        self.width_m = round(max(self.width_m, *(r.x + r.w for r in self.rooms)), 2)
        self.depth_m = round(max(self.depth_m, *(r.y + r.d for r in self.rooms)), 2)
        return self

    @property
    def total_area(self) -> float:
        return round(sum(r.area for r in self.rooms), 1)


# ---------------------------------------------------------------- building a layout


def pack_rooms(rooms: list[tuple[str, float, float]], *, title: str = "") -> Layout:
    """Place rooms that have a size but no position in rows, so the broker can type 'Bedroom 4 x 3.5' and get a model."""
    if not rooms:
        raise ValueError("at least one room is needed")
    total = sum(w * d for _, w, d in rooms)
    row_limit = max(math.sqrt(total * 1.5), max(w for _, w, _ in rooms))
    placed: list[Room] = []
    x = y = row_depth = 0.0
    for name, w, d in rooms:
        if x > 0 and x + w > row_limit:
            x, y, row_depth = 0.0, y + row_depth, 0.0
        placed.append(Room(name=name, x=round(x, 2), y=round(y, 2), w=w, d=d))
        x += w
        row_depth = max(row_depth, d)
    return Layout(title=title, rooms=placed)


class PlanRoom(BaseModel):
    name: str = Field(description="What the room is called on the plan, for example 'Living room', 'Bedroom 1', 'Bath', 'Shop G-M7'")
    left: float = Field(description="Left edge of the room, 0 at the left edge of the drawn plan and 1 at its right edge")
    top: float = Field(description="Top edge of the room, 0 at the top edge of the drawn plan and 1 at its bottom edge")
    right: float = Field(description="Right edge of the room, same scale as left")
    bottom: float = Field(description="Bottom edge of the room, same scale as top")


class PlanRead(BaseModel):
    """What the vision model reads from a floor-plan image."""

    rooms: list[PlanRoom] = Field(default_factory=list, description="Every separate room or space drawn on the plan, at most 40")
    overall_width_m: float | None = Field(default=None, description="Width of the whole drawn plan in metres if a dimension or scale shows it, otherwise null")
    overall_depth_m: float | None = Field(default=None, description="Depth of the whole drawn plan in metres if a dimension or scale shows it, otherwise null")
    width_to_depth: float = Field(default=1.3, description="Width of the drawn plan divided by its depth, as it looks on the page")


PLAN_PROMPT = (
    "This is an architectural floor plan. List every separate room or space on it with its name and where it is on the drawing, "
    "as fractions of the drawn plan (not of the whole photo): left, top, right, bottom, each between 0 and 1. "
    "Use the names written on the plan. If the plan shows dimensions or a scale, give the overall width and depth in metres, otherwise leave them null. "
    "Also give the width divided by the depth of the drawn plan. Do not invent rooms that are not drawn."
)


def layout_from_plan(read: PlanRead, *, sqm: float | None = None, title: str = "") -> Layout:
    """Turn what the model read into metres: from the dimensions on the plan if it had them, otherwise from the unit's stated area."""
    rooms = [r for r in read.rooms if r.right - r.left > 0.015 and r.bottom - r.top > 0.015][:MAX_ROOMS]
    if not rooms:
        raise ValueError("no rooms could be read from this plan")
    if read.overall_width_m and read.overall_depth_m and 1 < read.overall_width_m <= 200 and 1 < read.overall_depth_m <= 200:
        width, depth = read.overall_width_m, read.overall_depth_m
    else:
        aspect = min(max(read.width_to_depth, 0.4), 3.0)
        area = sqm if sqm and sqm > 4 else 80.0
        width = math.sqrt(area * aspect)
        depth = area / width
    clamp = lambda v: min(max(v, 0.0), 1.0)  # noqa: E731
    out = []
    for r in rooms:
        left, top, right, bottom = clamp(r.left), clamp(r.top), clamp(r.right), clamp(r.bottom)
        w, d = (right - left) * width, (bottom - top) * depth
        if w > 0.4 and d > 0.4:
            out.append(Room(name=r.name.strip()[:40] or "Room", x=round(left * width, 2), y=round(top * depth, 2), w=round(w, 2), d=round(d, 2)))
    if not out:
        raise ValueError("no rooms could be read from this plan")
    return Layout(title=title, width_m=round(width, 2), depth_m=round(depth, 2), rooms=out)


# ---------------------------------------------------------------- geometry shared by both outputs


@dataclass(frozen=True)
class Box:
    """Axis-aligned box in plan coordinates: x and y along the plan, z up, all in metres."""
    x0: float
    y0: float
    z0: float
    x1: float
    y1: float
    z1: float
    colour: tuple[int, int, int]


def boxes(layout: Layout, *, wall_height: float | None = None) -> list[Box]:
    height = wall_height if wall_height is not None else layout.wall_height_m
    out = [Box(0, 0, -SLAB_THICKNESS, layout.width_m, layout.depth_m, 0, WALL)]  # the base slab under everything
    for r in layout.rooms:
        out.append(Box(r.x, r.y, 0, r.x + r.w, r.y + r.d, 0.02, FLOORS[room_kind(r.name)]))
    t = WALL_THICKNESS
    seen: set[tuple] = set()
    for r in layout.rooms:
        for wall in ((r.x - t / 2, r.y - t / 2, r.x + r.w + t / 2, r.y + t / 2), (r.x - t / 2, r.y + r.d - t / 2, r.x + r.w + t / 2, r.y + r.d + t / 2),
                     (r.x - t / 2, r.y - t / 2, r.x + t / 2, r.y + r.d + t / 2), (r.x + r.w - t / 2, r.y - t / 2, r.x + r.w + t / 2, r.y + r.d + t / 2)):
            key = tuple(round(v, 1) for v in wall)
            if key not in seen:  # a wall two rooms share is built once
                seen.add(key)
                out.append(Box(wall[0], wall[1], 0, wall[2], wall[3], height, WALL))
    return out


# ---------------------------------------------------------------- glTF (.glb)


def _quad_faces(b: Box) -> list[tuple[tuple[float, float, float], list[tuple[float, float, float]]]]:
    """Six faces of a box as (outward normal, four corners), in glTF axes: x along the plan, y up, z along the plan's depth."""
    x0, x1, y0, y1, z0, z1 = b.x0, b.x1, b.z0, b.z1, b.y0, b.y1  # glTF y is up (our z), glTF z is our y
    faces = [
        ((1, 0, 0), [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
        ((-1, 0, 0), [(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)]),
        ((0, 1, 0), [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)]),
        ((0, -1, 0), [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]),
        ((0, 0, 1), [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
        ((0, 0, -1), [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)]),
    ]
    fixed = []
    for normal, c in faces:
        a = tuple(c[1][i] - c[0][i] for i in range(3))
        d = tuple(c[3][i] - c[0][i] for i in range(3))
        cross = (a[1] * d[2] - a[2] * d[1], a[2] * d[0] - a[0] * d[2], a[0] * d[1] - a[1] * d[0])
        if sum(cross[i] * normal[i] for i in range(3)) < 0:
            c = [c[0], c[3], c[2], c[1]]  # wind counter-clockwise as seen from outside
        fixed.append((normal, c))
    return fixed


def to_glb(layout: Layout) -> bytes:
    by_colour: dict[tuple[int, int, int], list[Box]] = {}
    for b in boxes(layout):
        by_colour.setdefault(b.colour, []).append(b)

    binary = bytearray()
    views, accessors, primitives, materials = [], [], [], []
    for colour, group in by_colour.items():
        positions: list[float] = []
        normals: list[float] = []
        indices: list[int] = []
        for b in group:
            for normal, corners in _quad_faces(b):
                base = len(positions) // 3
                for c in corners:
                    positions += c
                    normals += normal
                indices += [base, base + 1, base + 2, base, base + 2, base + 3]
        pos_bytes = struct.pack(f"<{len(positions)}f", *positions)
        nor_bytes = struct.pack(f"<{len(normals)}f", *normals)
        idx_bytes = struct.pack(f"<{len(indices)}I", *indices)
        xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
        first = len(accessors)
        for data, target in ((pos_bytes, 34962), (nor_bytes, 34962), (idx_bytes, 34963)):
            views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data), "target": target})
            binary += data
        accessors += [
            {"bufferView": len(views) - 3, "componentType": 5126, "count": len(positions) // 3, "type": "VEC3", "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]},
            {"bufferView": len(views) - 2, "componentType": 5126, "count": len(normals) // 3, "type": "VEC3"},
            {"bufferView": len(views) - 1, "componentType": 5125, "count": len(indices), "type": "SCALAR"},
        ]
        materials.append({"pbrMetallicRoughness": {"baseColorFactor": [c / 255 for c in colour] + [1.0], "metallicFactor": 0.0, "roughnessFactor": 0.9}, "doubleSided": False})
        primitives.append({"attributes": {"POSITION": first, "NORMAL": first + 1}, "indices": first + 2, "material": len(materials) - 1})

    doc = {
        "asset": {"version": "2.0", "generator": "PropPulse AI"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": layout.title or "Unit"}],
        "meshes": [{"primitives": primitives}], "materials": materials, "accessors": accessors, "bufferViews": views,
        "buffers": [{"byteLength": len(binary)}],
    }
    body = json.dumps(doc, separators=(",", ":")).encode()
    body += b" " * (-len(body) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(body) + 8 + len(binary)
    return b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I4s", len(body), b"JSON") + body + struct.pack("<I4s", len(binary), b"BIN\0") + bytes(binary)


# ---------------------------------------------------------------- picture


@dataclass
class _Canvas:
    scale: float = 1.0
    ox: float = 0.0
    oy: float = 0.0
    points: list[tuple[float, float]] = field(default_factory=list)

    def project(self, x: float, y: float, z: float) -> tuple[float, float]:
        return ((x - y) * math.cos(math.radians(30)) * self.scale + self.ox, (x + y) * 0.5 * self.scale - z * self.scale + self.oy)


def _shade(colour: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * factor))) for c in colour)  # type: ignore[return-value]


def render_png(layout: Layout, *, width: int = 1600, height: int = 1000) -> bytes:
    """The model seen from a corner, with the walls cut low so every room can be seen, and each room named with its size."""
    from PIL import Image, ImageDraw, ImageFont

    ss = 2  # draw at twice the size and shrink, for smooth edges
    W, H = width * ss, height * ss
    cut = min(layout.wall_height_m * 0.3, 0.9)
    items = boxes(layout, wall_height=cut)

    probe = _Canvas()
    xs, ys = [], []
    for b in items:
        for x in (b.x0, b.x1):
            for y in (b.y0, b.y1):
                for z in (b.z0, b.z1):
                    px, py = probe.project(x, y, z)
                    xs.append(px)
                    ys.append(py)
    margin_top, margin_bottom, margin_side = 110 * ss, 90 * ss, 70 * ss
    scale = min((W - 2 * margin_side) / (max(xs) - min(xs)), (H - margin_top - margin_bottom) / (max(ys) - min(ys)))
    cv = _Canvas(scale=scale, ox=margin_side - min(xs) * scale + ((W - 2 * margin_side) - (max(xs) - min(xs)) * scale) / 2,
                 oy=margin_top - min(ys) * scale + ((H - margin_top - margin_bottom) - (max(ys) - min(ys)) * scale) / 2)

    img = Image.new("RGB", (W, H), (24, 28, 36))
    draw = ImageDraw.Draw(img)

    def font(size: int):
        try:
            return ImageFont.load_default(size=size * ss)
        except Exception:
            return ImageFont.load_default()

    # the slab first, then every floor, then the walls from back to front, so a floor is never painted over a wall in front of it
    layer = lambda b: 0 if b.z1 <= 0 else 1 if b.z1 <= 0.05 else 2  # noqa: E731
    for b in sorted(items, key=lambda b: (layer(b), (b.x0 + b.x1) / 2 + (b.y0 + b.y1) / 2)):
        top = [cv.project(b.x0, b.y0, b.z1), cv.project(b.x1, b.y0, b.z1), cv.project(b.x1, b.y1, b.z1), cv.project(b.x0, b.y1, b.z1)]
        right = [cv.project(b.x1, b.y0, b.z1), cv.project(b.x1, b.y1, b.z1), cv.project(b.x1, b.y1, b.z0), cv.project(b.x1, b.y0, b.z0)]
        left = [cv.project(b.x0, b.y1, b.z1), cv.project(b.x1, b.y1, b.z1), cv.project(b.x1, b.y1, b.z0), cv.project(b.x0, b.y1, b.z0)]
        draw.polygon(right, fill=_shade(b.colour, 0.78))
        draw.polygon(left, fill=_shade(b.colour, 0.62))
        draw.polygon(top, fill=b.colour)

    name_font, size_font = font(21), font(17)
    for r in layout.rooms:
        cx, cy = cv.project(r.x + r.w / 2, r.y + r.d / 2, 0.05)
        label = r.name if len(r.name) <= 22 else r.name[:21] + "…"
        for text, f, dy in ((label, name_font, -14 * ss), (f"{r.w:.1f} x {r.d:.1f} m", size_font, 10 * ss)):
            draw.text((cx, cy + dy), text, font=f, fill=(24, 28, 36), anchor="mm", stroke_width=3 * ss, stroke_fill=(255, 255, 255))

    head, foot = font(34), font(19)
    draw.text((margin_side, 52 * ss), layout.title or "3D view", font=head, fill=(255, 255, 255), anchor="lm")
    draw.text((W - margin_side, 52 * ss), f"{layout.total_area:g} sqm in {len(layout.rooms)} rooms", font=foot, fill=(170, 178, 192), anchor="rm")
    draw.text((margin_side, H - 42 * ss), "Illustrative 3D view built from the floor plan. Walls are cut low so every room shows. Sizes are approximate.",
              font=foot, fill=(140, 148, 162), anchor="lm")

    out = BytesIO()
    img.resize((width, height), Image.LANCZOS).save(out, format="PNG", optimize=True)
    return out.getvalue()
