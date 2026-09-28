"""Скрытые факторы пожарного риска: отбор гипотез без отложенного года. ADR 0015.

Проверочные годы 2022–2025 уже много раз служили выбору. Новые гипотезы
отбираются на внутреннем году, который лежит раньше них:

- обучение на сутках до 2021-07-01;
- год отбора с 2021-07-01 по 2022-07-01.

Отложенный год скрипт не читает. Проверочные годы читает только режим
`confirm`, один раз для гипотез, прошедших отбор.

Гипотезы:

1. `loop_*`, шлейф. Датчик адресуется темой MQTT вида `прибор-линия.x.адрес.y`.
   Соседние адреса одной линии прибора лежат рядом физически (`INSIGHTS.md`
   §1.10). События соседей по шлейфу в пределах трёх адресов вчера и за
   неделю, а также события всего прибора вчера. Неисправная линия тревожит
   сериями.
2. `hawkes_*`, затухающая память. Сумма прошлых событий пикета с весом
   `exp(−давность / τ)`, τ равно 0,5, 2, 7 и 30 суткам. Процесс Хоукса
   описывает самовозбуждающиеся серии.
3. `emb_*`, скрытый тип места. Профиль пикета по его сигналам до начала
   обучения: доли по часам суток, доля выходных, доля пачек, доли типов
   датчика, число сигналов. Профиль сжимается PCA до пяти осей и UMAP до двух.
4. `wxpca_*`: погода, воздух и влажность, сжатые PCA до трёх осей вместо
   двадцати рядов. Масштаб и оси учатся на сутках обучения.
5. `graph_*`, выученный граф пикетов. Направленное ребро `v → u` между
   пикетами одного комплекса весит `n(v сегодня, u завтра) / (n(v) + 5)`:
   сглаженная доля событий `v`, за которыми назавтра пришло событие `u`.
   Признак это сумма весов рёбер от пикетов с событием вчера и та же сумма за
   три дня с затуханием. Рёбра для строк обучающего года считаются по
   остальным обучающим годам (кросс-фит), иначе признак знает метку. Для
   проверяемого года рёбра считаются по всем годам обучения.

Экзотические гипотезы:

6. `space_*`: геомагнитный индекс Kp вчера (GFZ Потсдам, CC BY 4.0). Буря
   наводит токи в длинных проводниках, а в коллекторах километры шлейфов.
7. `moon_*`: фаза Луны в сутки расчёта. Контроль: эффекта быть не должно.
   Если отбор его «найдёт», отбор ловит шум.
8. `char_*`: характер пикета по истории его событий до суток расчёта:
   взрывность интервалов `(σ − μ) / (σ + μ)`, коэффициент памяти (корреляция
   соседних интервалов), концентрация часа сигнала по Рэлею, энтропия часа.
9. `lz_*`: сложность Лемпеля–Зива строки событий пикета за 64 суток.
   Регулярный узор короче хаоса.
10. `iforest`: оценка аномальности строки Isolation Forest, обученным без
    учителя на сутках обучения.
11. `rank`: те же признаки, что у базы, но цель LambdaRank: порядок пикетов
    внутри суток, а не вероятность. Бюджет ставит верх списка суток.

Модель везде одна: признаки и параметры `wide_reg`, 335 деревьев без ранней
остановки, три семени. Счёт: PR-AUC и попадания при скользящем бюджете 7
суток против правила «событие было вчера».

Запуск из корня репозитория:

    .venv\\Scripts\\python.exe ml/fire/13_hypotheses.py screen
    .venv\\Scripts\\python.exe ml/fire/13_hypotheses.py confirm loop,hawkes

Выход: `out/hypotheses_screen.json`, `out/hypotheses_confirm.json`.
"""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys
import time

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.signal import lfilter
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parents[0] / "flood"))
sys.path.insert(0, str(HERE))
tr = importlib.import_module("06_train")
rolling = importlib.import_module("14_rolling_budget")
base = tr.base

