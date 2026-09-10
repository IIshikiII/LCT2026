# Frontend gaps found when the backend contract was written

`backend/docs/04-api-required-by-frontend.md` states what the backend must serve.
Writing it exposed gaps that belong to the frontend, not to the backend. This
document lists them as tasks.

A gap belongs here when one of these is true:

1. `frontend/docs/02-api-contract.md` disagrees with
   `frontend/src/shared/api/schemas.ts`.
2. The frontend sends a parameter that the mock handler ignores, so no test can
   prove that the backend behaviour is right.
3. The frontend declares a parameter that no screen ever sends, so the backend
   would build a feature with no caller.

The backend side of the same review is `backend/docs/05-gap-tasks.md`.

Every task that changes the contract must also change the mocks in the same
commit. `src/mocks/` is a backend stand-in, not a set of fixtures. A contract
change that the mocks do not follow makes `VITE_USE_MOCKS=true` a lie.

## Task 1. Fix the contract document

`docs/02-api-contract.md` calls itself a human readable projection of
`schemas.ts`, and it says that a difference is a bug to fix. The document has
drifted in eight places. Fix the document, not the code. In every case the code
is right.

- [ ] `AppMeta` misses three fields. The document lists `directions`,
      `riskLevels`, `journalColumns`, `dashboardWidgets` and `reasons`.
      `AppMetaSchema` also has `statuses`, `districts` and `orderColumns`.
- [ ] `GET /orders` misses the `sort` parameter. `orderParams` in
      `src/shared/api/filters.ts` sends it, and the mock handler sorts by it.
- [ ] `GET /facilities` misses the `district` parameter. `facilityParams` sends
      it, and the mock handler filters by it.
- [ ] The `properties` list of a map feature misses `address` and `collector`.
      The mock handler sends both, and the map popup shows them.
- [ ] `GET /metrics/pipeline` misses `targetComputeMs` and `targetHorizonHours`.
      Both are in `PipelineHealthSchema`, and the dashboard widget needs them to
      draw the target line.
- [ ] `GET /dashboard/summary` misses `byStatus`. The document lists `byLevel`,
      `byDirection`, `byOrderStatus` and `total`.
- [ ] The time series response misses `markerAt` and the optional `unit` of a
      series. Both are in `TimeSeriesResponseSchema`.
- [ ] The `/facilities` response misses the optional `lines` field, which carries
      the collector routes.

## Task 2. Decide what happens to `bbox`

`bbox` exists in `facilityParams` and in the signature of `useFacilities`, but
`MapScreen` calls `useFacilities(api.filters)` with one argument. No screen ever
sends it, and the mock handler in `src/mocks/handlers.ts` does not read it. The
backend specification asks for the parameter anyway.

The map loads every facility at once. The mock seed holds a few hundred objects,
so this works today. Real Moscow holds far more.

Recommended: keep `bbox` and make it work.

- [ ] Send the viewport bounds from `MapScreen` after the map stops moving.
      Round the numbers, so a small pan does not make a new cache key.
- [ ] Filter by `bbox` in the `/facilities` mock handler.
- [ ] Add a test that proves a facility outside the box does not come back.
- [ ] Write the rounding rule into `docs/02-api-contract.md`.

If the team decides against it, delete `bbox` from `filters.ts` and from
`useFacilities`, and remove it from the backend contract document. Do not leave a
third state where the parameter exists and nothing sends it.

## Task 3. Decide whether the map obeys the status filter

The journal filters predictions by `status`. The map does not, because
`facilityParams` drops `status` on purpose. The filter bar is shared, so a
dispatcher sets a status filter, switches to the map, and sees points that the
filter should have hidden.

This may be correct. A map of risk is not a map of workflow state. The problem is
that nothing says so, so the next reader treats it as a bug.

- [ ] Pick one: send `status` from the map, or keep the current behaviour.
- [ ] If the behaviour stays, note it in `docs/02-api-contract.md` and grey out
      the status filter on the map screen, so the interface does not lie.
