"""Ключи ответов обязаны быть camelCase. Фронт ждёт именно их."""

from __future__ import annotations

import re

import pytest

from app import schemas
from app.schemas.base import Dto

CAMEL_CASE = re.compile(r"^[a-z][a-zA-Z0-9]*$")


def dto_classes() -> list[type[Dto]]:
    found = [
        value
        for value in vars(schemas).values()
        if isinstance(value, type) and issubclass(value, Dto) and value is not Dto
    ]
    assert found, "в app.schemas нет ни одного DTO"
    return found


@pytest.mark.parametrize("model", dto_classes(), ids=lambda model: model.__name__)
def test_every_field_serialises_as_camel_case(model: type[Dto]) -> None:
    wrong = [
        f"{name} -> {field.alias or name}"
        for name, field in model.model_fields.items()
        if not CAMEL_CASE.match(field.alias or name)
    ]
    assert not wrong, f"{model.__name__}: не camelCase: {wrong}"


def test_snake_case_field_gets_a_camel_alias() -> None:
    assert schemas.Prediction.model_fields["horizon_hours"].alias == "horizonHours"
    assert schemas.WorkOrder.model_fields["work_type"].alias == "workType"
    assert schemas.PipelineHealth.model_fields["target_compute_ms"].alias == "targetComputeMs"


def test_page_envelope_dumps_the_four_contract_keys() -> None:
    page: schemas.Page[str] = schemas.Page(items=["a"], page=1, page_size=50, total=1)
    assert page.model_dump(by_alias=True) == {
        "items": ["a"],
        "page": 1,
        "pageSize": 50,
        "total": 1,
    }


def test_direction_level_and_status_stay_strings() -> None:
    # Спецификация §5 правило 1. Enum здесь означал бы миграцию на каждое
    # новое направление.
    for model, field in (
        (schemas.Prediction, "direction"),
        (schemas.Prediction, "level"),
        (schemas.Prediction, "status"),
        (schemas.WorkOrder, "status"),
    ):
        assert model.model_fields[field].annotation is str
