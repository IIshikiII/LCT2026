# Gap between the frontend contract and the backend

This document compares three things:

1. What the frontend needs. See [04-api-required-by-frontend.md](04-api-required-by-frontend.md).
2. What `../../ARM-ODS-backend-spec.md` promises.
3. What `backend/` contains today.

Current state of the code: the skeleton runs, `GET /healthz` answers, and
`python -m app.cli migrate` applies `001_init.sql`. Every package under `app/`
except `config`, `db`, `logging`, `main`, `migrate` and `cli` is an empty
`__init__.py`. None of the 14 contract endpoints exist yet.

Work through the tasks in order. Task 1 comes first, because the specification
is the input of every task after it.

The same review found six gaps that the frontend must close, not the backend.
They live in `frontend/docs/09-gap-tasks.md` and they are not repeated here. Read
that list before you start task 6, because two of its decisions change what
`/facilities` returns. See the section "Owned by the frontend" at the end.

## Task 1. Fix the specification

The specification is close to the frontend contract, but it disagrees with the
mocks in six places. Fix `../../ARM-ODS-backend-spec.md` before you write code
against it.

- [ ] **§8, order lifecycle uses `DONE`.** The line reads
      `AUTO_CREATED → CONFIRMED → IN_PROGRESS → DONE`. The frontend has no `DONE`
      status. `GET /meta` and the mock handlers use `CLOSED`, and the close action
      sets `CLOSED`. Replace `DONE` with `CLOSED`.
- [ ] **§5 rule 3, the list of action codes is wrong.** It reads
      "`confirm`, `reject`, `confirm_order` (прогноз), `start`, `close` (заявка)".
      In the mocks `confirm` belongs to the order, not to the prediction, and the
      prediction code `inspect` is missing. Correct sets: prediction takes
      `confirm_order`, `inspect`, `reject`. Order takes `confirm`, `reject`,
      `start`, `close`.
- [ ] **§4, `/facilities` misses the `district` parameter.** The specification
      lists `?bbox&direction&level`. `facilityParams` in
      `frontend/src/shared/api/filters.ts` also sends `district`. Add it.
- [ ] **§4, `/orders` misses the `sort` parameter.** The specification lists
      `?status&dueBefore&page&pageSize`. `orderParams` also sends `sort`. Add it,
      and add the sort whitelist of both lists to the specification.
- [ ] **§6, the data model has no source for the time series.** The card needs
      `GET /predictions/{id}/timeseries` with named series such as
      `Температура` and `Влажность в камере`. The schema holds `alarm_event`
      (discrete events) and `weather_hourly` (district level), but no table of
      sensor readings. Add a `sensor_reading` table to §6, partitioned by month
      like `alarm_event`.
- [ ] **§6, the data model has no source for the `/meta` dictionaries.** `/meta`
      must return `districts` with a label and `reasons` per direction. Neither
      lives in the schema. State in §6 that `app/meta/` owns them as a code
      registry, or add tables. Pick one and write it down.

Two more points are worth stating in the specification, because they surprise a
reader who only knows the mocks.

- [ ] **§4, `/facilities` properties.** The mocks also send `address` and
      `collector` in `properties`. The map popup uses them. Add both to the list.
- [ ] **§4, three endpoints skip the list envelope.** `/facilities`,
      `/metrics/models` and `/dashboard/top-risks` return a bare array or a
      GeoJSON object. The sentence "все списки - конверт" reads as if they do not.

## Task 2. Define the tables

- [ ] Write `app/tables/` as `sqlalchemy.Table` objects for every table in
      `001_init.sql`. No ORM, per specification §2.
- [ ] Add migration `002` with the `sensor_reading` table from task 1:
      `sensor_id`, `facility_id`, `metric` (text), `observed_at`, `value`, `unit`.
      Partition by month. Index on `(facility_id, metric, observed_at)`.
- [ ] Decide how `work_order` resolves the reason list for the close form.
      `prediction_id` is nullable today, so an order without a prediction has no
      direction. Either make the column `NOT NULL`, or store `direction` on the
      order.

## Task 3. Write the response DTOs

- [ ] Write `app/schemas/` as pydantic models that mirror
      `frontend/src/shared/api/schemas.ts` field for field.
- [ ] Set `alias_generator=to_camel` and `populate_by_name=True` on a shared base
      model. Dump every response with `by_alias=True`.
- [ ] Keep `direction`, `level` and `status` as `str`. No enum, per specification
      §5 rule 1.
- [ ] Add the list envelope as a generic model with `items`, `page`, `pageSize`
      and `total`.
- [ ] Write a test that fails when a DTO field name is not camelCase.

## Task 4. Build the meta registry and GET /meta

- [ ] Write `app/meta/` with the registry of directions, risk levels, statuses,
      districts, journal columns, order columns, dashboard widgets, and reason
      lists. Take the reference values from section 5 of
      [04-api-required-by-frontend.md](04-api-required-by-frontend.md).
- [ ] Serve `GET /api/v1/meta` from the registry.
- [ ] Write the flexibility test: add a fifth direction to the registry, and the
      new code must appear in `/meta`, in `/predictions` filters, in `/facilities`
      and in `/dashboard/summary` with no router change.

## Task 5. Mount the API

- [ ] `app/main.py` serves only `/healthz` today. Add an `APIRouter` per area
      (`meta`, `predictions`, `facilities`, `orders`, `metrics`, `dashboard`) and
      mount them under `/api/v1`.
- [ ] Return errors as `{"detail": ...}` with 400, 404, 409 or 422.
- [ ] Add a `request_id` to every log line, per specification §10.

