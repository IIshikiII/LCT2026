"""Действия диспетчера. Сервер считает их от статуса сущности.

Фронт кнопки не придумывает: он рисует то, что пришло в `actions`, и шлёт код
обратно в единственный эндпоинт действий. Спецификация §5 правило 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.meta import REJECTION_REASONS_REF, by_code
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
            # Длина мьюта — решение по случаю. Диспетчер знает, что работы на
            # объекте идут до пятницы, а конфиг не знает. Поле необязательное:
            # без него срок берётся от причины отклонения.
            FieldDef(
                name="suppressUntil",
                label="Не предлагать до",
                type="datetime",
                help=(
                    "Пока действует, новые заявки этого уровня не создаются. "
                    "Без ответа: дубль — сутки, особенность объекта — месяц, "
                    "остальное — неделя"
                ),
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


def order_actions(status: str, context: OrderContext | None = None) -> list[ActionDef]:
    """Действия заявки.

    Форма закрытия берёт список фактических причин у направления связанного
    прогноза. Поэтому `work_order.prediction_id` обязателен.
    """
    context = context or OrderContext()
    direction = context.direction

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
                    # Прогноз — это вероятность. Спросить «сбылась ли
                    # вероятность» нельзя, поэтому вопрос о пользе заявки.
                    # Формулировка временная, см. корневой TODO.md.
                    FieldDef(
                        name="predictionConfirmed",
                        label="Заявка была целесообразна",
                        type="boolean",
                        required=True,
                        help="Ответ обучает модель после ввода в эксплуатацию",
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
