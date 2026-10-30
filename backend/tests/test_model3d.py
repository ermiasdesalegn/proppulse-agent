"""The 3D model of a unit: building it from rooms, reading a plan, the dashboard endpoints, and sending it to a buyer who asks for details."""
import base64
import json
import struct
from types import SimpleNamespace

import httpx
import pytest

from app.main import create_app
from app.model3d import Layout, PlanRead, PlanRoom, Room, layout_from_plan, pack_rooms, render_png, room_kind, to_glb
from app.projects import ProjectService
from tests.agent_helpers import FakeGateway, ext, make_agent, make_chat, make_proactive, make_services, new_buyer
from tests.test_agent_graph import BOLE_3BR
from tests.test_proactive import NOAH, pid

ADMIN = {"X-Admin-Key": "test-admin-key"}
PNG = bytes.fromhex("89504e470d0a1a0a") + b"\0" * 32

ROOMS = [("Living room", 5.5, 4.2), ("Kitchen", 3.2, 3.0), ("Master bedroom", 4.2, 3.6), ("Bath", 2.4, 1.8), ("Balcony", 4.0, 1.6)]


def glb_parts(data: bytes) -> tuple[dict, bytes]:
    magic, version, length = struct.unpack("<4sII", data[:12])
    assert (magic, version, length) == (b"glTF", 2, len(data))
    json_len, json_type = struct.unpack("<I4s", data[12:20])
    assert json_type == b"JSON"
    doc = json.loads(data[20:20 + json_len])
    bin_len, bin_type = struct.unpack("<I4s", data[20 + json_len:28 + json_len])
    assert bin_type == b"BIN\0" and 28 + json_len + bin_len == len(data)
    return doc, data[28 + json_len:]


# ---------------- building a layout ----------------

def test_rooms_with_only_sizes_are_placed_in_rows_without_overlap():
    layout = pack_rooms(ROOMS, title="Test")
    assert len(layout.rooms) == 5 and layout.total_area == round(sum(w * d for _, w, d in ROOMS), 1)
    for a in layout.rooms:
        assert a.x + a.w <= layout.width_m + 1e-6 and a.y + a.d <= layout.depth_m + 1e-6
        for b in layout.rooms:
            if a is not b:
                apart = a.x + a.w <= b.x + 1e-6 or b.x + b.w <= a.x + 1e-6 or a.y + a.d <= b.y + 1e-6 or b.y + b.d <= a.y + 1e-6
                assert apart, f"{a.name} overlaps {b.name}"


def test_the_outline_always_contains_every_room():
    layout = Layout(width_m=3, depth_m=3, rooms=[Room(name="Hall", x=2, y=2, w=6, d=5)])
    assert (layout.width_m, layout.depth_m) == (8, 7)


@pytest.mark.parametrize("name,kind", [("Master Bedroom", "bedroom"), ("WC", "bath"), ("Open kitchen", "kitchen"), ("Balcony", "balcony"), ("Shop G-M7", "shop"), ("Storage", "other")])
def test_room_kinds_come_from_the_name(name, kind):
    assert room_kind(name) == kind


def test_a_plan_read_with_dimensions_uses_them_and_one_without_uses_the_unit_area():
    read = PlanRead(rooms=[PlanRoom(name="Living", left=0, top=0, right=0.5, bottom=1), PlanRoom(name="Bed", left=0.5, top=0, right=1, bottom=1)],
                    overall_width_m=10, overall_depth_m=8)
    measured = layout_from_plan(read)
    assert (measured.width_m, measured.depth_m) == (10, 8) and measured.rooms[0].w == 5
    guessed = layout_from_plan(read.model_copy(update={"overall_width_m": None, "overall_depth_m": None, "width_to_depth": 1.0}), sqm=100)
    assert round(guessed.total_area) == 100


def test_a_plan_with_no_usable_rooms_is_refused_and_rooms_outside_the_drawing_are_clipped():
    with pytest.raises(ValueError, match="no rooms"):
        layout_from_plan(PlanRead(rooms=[PlanRoom(name="Speck", left=0.1, top=0.1, right=0.11, bottom=0.11)]))
    clipped = layout_from_plan(PlanRead(rooms=[PlanRoom(name="Big", left=-0.5, top=0, right=1.5, bottom=1)], overall_width_m=10, overall_depth_m=10))
    assert clipped.rooms[0].x == 0 and clipped.rooms[0].w == 10


# ---------------- the files ----------------

