# Firmware för rumskorten

Ett kort per rum. Kortet känner av närvaro på två sätt, mäter ljuset och
skickar allt till kärnan:

- **Radar (LD2450):** upp till 3 personer med läge (X/Y), cirka 10 gånger
  per sekund, inom 6 m och ±60°.
- **Wi-Fi-CSI (ESPectre):** rörelse som syns i hur Wi-Fi-signalen ändras.
- **Ljus (BH1750):** hur ljust det är i rummet (lux).

Firmware byggs med [ESPHome](https://esphome.io). Allt finns i
[`homecontrol.yaml`](homecontrol.yaml).

## Det här behövs

| Del | Kommentar |
| --- | --- |
| ESP32-S3-kort med extern antenn | Till exempel ESP32-S3-DevKitC-1U (modul WROOM-1U) med en U.FL-antenn. Inbyggd kretskortsantenn ger sämre CSI. |
| HLK-LD2450 | 5 V-matning. Signalerna är 3,3 V. |
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

Stiften kan ändras med `substitutions` överst i `homecontrol.yaml`.

## Installera

1. Öppna webbinstalleraren: <https://yxhfr.github.io/Homecontrol/>
   (Chrome eller Edge på en dator).
2. Välj rum. Kortet får namnet `homecontrol-<rum>`.
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

Kärnan hittar nya kort själv och kopplar dem. Den delen byggs i
[#41](https://github.com/YXHFR/Homecontrol/issues/41) och finns inte ännu.
Starta därför Homecontrol så snart kortet har fått Wi-Fi. Innan kärnan har
kopplat kortet kan vem som helst i hemnätet ansluta och sätta en egen nyckel.
Nyckeln tas bara bort om kortet raderas via webbinstalleraren (**Erase
device**). Om kärnan tappar sina nycklar (till exempel om databasen raderas)
måste korten raderas och installeras om på samma sätt.

### Uppdatera

- **Över nätverket:** kortet kollar var 6:e timme om webbinstalleraren har
  en ny version och visar det i entiteten `Firmware`. Uppdateringen startar
  bara när kärnan ber om det (efter att du har godkänt). Kortet hämtar då
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
esphome -s rum gastrum run firmware/homecontrol.yaml
```

Ett eget rum får inga uppdateringar över nätverket, eftersom
webbinstalleraren inte har någon fil för det.

## Det här skickar kortet

Allt går via ESPHome:s krypterade native API (port 6053). Namnen är på
engelska och ändras inte, så att kärnan kan lita på dem.

| Namn | Vad |
| --- | --- |
| Radar Target 1–3 X / Y | Läge i mm, cirka 10 Hz |
| Radar Target 1–3 Speed | Fart i mm/s |
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
| Radar Firmware | LD2450:s egen firmware |
| Uptime, Free Heap, Loop Time | Hälsa (drifttid, ledigt minne, looptid) |

Radarns värden skickas utan ESPHome:s vanliga strypning till 1 gång per
sekund. Ett värde skickas bara när det ändras.

ESPectre lägger också till några egna inställningar och diagnostikvärden
(tröskel, profil, paketflöden).

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
- **Firmware-binären** som byggs innehåller [ESPectre](https://github.com/francescopace/espectre)
  (GPL-3.0) och ESPHome:s C++-kod (GPL-3.0). Binären som helhet sprids
  därför under **GPL-3.0**. Webbinstalleraren visar licenstexten och länkar
  till källkoden för exakt den version som installeras: den här mappen, den
  låsta ESPectre-commiten i `homecontrol.yaml` och ESPHome-versionen i
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
