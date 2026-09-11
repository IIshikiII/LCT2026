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

The same review found seven gaps on the frontend side. The frontend closed all
seven, so no task list remains there. Two of those decisions changed this
contract, and both are already folded into
[04-api-required-by-frontend.md](04-api-required-by-frontend.md): the map now
sends `bbox`, and the collector routes moved to `GET /facilities/lines`. The
section "Settled by the frontend" at the end lists all seven.

One later change also touched the contract: a status now carries `colorVar` and
`terminal` in `/meta`. Section 4 of the contract document describes both, and
task 1 below adds them to the specification.

## Task 1. Fix the specification

The specification is close to the frontend contract, but it disagrees with the
mocks in eight places. Fix `../../ARM-ODS-backend-spec.md` before you write code
against it.

The order status `DONE` was another item here. The team resolved it the other
way: the frontend moved to `DONE`, and specification §8 was right. Nothing to fix.

- [x] **§4, `statuses` misses two fields.** The specification writes
      `statuses[{code,label,scope}]`. A status also carries `colorVar` (colour of
      the dot in the label) and `terminal` (work is over, stop warning about the
      deadline). Both are optional in the schema and both change the screen, so
      the backend has to send them. See section 4 of
      [04-api-required-by-frontend.md](04-api-required-by-frontend.md) and
      `frontend/docs/adr/0010-status-colour-from-meta.md`.

- [x] **§4, `/facilities` misses the `/facilities/lines` endpoint.** The collector
      routes used to ride inside the `/facilities` response. The frontend moved
      them to their own endpoint, because the map polls the points once per
      minute and the routes never change. Add the endpoint to the list.
- [x] **§4, the time series take no parameters.** The specification writes
      `GET /predictions/{id}/timeseries?from&to`. Nothing ever sent them, and the
      frontend dropped them. Remove both from the line.
- [x] **§5 rule 3, the list of action codes is wrong.** It reads
      "`confirm`, `reject`, `confirm_order` (прогноз), `start`, `close` (заявка)".
      In the mocks `confirm` belongs to the order, not to the prediction, and the
      prediction code `inspect` is missing. Correct sets: prediction takes
      `confirm_order`, `inspect`, `reject`. Order takes `confirm`, `reject`,
      `start`, `close`.
- [x] **§4, `/facilities` misses the `district` parameter.** The specification
      lists `?bbox&direction&level`. `facilityParams` in
      `frontend/src/shared/api/filters.ts` also sends `district`. Add it.
- [x] **§4, `/orders` misses the `sort` parameter.** The specification lists
      `?status&dueBefore&page&pageSize`. `orderParams` also sends `sort`. Add it,
      and add the sort whitelist of both lists to the specification.
- [x] **§6, the data model has no source for the time series.** The card needs
      `GET /predictions/{id}/timeseries` with named series such as
      `Температура` and `Влажность в камере`. The schema holds `alarm_event`
      (discrete events) and `weather_hourly` (district level), but no table of
      sensor readings. Add a `sensor_reading` table to §6, partitioned by month
      like `alarm_event`.
- [x] **§6, the data model has no source for the `/meta` dictionaries.** `/meta`
      must return `districts` with a label and `reasons` per direction. Neither
      lives in the schema. State in §6 that `app/meta/` owns them as a code
      registry, or add tables. Pick one and write it down.

Two more points are worth stating in the specification, because they surprise a
reader who only knows the mocks.

- [x] **§4, `/facilities` properties.** The mocks also send `address` and
      `collector` in `properties`. The map popup uses them. Add both to the list.
- [x] **§4, four endpoints skip the list envelope.** `/facilities` and
      `/facilities/lines` return GeoJSON, `/metrics/models` and
      `/dashboard/top-risks` return a bare array. The sentence
      "все списки - конверт" reads as if they do not.

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

- [x] Write `app/schemas/` as pydantic models that mirror
      `frontend/src/shared/api/schemas.ts` field for field.
- [x] Set `alias_generator=to_camel` and `populate_by_name=True` on a shared base
      model. Dump every response with `by_alias=True`.
- [x] Keep `direction`, `level` and `status` as `str`. No enum, per specification
      §5 rule 1.
- [x] Add the list envelope as a generic model with `items`, `page`, `pageSize`
      and `total`.
- [x] Write a test that fails when a DTO field name is not camelCase.

## Task 4. Build the meta registry and GET /meta

- [ ] Write `app/meta/` with the registry of directions, risk levels, statuses,
      districts, journal columns, order columns, dashboard widgets, and reason
      lists. Take the reference values from section 5 of
      [04-api-required-by-frontend.md](04-api-required-by-frontend.md).
- [ ] Give every status a `colorVar` and mark the terminal ones with
      `terminal: true`. Skip this and the interface loses the colour of the
      status column and starts calling finished orders overdue.
- [ ] Serve `GET /api/v1/meta` from the registry.
- [ ] Write the flexibility test: add a fifth direction to the registry, and the
      new code must appear in `/meta`, in `/predictions` filters, in `/facilities`
      and in `/dashboard/summary` with no router change.

## Task 5. Mount the API

- [x] `app/main.py` serves only `/healthz` today. Add an `APIRouter` per area
      (`meta`, `predictions`, `facilities`, `orders`, `metrics`, `dashboard`) and
      mount them under `/api/v1`.
- [x] Return errors as `{"detail": ...}` with 400, 404, 409 or 422.
- [x] Add a `request_id` to every log line, per specification §10.

## Task 6. Read endpoints

- [x] `GET /predictions` with all nine parameters, the repeatable ones included,
      the inclusive end of day on `to`, the sort whitelist, and the envelope.
- [x] `GET /predictions/{id}` returning `blocks` from the stored JSONB and
      `actions` from the domain layer.
