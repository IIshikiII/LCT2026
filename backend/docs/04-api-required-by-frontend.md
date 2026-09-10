# API required by the frontend

This document lists every endpoint the backend must serve for the dispatcher
workstation to work. It states the exact request parameters, the exact response
shapes, and the behaviour that the frontend mocks already implement.

Source of truth in code:

| File | Holds |
|---|---|
| `frontend/src/shared/api/endpoints.ts` | Every URL path |
| `frontend/src/shared/api/schemas.ts` | Every response shape as a zod schema |
| `frontend/src/shared/api/filters.ts` | Every query parameter the frontend sends |
| `frontend/src/mocks/handlers.ts` | Reference behaviour of each endpoint |
| `frontend/src/mocks/db/actions.ts` | Action definitions and their form fields |
| `frontend/src/mocks/db/meta.ts` | Reference content of `GET /meta` |

The mocks are a backend stand-in, not test fixtures. When this document and the
mocks disagree, the mocks win. Read `frontend/docs/02-api-contract.md` for the
frontend side of the same contract.

## 1. Conventions

1. Base path is `/api/v1`. The frontend reads it from `VITE_API_BASE_URL`.
2. All field names are camelCase. Use `alias_generator=to_camel` and
   `populate_by_name=True` in pydantic, then dump with `by_alias=True`.
3. All timestamps are ISO-8601 UTC strings, for example `2026-09-09T12:00:00Z`.
4. List endpoints return the envelope `{ items, page, pageSize, total }`. Three
   endpoints break this rule on purpose. See the endpoint table.
5. The parameters `direction`, `level` and `status` repeat for multi-select, for
   example `?direction=FIRE_RISK&direction=WEAR_OUT`. In FastAPI declare them as
   `Query(default=[])`.
6. The frontend sorts repeated values and drops empty values before it sends a
   request. Treat parameter order as meaningless.
7. `sort` is one string in the form `field:asc` or `field:desc`. Accept only the
   fields in section 8. Ignore an unknown field instead of returning an error.
8. `direction`, `level` and `status` are free strings in every response. Never
   return an enum type and never branch on a direction code inside a router.
9. The frontend parses responses with `safeParse` and keeps unknown fields. A new
   field in a response is safe. A renamed field is not.
10. Any HTTP status at or above 400 makes the frontend show an error state with a
    retry button. The body is not read. Return `{"detail": "..."}` anyway, because
    the text helps during debugging.

## 2. Endpoint table

| # | Method | Path | Response | Screen |
|---|---|---|---|---|
| 1 | GET | `/meta` | `AppMeta` | All. Loaded once at start |
| 2 | GET | `/predictions` | Envelope of `Prediction` | Journal |
| 3 | GET | `/predictions/{id}` | `PredictionDetail` | Prediction card |
| 4 | GET | `/predictions/{id}/timeseries` | `TimeSeriesResponse` | Prediction card |
| 5 | POST | `/predictions/{id}/actions/{code}` | `PredictionDetail` | Prediction card |
| 6 | GET | `/facilities` | GeoJSON `FeatureCollection` | Map |
| 7 | GET | `/facilities/{id}` | `FacilityRef` | Map |
| 8 | GET | `/orders` | Envelope of `WorkOrder` | Orders |
| 9 | GET | `/orders/{id}` | `WorkOrder` | Order card |
| 10 | POST | `/orders/{id}/actions/{code}` | `WorkOrder` | Order card |
| 11 | GET | `/metrics/models` | Bare array of `ModelMetric` | Dashboard |
| 12 | GET | `/metrics/pipeline` | `PipelineHealth` | Dashboard |
| 13 | GET | `/dashboard/summary` | `DashboardSummary` | Dashboard |
| 14 | GET | `/dashboard/top-risks` | Bare array of `Prediction` | Dashboard |

Endpoints 6, 11 and 14 do not use the list envelope.

The frontend polls every endpoint except `/meta` once per minute. `/meta` loads
once per session. `/metrics/models` becomes stale after five minutes.

## 3. Endpoint detail

### 3.1 GET /meta

No parameters. Returns `AppMeta`. This response builds the whole interface:
filters, journal columns, dashboard widgets, and the option lists of every form.

