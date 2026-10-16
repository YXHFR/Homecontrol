-- Historiken (se homecontrol/history.py): spårens positioner och en
-- händelselogg. Allt stannar i den här databasen i hemmet.
--
-- Positionerna sparas med full upplösning (cirka 10 punkter per sekund och
-- spår) i track_position. Ett kontinuerligt aggregat i TimescaleDB nedsamplar
-- dem till en punkt per sekund och spår i track_position_per_second, som
-- komprimeras. Hur länge historiken sparas ställs in när kärnan startar
-- (history.configure), inte här, eftersom tiderna går att ändra.
--
-- Aggregatet uppdateras bara för den senaste halvtimmen (se policyn nedan),
-- så att ett kortare avbrott i databasen inte lämnar hål.
-- Det som redan är nedsamplat ligger kvar när de fullupplösta punkterna
-- tas bort. Uppdatera aldrig aggregatet för hela tiden
-- (refresh_continuous_aggregate med NULL), för då försvinner det
-- nedsamplade för tider vars fullupplösta punkter redan är borta.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE track_position (
    time timestamptz NOT NULL,
    -- Spårets slumpade id (Track.id). Radarns numrering av målen sparas aldrig.
    track_id uuid NOT NULL,
    floor_id text NOT NULL,
    x real NOT NULL,
    y real NOT NULL,
    -- Radarn har tappat spåret, men det hålls kvar.
    held boolean NOT NULL
);
SELECT create_hypertable('track_position', by_range('time', interval '1 hour'));

CREATE MATERIALIZED VIEW track_position_per_second
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(interval '1 second', time) AS time,
    track_id,
    floor_id,
    avg(x)::real AS x,
    avg(y)::real AS y,
    bool_and(held) AS held
FROM track_position
GROUP BY 1, 2, 3
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'track_position_per_second',
    start_offset => interval '30 minutes',
    end_offset => interval '2 seconds',
    schedule_interval => interval '1 minute'
);
ALTER MATERIALIZED VIEW track_position_per_second
    SET (timescaledb.enable_columnstore = true, timescaledb.segmentby = 'track_id',
         timescaledb.orderby = 'time');

-- Händelseloggen: varje gång ett rum blir upptaget, tomt eller okänt
-- (kind 'rum', detail är det nya läget), och regelmotorns förklaringar
-- (kind 'förklaring', detail är förklaringen i klartext). Den komprimeras
-- efter en vecka.
CREATE TABLE event_log (
    time timestamptz NOT NULL,
    kind text NOT NULL CHECK (kind IN ('rum', 'förklaring')),
    room_id text,
    lamp_id text,
    detail text NOT NULL
);
SELECT create_hypertable('event_log', by_range('time', interval '7 days'));
ALTER TABLE event_log SET (timescaledb.enable_columnstore = true, timescaledb.orderby = 'time');
CALL add_columnstore_policy('event_log', after => interval '7 days');