- [x] `GET /predictions/{id}/timeseries` reading `sensor_reading`, with
      `markerAt` set to `computedAt`.
- [x] `GET /facilities` returning GeoJSON, one feature per facility, the
      prediction with the highest probability, and `[lon, lat]` order. Filter by
      `bbox` with the edges included. Ignore a `bbox` that does not parse.
- [x] `GET /facilities/lines` returning the collector routes. Separate endpoint,
      because the map loads it once and never polls it.
- [x] `GET /facilities/{id}`.
- [x] `GET /orders` and `GET /orders/{id}`.
- [x] Return 404 with a readable message for every unknown id.

## Task 7. Actions and the domain layer

- [x] Write `app/domain/` with the action table: status to available actions, per
      entity. Take the reference sets from section 7 of
      [04-api-required-by-frontend.md](04-api-required-by-frontend.md).
- [x] Serve `POST /predictions/{id}/actions/{code}` and
      `POST /orders/{id}/actions/{code}`. Both take a flat body and return the
      whole updated entity.
- [x] An unknown code must not return 500. The mocks move a prediction to
      `IN_REVIEW`.
- [x] `close` must write `outcome` with `predictionConfirmed`, and it must move
      the linked prediction to `CLOSED`. Accept a boolean only. A string does not
      count as a confirmation.
- [x] Write every action to `action_log`.
- [x] Keep the cross entity effects the mocks have: `confirm_order` moves an
      `AUTO_CREATED` order to `CONFIRMED`, and `reject` on a prediction rejects
      its open order.

## Task 8. Automatic work orders

- [x] Create an order for a prediction at level `HIGH` or `CRITICAL` when the
      facility has no open order for the same direction.
- [x] Set `dueAt` to `computedAt + horizonHours * 0.5`, with the coefficient in
      the direction config.
- [x] Take `workType` from the direction plugin.
- [x] Keep the thresholds and the duplicate rule in config, not in code.

## Task 9. Metrics and dashboard

- [x] `GET /metrics/models` from `model_metric`, one row per direction, with
      `targetPrecision` 0.7 and `targetRecall` 0.5 in every entry. A direction
      without an evaluation stays out of the answer, because zeroes read as
      "the model is broken".
- [x] `GET /metrics/pipeline` from `pipeline_run`, with `targetComputeMs` 300000
      and `targetHorizonHours` 24. Only a run at status `DONE` counts.
- [x] `GET /dashboard/summary` with the four counter dictionaries and the total.
      The keys come from the registries, so a direction without predictions
      still gets a zero.
- [x] `GET /dashboard/top-risks` sorted by probability, without the statuses
      marked `terminal` in the registry.

The last item of this task was wrong, and the team dropped it. It read: compute
Precision and Recall from the orders at status `DONE`. That contradicts
specification §9 and [06-labels-and-metrics.md](06-labels-and-metrics.md).

The dispatcher mark `predictionConfirmed` measures the usefulness of an order,
not the accuracy of a prediction. It lives on a biased sample, because only the
predictions that sent a crew ever get one. Recall from those marks cannot be
computed at all: there are no missed events among closed orders.

So the real Precision and Recall come from the events in the data, an offline
evaluation on a holdout split by time writes them, and that belongs to task 10
with the model. The plan for the dispatcher marks after the service goes live
lies in `../../roadmap-post-deployment.md`.

- [ ] Write the offline evaluation in task 10, and fill `model_metric.method`
      with the honest name of the method. Never mix two methods in one number.

## Task 10. Data and the pipeline

The ML side of the team owns the model itself: the exploratory analysis, the
choice of model, the cross validation and the metrics. The backend gives them
the frame and does not pick the model for them.

- [x] Write `app/ml/` with the plugin protocol, the predictor registry and the
      version registry. Done ahead of task 9, see [08-ml-plugin.md](08-ml-plugin.md).
- [x] Add the `research` dependency group and the `research` service, so the
      exploratory work runs against the same database and the same MLflow.
- [ ] Write `app/synth/` to fill the raw tables, `sensor_reading` included.
      Keep it small: the real exports arrive soon, and a rich generator would
      be thrown away. It has to feed the frontend, not to prove a metric.
- [ ] Implement `python -m app.cli seed`, so the frontend works against the real
      backend after `docker compose up`.
- [ ] Write `app/features/` and the predictors in `app/ml/plugins/`.
- [ ] Write `app/pipeline/` with the run.
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

## Settled by the frontend

These seven gaps came out of the same review and belong to the frontend. All
seven are closed. They carry no task here, and
[04-api-required-by-frontend.md](04-api-required-by-frontend.md) already
describes the result. The list stays so that nobody reopens a settled question.

1. **The terminal order status.** Specification §8 says `DONE`, and the frontend
   used `CLOSED`. The frontend moved to `DONE`, so §8 stays as it is. An order
   ends at `DONE`. A prediction ends at `CLOSED`.
2. **The frontend contract document.** `frontend/docs/02-api-contract.md` had
   drifted from `schemas.ts` in eight places. It now matches.
3. **`bbox` works.** The map sends the viewport after every `moveend`, rounded
   outward to 0.05 degrees, and the mock filters by it.
4. **The map ignores `status` and the date range on purpose.** It shows risk, not
   the stage of work. The filter bar hides both controls on that screen, so the
   interface does not offer a filter that does nothing.
5. **The collector routes moved out of `/facilities`** into
   `GET /facilities/lines`, loaded once per session.
6. **The time series take no parameters.** `from` and `to` are gone from the
   frontend contract. Task 1 removes them from specification §4.
7. **`predictionConfirmed` takes a boolean only.** The string branch is gone from
   the mock, and a test holds that line.

Task 1 of this document still fixes the backend specification, because the
specification is wrong on its own terms.

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
