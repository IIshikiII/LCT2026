"""Роли: что видно и что можно. ADR 0007.

Границы взяты из фикстуры. Объекты `F-1` и `F-2` лежат в комплексе `K-CAO-1`
района `CAO`, объект `F-3` — в комплексе `K-SAO-1` района `SAO`. Значит:

- техник видит два объекта своего комплекса,
- диспетчер района видит те же два объекта своего района,
- диспетчер ОДС и группа реагирования видят все три.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db import engine
from app.main import API_PREFIX, app
from app.tables import app_user
from tests import roles

pytestmark = pytest.mark.usefixtures("seeded")

client = TestClient(app)

# Прогнозы объектов района CAO. Их видят все четыре роли.
IN_CAO = {"P-1", "P-2", "P-3"}
# Прогнозы объекта F-3. Их не видят ни техник, ни диспетчер района.
IN_SAO = {"P-4", "P-5"}


def get(path: str, **params: Any) -> Any:
    response = client.get(f"{API_PREFIX}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids_of(path: str = "/predictions") -> set[str]:
    return {item["id"] for item in get(path)["items"]}


def act(entity: str, entity_id: str, code: str, **body: Any) -> Any:
    return client.post(f"{API_PREFIX}/{entity}/{entity_id}/actions/{code}", json=body)


# --- область видимости -------------------------------------------------------


def test_the_ods_dispatcher_sees_the_whole_company() -> None:
    assert ids_of() == IN_CAO | IN_SAO


def test_the_district_dispatcher_sees_only_the_district() -> None:
    roles.sign_in("district")
    assert ids_of() == IN_CAO


def test_the_technician_sees_only_the_complex() -> None:
    roles.sign_in("tech")
    assert ids_of() == IN_CAO


def test_a_prediction_outside_the_border_answers_404() -> None:
    """Не 403: разный код ответа выдал бы сам факт существования прогноза."""
    roles.sign_in("tech")
    assert client.get(f"{API_PREFIX}/predictions/P-4").status_code == 404
    assert client.get(f"{API_PREFIX}/predictions/P-1").status_code == 200


def test_a_filter_cannot_widen_the_border() -> None:
    """Параметр запроса сужает выборку внутри видимого, а не расширяет её."""
    roles.sign_in("tech")
    assert get("/predictions", district="SAO")["total"] == 0


def test_the_border_cuts_the_map() -> None:
    roles.sign_in("tech")
    points = get("/facilities")["features"]
    assert {point["properties"]["facilityId"] for point in points} == {"F-1", "F-2"}


def test_the_border_cuts_the_dashboard() -> None:
    """Счётчик обязан сходиться с журналом, иначе строк не найти."""
    roles.sign_in("district")
    body = get("/dashboard/summary")

    assert body["total"] == len(IN_CAO)
    assert sum(body["byLevel"].values()) == len(IN_CAO)


def test_the_border_cuts_the_top_of_the_risk_list() -> None:
    roles.sign_in("tech")
    assert {item["id"] for item in get("/dashboard/top-risks")} <= IN_CAO


def test_the_border_cuts_the_orders() -> None:
    roles.sign_in("district")
    assert {item["id"] for item in get("/orders")["items"]} == {"O-1", "O-2"}


def test_an_order_outside_the_border_answers_404() -> None:
    roles.sign_in("district")
    assert client.get(f"{API_PREFIX}/orders/O-3").status_code == 404


def test_the_border_cuts_a_single_facility() -> None:
    roles.sign_in("tech")
    assert client.get(f"{API_PREFIX}/facilities/F-3").status_code == 404
    assert client.get(f"{API_PREFIX}/facilities/F-1").status_code == 200


def test_the_quality_of_the_model_is_the_same_for_everyone() -> None:
    """Оценка измерена на всей сети. Резать её границей значило бы врать."""
    roles.sign_in("tech")
    narrow = get("/metrics/models")
    roles.sign_in("ods")
    assert narrow == get("/metrics/models")


# --- права -------------------------------------------------------------------


def test_the_technician_gets_no_buttons() -> None:
    roles.sign_in("tech")
    assert get("/predictions/P-1")["actions"] == []


def test_the_technician_cannot_take_a_prediction() -> None:
    """Кнопки нет, но запрос можно послать и мимо интерфейса."""
    roles.sign_in("tech")
    response = act("predictions", "P-1", "take")

    assert response.status_code == 403
    assert "Техник" in response.json()["detail"]


def test_the_dispatcher_takes_and_decides() -> None:
    roles.sign_in("district")
    assert [item["code"] for item in get("/predictions/P-1")["actions"]] == ["take"]
    assert act("predictions", "P-1", "take").status_code == 200


def test_the_crew_does_not_decide_on_a_prediction() -> None:
    """Группа реагирования отвечает за факт на объекте, а не за уровень."""
    roles.sign_in("crew")
    assert get("/predictions/P-1")["actions"] == []
    assert act("predictions", "P-1", "take").status_code == 403


def test_the_dispatcher_does_not_close_an_order() -> None:
    roles.sign_in("district")
    assert act("orders", "O-2", "close", actualCause="X", factConfirmed=True).status_code == 403


def test_the_crew_does_not_assign_a_crew() -> None:
    roles.sign_in("crew")
    response = act("orders", "O-2", "assign", crew="Бригада 2", dueAt="2026-09-12T09:00:00Z")
    assert response.status_code == 403


def test_an_unknown_action_answers_404_before_the_permission() -> None:
    """Опечатка в коде не должна выглядеть как отказ по правам."""
    roles.sign_in("tech")
    assert act("predictions", "P-1", "выдумка").status_code == 404


def test_the_name_of_the_assignee_comes_from_the_token() -> None:
    """Ответ заказчика 3.6: запись остаётся за тем, кто взял прогноз."""
    roles.sign_in("district")
    act("predictions", "P-1", "take")

    assert get("/predictions/P-1")["assignee"] == "district"


def test_a_disabled_account_stops_acting_at_once() -> None:
    """Токен живёт до конца смены, а отключение записи действует сразу.

    Чтение по старому токену доживает до истечения срока, и это осознанный
    размен: чтений идёт по одному в минуту на экран, а действий единицы за
    смену. Проверка стоит там, где она что-то меняет. ADR 0007.
    """
    roles.sign_in("district")
    with engine().begin() as conn:
        conn.execute(
            update(app_user).where(app_user.c.username == "district").values(is_active=False)
        )

    assert act("predictions", "P-1", "take").status_code == 401


def test_the_border_cuts_the_collector_lines() -> None:
    roles.sign_in("tech")
    lines = get("/facilities/lines")["features"]
    assert [line["properties"]["code"] for line in lines] == ["K-CAO-1"]