If this endpoint fails, the frontend falls back to a built-in config and shows a
warning in the header. It does not retry in a loop. A wrong `/meta` is therefore
worse than a missing `/meta`.

Add a fifth direction to `directions` and to `reasons`, and it must appear in the
filters, on the map and on the dashboard with no frontend change. This is the
flexibility test that both sides must pass.

### 3.2 GET /predictions

| Parameter | Type | Repeats | Note |
|---|---|---|---|
| `direction` | string | yes | Match any of the given codes |
| `level` | string | yes | Match any of the given codes |
| `status` | string | yes | Match any of the given codes |
| `district` | string | no | Match `facility.district` |
| `from` | string | no | Date. Keep predictions with `computedAt >= from` |
| `to` | string | no | Date. Keep predictions with `computedAt <= to` end of day |
| `sort` | string | no | See section 8 |
| `page` | integer | no | Starts at 1 |
| `pageSize` | integer | no | The frontend always sends 50 |

The mocks compare `from` and `to` against `computedAt` as strings, and they
extend `to` with `T23:59:59.999Z`. Reproduce the inclusive end of day.

Returns `{ items: Prediction[], page, pageSize, total }`. `total` counts the rows
after filtering and before pagination.

### 3.3 GET /predictions/{id}

Returns `PredictionDetail`, which is a `Prediction` plus `blocks` and `actions`.
Return 404 with `{"detail": ...}` for an unknown id.

`blocks` carries the explanation of the prediction. `actions` carries the buttons
the dispatcher may press now. Both come from the server. The frontend renders
`blocks` through a type registry and falls back to a generic renderer for an
unknown type.

### 3.4 GET /predictions/{id}/timeseries

The frontend sends no parameters today. The contract keeps optional `from` and
`to` for later use.

Returns `{ series: Series[], markerAt?: string }`. `markerAt` marks the moment of
the prediction on the chart. The mocks set it to `computedAt`.

The mocks build these series names: `Температура`, `Влажность в камере`,
`Задымление`, `Уровень воды в приямке`, `Интенсивность эксплуатации`, and
`Срабатываний за сутки`. Each series holds hourly or daily points.

### 3.5 POST /predictions/{id}/actions/{code}

One endpoint serves every button. The body is a flat object of form values, for
example `{"reason": "MODEL_ERROR", "comment": "..."}`. The response is the whole
updated `PredictionDetail`, so the frontend can put it straight into its cache.

Never add a separate route for a button. See section 7 for the codes.

An unknown code must not fail. The mocks move the prediction to `IN_REVIEW`.

### 3.6 GET /facilities

| Parameter | Type | Repeats | Note |
|---|---|---|---|
| `direction` | string | yes | Same meaning as in `/predictions` |
| `level` | string | yes | Same meaning as in `/predictions` |
| `district` | string | no | Same meaning as in `/predictions` |
| `bbox` | string | no | `minLon,minLat,maxLon,maxLat`. No caller yet. See below |

The map shows one point per facility. When a facility has several predictions,
return the one with the highest `probability`. The mocks do exactly this and they
ignore `status` and the date range here.

Two warnings before you build this endpoint. The `bbox` parameter has no caller:
the map requests every facility at once, and the mock ignores `bbox`. The `lines`
field ships the collector routes on every poll, once per minute, although the
routes never change. Frontend tasks 2 and 4 in `frontend/docs/09-gap-tasks.md`
settle both. Wait for them.

