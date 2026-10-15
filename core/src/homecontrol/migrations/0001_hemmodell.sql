-- Hemmodellen (se homecontrol/home.py). Koordinater är meter med origo uppe
-- till vänster, rotation är grader medurs. sort_order håller ordningen så
-- att en export blir likadan som det som importerades.

CREATE TABLE home (
    -- Det finns bara ett hem, så raden har alltid id true.
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    name text NOT NULL
);

CREATE TABLE floor (
    id text PRIMARY KEY,
    sort_order integer NOT NULL,
    name text NOT NULL,
    level integer NOT NULL UNIQUE
);

CREATE TABLE room (
    id text PRIMARY KEY,
    floor_id text NOT NULL REFERENCES floor ON DELETE CASCADE,
    sort_order integer NOT NULL,
    name text NOT NULL,
    -- Hörnen som [[x, y], ...].
    polygon jsonb NOT NULL CHECK (jsonb_typeof(polygon) = 'array'),
    dark_below_lux double precision NOT NULL,
    bright_above_lux double precision NOT NULL,
    off_after_empty_seconds double precision NOT NULL CHECK (off_after_empty_seconds > 0),
    CHECK (dark_below_lux < bright_above_lux)
);

-- En vägg per sida i rummets polygon: sidan från hörn side till hörn side + 1.
-- En dörröppning är en sida utan vägg.
CREATE TABLE wall (
    room_id text NOT NULL REFERENCES room ON DELETE CASCADE,
    side integer NOT NULL CHECK (side >= 0),
    kind text NOT NULL CHECK (kind IN ('gips', 'betong', 'trä', 'glas', 'dörröppning')),
    PRIMARY KEY (room_id, side)
);

CREATE TABLE zone (
    id text PRIMARY KEY,
    room_id text NOT NULL REFERENCES room ON DELETE CASCADE,
    sort_order integer NOT NULL,
    name text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('vanlig', 'stillhet', 'ignorera', 'entré')),
    polygon jsonb NOT NULL CHECK (jsonb_typeof(polygon) = 'array'),
    -- Låter en lampa peka på en zon i just sitt eget rum.
    UNIQUE (id, room_id)
);

CREATE TABLE node (
    id text PRIMARY KEY,
    room_id text NOT NULL REFERENCES room ON DELETE CASCADE,
    sort_order integer NOT NULL,
    name text NOT NULL,
    x double precision NOT NULL,
    y double precision NOT NULL,
    height double precision NOT NULL CHECK (height >= 0),
    rotation double precision NOT NULL CHECK (rotation >= 0 AND rotation < 360),
    mirrored boolean NOT NULL,
    capabilities text[] NOT NULL CHECK (capabilities <@ ARRAY['radar', 'csi', 'lux'])
);

CREATE TABLE signal_source (
    id text PRIMARY KEY,
    floor_id text NOT NULL REFERENCES floor ON DELETE CASCADE,
    sort_order integer NOT NULL,
    name text NOT NULL,
    x double precision NOT NULL,
    y double precision NOT NULL,
    height double precision NOT NULL CHECK (height >= 0)
);

CREATE TABLE lamp (
    id text PRIMARY KEY,
    room_id text NOT NULL REFERENCES room ON DELETE CASCADE,
    sort_order integer NOT NULL,
    name text NOT NULL,
    x double precision NOT NULL,
    y double precision NOT NULL,
    zone_id text,
    profile text NOT NULL,
    FOREIGN KEY (zone_id, room_id) REFERENCES zone (id, room_id) ON DELETE SET NULL (zone_id)
);
