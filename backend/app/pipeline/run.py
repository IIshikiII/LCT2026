"""Конвейер прогноза. Спецификация §7, §9, ADR 0017.

Обходит активные объекты, вызывает плагин каждого включённого направления и
пишет прогноз в `prediction` со снимком признаков. После записи прогнозов
запускает создание автоматических заявок тем же прогоном. Итог записывается в
`pipeline_run`, чтобы дашборд видел здоровье последнего прохода.

Направление без обученной модели поднимает `RuntimeError` из `predict` —
решение об этом уже приняли плагин и T16. Конвейер ловит эту ошибку и
пропускает направление молча для всех объектов: модель отсутствует для
направления целиком, а не для одного объекта, поэтому повторять попытку на
каждом объекте бессмысленно.

Прогон направления идёт в два прохода. Первый считает признаки, вероятность и
объяснение на каждом объекте. Второй ставит уровни и пишет строки. Между ними
плагин может назначить границы уровней по всему прогону сразу: у подтопления
HIGH держит скользящий бюджет тревог (ADR 0013).

## Ритм и карточка на происшествие (ADR 0017)

Конвейер идёт по кругу раз в минуту. Направление само называет свой ритм
(`Direction.cadence`):

- `daily`. Модель училась на точке расчёта в полночь. Прогноз считается один
  раз за московские сутки, первым прогоном после 00:00, на момент полуночи.
  Остальные прогоны суток направление пропускают: они дали бы то же число.
- `stream`. Прогноз смотрит на окно до момента расчёта. Первый прогон суток
  считает все объекты, следующие только те, что назвал хук `candidates`.

Ключ карточки это тройка «объект, направление, происшествие». Происшествие
называет хук `incident`, без него ключом служат московские сутки. Повторный
прогон того же происшествия обновляет карточку, пока она новая и диспетчер её
не взял. Взятую карточку прогон не трогает.

## Задержка потока

После прогона конвейер считает события потока, принятые с начала прошлого
прогона, и наибольшую задержку от их метки времени до конца этого прогона.
Граница ТЗ §9 это 300 000 мс.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, literal_column, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from app.config import config
from app.domain import auto_orders
from app.ingest import weather_loop
from app.meta import PREDICTION_SCOPE, level_for, statuses_for
from app.meta import active as active_directions
from app.meta.directions import Direction
from app.ml.protocol import (
    Bands,
    Block,
    FeatureContext,
    FeatureVector,
    Predictor,
    applies,
    candidates,
    incident,
    last_event,
    level_bands,
    live_blocks,
    own_level,
    own_summary,
    prepare,
)
from app.ml.registry import get as get_predictor
from app.ml.registry import missing as missing_predictors
from app.pipeline import cards
from app.tables import facility, pipeline_run, prediction

log = logging.getLogger(__name__)

STATUS_NEW = "NEW"

# Статусы прогноза, в которых по объекту идёт работа: диспетчер взял прогноз
# или по нему открыта заявка. Такой объект новых прогнозов направления не
# получает, пока работа не кончилась. ADR 0020.
IN_WORK = ("IN_REVIEW", "ORDER_OPEN")
# Законченные статусы прогноза. Карточка в таком статусе заморожена: она
# хранит решение и итог такими, какими их видели люди, и конвейер её больше не
# меняет. ADR 0021.
FINISHED = tuple(item.code for item in statuses_for(PREDICTION_SCOPE) if item.terminal)
DEFAULT_MODEL_VERSION = "latest"
CADENCE_STREAM = "stream"
MOSCOW = timedelta(hours=3)
# Суточная карточка обновляет графики из потока не чаще этого шага. ADR 0018.
REFRESH_MINUTES = 5
# Демонстрационная шкала нужна сети, а не пяти объектам теста.
DEMO_MIN_FACILITIES = 12


@dataclass(frozen=True)
class RunResult:
    """Итог одного прогона. Возвращается вызывающему коду, не хранится."""

    run_id: int
    prediction_count: int
    order_count: int
    duration_ms: int
    stream_events: int = 0
    stream_lag_ms: int | None = None


def day_start(at: datetime) -> datetime:
    """Последняя московская полночь до `at` включительно, в UTC.

    Москва живёт в UTC+3 без перехода на летнее время.
    """
    local = at.astimezone(UTC) + MOSCOW
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - MOSCOW


def _facility_ids(conn: Connection) -> list[str]:
    statement = select(facility.c.id).where(facility.c.is_active.is_(True)).order_by(facility.c.id)
    return list(conn.execute(statement).scalars().all())


def _summary(direction: Direction, probability: float) -> str:
    return f"{direction.label}: вероятность {probability:.0%}"


@dataclass(frozen=True)
class Computed:
    """Прогноз одного объекта после первого прохода, до записи."""

    facility_id: str
    features: FeatureVector
    probability: float
    blocks: list[dict[str, Any]]
    compute_ms: int
    bucket: datetime
    level: str | None = None
    summary: str | None = None
    # Последнее событие, на котором стоит прогноз. Нужен, чтобы отличить новые
    # данные от старых у замороженной карточки происшествия. ADR 0021.
    event_at: datetime | None = None


def _compute(
    conn: Connection,
    predictor: Predictor,
    facility_id: str,
    at: datetime,
    fallback: datetime,
    now: datetime | None = None,
) -> Computed:
    """Прогноз одного объекта. `at` это точка расчёта модели, `now` момент
    прогона: живые блоки читают данные на него. У потокового направления они
    совпадают, у суточного точка расчёта стоит в полночь."""
    ctx = FeatureContext(conn=conn, facility_id=facility_id, at=at)
    started = time.monotonic()
    features = predictor.build_features(ctx)
    probability = predictor.predict(features)
    moment = now or at
    blocks = cards.merge(
        [b.as_dict() for b in predictor.explain(features, at=at)],
        live_blocks(predictor, conn, facility_id, moment),
    )
    if moment != at:
        blocks = cards.merge(blocks, [cards.freshness(at, moment)])
    return Computed(
        facility_id=facility_id,
        features=features,
        probability=probability,
        blocks=blocks,
        compute_ms=int((time.monotonic() - started) * 1000),
        bucket=incident(predictor, features, at) or fallback,
        level=own_level(predictor, features, probability),
        event_at=last_event(predictor, features, at),
        summary=own_summary(predictor, features, probability),
    )


def _stored(features: FeatureVector) -> dict[str, float | None]:
    """Признаки для JSONB. Пропуск (NaN) хранится как null: JSONB не знает NaN."""
    return {name: None if math.isnan(value) else value for name, value in features.items()}


def _write_prediction(
    conn: Connection,
    direction: Direction,
    item: Computed,
    at: datetime,
    run_id: int,
    bands: Bands | None,
) -> bool:
    """Пишет или обновляет карточку происшествия. Отдаёт True только за новую
    карточку: обновление той же и карточка, взятая диспетчером, дают False.

    Ключ карточки держит уникальный индекс на тройке «объект, направление,
    `computed_at_bucket`». В `computed_at_bucket` лежит начало происшествия.

    Карточка, которую взял диспетчер или по которой открыта заявка, хранит
    уровень, вероятность и объяснение такими, какими их видел человек. Её
    графики, хроника и таблица датчиков всё равно обновляются (ADR 0018).

    Открытая карточка на объект и направление одна. Нетронутая карточка идёт
    за свежим прогнозом: конвейер переписывает её, а не заводит вторую.
    ADR 0022.
    """
    values = {
        "probability": item.probability,
        "level": item.level or level_for(item.probability, direction, bands),
        "computed_at": at,
        "compute_ms": item.compute_ms,
        "summary": item.summary or _summary(direction, item.probability),
        "blocks": item.blocks,
        "features": _stored(item.features),
        "run_id": run_id,
    }
    followed = _follow_open_card(conn, direction, item, values)
    if followed is not None:
        return followed
    bucket = _card_bucket(conn, direction, item, at)
    if bucket is None:
        return False
    statement = pg_insert(prediction).values(
        id=f"{direction.code}-{item.facility_id}-{int(bucket.timestamp())}",
        direction=direction.code,
        facility_id=item.facility_id,
        horizon_hours=direction.min_horizon_hours,
        computed_at_bucket=bucket,
        status=STATUS_NEW,
        model_version=DEFAULT_MODEL_VERSION,
        **values,
    )
    upsert = statement.on_conflict_do_update(
        index_elements=["facility_id", "direction", "computed_at_bucket"],
        set_={name: statement.excluded[name] for name in values},
        where=and_(
            prediction.c.status == STATUS_NEW,
            prediction.c.assignee.is_(None),
            prediction.c.verdict.is_(None),
        ),
    ).returning(prediction.c.id, literal_column("(xmax = 0)").label("inserted"))
    row = conn.execute(upsert).first()
    if row is None:
        _refresh_taken(conn, direction, item, at, bucket)
        return False
    return bool(row.inserted)


def _untouched() -> Any:
    """Условие нетронутой карточки: никто её не брал и не решал."""
    return and_(
        prediction.c.status == STATUS_NEW,
        prediction.c.assignee.is_(None),
        prediction.c.verdict.is_(None),
    )


def _follow_open_card(
    conn: Connection, direction: Direction, item: Computed, values: dict[str, Any]
) -> bool | None:
    """Переписывает нетронутую карточку объекта свежим прогнозом. ADR 0022.

    Решений людей в нетронутой карточке нет, поэтому переписать её нечем
    навредить истории. Ключ переезжает на свежие сутки или происшествие, чтобы
    конвейер видел, что сутки посчитаны. Ключ, занятый другой карточкой того
    же объекта, остаётся прежним.

    Отдаёт None, если нетронутой карточки нет. Иначе отдаёт, открыл ли прогноз
    новые сутки или новое происшествие: такой прогноз прогон считает новым,
    хотя карточка та же.
    """
    card = conn.execute(
        select(prediction.c.id, prediction.c.computed_at_bucket)
        .where(prediction.c.facility_id == item.facility_id)
        .where(prediction.c.direction == direction.code)
        .where(_untouched())
        .order_by(prediction.c.computed_at_bucket.desc())
        .limit(1)
    ).first()
    if card is None:
        return None
    taken = conn.execute(
        select(prediction.c.id)
        .where(prediction.c.facility_id == item.facility_id)
        .where(prediction.c.direction == direction.code)
        .where(prediction.c.computed_at_bucket == item.bucket)
        .where(prediction.c.id != card.id)
        .limit(1)
    ).first()
    bucket = card.computed_at_bucket if taken else item.bucket
    conn.execute(
        prediction.update()
        .where(prediction.c.id == card.id)
        .where(_untouched())
        .values(**values, computed_at_bucket=bucket)
    )
    return bool(bucket != card.computed_at_bucket)


def _card_bucket(
    conn: Connection, direction: Direction, item: Computed, at: datetime
) -> datetime | None:
    """Ключ карточки, в которую идёт прогноз. None значит, что писать некуда.

    Карточка происшествия одна, пока её не закончили. Законченная карточка
    заморожена: в ней решение диспетчера и итог бригады, и конвейер их не
    трогает. Новые данные того же происшествия идут в её чистую копию с ключом
    момента прогона. Новыми данные считаются, когда последнее событие позже
    заморозки. Без новых данных законченная карточка просто остаётся как есть,
    иначе закрытое происшествие тут же всплыло бы снова. ADR 0021.
    """
    latest = conn.execute(
        select(prediction.c.computed_at_bucket, prediction.c.status, prediction.c.computed_at)
        .where(prediction.c.facility_id == item.facility_id)
        .where(prediction.c.direction == direction.code)
        .where(prediction.c.computed_at_bucket >= item.bucket)
        .order_by(prediction.c.computed_at_bucket.desc())
        .limit(1)
    ).first()
    if latest is None:
        return item.bucket
    if latest.status not in FINISHED:
        return latest.computed_at_bucket
    if item.event_at is not None and item.event_at > latest.computed_at:
        return at
    return None


def _refresh_taken(
    conn: Connection,
    direction: Direction,
    item: Computed,
    at: datetime,
    bucket: datetime | None = None,
) -> None:
    """Живые блоки взятой карточки. Уровень и факторы остаются прежними.

    Законченную карточку это не касается: она заморожена. ADR 0021.
    """
    live = [
        Block(type=b["type"], title=b["title"], data=b.get("data", {}))
        for b in item.blocks
        if b.get("type") in cards.REPLACEABLE or b.get("title") == cards.FRESHNESS_TITLE
    ]
    if not live:
        return
    key = (
        (prediction.c.facility_id == item.facility_id)
        & (prediction.c.direction == direction.code)
        & (prediction.c.computed_at_bucket == (bucket or item.bucket))
        & prediction.c.status.notin_(FINISHED)
    )
    stored = conn.execute(select(prediction.c.blocks).where(key)).scalar()
    conn.execute(
        prediction.update()
        .where(key)
        .values(blocks=cards.merge(list(stored or []), live), computed_at=at)
    )


def _done_today(conn: Connection, direction: Direction, today: datetime) -> bool:
    """Считало ли уже направление эти московские сутки.

    Суточное направление пишет ключ суток. Потоковое пишет ключи происшествий,
    поэтому для него признак это любой прогноз, посчитанный с полуночи.
    """
    column = (
        prediction.c.computed_at
        if direction.cadence == CADENCE_STREAM
        else prediction.c.computed_at_bucket
    )
    row = conn.execute(
        select(prediction.c.id)
        .where(prediction.c.direction == direction.code)
        .where(column >= today if direction.cadence == CADENCE_STREAM else column == today)
        .limit(1)
    ).first()
    return row is not None


def _plan(
    conn: Connection,
    direction: Direction,
    predictor: Predictor,
    at: datetime,
    facility_ids: list[str],
) -> tuple[datetime, list[str]] | None:
    """Момент расчёта и объекты направления на этот прогон. None значит пропуск."""
    today = day_start(at)
    first_of_day = not _done_today(conn, direction, today)
    if direction.cadence != CADENCE_STREAM:
        return (today, facility_ids) if first_of_day else None
    if first_of_day:
        return at, facility_ids
    chosen = candidates(predictor, conn, at)
    if chosen is None:
        return at, facility_ids
    return at, [fid for fid in facility_ids if fid in chosen]


def _log_forecasts(
    direction: Direction,
    computed: list[Computed],
    at: datetime,
    run_id: int,
    bands: Bands | None,
) -> None:
    """Дописывает каждый посчитанный прогноз в журнал-файл. ADR 0022.

    Карточка журнала диспетчера переписывается свежим прогнозом, и история
    самих прогнозов живёт здесь. Включается флагом `FORECAST_LOG`. Ошибка
    записи прогон не останавливает: журнал вспомогательный.
    """
    if not config.forecast_log or not computed:
        return
    path = Path(config.forecast_log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as sink:
            for item in computed:
                record = {
                    "at": at.isoformat(),
                    "runId": run_id,
                    "direction": direction.code,
                    "facilityId": item.facility_id,
                    "probability": None if math.isnan(item.probability) else item.probability,
                    "level": item.level or level_for(item.probability, direction, bands),
                    "bucket": item.bucket.isoformat(),
                }
                sink.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as error:
        log.warning("журнал прогнозов не записан", extra={"path": str(path), "error": str(error)})


def _in_work(conn: Connection, direction: Direction) -> set[str]:
    """Объекты направления, по которым сейчас идёт работа."""
    return set(
        conn.execute(
            select(prediction.c.facility_id)
            .where(prediction.c.direction == direction.code)
            .where(prediction.c.status.in_(IN_WORK))
            .distinct()
        ).scalars()
    )


def _stream_lag(
    conn: Connection, run_id: int, started: datetime, finished: datetime
) -> tuple[int, int | None]:
    """События потока с начала прошлого прогона и наибольшая задержка, мс."""
    previous = conn.execute(
        select(pipeline_run.c.started_at)
        .where(pipeline_run.c.id < run_id)
        .order_by(pipeline_run.c.id.desc())
        .limit(1)
    ).scalar()
    since = previous or started - timedelta(minutes=5)
    row = conn.execute(
        text(
            """
            SELECT count(*),
                   max(extract(epoch FROM (:finished - occurred_at)) * 1000)
            FROM alarm_event
            WHERE received_at > :since AND received_at <= :started
            """
        ),
        {"since": since, "started": started, "finished": finished},
    ).first()
    if row is None or not row[0]:
        return 0, None
    return int(row[0]), int(row[1])


def _refresh(conn: Connection, direction: Direction, predictor: Predictor, at: datetime) -> None:
    """Обновляет живые блоки суточных карточек этих суток. ADR 0018.

    Вероятность суточной модели посчитана в полночь и не меняется. Графики и
    хроника читают поток, и карточка обновляет их раз в `REFRESH_MINUTES`
    минут. Взятая карточка тоже: меняются только графики и хроника.
    """
    stale = conn.execute(
        select(prediction.c.id, prediction.c.facility_id, prediction.c.blocks)
        .where(prediction.c.direction == direction.code)
        .where(prediction.c.computed_at_bucket == day_start(at))
        .where(prediction.c.computed_at <= at - timedelta(minutes=REFRESH_MINUTES))
        # Законченная карточка заморожена. ADR 0021.
        .where(prediction.c.status.notin_(FINISHED))
    ).all()
    for row in stale:
        live = live_blocks(predictor, conn, row.facility_id, at)
        blocks = cards.merge(list(row.blocks or []), [*live, cards.freshness(day_start(at), at)])
        conn.execute(
            prediction.update()
            .where(prediction.c.id == row.id)
            .values(blocks=blocks, computed_at=at)
        )


def _demo_levels(direction: Direction, computed: list[Computed]) -> list[Computed]:
    """Демонстрационная шкала: уровни суточного направления по рангу. ADR 0018."""
    if (
        not config.demo_levels
        or direction.cadence == CADENCE_STREAM
        or len(computed) < DEMO_MIN_FACILITIES
    ):
        return computed
    levels = cards.rank_levels({item.facility_id: item.probability for item in computed})
    return [replace(item, level=levels[item.facility_id]) for item in computed]


def _relative(computed: list[Computed]) -> list[Computed]:
    """Строка прогноза «в N раз выше среднего по сети». ADR 0018.

    Плагин со своей строкой (пожар) её сохраняет. Среднее берётся по объектам
    направления в этом прогоне: суточное направление считает их все сразу.
    """
    values = [c.probability for c in computed if not math.isnan(c.probability)]
    if len(values) < 2:
        return computed
    mean = sum(values) / len(values)
    return [
        c if c.summary else replace(c, summary=cards.relative_summary(c.probability, mean))
        for c in computed
    ]


def _run_direction(
    conn: Connection,
    direction: Direction,
    predictor: Predictor,
    at: datetime,
    facility_ids: list[str],
    run_id: int,
    model_versions: dict[str, str],
) -> int:
    """Прогон одного направления. Отдаёт число новых карточек."""
    planned = _plan(conn, direction, predictor, at, facility_ids)
    model_versions[direction.code] = DEFAULT_MODEL_VERSION
    if planned is None:
        _refresh(conn, direction, predictor, at)
        return 0
    point, targets = planned

    prepare(predictor, conn, point)
    fallback = day_start(at)
    computed: list[Computed] = []
    for facility_id in targets:
        if not applies(predictor, conn, facility_id):
            continue
        try:
            computed.append(_compute(conn, predictor, facility_id, point, fallback, at))
        except RuntimeError as error:
            log.warning(
                "направление пропущено: модели нет",
                extra={"direction": direction.code, "error": str(error)},
            )
            del model_versions[direction.code]
            return 0
    if not computed:
        return 0

    computed = _demo_levels(direction, computed)
    computed = _relative(computed)
    fresh = {item.facility_id: (item.probability, item.features) for item in computed}
    bands = level_bands(predictor, conn, point, fresh)
    # Объект в работе ждёт итога: новый прогноз поверх взятого дублировал бы
    # его в журнале и в тревоге. Взятая карточка того же происшествия всё равно
    # обновляет графики и хронику (ADR 0018), новой карточки объект не получает.
    busy = _in_work(conn, direction)
    _log_forecasts(direction, computed, point, run_id, bands)
    written = 0
    for item in computed:
        if item.facility_id in busy:
            _refresh_taken(conn, direction, item, point)
            continue
        if _write_prediction(conn, direction, item, point, run_id, bands):
            written += 1
    return written


def run(engine: Engine, at: datetime | None = None) -> RunResult:
    """Прогоняет все активные объекты и направления, пишет итог в `pipeline_run`.

    Потоковые направления идут первыми, и каждое направление фиксируется своей
    транзакцией вместе со своими автозаявками. Карточка пожара появляется
    через секунды, даже когда первый прогон суток считает суточные модели
    минутами. Задержка потока меряется до фиксации потоковых направлений.
    """
    at = (at or datetime.now(UTC)).replace(microsecond=0)
    started = time.monotonic()

    with engine.begin() as conn:
        inserted = conn.execute(
            pipeline_run.insert().values(started_at=at, status="RUNNING")
        ).inserted_primary_key
        assert inserted is not None
        run_id = inserted[0]

    prediction_count = 0
    order_count = 0
    model_versions: dict[str, str] = {}
    stream_done: datetime | None = None

    try:
        with engine.begin() as conn:
            weather_loop.extend(conn, at)
            facility_ids = _facility_ids(conn)
        directions = sorted(active_directions(), key=lambda d: d.cadence != CADENCE_STREAM)
        for direction in directions:
            predictor = get_predictor(direction.code)
            if predictor is None:
                continue
            with engine.begin() as conn:
                prediction_count += _run_direction(
                    conn, direction, predictor, at, facility_ids, run_id, model_versions
                )
                order_count += len(auto_orders.create_missing(conn, at, run_id))
            if direction.cadence == CADENCE_STREAM:
                stream_done = datetime.now(UTC)
    except Exception as error:
        duration_ms = int((time.monotonic() - started) * 1000)
        with engine.begin() as conn:
            conn.execute(
                pipeline_run.update()
                .where(pipeline_run.c.id == run_id)
                .values(
                    finished_at=datetime.now(UTC),
                    duration_ms=duration_ms,
                    status="FAILED",
                    error=str(error),
                )
            )
        raise

    duration_ms = int((time.monotonic() - started) * 1000)
    finished = datetime.now(UTC)
    with engine.begin() as conn:
        stream_events, stream_lag_ms = _stream_lag(conn, run_id, at, stream_done or finished)
        conn.execute(
            pipeline_run.update()
            .where(pipeline_run.c.id == run_id)
            .values(
                finished_at=finished,
                duration_ms=duration_ms,
                prediction_count=prediction_count,
                model_versions=model_versions,
                status="DONE",
                stream_events=stream_events,
                stream_lag_ms=stream_lag_ms,
            )
        )

    still_missing = missing_predictors()
    if still_missing:
        log.info("направления без предиктора пропущены", extra={"directions": still_missing})

    return RunResult(
        run_id=run_id,
        prediction_count=prediction_count,
        order_count=order_count,
        duration_ms=duration_ms,
        stream_events=stream_events,
        stream_lag_ms=stream_lag_ms,
    )
