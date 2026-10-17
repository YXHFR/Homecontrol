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

Filerna som är ändrade säger det överst. Ändringarna är:

- **Målens läge skickas.** Komponenten läste in radarns ramar men flyttade
  aldrig koordinaterna till sensorerna, så X och Y var alltid 0. Nu förs de
  över för varje ram (`ld2461.cpp`, `LD2461::to_mm`).
- **Läget är i mm**, som på LD2450-kortet. Varje koordinat i ramen tolkas
  som en byte med tecken i 0,1 m, så som komponentens ramformat ser ut. Det
  är inte provat mot en riktig radar.
- **Bara hela ramar läses.** En för kort ram läses inte längre utanför
  bufferten.
- **Number-delen byggs bara när den används** (`USE_LD2461_NUMBER` i stället
  för `USE_NUMBER`). Annars gick firmware inte att bygga, eftersom ESPectre
  också har inställningar. Number-delen själv (zoner och inställningar i
  radarn) fungerar inte med ESPHome 2026.9 och används inte av Homecontrol.
- **Radarns firmwareversion** skrivs med fyra fält för de fyra värdena.
  Formatet hade fem fält, och det femte lästes från fel ställe.

Allt annat är oförändrat.

## Inte provat

Inget i komponenten är provat på riktig hårdvara av Homecontrol: varken
ramens tolkning, X-riktningen, uppdateringstakten, räckvidden eller att
radarn följer 5 personer. Det är tillverkarens uppgifter. Se
[firmware/README.md](../../README.md#ld2461-i-stället-för-ld2450).

## Uppdatera

Byt inte filerna mot en nyare version utan att föra över ändringarna ovan
och uppdatera commiten här.