OUT = HERE / "out"
EDA = ROOT / "eda" / "out" / "eda.duckdb"
CHANNELS = ROOT / "notebooks" / "out" / "channel_features.parquet"
ALARMS = OUT / "fire_alarms.parquet"
SCREEN = ("2021-07-01", "2022-07-01")
CONFIRM_FOLDS = base.FOLDS
ROUNDS = 335
WINDOW = 7
LOOP_ADDRESSES = 3
TAUS = (0.5, 2.0, 7.0, 30.0)
BURST, BURST_MINUTES = 2, 60
KEY = ["object_id", "gallery", "picket"]
OBJECTS = ROOT / "raw_task" / "dataset" / "справочник_объектов_диспетчер.csv"
GRAPH_PRIOR = 5.0
KP = OUT / "kp_since_1932.txt"
LZ_DAYS = 64
GRAPH_LAGS = 3


def wide_config() -> dict:
    return next(i for i in tr.train.read_log() if i["name"] == "wide_reg")["config"]


def load_panel(columns: list[str], hi: str) -> pd.DataFrame:
    con, _ = base.connect()
    frame = con.execute(
        f"SELECT object_id, gallery, picket, hour, label, naive, {', '.join(columns)} "
        f"FROM panel WHERE hour < TIMESTAMP '{hi}' ORDER BY object_id, gallery, picket, hour"
    ).df()
    frame["day"] = frame["hour"].dt.floor("D")
    return frame


def channel_events() -> duckdb.DuckDBPyConnection:
    """Сутки канала с событием метки: сигнал вне пачки, как в панели."""
    con = duckdb.connect()
    con.execute(f"ATTACH '{EDA.as_posix()}' AS eda (READ_ONLY)")
    con.execute(
        f"""
        CREATE TABLE chan AS
        SELECT f.cid AS channel_id, f.oid AS object_id, f.gal_key AS gallery, f.picket,
            c.root || '-' || c.sub AS device, try_cast(c.p3 AS INTEGER) AS address
        FROM read_parquet('{CHANNELS.as_posix()}') f
        JOIN eda.ch c ON c.cid = f.cid
        WHERE f.stype IN ('Датчик дыма', 'Датчик температуры', 'Тепловой датчик')
        """
    )
    con.execute(
        f"""
        CREATE TABLE alarm AS SELECT * FROM read_parquet('{ALARMS.as_posix()}')
        """
    )
    con.execute(
        f"""
        CREATE TABLE ev_chan AS
        WITH nb AS (
            SELECT a.channel_id, a.moment::DATE AS day,
                (SELECT count(DISTINCT (r.gallery, r.picket)) FROM alarm r
                 WHERE r.object_id = a.object_id
                   AND (r.gallery <> a.gallery OR r.picket <> a.picket)
                   AND r.moment::DATE = a.moment::DATE
                   AND r.moment BETWEEN a.moment - INTERVAL {BURST_MINUTES} MINUTE
                                    AND a.moment + INTERVAL {BURST_MINUTES} MINUTE) AS n
            FROM alarm a
        )
        SELECT DISTINCT channel_id, day FROM nb WHERE n <= {BURST}
        """
    )
    return con


