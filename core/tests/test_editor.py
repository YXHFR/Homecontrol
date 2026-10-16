"""Kartredigerarens API: hemmet och bilderna av planritningarna."""

import pytest
from fastapi.testclient import TestClient

from homecontrol.app import create_app
from homecontrol.editor import MAX_HOME_BYTES, MAX_IMAGE_BYTES, MemoryStore
from homecontrol.home import Home
from homecontrol.homefile import to_document
from homecontrol.images import StoredImage
from homes import FLOOR, one_room_home, room

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 32
WEBP = b"RIFF\0\0\0\0WEBPVP8 " + b"\0" * 32


def client(store: MemoryStore | None = None) -> TestClient:
    # TestClient anropar kärnan som http://testserver.
    app = create_app(allowed_hosts={"testserver"}, home=one_room_home(), home_store=store)
    return TestClient(app)


def with_kitchen() -> Home:
    """Enrumshemmet med ett nyritat kök bredvid vardagsrummet."""
    home = one_room_home()
    floor = home.floors[0]
    kitchen = room("kok", "Kök", 5, 0, 3, 4)
    return home.model_copy(
        update={"floors": (floor.model_copy(update={"rooms": (*floor.rooms, kitchen)}),)}
    )


def test_home_is_read_in_the_same_form_as_the_file() -> None:
    response = client().get("/api/home")

    assert response.status_code == 200
    assert response.json() == to_document(one_room_home())


def test_saved_home_is_stored_and_used_by_the_core() -> None:
    store = MemoryStore()
    with client(store) as c:
        response = c.put("/api/home", json=to_document(with_kitchen()))

        assert response.status_code == 204
        assert store.home == with_kitchen()
        assert c.get("/api/home").json() == to_document(with_kitchen())
        assert c.app.state.controller.home == with_kitchen()  # type: ignore[attr-defined]


def test_live_view_gets_the_new_home_without_restart() -> None:
    with client() as c, c.websocket_connect("/api/live") as ws:
        assert ws.receive_json()["type"] == "home"
        assert ws.receive_json()["type"] == "state"

        c.put("/api/home", json=to_document(with_kitchen()))

        home = ws.receive_json()
        assert home["type"] == "home"
        assert [r["id"] for r in home["floors"][0]["rooms"]] == ["vardagsrum", "kok"]
        state = ws.receive_json()
        assert [r["id"] for r in state["rooms"]] == ["vardagsrum", "kok"]


def test_invalid_home_is_refused_with_an_explanation_and_nothing_changes() -> None:
    store = MemoryStore()
    broken = to_document(one_room_home())
    broken["home"]["floors"][0]["rooms"][0]["name"] = ""
    with client(store) as c:
        response = c.put("/api/home", json=broken)

        assert response.status_code == 422
        assert "får inte vara tomt" in response.json()["detail"]
        assert store.home is None
        assert c.get("/api/home").json() == to_document(one_room_home())


@pytest.mark.parametrize("body", [b"inte json", b"\xff\xfe", b"[]"])
def test_garbage_is_refused(body: bytes) -> None:
    assert client().put("/api/home", content=body).status_code == 422


def test_too_large_home_is_refused() -> None:
    response = client().put("/api/home", content=b" " * (MAX_HOME_BYTES + 1))

    assert response.status_code == 413


def test_failing_store_gives_an_error_and_the_core_keeps_the_old_home() -> None:
    class BrokenStore(MemoryStore):
        def save_home(self, home: Home) -> None:
            raise OSError("databasen svarar inte")

    with client(BrokenStore()) as c:
        response = c.put("/api/home", json=to_document(with_kitchen()))

        assert response.status_code == 503
        assert c.get("/api/home").json() == to_document(one_room_home())


