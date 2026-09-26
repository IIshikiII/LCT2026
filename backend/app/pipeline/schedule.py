"""Прогон конвейера по кругу. ADR 0017.

Шаг по умолчанию одна минута. Граница ТЗ §9 для задержки потока равна пяти
минутам. Шаг в минуту держит задержку в пределах шага и времени прогона, с
запасом до границы. Суточные направления внутри суток прогон пропускает, так
что частый шаг стоит только потоковому пожару.

Прогоны выровнены по часам: при шаге 60 с прогон стартует в начале каждой
минуты. Прогон, который упал, пишется в журнал, и цикл идёт дальше: одна
ошибка данных не должна останавливать расписание.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from types import FrameType

log = logging.getLogger(__name__)


def next_tick(now: float, step: int) -> float:
    """Ближайшая отметка, кратная шагу, строго после `now`."""
    return (int(now // step) + 1) * step


def every(step: int, job: Callable[[], None], *, stop_after: int | None = None) -> None:
    """Зовёт `job` каждые `step` секунд до сигнала остановки.

    `stop_after` ограничивает число прогонов. Он нужен тестам.
    """
    running = True

    def _stop(signum: int, _frame: FrameType | None) -> None:
        nonlocal running
        running = False
        log.info("расписание остановлено сигналом", extra={"signal": signum})

    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _stop)

    done = 0
    while running and (stop_after is None or done < stop_after):
        try:
            job()
        except Exception:
            log.exception("прогон по расписанию упал, следующий по шагу")
        done += 1
        if stop_after is not None and done >= stop_after:
            break
        pause = next_tick(time.time(), step) - time.time()
        while running and pause > 0:
            time.sleep(min(pause, 1.0))
            pause -= 1.0