def test_the_glb_is_a_valid_binary_gltf_whose_indices_stay_inside_the_vertices():
    doc, binary = glb_parts(to_glb(pack_rooms(ROOMS, title="Test")))
    assert doc["asset"]["version"] == "2.0" and doc["buffers"][0]["byteLength"] == len(binary)
    for primitive in doc["meshes"][0]["primitives"]:
        position = doc["accessors"][primitive["attributes"]["POSITION"]]
        indices = doc["accessors"][primitive["indices"]]
        view = doc["bufferViews"][indices["bufferView"]]
        values = struct.unpack(f"<{indices['count']}I", binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]])
        assert max(values) < position["count"] and indices["count"] % 3 == 0


def test_the_same_layout_always_gives_the_same_files():
    layout = pack_rooms(ROOMS)
    assert to_glb(layout) == to_glb(layout) and render_png(layout, width=400, height=250) == render_png(layout, width=400, height=250)


def test_the_picture_is_a_png_of_the_requested_size():
    from io import BytesIO

    from PIL import Image

    picture = render_png(pack_rooms(ROOMS, title="Test"), width=640, height=400)
    assert picture.startswith(bytes.fromhex("89504e470d0a1a0a"))
    assert Image.open(BytesIO(picture)).size == (640, 400)


# ---------------- the dashboard endpoints ----------------

pytestmark = pytest.mark.usefixtures("quiet_world")


class PlanGateway(FakeGateway):
    async def describe_image(self, image, *, mime_type, prompt, schema, system=None):
        self.calls.append(("image", f"{len(image)} bytes"))
        if getattr(self, "fail_image", False):
            raise RuntimeError("vision down")
        assert schema is PlanRead
        return PlanRead(rooms=[PlanRoom(name="Living room", left=0, top=0, right=0.6, bottom=1), PlanRoom(name="Bedroom", left=0.6, top=0, right=1, bottom=1)],
                        overall_width_m=10, overall_depth_m=8)


async def client(repo, gateway=None):
    gateway = gateway or PlanGateway()
    chat, services, _ = make_chat(repo, gateway, admin_api_key="test-admin-key")
    proactive, _, _ = make_proactive(repo, services=services, admin_api_key="test-admin-key")
    app = create_app(use_lifespan=False)
    app.state.settings, app.state.repo, app.state.chat, app.state.proactive = chat.settings, repo, chat, proactive
    app.state.projects = ProjectService(repo, gateway, services, chat.settings, proactive=proactive)
    app.state.ingest = SimpleNamespace(gateway=gateway)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/layouts/pack"), ("GET", "/api/properties/00000000-0000-0000-0000-000000000000/layout"),
    ("PUT", "/api/properties/00000000-0000-0000-0000-000000000000/layout"), ("DELETE", "/api/properties/00000000-0000-0000-0000-000000000000/layout"),
    ("POST", "/api/properties/00000000-0000-0000-0000-000000000000/layout/read-plan"),
    ("GET", "/api/properties/00000000-0000-0000-0000-000000000000/model.glb"), ("GET", "/api/properties/00000000-0000-0000-0000-000000000000/preview.png"),
])
async def test_3d_endpoints_need_the_admin_key(spy_repo, method, path):
    async with await client(spy_repo) as c:
        assert (await c.request(method, path, json={})).status_code == 401


async def test_save_read_build_and_delete_a_layout(spy_repo, db_conn):
    unit = str(await pid(db_conn, NOAH))
    async with await client(spy_repo) as c:
        assert (await c.get(f"/api/properties/{unit}/layout", headers=ADMIN)).json() == {"layout": None}
        assert (await c.get(f"/api/properties/{unit}/model.glb", headers=ADMIN)).status_code == 404, "no layout, no model"

        packed = (await c.post("/api/layouts/pack", headers=ADMIN, json={"title": "Noah 3BR", "rooms": [{"name": n, "w": w, "d": d} for n, w, d in ROOMS]})).json()
        assert len(packed["rooms"]) == 5
        assert (await c.put(f"/api/properties/{unit}/layout", headers=ADMIN, json=packed)).status_code == 200
        assert len((await c.get(f"/api/properties/{unit}/layout", headers=ADMIN)).json()["layout"]["rooms"]) == 5

        glb = await c.get(f"/api/properties/{unit}/model.glb", headers=ADMIN)
        assert glb.status_code == 200 and glb.headers["content-type"] == "model/gltf-binary" and ".glb" in glb.headers["content-disposition"]
        glb_parts(glb.content)
        png = await c.get(f"/api/properties/{unit}/preview.png", headers=ADMIN)
        assert png.status_code == 200 and png.content.startswith(bytes.fromhex("89504e470d0a1a0a"))

        assert (await c.delete(f"/api/properties/{unit}/layout", headers=ADMIN)).json() == {"deleted": True}
        assert (await c.get(f"/api/properties/{unit}/preview.png", headers=ADMIN)).status_code == 404