Response is a GeoJSON `FeatureCollection`:

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [37.61, 55.75] },
      "properties": {
        "facilityId": "F-0001",
        "predictionId": "P-0001",
        "direction": "FIRE_RISK",
        "level": "HIGH",
        "probability": 0.71,
        "address": "...",
        "collector": "..."
      }
    }
  ],
  "lines": { "type": "FeatureCollection", "features": [] }
}
```

`coordinates` is `[lon, lat]` in that order. The optional `lines` field carries
the collector routes as `LineString` features with a `collector` property. The
map draws them as a background layer.

### 3.7 GET /facilities/{id}

Returns one `FacilityRef`. Return 404 for an unknown id.

### 3.8 GET /orders

| Parameter | Type | Repeats | Note |
|---|---|---|---|
| `status` | string | yes | Match any of the given codes |
| `dueBefore` | string | no | Date. Keep orders with `dueAt <= dueBefore` end of day |
| `sort` | string | no | See section 8 |
| `page` | integer | no | Starts at 1 |
| `pageSize` | integer | no | The frontend always sends 50 |

Returns `{ items: WorkOrder[], page, pageSize, total }`.

### 3.9 GET /orders/{id}

Returns one `WorkOrder`. Return 404 for an unknown id.

### 3.10 POST /orders/{id}/actions/{code}

Same rules as the prediction action endpoint. The response is the whole updated
`WorkOrder`.

The code `close` must accept `predictionConfirmed` and store it in
`outcome.predictionConfirmed`. This flag is the source of honest Precision and
Recall on real data. Accept a boolean only. The mock also accepts the string
`"true"`, but the close form already sends a real boolean, so that branch is
dead. Frontend task 6 removes it. Do not copy it.

### 3.11 GET /metrics/models

No parameters. Returns a bare array of `ModelMetric`, one entry per active
direction. Include `targetPrecision` and `targetRecall` in every entry, because
the frontend does not hardcode the targets from the terms of reference.

### 3.12 GET /metrics/pipeline

No parameters. Returns one `PipelineHealth` object with both the measured values
and the target values.

### 3.13 GET /dashboard/summary

No parameters. Returns four counter dictionaries and a total. The keys are codes,
so a new direction adds a key and the dashboard picks it up.

### 3.14 GET /dashboard/top-risks

| Parameter | Type | Note |
|---|---|---|
| `limit` | integer | Default 10 |

Returns a bare array of `Prediction`, sorted by `probability` descending. The
mocks drop predictions with status `REJECTED` or `CLOSED`.

## 4. Response types

### AppMeta

| Field | Type | Note |
|---|---|---|
| `directions` | `DirectionMeta[]` | Active prediction directions |
| `riskLevels` | `RiskLevelMeta[]` | Risk levels, low to high |
| `statuses` | `StatusMeta[]` | Statuses of both entities |
| `districts` | `{code,label}[]` | Districts for the filter |
| `journalColumns` | `string[]` | Column keys, in display order |
| `orderColumns` | `string[]` | Column keys, in display order |
| `dashboardWidgets` | `string[]` | Widget codes, in display order |
| `reasons` | `{ [key]: {code,label}[] }` | Option lists for `select` fields |

`DirectionMeta`: `code`, `label`, `shortLabel`, `accent` (a CSS colour),
`minHorizonHours` (number, at least 24).

`RiskLevelMeta`: `code`, `label`, `colorVar` (a CSS variable name such as
`--risk-high`), `order` (number).

`StatusMeta`: `code`, `label`, `scope`. `scope` is `prediction` or `order`. The
same code may appear twice with a different scope. `REJECTED` does.

A key of `reasons` matches the `optionsRef` of a form field. See section 7.

### FacilityRef

| Field | Type | Required |
|---|---|---|
| `id` | string | yes |
| `collector` | string | yes |
| `section` | string | no |
| `chamber` | string | no |
| `device` | string | no |
| `district` | string | yes |
| `address` | string | yes |
| `lat` | number | yes |
| `lon` | number | yes |

### Prediction

| Field | Type | Required | Note |
|---|---|---|---|
| `id` | string | yes | |
| `direction` | string | yes | A direction code |
| `level` | string | yes | A risk level code |
| `probability` | number | yes | 0 to 1 |
| `horizonHours` | number | yes | At least 24 |
| `computedAt` | string | yes | ISO timestamp |
| `computeMs` | number | yes | Below 300000, the 5 minute target |
| `status` | string | yes | A prediction status code |
| `facility` | `FacilityRef` | yes | Embedded, not an id |
| `summary` | string | yes | One line for the journal |
| `orderId` | string | no | Set when an order exists |

### PredictionDetail

`Prediction` plus:

| Field | Type | Note |
|---|---|---|
| `blocks` | `CardBlock[]` | Explanation blocks. See section 6 |
| `actions` | `ActionDef[]` | Buttons for the current status |

### CardBlock

`{ type: string, title: string, data: unknown }`. `data` is free JSON. Its shape
depends on `type`.

### ActionDef

| Field | Type | Required | Note |
|---|---|---|---|
| `code` | string | yes | Goes into the action URL |
| `label` | string | yes | Button text |
| `kind` | string | yes | `primary`, `secondary` or `danger` |
| `confirm` | string | no | Text of a confirmation dialogue |
| `fields` | `FieldDef[]` | yes | May be empty |

### FieldDef

| Field | Type | Required | Note |
|---|---|---|---|
| `name` | string | yes | Key in the request body |
| `label` | string | yes | |
| `type` | string | yes | `text`, `textarea`, `select`, `boolean`, `datetime` |
| `required` | boolean | no | |
| `minLength` | number | no | For text and textarea |
| `optionsRef` | string | no | Key in `meta.reasons`. Needed for `select` |
| `placeholder` | string | no | |
| `help` | string | no | |

### WorkOrder

| Field | Type | Required | Note |
|---|---|---|---|
| `id` | string | yes | |
| `number` | string | yes | Human readable, for example `2026-0001` |
| `predictionId` | string | yes | |
| `facility` | `FacilityRef` | yes | Embedded |
| `workType` | string | yes | |
| `dueAt` | string | yes | ISO timestamp |
| `status` | string | yes | An order status code |
| `createdAt` | string | yes | ISO timestamp |
| `actions` | `ActionDef[]` | yes | |
| `outcome` | object | no | Present when the order is closed |

`outcome`: `actualCause` (string), `predictionConfirmed` (boolean), `comment`
(string), `closedAt` (ISO timestamp).

### ModelMetric

`direction`, `precision`, `recall`, `targetPrecision`, `targetRecall`,
`evaluatedAt`. The targets from the terms of reference are 0.7 and 0.5.

### PipelineHealth

`lastRunAt`, `lastRunMs`, `freshnessMinutes`, `maxComputeMs`, `minHorizonHours`,
`targetComputeMs`, `targetHorizonHours`. The mocks set `targetComputeMs` to
300000 and `targetHorizonHours` to 24.

### DashboardSummary

`byLevel`, `byDirection`, `byStatus`, `byOrderStatus`, `total`. The first four are
dictionaries from code to count. The first three count predictions. The fourth
counts orders. `total` counts all predictions.

### TimeSeriesResponse

`{ series: Series[], markerAt?: string }`.
`Series`: `{ name: string, unit?: string, points: [{ t: string, v: number }] }`.

## 5. Vocabularies

These are the values the mocks use. The backend owns them and serves them from
`/meta`. The frontend treats them as opaque strings.

Directions:

| Code | Label | Share of mock data | Precision, Recall in mocks |
|---|---|---|---|
| `SENSOR_FAILURE` | Отказ датчика | 0.34 | 0.81, 0.63 |
| `FIRE_RISK` | Пожарный риск | 0.24 | see plugin |
| `WEAR_OUT` | Износ инфраструктуры | 0.24 | see plugin |
| `UNAUTHORIZED_ACCESS` | Несанкционированный доступ | 0.18 | see plugin |
| `FLOOD_RISK` | Подтопление | test only | The fifth direction for the flexibility test |

Risk levels and the probability bands the mocks use:

| Code | Label | Probability |
|---|---|---|
| `LOW` | Низкий | below 0.3 |
| `MEDIUM` | Средний | 0.3 to 0.55 |
| `HIGH` | Высокий | 0.55 to 0.78 |
| `CRITICAL` | Критический | 0.78 and above |

Prediction statuses: `NEW`, `IN_REVIEW`, `ORDER_CONFIRMED`, `REJECTED`, `CLOSED`.

Order statuses: `AUTO_CREATED`, `CONFIRMED`, `IN_PROGRESS`, `REJECTED`, `DONE`.

The terminal status of an order is `DONE`. The terminal status of a prediction is
`CLOSED`. The two codes differ on purpose, because the two entities have separate
lifecycles. `REJECTED` is the only code that both share, and `scope` separates it.

Journal columns: `risk`, `computedAt`, `direction`, `facility`, `summary`,
`probability`, `horizon`, `status`, `order`.

Order columns: `number`, `facility`, `workType`, `dueAt`, `orderStatus`,
`prediction`.

Dashboard widgets: `risk-counters`, `model-metrics`, `pipeline-health`,
`direction-split`, `top-risks`, `order-counters`.

Keys of `reasons`: `rejection` plus one key per direction, named after the
direction code. The mocks use the direction code itself as the key.

## 6. Card block types

The frontend has a renderer for these five types. Any other type falls back to a
generic renderer, so the backend may send an experimental block without asking.

| `type` | Shape of `data` |
|---|---|
| `factors` | `{ items: [{ label, weight, value? }] }`. `weight` is -1 to 1 |
| `timeseries` | `{ series: Series[], markerAt: string }` |
| `timeline` | `{ events: [{ at, title, kind, note? }] }` |
| `table` | `{ columns: [{ key, header, align? }], rows: [{}] }` |
| `keyvalue` | `{ items: [{ label, value }] }` |

A prediction must carry at least one `factors` block and one `timeseries` block.
Without them the dispatcher cannot judge the prediction.

## 7. Actions

The server computes `actions` from the current status. The frontend renders the
buttons and the forms, and it knows nothing about what a code means.

### Prediction actions

| Status | Codes |
|---|---|
| `NEW`, `IN_REVIEW` | `confirm_order`, `inspect`, `reject` |
| `ORDER_CONFIRMED` | `reject` |
| `REJECTED`, `CLOSED` | none |

| Code | Kind | Fields | Effect in the mocks |
|---|---|---|---|
| `confirm_order` | primary | `comment` (textarea) | Status to `ORDER_CONFIRMED`. An `AUTO_CREATED` order moves to `CONFIRMED` |
| `inspect` | secondary | `plannedAt` (datetime, required), `crew` (text, required, min 2), `comment` (textarea) | Status to `IN_REVIEW` |
| `reject` | danger | `reason` (select, required, `optionsRef: rejection`), `comment` (textarea, required, min 5) | Status to `REJECTED`. An open order moves to `REJECTED` |

### Order actions

| Status | Codes |
|---|---|
| `AUTO_CREATED` | `confirm`, `reject` |
| `CONFIRMED` | `start` |
| `IN_PROGRESS` | `close` |
| `REJECTED`, `DONE` | none |

| Code | Kind | Fields | Effect in the mocks |
|---|---|---|---|
| `confirm` | primary | `assignee` (text, required, min 2), `comment` (textarea) | Status to `CONFIRMED` |
| `reject` | danger | `reason` (select, required, `optionsRef: rejection`), `comment` (textarea, required, min 5) | Status to `REJECTED` |
| `start` | primary | `crew` (text, required, min 2) | Status to `IN_PROGRESS` |
| `close` | primary | `actualCause` (select, required, `optionsRef` of the direction), `predictionConfirmed` (boolean, required), `comment` (textarea, required, min 5) | Order to `DONE`. Writes `outcome`. The linked prediction moves to `CLOSED` |

The `optionsRef` of `actualCause` points at the reason list of the direction of
the linked prediction. The frontend needs no extra request to resolve it, because
`/meta` already carries every list.

## 8. Sort fields

Accept only these values in `sort`. The keys match the column keys, so the table
header sorts what it shows.

Predictions: `computedAt`, `probability`, `horizon`, `direction`, `status`,
`facility` (sorts by address), `summary`, `risk` (sorts by probability).

Orders: `number`, `dueAt`, `orderStatus`, `workType`, `facility` (sorts by
address).

## 9. Rules the backend must not break

1. Never return an enum for `direction`, `level` or `status`. A new direction
   must need no migration and no router change.
2. Keep one action endpoint per entity. Do not add `/confirm` or `/close` routes.
3. Return the whole entity from an action. The frontend does not guess what
   changed.
4. Compute `actions` on the server from the status.
5. Serve the screen composition from `/meta`: columns, widgets, and option lists.
6. Adding a field to a response is safe. Renaming a field breaks the frontend.
   A rename needs a matching change in `schemas.ts` and in the mock handlers in
   the same pull request.
