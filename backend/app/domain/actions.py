"""Действия диспетчера. Сервер считает их от статуса сущности.

Фронт кнопки не придумывает: он рисует то, что пришло в `actions`, и шлёт код
обратно в единственный эндпоинт действий. Спецификация §5 правило 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.auth.actor import Actor
from app.meta import REJECTION_REASONS_REF, RISK_LEVELS_REF, by_code
from app.schemas import ActionDef, FieldCondition, FieldDef


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


def allowed(actions: list[ActionDef], actor: Actor | None) -> list[ActionDef]:
    """Оставляет действия, на которые у роли есть право. ADR 0007.

    Отбор стоит здесь, а не в роутере, потому что список действий и есть
    интерфейс: кнопка без права не доезжает до экрана, и объяснять там нечего.
    Запрос мимо интерфейса ловит `check_permission` и отвечает 403.

    Роль не названа значит отбора нет. Так зовут эту функцию конвейер и тесты
    домена: у них нет ни токена, ни человека.
    """
    if actor is None:
        return actions
    return [action for action in actions if actor.may(action.code)]


def prediction_actions(
    status: str,
    assignee: str | None = None,
    actor: Actor | None = None,
    level: str | None = None,
) -> list[ActionDef]:
    """Действия прогноза. ADR 0006.

    Диспетчер отвечает на один вопрос: верен ли уровень критичности. Ответ
    попадает в поля `verdict` и `dispatcher_level`, а нужен ли выезд, решает
    итоговый уровень, а не согласие. Поэтому действие одно, а не два.
    """
    return allowed(_prediction_actions(status, assignee, level), actor)


def _prediction_actions(status: str, assignee: str | None, level: str | None) -> list[ActionDef]:
    if status == "NEW":
        # Одно действие: пока прогноз ничей, решать по нему нельзя. Имя
        # исполнителя записывается первым, и только потом открывается решение.
        return [
            ActionDef(
                code="take",
                label="Взять в работу",
                kind="primary",
                help="Решение станет доступно после того, как прогноз закреплён за вами",
                fields=[],
            )
        ]
    if status == "IN_REVIEW":
        return [
            _decide(level),
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


def _decide(level: str | None = None) -> ActionDef:
    """Единственное решение диспетчера по прогнозу.

    Уровень обязателен и подставляется уровнем модели. Диспетчер либо
    соглашается, либо ставит свой, и по итоговому уровню система сама решает,
    нужна ли заявка. Отклонение без последствия стало невозможным.

    Причина изменения доступна, только когда уровень отличается от уровня
    модели: подтверждению причина не нужна.
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
                default=level,
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
                help="Доступна, когда уровень отличается от предложенного моделью",
                enabled_when=(
                    FieldCondition(field="dispatcherLevel", not_equals=level) if level else None
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


def order_actions(
    status: str, context: OrderContext | None = None, actor: Actor | None = None
) -> list[ActionDef]:
    """Действия заявки.

    Форма закрытия берёт список фактических причин у направления связанного
    прогноза. Поэтому `work_order.prediction_id` обязателен.

    Отбор по правам делит эти действия между двумя ролями. Диспетчер назначает
    бригаду и отклоняет заявку, группа реагирования закрывает её отметкой о
    факте. Заказчик развёл эти роли прямо на QA-сессии.
    """
    return allowed(_order_actions(status, context), actor)


def _order_actions(status: str, context: OrderContext | None) -> list[ActionDef]:
    context = context or OrderContext()
    direction = context.direction

    if status == "AUTO_CREATED":
        # Заявка обогнала человека: конвейер создал её по порогу, а решения по
        # прогнозу ещё нет. Диспетчер работает с прогнозом, а не с заявкой,
        # поэтому здесь предлагается только отказ.
        return [_reject("Отклонить заявку")]

    if status == "CONFIRMED":
        return [
            ActionDef(
                code="assign",
                label="Назначить бригаду",
                kind="primary",
                help="Заявка подтверждена решением по прогнозу, осталось назначить выезд",
                fields=[
                    FieldDef(
                        name="crew",
                        label="Бригада",
                        type="text",
                        required=True,
                        min_length=2,
                    ),
                    # Срок автосоздания был заглушкой от горизонта. Настоящий
                    # срок называет человек: он один знает загрузку бригад.
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
