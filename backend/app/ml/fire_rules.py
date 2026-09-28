"""Экспертные правила пожарного риска. ADR 0016.

Модель пожара не обогнала правило «событие было вчера» (ADR 0015).
Подтверждённых пожаров в выгрузке нет, поэтому учить модель настоящему пожару
не на чем. Направление работает на экспертных правилах: признаки, похожие на
настоящий пожар, совпадают, и чем больше независимых совпадений, тем выше
уровень.

Модуль не знает базы. Вход это факты эпизода пикета за окно наблюдения, выход
это уровень, баллы и объяснение. Тот же код размечает историю
(`ml/fire/15_expert_history.py`) и работает в сервисе
(`app/ml/plugins/fire_risk.py`). Поэтому нагрузка на диспетчера, измеренная на
истории, относится ровно к этим правилам.

## Признаки

Сигнал это «Обнаружен дым» датчика дыма, «Не замкнут» теплового датчика или
«Температура выше 40ºC» датчика температуры.

- **Пачка.** Сигнал, рядом с которым за час в тех же сутках тревожили больше
  двух других пикетов объекта. Это обход с проверкой или сбой линии, не пожар
  (ADR 0014). Пачка тревоги не поднимает.
- **Распространение.** Одиночный сигнал на соседнем пикете за 30 минут.
- **Независимое тепло.** Дым и тепловой сигнал другого типа датчика на том же
  или соседнем пикете за час. Два физических принципа подтверждают друг друга.
- **Рост температуры** коллектора на 10 °C и больше над нормой за 30 суток.
- **Ночь без обхода.** Сигнал ночью (22:00–6:00) или в нерабочий день, и в
  объекте в эти сутки не было обхода.
- **Обесточенная фаза** в комплексе за час до или после сигнала. Справка для
  диспетчера, а не признак: она есть у 69 % эпизодов, и после неё сигнал
  назавтра повторяется реже, 9,9 % против 15,6 %. Первая версия правил считала
  её признаком и поднимала уровень. Разметка истории показала, что это фон
  (ADR 0016, пересмотр по нагрузке).
- **Газ.** Метан вне окна ППР и ТО. От 5 % это нижний предел воспламенения.

## Уровни

| Уровень | Условие |
|---|---|
| CRITICAL | дым и независимое тепло, распространение и тепло, газ от 5 % |
| HIGH | распространение или три признака и больше |
| MEDIUM | одиночный сигнал ночью без обхода |
| LOW | одиночный сигнал днём, пачка или сигнала нет |

Газ от 1 % вне рабочих часов поднимает уровень одиночного сигнала на ступень,
но не выше HIGH. Охлаждение нового датчика (15 суток) и нового
объекта (180 суток или отметка пусконаладки) опускает уровень на ступень.
CRITICAL не опускается никогда: настоящий пожар на новом датчике тоже пожар.

Пороги выбраны до разметки истории и по её итогу не меняются. Точность
правил не измерена: подтверждённых пожаров нет. Её считают отметки бригад.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

TEMP_RISE_C = 10.0
GAS_ALARM_PCT = 1.0
GAS_IGNITION_PCT = 5.0
# Период охлаждения по кривой частоты от возраста (`ml/fire/14_burn_in.py`).
SENSOR_BURN_IN_DAYS = 15
OBJECT_BURN_IN_DAYS = 180
SIGNS_FOR_HIGH = 3

# Доля эпизодов уровня, после которых на пикете назавтра пришёл сигнал вне
# пачки. История 2019-01-01 … 2025-06-30, `ml/fire/out/expert_history.json`.
# Это не точность обнаружения пожара: подтверждённых пожаров нет. У CRITICAL
# десять эпизодов за 6,5 лет, доля по ним не держится, поэтому взята доля по
# всем эпизодам с сигналом.
NEXT_DAY_SIGNAL_RATE = {
    "LOW": 0.0957,
    "MEDIUM": 0.1558,
    "HIGH": 0.1304,
    "CRITICAL": 0.1165,
}
# Сутки пикета без сигнала: доля сигнала назавтра на той же истории.
QUIET_NEXT_DAY_RATE = 0.000372


@dataclass(frozen=True)
class Facts:
    """Факты эпизода пикета за окно наблюдения. Всё известно на момент расчёта."""

    signal: bool = False
    burst_only: bool = False
    smoke: bool = False
    heat_independent: bool = False
    spread: bool = False
    temp_rise_c: float = 0.0
    off_hours_no_walk: bool = False
    power_off: bool = False
    gas_pct: float = 0.0
    gas_off_hours: bool = False
    sensor_cooling: bool = False
    object_cooling: bool = False
    cooling_days_left: int = 0


@dataclass(frozen=True)
class Assessment:
    level: str
    signs: int
    reasons: list[str] = field(default_factory=list)
    tag: str = ""


def _step(level: str, delta: int, ceiling: str = "CRITICAL") -> str:
    index = LEVELS.index(level) + delta
    index = max(0, min(index, LEVELS.index(ceiling)))
    return LEVELS[index]


def signs(facts: Facts) -> list[str]:
    """Независимые признаки, похожие на пожар."""
    found = []
    if facts.spread:
        found.append("сигнал перешёл на соседний пикет")
    if facts.heat_independent:
        found.append("дым подтверждён тепловым сигналом другого датчика")
    if facts.temp_rise_c >= TEMP_RISE_C:
        found.append(f"температура выше нормы на {facts.temp_rise_c:.0f} °C")
    if facts.off_hours_no_walk:
        found.append("ночь или выходной, обхода в объекте не было")
    return found


def assess(facts: Facts) -> Assessment:
    """Уровень эпизода по совпадению признаков."""
    gas_ignition = facts.gas_pct >= GAS_IGNITION_PCT
    if not facts.signal and not gas_ignition:
        if facts.burst_only:
            return Assessment("LOW", 0, ["сигналы шли пачкой: обход или сбой линии"], "обход")
        return Assessment("LOW", 0, [], "")

    found = signs(facts) if facts.signal else []
    heat = facts.heat_independent or facts.temp_rise_c >= TEMP_RISE_C
    reasons = list(found)
    if gas_ignition:
        reasons.insert(0, f"метан {facts.gas_pct:.1f} % вне окна ППР и ТО")

    if (facts.smoke and facts.heat_independent) or (facts.spread and heat) or gas_ignition:
        return Assessment("CRITICAL", len(found), reasons, "признаки пожара")

    if facts.spread or len(found) >= SIGNS_FOR_HIGH:
        level = "HIGH"
    elif facts.off_hours_no_walk:
        level = "MEDIUM"
    else:
        level = "LOW"

    if facts.gas_pct >= GAS_ALARM_PCT and facts.gas_off_hours:
        level = _step(level, +1, ceiling="HIGH")
        reasons.append(f"метан {facts.gas_pct:.1f} % вне рабочих часов")
    if facts.power_off:
        reasons.append("справка: обесточена фаза в комплексе рядом по времени")

    if facts.sensor_cooling or facts.object_cooling:
        level = _step(level, -1)
        what = "датчик" if facts.sensor_cooling else "объект"
        reasons.append(
            f"{what} в периоде охлаждения, осталось {facts.cooling_days_left} сут.: "
            "уровень понижен на ступень"
        )
    if not reasons:
        reasons.append("одиночный сигнал днём")
    return Assessment(level, len(found), reasons, "одиночный сигнал")
