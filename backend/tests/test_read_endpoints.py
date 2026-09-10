"""Эндпоинты чтения. Проверяется контракт и SQL, а не правдоподобие данных."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import API_PREFIX, app

pytestmark = pytest.mark.usefixtures("seeded")

client = TestClient(app)


def get(path: str, **params: Any) -> Any:
    response = client.get(f"{API_PREFIX}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- журнал прогнозов ---


def test_journal_returns_the_envelope() -> None:
    body = get("/predictions")
    assert set(body) == {"items", "page", "pageSize", "total"}
    assert body["total"] == 5
    assert body["pageSize"] == 50


def test_journal_sorts_by_computed_at_descending_by_default() -> None:
    stamps = [item["computedAt"] for item in get("/predictions")["items"]]
    assert stamps == sorted(stamps, reverse=True)


def test_repeated_direction_means_any_of() -> None:
    body = get("/predictions", direction=["SENSOR_FAILURE", "FIRE_RISK"])
    assert body["total"] == 4


def test_level_and_status_filter_together() -> None:
    body = get("/predictions", level=["CRITICAL"], status=["NEW"])
    assert [item["id"] for item in body["items"]] == ["P-1"]


def test_district_filters_by_the_facility() -> None:
    assert get("/predictions", district="SAO")["total"] == 2


def test_to_covers_the_whole_last_day() -> None:
    # Прогноз P-1 посчитан в 12:00. Фильтр по его дате обязан его вернуть.
    body = get("/predictions", **{"from": "2026-09-10", "to": "2026-09-10"})
    assert "P-1" in [item["id"] for item in body["items"]]


def test_sort_by_probability_ascending() -> None:
    values = [item["probability"] for item in get("/predictions", sort="probability:asc")["items"]]
    assert values == sorted(values)


def test_unknown_sort_field_is_not_an_error() -> None:
    assert get("/predictions", sort="нетакогополя:desc")["total"] == 5


def test_paging_splits_the_list() -> None:
    first = get("/predictions", page=1, pageSize=2)
    second = get("/predictions", page=2, pageSize=2)
    assert len(first["items"]) == 2
    assert first["total"] == second["total"] == 5
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_prediction_carries_its_order_id() -> None:
    items = {item["id"]: item for item in get("/predictions")["items"]}
    assert items["P-1"]["orderId"] == "O-1"
    assert items["P-2"].get("orderId") is None


def test_facility_is_embedded_not_an_id() -> None:
    item = get("/predictions")["items"][0]
    assert item["facility"]["address"]
    assert item["facility"]["district"]


# --- карточка прогноза ---


def test_card_returns_blocks_and_actions() -> None:
    body = get("/predictions/P-1")
    assert [block["type"] for block in body["blocks"]] == ["factors", "timeseries"]
    assert [action["code"] for action in body["actions"]] == [
        "confirm_order",
        "inspect",
        "reject",
    ]


def test_actions_depend_on_the_status() -> None:
    assert [a["code"] for a in get("/predictions/P-3")["actions"]] == ["reject"]
    assert get("/predictions/P-4")["actions"] == []


def test_broken_blocks_do_not_break_the_card() -> None:
    # Направление UNAUTHORIZED_ACCESS отдаёт мусор намеренно.
    body = get("/predictions/P-4")
    types = [block["type"] for block in body["blocks"]]
    assert types == ["factors", "experimental_heatmap"]


def test_unknown_prediction_answers_404_with_detail() -> None:
    response = client.get(f"{API_PREFIX}/predictions/нет-такого")
    assert response.status_code == 404
    assert "не найден" in response.json()["detail"]


def test_timeseries_marks_the_moment_of_the_prediction() -> None:
    body = get("/predictions/P-1/timeseries")
    assert body["markerAt"] == get("/predictions/P-1")["computedAt"]
    assert body["series"] == []


# --- карта ---


def test_facilities_answer_geojson_with_lon_first() -> None:
    body = get("/facilities")
    assert body["type"] == "FeatureCollection"
    point = body["features"][0]["geometry"]["coordinates"]
    assert 37 < point[0] < 38, "первым обязан идти lon"
    assert 55 < point[1] < 56


def test_one_point_per_facility_takes_the_highest_probability() -> None:
    features = get("/facilities")["features"]
    by_id = {f["properties"]["facilityId"]: f["properties"] for f in features}
    assert len(features) == 3
    assert by_id["F-1"]["predictionId"] == "P-1"


def test_map_properties_carry_the_popup_fields() -> None:
    properties = get("/facilities")["features"][0]["properties"]
    assert {"facilityId", "predictionId", "direction", "level", "probability"} <= set(properties)
    assert properties["address"] and properties["collector"]


def test_bbox_keeps_the_edges() -> None:
    body = get("/facilities", bbox="37.61,55.75,37.62,55.76")
    ids = {f["properties"]["facilityId"] for f in body["features"]}
    assert ids == {"F-1"}, "точка ровно на краю обязана попасть в ответ"


def test_broken_bbox_returns_everything() -> None:
    assert len(get("/facilities", bbox="мусор")["features"]) == 3


def test_empty_bbox_result_is_valid() -> None:
    assert get("/facilities", bbox="1,1,2,2")["features"] == []


def test_lines_come_from_their_own_endpoint() -> None:
    body = get("/facilities/lines")
    assert body["features"][0]["geometry"]["type"] == "LineString"
    assert body["features"][0]["properties"]["collector"]


def test_one_facility_by_id() -> None:
    assert get("/facilities/F-2")["address"] == "Арбат, 12"
    assert client.get(f"{API_PREFIX}/facilities/нет").status_code == 404


# --- заявки ---


def test_orders_return_the_envelope_sorted_by_due_date() -> None:
    body = get("/orders")
    assert body["total"] == 4
    dates = [item["dueAt"] for item in body["items"]]
    assert dates == sorted(dates)


def test_orders_filter_by_status_and_due_date() -> None:
    assert get("/orders", status=["DONE"])["total"] == 1
    # Срок O-4 истёк 10-го, O-1 и O-2 наступают 11-го, O-3 — 12-го.
    assert get("/orders", dueBefore="2026-09-10")["total"] == 1
    assert get("/orders", dueBefore="2026-09-11")["total"] == 3


def test_order_actions_follow_the_lifecycle() -> None:
    items = {item["id"]: item for item in get("/orders")["items"]}
    assert [a["code"] for a in items["O-1"]["actions"]] == ["confirm", "reject"]
    assert [a["code"] for a in items["O-2"]["actions"]] == ["start"]
    assert [a["code"] for a in items["O-3"]["actions"]] == ["close"]
    assert items["O-4"]["actions"] == []


def test_close_form_asks_for_the_direction_reasons() -> None:
    order = get("/orders/O-3")
    close = order["actions"][0]
    cause = next(field for field in close["fields"] if field["name"] == "actualCause")
    # Заявка O-3 висит на прогнозе направления UNAUTHORIZED_ACCESS.
    assert cause["optionsRef"] == "UNAUTHORIZED_ACCESS"
    confirmed = next(f for f in close["fields"] if f["name"] == "predictionConfirmed")
    assert confirmed["type"] == "boolean" and confirmed["required"]


def test_closed_order_carries_its_outcome() -> None:
    outcome = get("/orders/O-4")["outcome"]
    assert outcome["predictionConfirmed"] is True
    assert outcome["actualCause"] == "HOT_WORKS"


def test_unknown_order_answers_404() -> None:
    assert client.get(f"{API_PREFIX}/orders/нет").status_code == 404
