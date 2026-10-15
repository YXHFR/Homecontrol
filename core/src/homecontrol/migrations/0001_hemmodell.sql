-- Hemmodellen (se homecontrol/home.py) sparas som ett enda dokument i samma
-- form som exportfilen (se homecontrol/homefile.py). Hemmet läses och skrivs
-- alltid i ett stycke, och modellen kontrolleras varje gång det läses, så
-- formen beskrivs bara på ett ställe: i koden.
--
-- Andra tabeller (till exempel historik) pekar ut rum och lampor med deras
-- id som text, utan främmande nycklar, så att en import aldrig tar bort dem.

CREATE TABLE home (
    -- Det finns bara ett hem, så raden har alltid id true.
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    updated_at timestamptz NOT NULL DEFAULT now()
);
