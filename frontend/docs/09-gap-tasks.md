# Frontend gaps found when the backend contract was written

`backend/docs/04-api-required-by-frontend.md` states what the backend must serve.
Writing it exposed seven gaps that belong to the frontend, not to the backend.

**All seven are closed.** This document keeps the record: what was wrong, what
changed, and why. Read it before you reopen one of these questions.

A gap belonged here when one of these was true:

1. `docs/02-api-contract.md` disagreed with `src/shared/api/schemas.ts`.
2. The frontend sent a parameter that the mock handler ignored, so no test could
   prove that the backend behaviour was right.
3. The frontend declared a parameter that no screen ever sent, so the backend
   would build a feature with no caller.

The backend side of the same review is `backend/docs/05-gap-tasks.md`.

Every change here also changed the mocks. `src/mocks/` is a backend stand-in, not
a set of fixtures. A contract change that the mocks do not follow makes
`VITE_USE_MOCKS=true` a lie.

## Task 1. Fix the contract document. Done

`docs/02-api-contract.md` calls itself a human readable projection of
`schemas.ts`, and it says that a difference is a bug to fix. The document had
drifted in eight places. The code was right in every case, so the document moved.

- [x] `AppMeta` missed `statuses`, `districts` and `orderColumns`.
- [x] `GET /orders` missed the `sort` parameter, which `orderParams` sends.
- [x] `GET /facilities` missed the `district` parameter, which `facilityParams`
      sends.
- [x] The `properties` list of a map feature missed `address` and `collector`.
- [x] `GET /metrics/pipeline` missed `targetComputeMs` and `targetHorizonHours`.
- [x] `GET /dashboard/summary` missed `byStatus`.
- [x] The time series response missed `markerAt` and the optional `unit`.
- [x] The `/facilities` response documented no collector routes. Task 4 moved
      them to their own endpoint, and the document now describes that.

## Task 2. Make `bbox` work. Done

`bbox` lived in `facilityParams` and in the signature of `useFacilities`, but
`MapScreen` called `useFacilities(api.filters)` with one argument. No screen sent
it, and the mock handler did not read it. The backend specification asked for the
parameter anyway, so the backend would have built a feature with no caller.

The map loaded every facility at once. The mock seed holds a few hundred objects,
so this worked. Real Moscow holds far more. The parameter stays, and it works.

- [x] `MapView` reports the viewport on `load` and on `moveend` through a new
      `onBoundsChange` prop. It never reports during the movement itself.
- [x] Bounds round outward to 0.05 degrees, about 3 km of longitude in Moscow.
      Without rounding every pixel of panning would mint a new cache key. The
      outward half step also pulls in objects just off screen, so a small pan
      needs no request.
- [x] `MapScreen` holds the box in state and passes it to `useFacilities`. Until
      the map reports, the request goes without `bbox`.
- [x] `useFacilities` keeps the previous page while a new box loads, so the map
      does not blink empty.
- [x] The `/facilities` mock handler filters by `bbox`, edges included.
- [x] A `bbox` that does not parse is ignored, not an error. A dispatcher with a
      broken URL sees every object, not an empty map.
- [x] Tests in `src/mocks/handlers.test.ts` cover the filter, the empty area and
      the broken value.

## Task 3. Decide whether the map obeys the status filter. Done, no code change

The journal filters predictions by `status`. The map does not, because
`facilityParams` drops `status` and the date range.

This turned out to be correct, and the interface already told the truth: the map
passes `withStatus={false}` and `withPeriod={false}` to `FilterBar`, so neither
control appears on that screen. A map of risk is not a map of workflow state.

The gap was in the documentation alone. Nothing said so, so the next reader would
have treated it as a bug.

- [x] `docs/02-api-contract.md` records the decision and the reason.
- [x] `backend/docs/04-api-required-by-frontend.md` tells the backend author not
      to expect `status` on this endpoint.

## Task 4. Stop refetching the collector routes every minute. Done

`/facilities` returned the point features and the collector routes together. The
application polls once per minute, so the map refetched geometry that never
changes 60 times per hour. On real network data that payload would dominate.

- [x] New endpoint `GET /facilities/lines`, added to `endpoints.ts`,
      `schemas.ts` (`LineCollectionSchema`) and `types.ts` (`LineCollection`).
- [x] New hook `useFacilityLines` with `staleTime: Infinity` and no polling.
- [x] `lines` left `FacilityCollection`. `MapView` takes the routes as its own
      prop and fills that source in its own effect.
- [x] The mock handler serves the new endpoint, and `/facilities` no longer
      carries `lines`.
- [x] Tests cover both sides: the new endpoint returns `LineString` features, and
      the old response no longer carries them.

The okrug polygons were never part of this. They load from
`public/geo/moscow-okrugs.geo.json` and never touch the API.

## Task 5. Decide whether the card picks a time window. Done

The contract declared `GET /predictions/{id}/timeseries?from&to`.
`usePredictionTimeseries` sent neither, and the mock handler read neither.

The card shows the window the server chose, and no screen offers a window
selector. Two parameters that nothing sends are two parameters the backend would
implement blind, so they are gone.

- [x] `docs/02-api-contract.md` drops them and says that the server picks the
      window.
- [x] `backend/docs/04-api-required-by-frontend.md` records the same.
- [x] Task 1 of `backend/docs/05-gap-tasks.md` removes them from specification §4.

The frontend code needed no change. It never sent them.

## Task 6. Remove the string fallback for `predictionConfirmed`. Done

The `/orders/:id/actions/:code` mock handler accepted the boolean `true` and the
string `"true"`. `CloseOrderForm` sends a real boolean (`confirmed === 'yes'`), so
the string branch was dead. Left alone, the backend would have copied it, and the
field that feeds Precision and Recall would accept two types for no reason.

- [x] The string branch is gone from the mock handler.
- [x] A test proves that `"true"` does not count as a confirmation and that
      `true` does.
- [x] `backend/docs/04-api-required-by-frontend.md` now says "boolean only".

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

These came out of the same review and belong to the backend. They stay in
`backend/docs/05-gap-tasks.md`.

1. The backend specification lists the action codes wrongly. It puts `confirm` on
   the prediction and forgets `inspect`.
2. The database schema has no table of sensor readings, so
   `/predictions/{id}/timeseries` has no source.
3. The database schema has no source for the `districts` and `reasons`
   dictionaries of `/meta`.
