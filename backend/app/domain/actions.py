"""Действия диспетчера. Сервер считает их от статуса сущности.

Фронт кнопки не придумывает: он рисует то, что пришло в `actions`, и шлёт код
обратно в единственный эндпоинт действий. Спецификация §5 правило 3.
"""

from __future__ import annotations

from app.meta import REJECTION_REASONS_REF, by_code
from app.schemas import ActionDef, FieldDef

COMMENT = FieldDef(name="comment", label="Комментарий", type="textarea")


def _reject(scope_label: str) -> ActionDef:
    return ActionDef(
        code="reject",
        label=scope_label,
        kind="danger",
        confirm="Отклонить без возможности вернуть?",
        fields=[
            FieldDef(
                name="reason",
                label="Причина",
                type="select",
                required=True,
                options_ref=REJECTION_REASONS_REF,
            ),
            FieldDef(
                name="comment",
                label="Комментарий",
                type="textarea",
                required=True,
                min_length=5,
            ),
        ],
    )


def prediction_actions(status: str) -> list[ActionDef]:
    if status in ("NEW", "IN_REVIEW"):
        return [
            ActionDef(
                code="confirm_order",
                label="Подтвердить заявку",
                kind="primary",
                fields=[COMMENT],
            ),
            ActionDef(
                code="inspect",
                label="Назначить осмотр",
                kind="secondary",
                fields=[
                    FieldDef(
                        name="plannedAt",
                        label="Дата и время",
                        type="datetime",
                        required=True,
                    ),
                    FieldDef(
                        name="crew",
                        label="Бригада",
                        type="text",
                        required=True,
                        min_length=2,
                    ),
                    COMMENT,
                ],
            ),
            _reject("Отклонить прогноз"),
        ]
    if status == "ORDER_CONFIRMED":
        return [_reject("Отклонить прогноз")]
    return []


def order_actions(status: str, direction: str | None) -> list[ActionDef]:
    """Действия заявки.

    Форма закрытия берёт список фактических причин у направления связанного
    прогноза. Поэтому `work_order.prediction_id` обязателен.
    """
    if status == "AUTO_CREATED":
        return [
            ActionDef(
                code="confirm",
                label="Подтвердить",
                kind="primary",
                fields=[
                    FieldDef(
                        name="assignee",
                        label="Исполнитель",
                        type="text",
                        required=True,
                        min_length=2,
                    ),
                    COMMENT,
                ],
            ),
            _reject("Отклонить заявку"),
        ]
    if status == "CONFIRMED":
        return [
            ActionDef(
                code="start",
                label="Начать работы",
                kind="primary",
                fields=[
                    FieldDef(
                        name="crew",
                        label="Бригада",
                        type="text",
                        required=True,
                        min_length=2,
                    )
                ],
            )
        ]
    if status == "IN_PROGRESS":
        options_ref = direction if direction and by_code(direction) else None
        return [
            ActionDef(
                code="close",
                label="Закрыть заявку",
                kind="primary",
                fields=[
                    FieldDef(
                        name="actualCause",
                        label="Фактическая причина",
                        type="select",
                        required=True,
                        options_ref=options_ref,
                    ),
                    FieldDef(
                        name="predictionConfirmed",
                        label="Прогноз подтвердился",
                        type="boolean",
                        required=True,
                        help="Ответ идёт в расчёт Precision и Recall",
                    ),
                    FieldDef(
                        name="comment",
                        label="Комментарий",
                        type="textarea",
                        required=True,
                        min_length=5,
                    ),
                ],
            )
        ]
    return []
