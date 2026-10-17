# Firmware för rumskorten

Ett kort per rum. Kortet känner av närvaro på två sätt, mäter ljuset och
skickar allt till kärnan:

- **Radar (LD2450):** upp till 3 personer med läge (X/Y), cirka 10 gånger
  per sekund, inom 6 m och ±60°. Som alternativ finns **LD2461**, som
  enligt tillverkaren följer upp till 5 personer (se
  [LD2461 i stället för LD2450](#ld2461-i-stället-för-ld2450)).
- **Wi-Fi-CSI (ESPectre):** rörelse som syns i hur Wi-Fi-signalen ändras.
- **Ljus (BH1750):** hur ljust det är i rummet (lux).

Firmware byggs med [ESPHome](https://esphome.io). Det finns ett paket per
radar: [`homecontrol.yaml`](homecontrol.yaml) för LD2450 och
[`homecontrol-ld2461.yaml`](homecontrol-ld2461.yaml) för LD2461. Allt som är
lika på båda (Wi-Fi-CSI, ljussensorn, API:t och uppdateringarna) finns i
[`common.yaml`](common.yaml).

## Det här behövs

| Del | Kommentar |
| --- | --- |
| ESP32-S3-kort med extern antenn | Till exempel ESP32-S3-DevKitC-1U (modul WROOM-1U) med en U.FL-antenn. Inbyggd kretskortsantenn ger sämre CSI. |
| HLK-LD2450 | 5 V-matning. Signalerna är 3,3 V. |
| eller HLK-LD2461 | I stället för LD2450. Se [LD2461 i stället för LD2450](#ld2461-i-stället-för-ld2450). |
| BH1750 | Ljussensor på I²C, 3,3 V. |
| USB-kabel som klarar data | Laddkablar fungerar inte. |

### Koppling

| Från | Till ESP32-S3 |
| --- | --- |
| LD2450 5V | 5V |
| LD2450 GND | GND |
| LD2450 TX | GPIO18 (RX) |
| LD2450 RX | GPIO17 (TX) |
| BH1750 VCC | 3V3 |
| BH1750 GND | GND |
| BH1750 SDA | GPIO8 |
| BH1750 SCL | GPIO9 |

LD2461 kopplas på samma stift (TX till GPIO18, RX till GPIO17). Kontrollera
matningsspänningen i Hi-Links datablad för LD2461 innan du kopplar in den.

Stiften kan ändras med `substitutions` överst i `homecontrol.yaml` och
`homecontrol-ld2461.yaml`.

## Installera

1. Öppna webbinstalleraren i Chrome eller Edge på en dator. Den är inte
   publicerad på webben än: den byggs av workflowen Firmware och sparas som
   artefakten `webbinstallerare`. Hur du hämtar och öppnar den står i
   [README i repots rot](../README.md#steg-6-installera-rumskorten).
2. Välj rum. Kortet får namnet `homecontrol-<rum>`. Välj också radarn som
   sitter på kortet: LD2450 eller LD2461. Med fel radar svarar kortets
   radar inte.
3. Anslut kortet med USB. På kort med två USB-uttag: använd det som är
   märkt **USB** (inte **UART**). Annars går det inte att ställa in Wi-Fi.
4. Tryck på **Connect**, välj kortet i listan och sedan **Install**.
5. Första gången: kryssa i **Erase device**.
6. När installationen är klar frågar sidan efter Wi-Fi. Nätverk och
   lösenord skickas bara till kortet och sparas där.

Rumsnamnet syns för alla i hemnätet (till exempel i routerns lista).

### Koppla kortet till kärnan

API:t är krypterat, men nyckeln finns inte i firmware. Kärnan skapar en
egen nyckel för varje kort och skickar den vid första anslutningen. Efter
det tar kortet bara emot krypterade anslutningar med just den nyckeln.

Kärnan hittar nya kort själv (mDNS) och visar dem i livevyn. Placera kortet
i kartredigeraren (se README i repots rot); först då ansluter kärnan och
ger kortet dess nyckel. Nyckeln räknas fram ur `ESPHOME_SECRET` i `.env`
och kortets namn, så den sparas ingenstans. Placera därför kortet så snart
det har fått Wi-Fi: innan kärnan har kopplat kortet kan vem som helst i
hemnätet ansluta och sätta en egen nyckel. Då varnar livevyn att kortet har
en annan nyckel. Nyckeln tas bara bort om kortet raderas via
webbinstalleraren (**Erase device**). Byts `ESPHOME_SECRET` måste korten
raderas och installeras om på samma sätt.

### Uppdatera

- **Över nätverket:** fungerar först när webbinstalleraren är publicerad på
  webben, vilket den inte är än. Kortet kollar var 6:e timme om
  webbinstalleraren har en ny version och visar det i entiteten `Firmware`. Uppdateringen startar
  bara när kärnan ber om det, efter att du har tryckt på **Godkänn
  uppdatering** under **Rumskort** i livevyn. Kortet hämtar då
  själv filen över https. Det tar inte emot firmware från någon annan.
  Adressen är `yxhfr.github.io`, så GitHub-kontot **YXHFR får inte byta
  namn**. Då kan någon annan ta namnet och skicka firmware till korten.
- **Över USB:** kör webbinstalleraren igen och lämna **Erase device** tom.
  Kortet behåller Wi-Fi och nyckeln.

### Rummet finns inte i listan

Webbinstalleraren har färdiga filer för rummen i [`rooms.txt`](rooms.txt).
Ett nytt rum kan läggas till där (bara a–z, 0–9 och bindestreck, högst
19 tecken). Eller bygg själv:

```sh
pip install --require-hashes -r firmware/requirements.txt
esphome -s rum gastrum run firmware/homecontrol.yaml          # LD2450
esphome -s rum gastrum run firmware/homecontrol-ld2461.yaml   # LD2461
```

Ett eget rum får inga uppdateringar över nätverket, eftersom
webbinstalleraren inte har någon fil för det.

## Det här skickar kortet

Allt går via ESPHome:s krypterade native API (port 6053). Namnen är på
engelska och ändras inte, så att kärnan kan lita på dem.

| Namn | Vad |
| --- | --- |
| Radar Target 1–3 X / Y | Läge i mm, cirka 10 Hz. Med LD2461 också mål 4 och 5 |
| Radar Target 1–3 Speed | Fart i mm/s. Finns inte med LD2461 |
| Radar Target Count | Antal mål |
| Radar Presence | Någon syns på radarn |
| Radar Online | Radarn skickar data. Av efter 3 s utan data (lita då inte på Radar Presence) |
| CSI Motion | Rörelse enligt Wi-Fi-CSI |
| CSI Movement Score | Hur mycket rörelse CSI ser |
| CSI Calibrating | CSI kalibrerar (rummet ska vara stilla) |
| CSI Recalibrate | Knapp: kalibrera om CSI |
| Illuminance | Ljus i lux, var 5:e sekund |
| WiFi Signal | Signalstyrka i dBm |
| Firmware Version | Homecontrol-versionen |
| Firmware | Uppdatering: om en ny version finns, och knappen för att installera |
| ESPHome Version | ESPHome-versionen |
| Radar Firmware | Radarns egen firmware |
| Uptime, Free Heap, Loop Time | Hälsa (drifttid, ledigt minne, looptid) |

Radarns värden skickas utan ESPHome:s vanliga strypning till 1 gång per
sekund. Med LD2450 skickas ett värde bara när det ändras. Med LD2461 skickas
alla mål 10 gånger per sekund.

Kärnan ser på entiteterna vilken radar kortet har: bara kortet med LD2461
har `Radar Target 4` och `5`.

ESPectre lägger också till några egna inställningar och diagnostikvärden
(tröskel, profil, paketflöden).

## LD2461 i stället för LD2450

LD2461 är en annan radar från Hi-Link. **Att den följer upp till 5
personer, och hur noga den mäter, är tillverkarens uppgifter. Inget av det
är provat med Homecontrol än.** LD2450 är fortfarande det som rekommenderas.

- Kortet heter `homecontrol-<rum>` med båda radarerna, och allt annat på
  kortet är likadant. Kärnan ser själv vilken radar kortet har.
- Firmware för LD2461 har egna filer i webbinstalleraren
  (`manifest-ld2461-<rum>.json`), så ett kort med LD2461 uppdateras bara
  med firmware för LD2461.
- Det finns ingen underhållen ESPHome-komponent för LD2461. Firmware
  använder en rättad kopia av
  [Chreece/LD2461-ESPHome](https://github.com/Chreece/LD2461-ESPHome),
  låst på en commit. Vad som är ändrat står i
  [`components/ld2461/README.md`](components/ld2461/README.md).
- Radarn kör 9600 baud. Komponenten skickar ingen fart.
- Kontrollera i livevyn att prickarna hamnar på rätt sida om kortet. Gör de
  inte det: kryssa i **Spegelvänd sidled** för kortet i kartredigeraren.
- Simuleringen kan låtsas att ett kort har en LD2461 (**Radar i
  simuleringen** i kartredigeraren). Den ser då lika långt och brett som en
  LD2450, eftersom LD2461:s räckvidd inte är provad.

**Att prova med riktig hårdvara** innan LD2461 används på riktigt: gör
[hårdvarutestet](#hårdvarutest-ld2450-och-espectre-på-samma-kort-i-24-h)
med LD2461, och kontrollera också att

- `Radar Target 1 X` och `Y` följer en person som går runt, och att
  avståndet stämmer med ett måttband,
- 4 och 5 personer i rummet ger 4 och 5 mål,
- `Radar Online` är på hela tiden och `Radar Firmware` visar en version.

## Kända begränsningar

- **LD2450-firmware v2.14.x** har rapporterats fungera dåligt med ESPHome.
  Kontrollera `Radar Firmware` och byt firmware på radarn (via HLKRadarTool)
  om den visar 2.14.
- **ESPectre** tar cirka 5–12 % av processorn och kräver att Bluetooth är
  avstängt. Bluetooth är därför avstängt helt (`CONFIG_BT_ENABLED: n`).
- Ingen egen åtkomstpunkt eller captive portal. ESPHome:s captive portal
  tar med firmwareuppladdning utan lösenord och startar av sig själv när
  Wi-Fi försvinner. Byter du Wi-Fi-nät ställer du in det igen via USB.
- Kortet startar inte om när kärnan är borta. Det startar bara om om
  Wi-Fi är borta i 15 minuter.
- ESPectre skickar ping till routern för att få jämn CSI-trafik.

## Licens

- **Konfigurationen** i den här mappen (YAML, installationssidan, skript)
  är [Apache-2.0](../LICENSE), som resten av Homecontrol.
- **LD2461-komponenten** i [`components/ld2461`](components/ld2461) kommer
  från Chreece/LD2461-ESPHome och är [Apache-2.0](components/ld2461/LICENSE),
  med Homecontrols ändringar.
- **Firmware-binären** som byggs innehåller [ESPectre](https://github.com/francescopace/espectre)
  (GPL-3.0) och ESPHome:s C++-kod (GPL-3.0). Binären som helhet sprids
  därför under **GPL-3.0**. Webbinstalleraren visar licenstexten och länkar
  till källkoden för exakt den version som installeras: den här mappen, den
  låsta ESPectre-commiten i `common.yaml` och ESPHome-versionen i
  [`requirements.in`](requirements.in) (hela kedjan, med hashar, i
  `requirements.txt`).

## Hårdvarutest: LD2450 och ESPectre på samma kort i 24 h

Radarn och CSI delar processor och avbrott. Testet visar att de fungerar
tillsammans över tid. Gör det innan ett nytt kort, en ny ESPHome-version
eller en ny ESPectre-version används på riktigt.

**Förberedelse**

1. Installera firmware på ett kort med LD2450 och BH1750 inkopplade och
   koppla det till kärnan (eller till `esphome logs`).
2. Kontrollera att `Radar Firmware` inte är 2.14.x.
3. Låt kortet stå på sin vanliga plats i rummet, på vanlig strömkälla.

**Under 24 timmar**

| Kontroll | Godkänt |
| --- | --- |
| `Uptime` växer hela tiden | Ingen omstart under testet |
| `Radar Online` | På hela tiden |
| `Free Heap` | Ingen stadig minskning (läcka) |
| `Loop Time` | Normalt under 30 ms, inga långa toppar |
| Radarns uppdateringstakt när någon rör sig | 8–10 värden per sekund för X/Y |
| Radarns mål | Rätt antal personer, X/Y följer rörelsen |
| `CSI Calibrating` | Klar inom några minuter efter start |
| `CSI Motion` | Slår till när någon går i rummet, släpper när rummet är tomt |
| `Illuminance` | Följer dagsljus och lampor |
| `WiFi Signal` | Stabil, bättre än −70 dBm |
| Loggen | Inga upprepade UART-fel från LD2450, inga varningar om full buffert |

**Så här mäts radarns takt:** gå runt i rummet i en minut och räkna hur
många gånger `Radar Target 1 X` uppdateras (i kärnans logg eller med
`esphome logs`). Omkring 600 betyder 10 Hz.

**Rapportera** resultatet i ett ärende: kortmodell, firmware-versioner
(Homecontrol, ESPHome, ESPectre, LD2450), tabellen ovan och eventuella
avvikelser.
