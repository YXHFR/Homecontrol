-- Bilden av planritningen för en våning (se Floor.plan_image i homecontrol/home.py).
-- Bilden hålls utanför hemmets dokument så att dokumentet förblir litet och
-- läsbart, och så att exportfilen inte innehåller den.

CREATE TABLE plan_image (
    floor_id text PRIMARY KEY,
    media_type text NOT NULL CHECK (media_type IN ('image/png', 'image/jpeg', 'image/webp')),
    data bytea NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