def loop_features() -> pd.DataFrame:
    """События соседей по шлейфу и прибора на сутки расчёта `day`."""
    con = channel_events()
    con.execute(
        f"""
        CREATE TABLE pairs AS
        SELECT DISTINCT a.object_id, a.gallery, a.picket, b.channel_id AS nb_channel
        FROM chan a JOIN chan b ON a.device = b.device
            AND abs(a.address - b.address) <= {LOOP_ADDRESSES}
            AND b.channel_id <> a.channel_id
            AND NOT (b.object_id = a.object_id AND b.gallery = a.gallery
                     AND b.picket = a.picket)
        WHERE a.picket IS NOT NULL
        """
    )
    con.execute(
        """
        CREATE TABLE unit_device AS
        SELECT DISTINCT a.object_id, a.gallery, a.picket, a.device FROM chan a
        WHERE a.picket IS NOT NULL
        """
    )
    loop = con.execute(
        """
        WITH e AS (
            SELECT p.object_id, p.gallery, p.picket, e.day, count(*) AS n
            FROM pairs p JOIN ev_chan e ON e.channel_id = p.nb_channel
            GROUP BY ALL
        )
        SELECT object_id, gallery, picket, d.day AS day,
            sum(n) FILTER (WHERE e.day = d.day - 1) AS loop_events_1d,
            sum(n) AS loop_events_7d
        FROM e, LATERAL (SELECT unnest(generate_series(e.day + 1, e.day + 7,
                                                       INTERVAL 1 DAY))::DATE AS day) d
        GROUP BY ALL
        """
    ).df()
    device = con.execute(
        """
        WITH e AS (
            SELECT c.device, e.day, count(*) AS n
            FROM ev_chan e JOIN chan c USING (channel_id) GROUP BY ALL
        )
        SELECT u.object_id, u.gallery, u.picket, (e.day + 1)::DATE AS day,
            sum(e.n) AS loop_device_1d
        FROM unit_device u JOIN e USING (device) GROUP BY ALL
        """
    ).df()
    out = loop.merge(device, on=KEY + ["day"], how="outer")
    out["day"] = pd.to_datetime(out["day"])
    return out


def add_loop(frame: pd.DataFrame, loop: pd.DataFrame) -> list[str]:
    cols = ["loop_events_1d", "loop_events_7d", "loop_device_1d"]
    merged = frame[KEY + ["day"]].merge(loop, on=KEY + ["day"], how="left")
    for c in cols:
        frame[c] = merged[c].fillna(0).to_numpy()
    # Сам пикет входит в прибор: его событие вчера вычитается из счёта прибора.
    frame["loop_device_1d"] = np.maximum(frame["loop_device_1d"] - frame["evt_days_1d"], 0)
    return cols


def add_hawkes(frame: pd.DataFrame) -> list[str]:
    ev = frame["label"].to_numpy(dtype=float)
    keys = frame[KEY].to_numpy()
    starts = np.flatnonzero(np.r_[True, (keys[1:] != keys[:-1]).any(axis=1)])
    ends = np.r_[starts[1:], len(frame)]
    cols = []
    for tau in TAUS:
        a = np.exp(-1.0 / tau)
        h = np.zeros(len(frame))
        for s, e in zip(starts, ends, strict=True):
            # h[t] = a * (h[t-1] + ev[t-1]): только прошлые сутки.
            h[s:e] = lfilter([0.0, a], [1.0, -a], ev[s:e])
        name = f"hawkes_tau{str(tau).replace('.', '_')}"
        frame[name] = h
        cols.append(name)
    return cols


def add_embedding(frame: pd.DataFrame, cutoff: str) -> tuple[list[str], list[str]]:
    """Профиль пикета по сигналам до `cutoff`, сжатый PCA и UMAP."""
    import umap

    con = duckdb.connect()
    prof = con.execute(
        f"""
        WITH a AS (
            SELECT *, hour(moment) AS h, isodow(moment) >= 6 AS wkend
            FROM read_parquet('{ALARMS.as_posix()}') WHERE moment < TIMESTAMP '{cutoff}'
        )
        SELECT object_id, gallery, picket, ln(1 + count(*)) AS n_log,
            avg((h < 6)::INT) AS h0, avg((h >= 6 AND h < 10)::INT) AS h1,
            avg((h >= 10 AND h < 14)::INT) AS h2, avg((h >= 14 AND h < 18)::INT) AS h3,
            avg((h >= 18)::INT) AS h4, avg(wkend::INT) AS wkend,
            avg((stype = 'Датчик дыма')::INT) AS smoke,
            avg((stype = 'Тепловой датчик')::INT) AS heat,
            count(DISTINCT moment::DATE) AS days, count(DISTINCT channel_id) AS chans
        FROM a GROUP BY ALL HAVING count(*) >= 3
        """
    ).df()
    feats = [c for c in prof.columns if c not in KEY]
    z = StandardScaler().fit_transform(prof[feats].to_numpy(dtype=float))
    pca = PCA(n_components=5, random_state=0).fit_transform(z)
    emb = umap.UMAP(n_components=2, n_neighbors=15, min_dist=0.1,
                    random_state=0).fit_transform(z)
    pca_cols = [f"emb_pca{i + 1}" for i in range(5)]
    umap_cols = ["emb_umap1", "emb_umap2"]
    table = prof[KEY].copy()
    table[pca_cols] = pca
    table[umap_cols] = emb
    merged = frame[KEY].merge(table, on=KEY, how="left")
    for c in pca_cols + umap_cols:
        frame[c] = merged[c].to_numpy()
    return pca_cols, umap_cols


