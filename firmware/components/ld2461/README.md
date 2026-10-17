# LD2461-komponenten

ESPHome-komponent för radarn Hi-Link HLK-LD2461. Den används av
[`homecontrol-ld2461.yaml`](../../homecontrol-ld2461.yaml).

## Ursprung

Komponenten är tagen från
[Chreece/LD2461-ESPHome](https://github.com/Chreece/LD2461-ESPHome), commit
[`11e3ea98a7f9bdf1cc6a74f5a2ada923b7ee1a3c`](https://github.com/Chreece/LD2461-ESPHome/tree/11e3ea98a7f9bdf1cc6a74f5a2ada923b7ee1a3c/components/ld2461).
Licensen är Apache-2.0 (se [`LICENSE`](LICENSE)), samma som Homecontrols.

Den ligger här i stället för att hämtas som extern komponent, eftersom den
behövde rättas. Det finns ingen ESPHome-komponent för LD2461 som är
underhållen. Den här är originalet (autogyro/LD2461 är en äldre kopia av
den) och den enda som har alla 5 mål. Upphovspersonen har slutat utveckla
den (se issue #1 i det repot), och README där säger "UNDER DEVELOPMENT".

## Ändringar för Homecontrol

Filerna som är ändrade säger det överst, och varje ändring i C++-koden är
märkt `Homecontrol:`. Kommentarerna där är på engelska, som resten av
komponenten. Ändringarna är:

- **Målens läge skickas.** Komponenten läste in radarns ramar men flyttade
  aldrig koordinaterna till sensorerna, så X och Y var alltid 0. Nu förs de
  över för varje ram (`ld2461.cpp`, `LD2461::to_mm`).
- **Läget är i mm**, som på LD2450-kortet. Varje koordinat i ramen tolkas
  som en byte med tecken i 0,1 m, så som komponentens ramformat
  (`typedefs.h`) ser ut.
- **Den tomma ramen läses.** En ram med mål har inget kommandovärde: där
  ligger första målets X. Originalet kastade därför varje ram där första
  målet hade X = 0, också ramen när ingen syns, och sista läget låg kvar.
- **Byte läses utan tecken**, så att `0xDD` och `0xFF` känns igen som slutet
  på ramhuvudet och ramen också där `char` har tecken.
- **Bara det som har tagits emot läses.** En ram med mål läses bara om den
  är exakt så lång som väntat från ramhuvudet. Ingen ram läses utanför
  bufferten, och bufferten börjar om i stället för att slå runt.
- **`take_new_coordinates()`** säger om en hel ram med mål har kommit.
  `homecontrol-ld2461.yaml` håller `Radar Online` på med den.
- **Målen börjar på 0** i stället för det som råkade ligga i minnet.
- **Number-delen byggs bara när den används** (`USE_LD2461_NUMBER` i stället
  för `USE_NUMBER`). Annars gick firmware inte att bygga, eftersom ESPectre
  också har inställningar. Number-delen själv (zoner och inställningar i
  radarn) fungerar inte med ESPHome 2026.9.
- **Radarns firmwareversion** skrivs med fyra fält för de fyra värdena.
  Formatet hade fem fält, och det femte lästes från fel ställe.

Allt annat är oförändrat. Homecontrol använder bara sensorerna (målens
läge, antal mål), närvaron och radarns firmwareversion. Number-, select-
och button-delarna används inte. Antal mål och närvaro räknar som i
originalet bara mål vars X inte är 0. Kärnan använder dem inte.

Ramtolkningen prövas på datorn med påhittade ramar i
[`scripts/tests/test_ld2461_frames.py`](../../../scripts/tests/test_ld2461_frames.py).

## Inte provat

Inget i komponenten är provat på riktig hårdvara av Homecontrol. Att radarn
följer 5 personer, och hur långt och noga den ser, är tillverkarens
uppgifter. Ramformatet och X-riktningen kommer från komponenten, och att
radarn skickar ramar också när ingen syns är ett antagande. Se
[firmware/README.md](../../README.md#ld2461-i-stället-för-ld2450).

## Uppdatera

Byt inte filerna mot en nyare version utan att föra över ändringarna ovan
och uppdatera commiten här.