async def test_bad_layouts_and_unknown_units_are_refused(spy_repo, db_conn):
    unit = str(await pid(db_conn, NOAH))
    async with await client(spy_repo) as c:
        assert (await c.put(f"/api/properties/{unit}/layout", headers=ADMIN, json={"rooms": []})).status_code == 422
        assert (await c.put(f"/api/properties/{unit}/layout", headers=ADMIN, json={"rooms": [{"name": "x", "x": 0, "y": 0, "w": -1, "d": 3}]})).status_code == 422
        assert (await c.post("/api/layouts/pack", headers=ADMIN, json={"rooms": [{"name": "Hall", "w": 0, "d": 3}]})).status_code == 422
        missing = "00000000-0000-0000-0000-000000000001"
        assert (await c.get(f"/api/properties/{missing}/layout", headers=ADMIN)).status_code == 404
        assert (await c.put(f"/api/properties/{missing}/layout", headers=ADMIN, json={"rooms": [{"name": "Hall", "x": 0, "y": 0, "w": 3, "d": 3}]})).status_code == 404


async def test_reading_a_plan_returns_rooms_to_check_and_saves_nothing(spy_repo, db_conn):
    unit = str(await pid(db_conn, NOAH))
    body = {"image_base64": base64.b64encode(PNG).decode(), "mime": "image/png"}
    gateway = PlanGateway()
    async with await client(spy_repo, gateway) as c:
        read = await c.post(f"/api/properties/{unit}/layout/read-plan", headers=ADMIN, json=body)
        assert read.status_code == 200 and [r["name"] for r in read.json()["rooms"]] == ["Living room", "Bedroom"]
        assert (await c.get(f"/api/properties/{unit}/layout", headers=ADMIN)).json() == {"layout": None}, "the broker checks it before it is saved"

        assert (await c.post(f"/api/properties/{unit}/layout/read-plan", headers=ADMIN, json={**body, "image_base64": base64.b64encode(b"not an image").decode()})).status_code == 422
        gateway.fail_image = True
        failed = await c.post(f"/api/properties/{unit}/layout/read-plan", headers=ADMIN, json=body)
        assert failed.status_code == 502 and "enter the rooms by hand" in failed.json()["detail"]


# ---------------- the buyer ----------------

async def test_a_buyer_who_asks_for_details_gets_the_3d_view_and_file_when_the_unit_has_a_layout(spy_repo, db_conn, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR)], vector=await vector_of("Noah Bole Towers 3BR%"))
    services = make_services()
    agent, buyer = make_agent(spy_repo, gw, services=services), await new_buyer(spy_repo, "p3d")
    inbound = lambda text: dict(buyer_id=str(buyer["id"]), thread_id=buyer["thread_id"], inbound={"kind": "text", "text": text}, chat_id=4242)  # noqa: E731
    found = await agent.handle(**inbound("3BR in Bole"))
    first = found.state["active_matches"][0]

    plain = await agent.handle(**inbound("Tell me more about option 1"))
    assert "3D" not in plain.reply and not services.telegram.photos and not services.telegram.documents, "no layout saved, nothing is sent"

    await spy_repo.set_layout(first["id"], pack_rooms(ROOMS, title=first["title"]).model_dump(mode="json"))
    shown = await agent.handle(**inbound("Tell me more about option 1"))
    assert "3D view of the layout" in shown.reply
    (photo,), (document,) = services.telegram.photos, services.telegram.documents
    assert photo["chat_id"] == 4242 and photo["photo_bytes"] > 1000 and first["title"] in photo["caption"]
    assert document["chat_id"] == 4242 and document["filename"].endswith(".glb")
    glb_parts(document["content"])


async def test_a_failure_while_sending_the_3d_view_never_spoils_the_details(spy_repo, vector_of):
    gw = FakeGateway([ext(**BOLE_3BR)], vector=await vector_of("Noah Bole Towers 3BR%"))
    services = make_services()
    agent, buyer = make_agent(spy_repo, gw, services=services), await new_buyer(spy_repo, "p3d-fail")
    inbound = lambda text: dict(buyer_id=str(buyer["id"]), thread_id=buyer["thread_id"], inbound={"kind": "text", "text": text}, chat_id=4242)  # noqa: E731
    first = (await agent.handle(**inbound("3BR in Bole"))).state["active_matches"][0]
    await spy_repo.set_layout(first["id"], pack_rooms(ROOMS).model_dump(mode="json"))

    async def broken(*a, **k):
        raise RuntimeError("telegram down")

    services.telegram.send_photo_bytes = broken
    out = await agent.handle(**inbound("Tell me more about option 1"))
    assert "Payment plan:" in out.reply and "3D view" not in out.reply, "the details still arrive, and the buyer is not told something that did not happen"