def add_graph(frame: pd.DataFrame, train_end: str) -> list[str]:
    """Признаки распространения по графу, выученному на сутках до `train_end`."""
    con = duckdb.connect()
    ev = frame.loc[frame["label"] == 1, KEY + ["day"]].copy()
    units = frame[KEY].drop_duplicates().reset_index(drop=True)
    units["uid"] = np.arange(len(units))
    con.register("ev_raw", ev)
    con.register("units", units)
    con.execute(
        f"""
        CREATE TABLE cx AS SELECT "ид_объект" AS object_id, "родитель" AS complex_id
        FROM read_csv_auto('{OBJECTS.as_posix()}')
        """
    )
    con.execute(
        """
        CREATE TABLE ev AS
        SELECT u.uid, c.complex_id, e.day::DATE AS day, year(e.day) AS y
        FROM ev_raw e JOIN units u USING (object_id, gallery, picket)
        JOIN cx c USING (object_id)
        """
    )
    train_years = sorted(int(r[0]) for r in con.execute(
        f"SELECT DISTINCT y FROM ev WHERE day < DATE '{train_end}'").fetchall())
    con.execute(
        f"""
        CREATE TABLE pair AS
        SELECT a.uid AS v, b.uid AS u, a.y, count(*) AS n
        FROM ev a JOIN ev b ON b.complex_id = a.complex_id AND b.uid <> a.uid
            AND b.day = a.day + 1
        WHERE a.day < DATE '{train_end}' AND b.day < DATE '{train_end}'
        GROUP BY ALL
        """
    )
    con.execute(
        f"""
        CREATE TABLE src AS SELECT uid AS v, y, count(*) AS n FROM ev
        WHERE day < DATE '{train_end}' GROUP BY ALL
        """
    )
    # Ключ рёбер: год, который из них исключён, или 0 для всех лет обучения.
    keys = [0, *train_years]
    con.execute("CREATE TABLE edge (k INTEGER, v INTEGER, u INTEGER, w DOUBLE)")
    for k in keys:
        con.execute(
            f"""
            INSERT INTO edge
            WITH p AS (SELECT v, u, sum(n) AS n FROM pair WHERE y <> {k} GROUP BY ALL),
                 s AS (SELECT v, sum(n) AS n FROM src WHERE y <> {k} GROUP BY ALL)
            SELECT {k}, p.v, p.u, p.n / (s.n + {GRAPH_PRIOR}) FROM p JOIN s USING (v)
            """
        )
    rows = frame[KEY + ["day"]].merge(units, on=KEY, how="left")
    rows["k"] = np.where(rows["day"] < pd.Timestamp(train_end), rows["day"].dt.year, 0)
    rows = rows[rows["k"].isin(keys)]
    con.register("rows", rows[["uid", "day", "k"]])
    feats = con.execute(
        f"""
        WITH hit AS (
            SELECT r.uid, r.day, e.day AS ev_day, g.w
            FROM rows r
            JOIN edge g ON g.u = r.uid AND g.k = r.k
            JOIN ev e ON e.uid = g.v AND e.day BETWEEN r.day::DATE - {GRAPH_LAGS}
                                                  AND r.day::DATE - 1
        )
        SELECT uid, day,
            sum(w) FILTER (WHERE ev_day = day::DATE - 1) AS graph_1d,
            sum(w * exp(-(day::DATE - ev_day - 1) / 1.5)) AS graph_3d,
            max(w) FILTER (WHERE ev_day = day::DATE - 1) AS graph_max_1d
        FROM hit GROUP BY ALL
        """
    ).df()
    merged = frame[KEY + ["day"]].merge(units, on=KEY, how="left").merge(
        feats, on=["uid", "day"], how="left")
    cols = ["graph_1d", "graph_3d", "graph_max_1d"]
    for c in cols:
        frame[c] = merged[c].fillna(0).to_numpy()
    return cols