- [ ] If `status` starts to travel, add it to `facilityParams`, read it in the
      `/facilities` mock handler, and add a test.

## Task 4. Stop refetching the collector routes every minute

`/facilities` returns the point features and the `lines` collection together. The
whole application polls once per minute, so the map refetches the collector
routes 60 times per hour. The routes do not change. On real network data this
payload dominates the response.

- [ ] Choose the fix: a separate endpoint such as `GET /facilities/lines` with a
      long `staleTime`, or an `include=lines` parameter that the map sends once.
- [ ] Change the `/facilities` mock handler to match the choice.
- [ ] Update `docs/02-api-contract.md` and
      `backend/docs/04-api-required-by-frontend.md` together.

Note for the map: the okrug polygons already load from
`public/geo/moscow-okrugs.geo.json` and never touch the API. The collector routes
are the only geometry that still travels on every poll.

## Task 5. Decide whether the card picks a time window

The contract declares `GET /predictions/{id}/timeseries?from&to`.
`usePredictionTimeseries` sends neither, and the mock handler reads neither. The
card shows whatever window the server chose.

- [ ] Pick one: add a window selector to the card, or drop the two parameters.
- [ ] If the parameters stay, send them from the card and honour them in the mock
      handler, and add a test.
- [ ] If they go, remove them from `docs/02-api-contract.md` and tell the backend
      to remove them from specification §4.

## Task 6. Remove the string fallback for `predictionConfirmed`

The `/orders/:id/actions/:code` mock handler accepts the boolean `true` and the
string `"true"`:

```ts
predictionConfirmed: body['predictionConfirmed'] === true || body['predictionConfirmed'] === 'true',
```

`CloseOrderForm` already sends a real boolean (`confirmed === 'yes'`), so the
string branch is dead. If it stays, the backend copies it, and the field that
feeds Precision and Recall gains a second accepted type for no reason.

- [ ] Delete the string branch from the mock handler.
- [ ] Add a test that a non boolean value does not count as a confirmation.
- [ ] Change the note in `backend/docs/04-api-required-by-frontend.md`, which
      currently records the string form as accepted behaviour.

## Task 7. Rename the terminal order status to `DONE`. Done

The backend specification §8 ends the order lifecycle at `DONE`. The frontend
used `CLOSED` for both entities. The team chose `DONE`, so the frontend moved.

An order now ends at `DONE` with the label "Выполнена". A prediction still ends
at `CLOSED`. The two codes differ on purpose, because the two entities have
separate lifecycles. `REJECTED` stays the only code that both share, and `scope`
in `/meta` separates it.

- [x] `src/mocks/db/meta.ts` and `src/shared/config/fallbacks.ts`: the order
      status is `DONE`, labelled "Выполнена".
- [x] `src/mocks/db/orders.ts`: the seed produces `DONE`, and an order at `DONE`
      always carries `outcome`.
- [x] `src/mocks/handlers.ts`: the `close` action sets `DONE`. The `reject`
      action on a prediction skips an order that already reached `DONE`.
- [x] `src/card/OrderCard.tsx`: an order at `DONE` is never overdue.
- [x] Tests: `seed.test.ts`, `OrdersScreen.test.tsx`, `risk.test.ts`. The scope
      test now uses `REJECTED`, which is the remaining shared code.
- [x] Documents: `docs/08-glossary.md`, `ARM-ODS-frontend-spec.md`, and
      `backend/docs/04-api-required-by-frontend.md`.

The action code stays `close`. Only the resulting status changed.

## Not a frontend gap

These came up in the same review and belong to the backend. They stay in
`backend/docs/05-gap-tasks.md`.

1. The backend specification lists the action codes wrongly. It puts `confirm` on
   the prediction and forgets `inspect`.
2. The database schema has no table of sensor readings, so
   `/predictions/{id}/timeseries` has no source.
3. The database schema has no source for the `districts` and `reasons`
   dictionaries of `/meta`.
