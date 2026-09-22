"""Действия диспетчера. Сервер считает их от статуса сущности.

Фронт кнопки не придумывает: он рисует то, что пришло в `actions`, и шлёт код
обратно в единственный эндпоинт действий. Спецификация §5 правило 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.meta import REJECTION_REASONS_REF, RISK_LEVELS_REF, by_code
from app.schemas import ActionDef, FieldDef


@dataclass(frozen=True)
class OrderContext:
    """Числа этой заявки, которые видит диспетчер при подтверждении."""

    direction: str | None = None
    computed_at: datetime | None = None
    horizon_hours: int | None = None

    def deadline_help(self) -> str:
        """Собирает подсказку к сроку из живых чисел прогноза.

        Действия считает сервер, поэтому подсказка несёт контекст ещё до того,
        как фронт научится рисовать его сам.
        """
        if self.computed_at is None or self.horizon_hours is None:
            return "Срок, к которому работы должны быть закончены"
        ends_at = self.computed_at + timedelta(hours=self.horizon_hours)
        left = ends_at - datetime.now(UTC)
        hours_left = int(left.total_seconds() // 3600)
        tail = f"осталось {hours_left} ч" if hours_left > 0 else "горизонт уже истёк"
        return (
            f"Прогноз посчитан {self.computed_at:%d.%m %H:%M}, "
            f"горизонт {self.horizon_hours} ч, истекает {ends_at:%d.%m %H:%M}, {tail}"
        )


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
                name="suppressUntil",
                label="Не предлагать до",
                type="datetime",
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


def prediction_actions(status: str, assignee: str | None = None) -> list[ActionDef]:
    """Действия прогноза. ADR 0006.

    Диспетчер отвечает на один вопрос: верен ли уровень критичности. Ответ
    попадает в поля `verdict` и `dispatcher_level`, а нужен ли выезд, решает
    итоговый уровень, а не согласие. Поэтому действие одно, а не два.
    """
    if status == "NEW":
        return [
            ActionDef(
                code="take",
                label="Взять в работу",
                kind="secondary",
                fields=[],
            ),
            _decide(),
        ]
    if status == "IN_REVIEW":
        return [
            _decide(),
            ActionDef(
                code="release",
                label="Вернуть в очередь",
                kind="ghost",
                help=(
                    f"Сейчас за прогнозом закреплён {assignee}"
                    if assignee
                    else "Снять закрепление за собой"
                ),
                fields=[],
            ),
        ]
    return []


def _decide() -> ActionDef:
    """Единственное решение диспетчера по прогнозу.

    Уровень обязателен и подставляется уровнем модели. Диспетчер либо
    соглашается, либо ставит свой, и по итоговому уровню система сама решает,
    нужна ли заявка. Отклонение без последствия стало невозможным.
    """
    return ActionDef(
        code="decide",
        label="Принять решение",
        kind="primary",
        fields=[
            FieldDef(
                name="dispatcherLevel",
                label="Уровень по решению диспетчера",
                type="select",
                required=True,
                options_ref=RISK_LEVELS_REF,
                help=(
                    "Уровни «Высокий» и «Критический» требуют выезда: система "
                    "создаст заявку. На «Низком» и «Среднем» прогноз закрывается."
                ),
            ),
            FieldDef(
                name="reason",
                label="Причина изменения уровня",
                type="select",
                options_ref=REJECTION_REASONS_REF,
                help="Заполняется, когда уровень отличается от предложенного моделью",
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


def order_actions(status: str, context: OrderContext | None = None) -> list[ActionDef]:
    """Действия заявки.

    Форма закрытия берёт список фактических причин у направления связанного
    прогноза. Поэтому `work_order.prediction_id` обязателен.
    """
    context = context or OrderContext()
    direction = context.direction

    if status in ("AUTO_CREATED", "MANUAL_CREATED"):
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
                    # Срок при автосоздании — заглушка от горизонта. Настоящий
                    # срок назначает человек: он один знает загрузку бригад.
                    FieldDef(
                        name="dueAt",
                        label="Закончить работы к",
                        type="datetime",
                        required=True,
                        help=context.deadline_help(),
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
                    # Итог бригады. Он закрывает и заявку, и связанный прогноз,
                    # поэтому вопрос задан про факт на объекте, а не про пользу
                    # выезда: пользу измерить нечем, факт бригада видела. ADR 0006.
                    FieldDef(
                        name="factConfirmed",
                        label="Факт подтверждён на объекте",
                        type="boolean",
                        required=True,
                        help="Ответ закрывает прогноз и идёт в дообучение модели",
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
