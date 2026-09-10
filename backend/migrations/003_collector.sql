-- Трассы коллекторов для подложки карты. Отдаёт GET /facilities/lines.
-- Геометрия — массив пар [lon, lat] в JSONB, как в GeoJSON. PostGIS не нужен:
-- трассы только рисуются, пересечения и буферы мы не считаем.

CREATE TABLE collector (
    code      text PRIMARY KEY,
    label     text NOT NULL,
    district  text,
    line      jsonb NOT NULL
);
