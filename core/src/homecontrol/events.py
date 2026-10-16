"""Händelseloggens gränssnitt: dit förklaringarna av kärnans beslut skickas.

Bara `Controller` anropar `explain`. Regelmotorn är en ren funktion och
förklarar sina beslut i `LampCommand.reason`; `Controller` skickar varje
kommandos förklaring hit, efter att rummets nya läge har skickats till sina
lyssnare. Nya regler gör likadant och anropar aldrig `explain` själva.
Historiken (`history.History`) sparar förklaringarna, och i tester kan vad
som helst med samma metod ta emot dem.

`explain` anropas från händelseloopen och får aldrig vänta på något, så att
lamporna inte fördröjs. En förklaring säger vad som hände i ett rum och
aldrig vem det gällde.
"""

from typing import Protocol


class EventLog(Protocol):
    def explain(self, text: str, *, room_id: str | None = None, lamp_id: str | None = None) -> None:
        """Lägger förklaringen i loggen, med rummet och lampan den gäller om det finns."""
        ...