@pytest.mark.parametrize(
    ("data", "media_type"), [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")]
)
def test_plan_image_is_stored_and_served(data: bytes, media_type: str) -> None:
    with client() as c:
        # Formatet avgörs av innehållet, inte av vad avsändaren påstår.
        put = c.put(
            f"/api/floors/{FLOOR}/plan-image?width=500",
            content=data,
            headers={"content-type": "text/html"},
        )
        get = c.get(f"/api/floors/{FLOOR}/plan-image")

    assert put.status_code == 204
    assert get.status_code == 200
    assert get.content == data
    assert get.headers["content-type"] == media_type
    assert get.headers["x-content-type-options"] == "nosniff"
    assert get.headers["cross-origin-resource-policy"] == "same-origin"


def test_plan_image_can_be_removed() -> None:
    with client() as c:
        c.put(f"/api/floors/{FLOOR}/plan-image?width=500", content=PNG)

        assert c.delete(f"/api/floors/{FLOOR}/plan-image").status_code == 204
        assert c.get(f"/api/floors/{FLOOR}/plan-image").status_code == 404


def test_only_images_are_accepted() -> None:
    response = client().put(
        f"/api/floors/{FLOOR}/plan-image?width=500", content=b"<svg onload=alert(1)>"
    )

    assert response.status_code == 415


def test_too_large_image_is_refused() -> None:
    response = client().put(
        f"/api/floors/{FLOOR}/plan-image?width=500", content=PNG + b"\0" * MAX_IMAGE_BYTES
    )

    assert response.status_code == 413


def test_image_for_unknown_floor_is_refused() -> None:
    with client() as c:
        assert c.put("/api/floors/vinden/plan-image?width=500", content=PNG).status_code == 404
        assert c.get("/api/floors/vinden/plan-image").status_code == 404


def test_images_of_removed_floors_are_forgotten() -> None:
    store = MemoryStore()
    store.images = {"gammal": StoredImage("image/png", PNG), FLOOR: StoredImage("image/png", PNG)}

    store.save_home(one_room_home())

    assert set(store.images) == {FLOOR}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/home"),
        ("PUT", "/api/home"),
        ("GET", f"/api/floors/{FLOOR}/plan-image"),
        ("PUT", f"/api/floors/{FLOOR}/plan-image?width=500"),
        ("DELETE", f"/api/floors/{FLOOR}/plan-image"),
    ],
)
def test_other_sites_may_not_read_or_change_the_home(method: str, path: str) -> None:
    store = MemoryStore()
    c = client(store)

    other_origin = c.request(
        method, path, headers={"origin": "http://elak.example"}, json=to_document(with_kitchen())
    )
    rebinding = c.request(
        method, path, headers={"host": "elak.example"}, json=to_document(with_kitchen())
    )

    assert other_origin.status_code == 403
    assert rebinding.status_code == 403
    assert store.home is None


def test_same_origin_may_save() -> None:
    response = client().put(
        "/api/home", json=to_document(with_kitchen()), headers={"origin": "http://testserver"}
    )

    assert response.status_code == 204


def test_uploaded_image_is_placed_on_the_floor_together_with_the_home() -> None:
    store = MemoryStore()
    with client(store) as c:
        c.put(f"/api/floors/{FLOOR}/plan-image?width=400", content=PNG)

        plan = c.get("/api/home").json()["home"]["floors"][0]["plan_image"]
        assert plan == {"position": [0, 0], "meters_per_pixel": 0.05}
        assert store.home is not None
        assert store.home.floors[0].plan_image is not None
        assert set(store.images) == {FLOOR}

        c.delete(f"/api/floors/{FLOOR}/plan-image")

        assert c.get("/api/home").json()["home"]["floors"][0]["plan_image"] is None
        assert store.images == {}


def test_image_without_width_is_refused() -> None:
    assert client().put(f"/api/floors/{FLOOR}/plan-image", content=PNG).status_code == 422


def test_saving_over_someone_elses_change_is_refused() -> None:
    with client() as c:
        first = c.get("/api/home").headers["etag"]
        # Ett annat fönster sparar först.
        saved = c.put("/api/home", json=to_document(with_kitchen()), headers={"if-match": first})
        assert saved.status_code == 204

        stale = c.put("/api/home", json=to_document(one_room_home()), headers={"if-match": first})

        assert stale.status_code == 409
        assert "annat fönster" in stale.json()["detail"]
        assert c.get("/api/home").json() == to_document(with_kitchen())
        latest = c.get("/api/home").headers["etag"]
        assert latest == saved.headers["etag"] != first
        again = c.put("/api/home", json=to_document(one_room_home()), headers={"if-match": latest})
        assert again.status_code == 204


def test_home_is_private_to_the_page() -> None:
    headers = client().get("/api/home").headers

    assert headers["cross-origin-resource-policy"] == "same-origin"
    assert headers["cache-control"] == "no-store"