def add_space_moon(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    rows = [line.split() for line in KP.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")]
    kp = pd.DataFrame({"day": pd.to_datetime([f"{r[0]}-{r[1]}-{r[2]}" for r in rows]),
                       "kp": [float(r[7]) for r in rows]})
    daily = kp.groupby("day")["kp"].agg(["max", "mean"]).sort_index()
    table = pd.DataFrame({
        "space_kp_max_1d": daily["max"].shift(1),
        "space_kp_mean_1d": daily["mean"].shift(1),
        "space_storm_1d": (daily["max"].shift(1) >= 5).astype(float),
        "space_kp_max_3d": daily["max"].shift(1).rolling(3).max(),
    })
    merged = frame[["day"]].merge(table, left_on="day", right_index=True, how="left")
    space = list(table.columns)
    for c in space:
        frame[c] = merged[c].to_numpy()
    # Синодический месяц от новолуния 2000-01-06 18:14 UT.
    days = (frame["day"] - pd.Timestamp("2000-01-06 18:14")).dt.total_seconds() / 86400
    phase = 2 * np.pi * ((days / 29.530588853) % 1)
    frame["moon_cos"], frame["moon_sin"] = np.cos(phase), np.sin(phase)
    return space, ["moon_cos", "moon_sin"]


def event_days() -> pd.DataFrame:
    """Сутки пикета с событием и первый момент события в эти сутки."""
    con = duckdb.connect()
    con.execute(f"CREATE TABLE alarm AS SELECT * FROM read_parquet('{ALARMS.as_posix()}')")
    return con.execute(
        f"""
        WITH nb AS (
            SELECT a.object_id, a.gallery, a.picket, a.moment,
                (SELECT count(DISTINCT (r.gallery, r.picket)) FROM alarm r
                 WHERE r.object_id = a.object_id
                   AND (r.gallery <> a.gallery OR r.picket <> a.picket)
                   AND r.moment::DATE = a.moment::DATE
                   AND r.moment BETWEEN a.moment - INTERVAL {BURST_MINUTES} MINUTE
                                    AND a.moment + INTERVAL {BURST_MINUTES} MINUTE) AS n
            FROM alarm a
        )
        SELECT object_id, gallery, picket, min(moment) AS moment
        FROM nb WHERE n <= {BURST} GROUP BY object_id, gallery, picket, moment::DATE
        ORDER BY object_id, gallery, picket, moment
        """
    ).df()


def add_character(frame: pd.DataFrame) -> list[str]:
    ev = event_days()
    g = ev.groupby(KEY, sort=False)
    ev["gap"] = g["moment"].diff().dt.total_seconds() / 86400
    ev["prev_gap"] = g["gap"].shift(1)
    hour = ev["moment"].dt.hour + ev["moment"].dt.minute / 60
    ev["c"], ev["s"] = np.cos(2 * np.pi * hour / 24), np.sin(2 * np.pi * hour / 24)
    ev["bin"] = (hour // 4).astype(int)
    ev["n"] = g.cumcount() + 1
    for col in ("c", "s"):
        ev[f"cum_{col}"] = ev.groupby(KEY, sort=False)[col].cumsum()
    gap = ev["gap"].fillna(0)
    ev["g1"] = gap.groupby([ev[k] for k in KEY], sort=False).cumsum()
    ev["g2"] = (gap ** 2).groupby([ev[k] for k in KEY], sort=False).cumsum()
    ev["ng"] = ev["gap"].notna().groupby([ev[k] for k in KEY], sort=False).cumsum()
    pair = ev["gap"].notna() & ev["prev_gap"].notna()
    for name, val in (("px", ev["prev_gap"]), ("py", ev["gap"]), ("pxx", ev["prev_gap"] ** 2),
                      ("pyy", ev["gap"] ** 2), ("pxy", ev["prev_gap"] * ev["gap"])):
        ev[name] = val.where(pair, 0).groupby([ev[k] for k in KEY], sort=False).cumsum()
    ev["np"] = pair.groupby([ev[k] for k in KEY], sort=False).cumsum()
    bins = np.zeros((len(ev), 6))
    bins[np.arange(len(ev)), ev["bin"].to_numpy()] = 1
    bins = pd.DataFrame(bins).groupby([ev[k].to_numpy() for k in KEY], sort=False).cumsum()
    share = bins.to_numpy() / ev["n"].to_numpy()[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        ev["char_hour_entropy"] = -np.nansum(np.where(share > 0, share * np.log(share), 0), axis=1)
        mu = ev["g1"] / ev["ng"]
        sd = np.sqrt(np.maximum(ev["g2"] / ev["ng"] - mu ** 2, 0))
        ev["char_burstiness"] = np.where(ev["ng"] >= 2, (sd - mu) / (sd + mu), np.nan)
        n = ev["np"]
        cov = ev["pxy"] / n - (ev["px"] / n) * (ev["py"] / n)
        vx = ev["pxx"] / n - (ev["px"] / n) ** 2
        vy = ev["pyy"] / n - (ev["py"] / n) ** 2
        ev["char_memory"] = np.where(n >= 3, cov / np.sqrt(vx * vy), np.nan)
        ev["char_rayleigh"] = np.sqrt(ev["cum_c"] ** 2 + ev["cum_s"] ** 2) / ev["n"]
        ev["char_hour_cos"] = ev["cum_c"] / ev["n"]
        ev["char_hour_sin"] = ev["cum_s"] / ev["n"]
    cols = ["char_burstiness", "char_memory", "char_rayleigh", "char_hour_cos",
            "char_hour_sin", "char_hour_entropy"]
    # Строке суток D достаются свойства событий строго до полуночи D.
    ev["key"] = ev[KEY].astype(str).agg("|".join, axis=1)
    left = frame[KEY + ["day"]].copy()
    left["key"] = left[KEY].astype(str).agg("|".join, axis=1)
    left["pos"] = np.arange(len(left))
    right = ev[["key", "moment", *cols]].sort_values("moment")
    merged = pd.merge_asof(left.sort_values("day"), right, left_on="day", right_on="moment",
                           by="key", allow_exact_matches=False).sort_values("pos")
    for c in cols:
        frame[c] = merged[c].to_numpy()
    return cols


def lz76(bits: np.ndarray) -> int:
    """Число фраз разбиения Лемпеля–Зива 1976 года."""
    s = "".join("1" if b else "0" for b in bits)
    i, c, n = 0, 0, len(s)
    while i < n:
        k = 1
        while i + k <= n and s[i:i + k] in s[:i + k - 1]:
            k += 1
        c += 1
        i += k
    return c


def add_lz(frame: pd.DataFrame) -> list[str]:
    y = frame["label"].to_numpy()
    keys = frame[KEY].to_numpy()
    starts = np.flatnonzero(np.r_[True, (keys[1:] != keys[:-1]).any(axis=1)])
    ends = np.r_[starts[1:], len(frame)]
    out = np.zeros(len(frame))
    for s0, e0 in zip(starts, ends, strict=True):
        seg = y[s0:e0]
        if seg.sum() == 0:
            continue
        csum = np.r_[0, np.cumsum(seg)]
        for j in range(e0 - s0):
            lo = max(0, j - LZ_DAYS)
            if csum[j] - csum[lo] > 0:
                out[s0 + j] = lz76(seg[lo:j])
    frame["lz_complexity"] = out
    return ["lz_complexity"]


def add_iforest(frame: pd.DataFrame, columns: list[str], train_end: str) -> list[str]:
    from sklearn.ensemble import IsolationForest

    fit = (frame["hour"] < pd.Timestamp(train_end)).to_numpy()
    x = frame[columns].to_numpy(dtype=np.float32)
    med = np.nanmedian(x[fit], axis=0)
    x = np.where(np.isnan(x), med, x)
    rng = np.random.default_rng(0)
    sample = rng.choice(np.flatnonzero(fit), size=min(200_000, int(fit.sum())), replace=False)
    forest = IsolationForest(n_estimators=200, random_state=0, n_jobs=-1).fit(x[sample])
    frame["iforest_score"] = -forest.score_samples(x)
    return ["iforest_score"]


def add_wx_pca(frame: pd.DataFrame, train_end: str) -> tuple[list[str], list[str]]:
    wx = [c for c in frame.columns if c.startswith("wx_")]
    fit = frame["hour"] < pd.Timestamp(train_end)
    medians = frame.loc[fit, wx].median()
    values = frame[wx].fillna(medians).to_numpy(dtype=float)
    scaler = StandardScaler().fit(values[fit.to_numpy()])
    pca = PCA(n_components=3, random_state=0).fit(scaler.transform(values[fit.to_numpy()]))
    comp = pca.transform(scaler.transform(values))
    cols = [f"wxpca{i + 1}" for i in range(3)]
    frame[cols] = comp
    return cols, wx


def evaluate(frame, columns, train_end, valid_end, config, rank: bool = False) -> dict:
    fit = (frame["hour"] < pd.Timestamp(train_end)).to_numpy()
    val = ((frame["hour"] >= pd.Timestamp(train_end))
           & (frame["hour"] < pd.Timestamp(valid_end))).to_numpy()
    x = frame[columns].to_numpy(dtype=np.float32)
    y = frame["label"].to_numpy(dtype=np.int8)
    extra: dict = {}
    if rank:
        # LambdaRank: запрос это сутки, строки идут подряд по суткам.
        order = np.argsort(frame["hour"].to_numpy()[fit], kind="stable")
        idx = np.flatnonzero(fit)[order]
        _, sizes = np.unique(frame["hour"].to_numpy()[idx], return_counts=True)
        data = lgb.Dataset(x[idx], label=y[idx], group=sizes, params={"verbose": -1}).construct()
        extra = {"objective": "lambdarank", "metric": "ndcg", "eval_at": [5]}
    else:
        data = lgb.Dataset(x[fit], label=y[fit], params={"verbose": -1}).construct()
    margins, aucs = [], []
    for seed in base.SEEDS:
        booster = lgb.train({**base.BASE_PARAMS, **config.get("params", {}), **extra,
                             "seed": seed, "num_threads": 28}, data, num_boost_round=ROUNDS)
        m = booster.predict(x[val], raw_score=True)
        margins.append(m)
        aucs.append(base.pr_auc(y[val], m))
    p = 1 / (1 + np.exp(-np.mean(margins, axis=0)))
    nv = frame["naive"].to_numpy()[val]
    days = frame["hour"].to_numpy()[val].astype("datetime64[D]")
    flag = rolling.rolling_alerts(p, nv, days, WINDOW)
    return {
        "pr_auc": round(base.pr_auc(y[val], p), 5),
        "pr_auc_seed_spread": round(max(aucs) - min(aucs), 5),
        "rule": base.rule_point(y[val], nv == 1),
        "model": base.rule_point(y[val], flag),
    }


def build(hi: str, cutoff: str, train_end: str):
    config = wide_config()
    columns = base.columns_for(base.connect()[1], config["groups"])
    frame = load_panel(columns, hi)
    t = time.time()
    groups = {"loop": add_loop(frame, loop_features())}
    groups["hawkes"] = add_hawkes(frame)
    groups["emb_pca"], groups["emb_umap"] = add_embedding(frame, cutoff)
    groups["wxpca"], wx = add_wx_pca(frame, train_end)
    groups["graph"] = add_graph(frame, train_end)
    groups["space"], groups["moon"] = add_space_moon(frame)
    groups["char"] = add_character(frame)
    groups["lz"] = add_lz(frame)
    groups["iforest"] = add_iforest(frame, columns, train_end)
    print(f"признаки гипотез за {time.time() - t:.0f} c", flush=True)
    return frame, columns, groups, wx, config


def variants(columns, groups, wx) -> dict[str, list[str]]:
    no_wx = [c for c in columns if c not in wx]
    return {
        "base": columns,
        "loop": columns + groups["loop"],
        "hawkes": columns + groups["hawkes"],
        "emb_pca": columns + groups["emb_pca"],
        "emb_umap": columns + groups["emb_umap"],
        "wxpca": no_wx + groups["wxpca"],
        "hawkes_wxpca": no_wx + groups["wxpca"] + groups["hawkes"],
        "graph": columns + groups["graph"],
        "space": columns + groups["space"],
        "moon": columns + groups["moon"],
        "char": columns + groups["char"],
        "lz": columns + groups["lz"],
        "iforest": columns + groups["iforest"],
        "rank": columns,
        "all": no_wx + groups["wxpca"] + groups["loop"] + groups["hawkes"]
        + groups["emb_pca"] + groups["emb_umap"],
    }


def cmd_screen(args) -> None:
    frame, columns, groups, wx, config = build(SCREEN[1], SCREEN[0], SCREEN[0])
    result = {"screen_year": SCREEN, "train_before": SCREEN[0], "variants": {}}
    only = set(args.only.split(",")) if args.only else None
    for name, cols in variants(columns, groups, wx).items():
        if only and name not in only:
            continue
        r = evaluate(frame, cols, SCREEN[0], SCREEN[1], config, rank=name == "rank")
        result["variants"][name] = {"features": len(cols), **r}
        print(f"{name:9s} признаков {len(cols):3d}  PR-AUC {r['pr_auc']:.4f} ± "
              f"{r['pr_auc_seed_spread']:.4f}  попаданий правило {r['rule']['hits']}"
              f"/{r['rule']['alerts']}, модель {r['model']['hits']}/{r['model']['alerts']}",
              flush=True)
    target = OUT / ("hypotheses_screen.json" if not only else
                    f"hypotheses_screen_{'_'.join(sorted(only))}.json")
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_confirm(args) -> None:
    chosen = args.variants.split(",")
    result = {"variants": {}, "folds": []}
    pooled: dict[str, dict] = {}
    for start, end in CONFIRM_FOLDS:
        # Профиль пикета и оси погоды учатся только на сутках до фолда.
        frame, columns, groups, wx, config = build(end, start, start)
        fold = {"valid": f"{start}..{end}"}
        for name in ["base", *chosen]:
            cols = variants(columns, groups, wx)[name]
            fold[name] = evaluate(frame, cols, start, end, config, rank=name == "rank")
            acc = pooled.setdefault(name, {"hits": 0, "alerts": 0, "rule_hits": 0})
            acc["hits"] += fold[name]["model"]["hits"]
            acc["alerts"] += fold[name]["model"]["alerts"]
            acc["rule_hits"] += fold[name]["rule"]["hits"]
            print(f"{start}..{end} {name:9s} PR-AUC {fold[name]['pr_auc']:.4f}  "
                  f"правило {fold[name]['rule']['hits']}, модель {fold[name]['model']['hits']}"
                  f"/{fold[name]['model']['alerts']}", flush=True)
        result["folds"].append(fold)
    result["pooled"] = pooled
    (OUT / "hypotheses_confirm.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(pooled, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("screen")
    sc.add_argument("--only", default="")
    c = sub.add_parser("confirm")
    c.add_argument("variants")
    args = parser.parse_args()
    {"screen": cmd_screen, "confirm": cmd_confirm}[args.cmd](args)


if __name__ == "__main__":
    main()