## Task 6. Read endpoints

- [ ] `GET /predictions` with all nine parameters, the repeatable ones included,
      the inclusive end of day on `to`, the sort whitelist, and the envelope.
- [ ] `GET /predictions/{id}` returning `blocks` from the stored JSONB and
      `actions` from the domain layer.
- [ ] `GET /predictions/{id}/timeseries` reading `sensor_reading`, with
      `markerAt` set to `computedAt`.
- [ ] `GET /facilities` returning GeoJSON, one feature per facility, the
      prediction with the highest probability, and `[lon, lat]` order.
      Two parts of this endpoint wait on a frontend decision. Do not build them
      yet. `bbox` has no caller today, and the collector routes may move out of
      this response. See frontend tasks 2 and 4.
- [ ] `GET /facilities/{id}`.
- [ ] `GET /orders` and `GET /orders/{id}`.
- [ ] Return 404 with a readable message for every unknown id.

## Task 7. Actions and the domain layer

- [ ] Write `app/domain/` with the action table: status to available actions, per
      entity. Take the reference sets from section 7 of
      [04-api-required-by-frontend.md](04-api-required-by-frontend.md).
- [ ] Serve `POST /predictions/{id}/actions/{code}` and
      `POST /orders/{id}/actions/{code}`. Both take a flat body and return the
      whole updated entity.
- [ ] An unknown code must not return 500. The mocks move a prediction to
      `IN_REVIEW`.
- [ ] `close` must write `outcome` with `predictionConfirmed`, and it must move
      the linked prediction to `CLOSED`. Accept a boolean only. The mock handler
      also accepts the string `"true"`, and frontend task 6 removes that.
- [ ] Write every action to `action_log`.
- [ ] Keep the cross entity effects the mocks have: `confirm_order` moves an
      `AUTO_CREATED` order to `CONFIRMED`, and `reject` on a prediction rejects
      its open order.

## Task 8. Automatic work orders

- [ ] Create an order for a prediction at level `HIGH` or `CRITICAL` when the
      facility has no open order for the same direction.
- [ ] Set `dueAt` to `computedAt + horizonHours * 0.5`, with the coefficient in
      the direction config.
- [ ] Take `workType` from the direction plugin.
- [ ] Keep the thresholds and the duplicate rule in config, not in code.

## Task 9. Metrics and dashboard

- [ ] `GET /metrics/models` from `model_metric`, one row per direction, with
      `targetPrecision` 0.7 and `targetRecall` 0.5 in every entry.
- [ ] `GET /metrics/pipeline` from `pipeline_run`, with `targetComputeMs` 300000
      and `targetHorizonHours` 24.
- [ ] `GET /dashboard/summary` with the four counter dictionaries and the total.
- [ ] `GET /dashboard/top-risks` sorted by probability, without `REJECTED` and
      `CLOSED`.
- [ ] Compute Precision and Recall from closed orders, per specification §9.
      Write the method into `model_metric.method` and into `backend/docs/`.

## Task 10. Data and the pipeline

- [ ] Write `app/synth/` to fill the raw tables, `sensor_reading` included.
- [ ] Implement `python -m app.cli seed`, so the frontend works against the real
      backend after `docker compose up`.
- [ ] Write `app/features/`, `app/ml/` with the direction plugin protocol, and
      `app/pipeline/` with the run.
- [ ] Guard the run with `pg_try_advisory_lock`, per specification §7.
- [ ] Enforce the two hard numbers: `horizonHours` at least 24, `computeMs` below
      300000.
- [ ] A published prediction must carry at least one `factors` block and one
      `timeseries` block.
- [ ] Implement the remaining CLI commands: `run-pipeline`, `train`, `ingest`.
      They print "not implemented" today.

## Task 11. Project skills

- [ ] Add the four project skills from specification §12 once the code exists:
      `arm-backend-api-contract`, `arm-direction-plugin`, `arm-ingest`,
      `arm-ml-eval`.

## Owned by the frontend

These six gaps came out of the same review. The backend does not fix them, and
they carry no task here. They are open in `frontend/docs/09-gap-tasks.md`.

1. `frontend/docs/02-api-contract.md` has drifted from `schemas.ts` in eight
   places. That document is the human readable face of this contract, so a
   backend author who reads it gets a wrong `AppMeta`, a wrong
   `PipelineHealth` and a wrong `DashboardSummary`.
2. `bbox` has no caller. The parameter exists in the frontend filter builder, but
   no screen sends it and the mock ignores it.
3. The map drops the `status` filter while the journal keeps it. The frontend
   decides whether this stays.
4. The collector routes travel inside `/facilities` on every poll, once per
   minute. The frontend decides where they move.
5. `GET /predictions/{id}/timeseries` declares `from` and `to`, and the card
   sends neither. If the frontend drops them, remove them from specification §4.
6. The mock accepts the string `"true"` for `predictionConfirmed`. Do not copy
   this into the backend.

Task 1 of this document still fixes the backend specification, because the
specification is wrong on its own terms. The frontend list fixes the frontend
document. The two do not overlap.

## Differences that are intentional

Do not "fix" these.

1. Specification §13 gives the four directions an uneven fill: `WEAR_OUT`
   produces no predictions at all, and `UNAUTHORIZED_ACCESS` produces broken
   ones. The frontend mocks fill all four evenly. Both are correct. The mocks
   prove the interface works, and the synthetic data proves the interface
   survives holes.
2. The frontend parses responses without throwing. A missing field shows a dash
   instead of an error. This hides a contract break during a demo, so trust the
   tests, not the screen.
