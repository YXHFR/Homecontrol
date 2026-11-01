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
| ESP32-S3-kort med extern antenn | Till exempel ESP32-S3-DevKitC-1U (modul WROOM-1U) med en U.FL-antenn. Inbyggd kretskortsantenn ger sämre CSI. Det lilla [Waveshare ESP32-S3-Zero](#waveshare-esp32-s3-zero) fungerar också. |
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

1. Öppna webbinstalleraren i Chrome eller Edge på en dator. Den finns på
   `https://homecontrol-installer.<ditt-namn>.workers.dev` när den är
   publicerad (se [Publicera webbinstalleraren](#publicera-webbinstalleraren)).
   Varje bygge sparas också som artefakten `webbinstallerare` under
   workflowen Firmware i fliken Actions.
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

Kärnan hittar nya kort själv (mDNS). Ett nytt kort syns som **ny enhet** i
hälsoraden och i listan **Enheter** i rummets kort. Tryck på det och sedan
på **Placera på kartan** i dess kort; först då
ansluter kärnan och ger kortet dess nyckel. Nyckeln räknas fram ur `ESPHOME_SECRET` i `.env`
och kortets namn, så den sparas ingenstans. Placera därför kortet så snart
det har fått Wi-Fi: innan kärnan har kopplat kortet kan vem som helst i
hemnätet ansluta och sätta en egen nyckel. Då visar hälsoraden en varning,
och kortets kort säger att kortet har en annan nyckel. Nyckeln tas bara bort om kortet raderas via
webbinstalleraren (**Erase device**). Byts `ESPHOME_SECRET` måste korten
raderas och installeras om på samma sätt.

### Uppdatera

- **Över nätverket:** fungerar när webbinstalleraren är publicerad och
  firmware på kortet är byggd med dess adress (variabeln `INSTALLER_URL`,
  se [Publicera webbinstalleraren](#publicera-webbinstalleraren)). Ett kort
  som installerades innan dess behöver installeras om en gång över USB.
  Kortet kollar var 6:e timme om webbinstalleraren har en ny version och
  visar det i entiteten `Firmware`. Uppdateringen startar
  bara när kärnan ber om det, efter att du har tryckt på **Uppdatera till
  …** (med den nya versionen) i rumskortets kort. Kortet hämtar då
  själv filen över https. Det tar inte emot firmware från någon annan.
  Adressen innehåller ditt namn på workers.dev och Workerns namn
  (`homecontrol-installer`), så **byt inte namnen och ta inte bort
  Workern eller Cloudflare-kontot** så länge korten används. Om namnet
  blir ledigt kan någon annan ta det och skicka firmware till korten.
- **Över USB:** kör webbinstalleraren igen och lämna **Erase device** tom.
  Kortet behåller Wi-Fi och nyckeln.

### Publicera webbinstalleraren

Webbinstalleraren publiceras på Cloudflare (en Worker med bara statiska
filer, [`wrangler.jsonc`](wrangler.jsonc)) varje gång firmware ändras på
`main`. Adressen är https, vilket webbläsaren kräver för att få prata med
kortet över USB (Web Serial). Ingen hemlighet finns i repot; ägaren ställer
in det här en gång:

1. **Namn på workers.dev.** Logga in på [Cloudflare](https://dash.cloudflare.com)
   och öppna **Workers & Pages** en gång. Första gången ber Cloudflare
   dig välja ett namn, `<ditt-namn>` i adressen
   `homecontrol-installer.<ditt-namn>.workers.dev`.
2. **API-token.** Klicka på profilbilden → **My Profile** → **API Tokens**
   → **Create Token** och välj mallen **Edit Cloudflare Workers**. Välj
   bara ditt eget konto där sidan frågar efter konto, och sätt gärna ett
   slutdatum. Skapa token och kopiera den; den visas bara en gång.
3. **Account ID.** Öppna **Workers & Pages** i Cloudflare. Under **Account
   Details** finns **Account ID** med en kopieringsknapp.
4. **Lägg in dem i GitHub.** I repot: **Settings** → **Secrets and
   variables** → **Actions** → fliken **Secrets** → **New repository
   secret**. Skapa `CLOUDFLARE_API_TOKEN` (token från steg 2) och
   `CLOUDFLARE_ACCOUNT_ID` (från steg 3).
5. **Publicera.** **Actions** → **Firmware** → **Run workflow** (på
   `main`). När jobbet **Publicera webbinstalleraren på Cloudflare** är
   klart finns sidan på `https://homecontrol-installer.<ditt-namn>.workers.dev`.
   Adressen står i jobbets logg och under **Workers & Pages** i
   Cloudflare.
6. **Berätta adressen för korten.** Samma sida i GitHub som i steg 4, men
   fliken **Variables** → **New repository variable**: `INSTALLER_URL` =
   adressen från steg 5, utan `/` sist. Kör workflowen igen (steg 5). Nu
   hämtar korten sina uppdateringar därifrån, och workflowen kontrollerar
   att adressen visar den nya versionen. Installera om korten en gång över
   USB, så att de får adressen.

**Extra skydd (valfritt).** Med token kan man byta firmware för alla kort,
och repots secrets kan läsas av workflows på alla grenar. Skapa därför
gärna miljön **cloudflare-installer** under **Settings** → **Environments**,
ställ in **Deployment branches** till bara `main`, och lägg de två
hemligheterna där i stället för bland repots secrets.

Saknas hemligheterna hoppas publiceringen över med en notis, och
workflowen blir ändå grön. Publiceringen körs bara från `main` i det här
repot, aldrig från en PR eller en fork.

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
| Radar Target 1–3 X / Y | Läge i mm, cirka 10 Hz. Med LD2461 mål 1–5, i steg om 100 mm (enligt komponentens ramformat, inte provat) |
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
sekund. Med LD2450 skickas ett värde bara när det ändras. Med LD2461 skickar
firmware alla mål 10 gånger per sekund (så är det inställt). Hur ofta LD2461
själv mäter är inte provat.

Kärnan ser på entiteterna vilken radar kortet har: bara kortet med LD2461
har `Radar Target 4` och `5`.

ESPectre lägger också till några egna inställningar och diagnostikvärden
(tröskel, profil, paketflöden).

## Waveshare ESP32-S3-Zero

[ESP32-S3-Zero](https://www.waveshare.com/wiki/ESP32-S3-Zero) är ett
mycket litet ESP32-S3-kort (4 MB flash, 2 MB PSRAM, USB-C). Firmware kan
användas som den är, men det finns två saker att känna till.

**Antennen.** Kortet har en liten keramisk antenn och inget uttag för
extern antenn (U.FL). Wi-Fi-CSI (ESPectre) blir därför sämre än med en
extern antenn. Radarn är ändå den viktigaste källan för närvaro och
fungerar lika bra.

**Stiften.** Enligt Waveshares pinout-bild och schema (på wikisidan ovan):

| Stift | Var på kortet |
| --- | --- |
| GPIO8, GPIO9 | Kantstiften på högra sidan (märkta 8 och 9). |
| GPIO17, GPIO18 | **Inte** på kantstiften. Bara som små lödpunkter på kortets undersida (märkta 17 och 18, bredvid 38–45). |
| 5V, GND, 3V3 | Kantstiften på vänstra sidan, överst. |

Ljussensorn (GPIO8 och GPIO9) kopplas alltså som vanligt. För radarn
finns två sätt:

1. **Löd på undersidan (rekommenderas).** Löd radarns TX till punkten 18
   och radarns RX till punkten 17. Då passar firmware från
   webbinstalleraren som den är, och kortet får uppdateringar över
   nätverket.
2. **Använd andra stift.** Till exempel GPIO1 (TX) och GPIO2 (RX) på
   vänstra kanten, bredvid 5V och GND. Då måste du bygga firmware själv:

   ```sh
   esphome -s rum kok -s ld2450_tx_pin GPIO1 -s ld2450_rx_pin GPIO2 run firmware/homecontrol.yaml
   esphome -s rum kok -s ld2461_tx_pin GPIO1 -s ld2461_rx_pin GPIO2 run firmware/homecontrol-ld2461.yaml
   ```

   Ett sådant kort får inga uppdateringar över nätverket. **Installera inte
   webbinstallerarens firmware på det över USB**: den har GPIO17 och
   GPIO18, och då svarar radarn inte.

Övrigt:

| Från | Till ESP32-S3-Zero |
| --- | --- |
| Radarns 5V | 5V (kortet får ström via USB-C) |
| Radarns GND | GND |
| BH1750 VCC | 3V3 |
| BH1750 GND | GND |

- Kortet har bara den inbyggda USB-porten (ingen separat USB-UART-krets).
  Det är den porten webbinstalleraren och Wi-Fi-inställningen använder,
  så det fungerar som vanligt.
- Kommer kortet inte in i installationsläget: håll knappen **BOOT** nere
  när du sätter i USB-kabeln.
- Den färgade lysdioden sitter på GPIO21 och används inte av firmware.

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
- Kontrollera på kartan att prickarna hamnar på rätt sida om kortet. Gör de
  inte det: kryssa i **Spegelvänd sidled** under **Fler inställningar** i
  rumskortets kort.
- Simuleringen kan låtsas att ett kort har en LD2461 (**Radar i
  simuleringen**, som syns när kortet väljs i **Ändra rummet**). Den ser då lika långt och brett som en
  LD2450, eftersom LD2461:s räckvidd inte är provad.

**Att prova med riktig hårdvara** innan LD2461 används på riktigt: gör
[hårdvarutestet](#hårdvarutest-radarn-och-espectre-på-samma-kort-i-24-h)
med LD2461, och kontrollera också att

- `Radar Target 1 X` och `Y` följer en person som går runt, och att
  avståndet stämmer med ett måttband,
- 4 och 5 personer i rummet ger 4 och 5 mål,
- `Radar Online` är på hela tiden, även när rummet är tomt. Firmware håller
  `Radar Online` på bara när en hel ram med mål kommer från radarn. Skickar
  LD2461 inga ramar när ingen syns blir rummet okänt, och då behöver det
  ändras.

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

## Dörrklockan

[`dorrklocka.yaml`](dorrklocka.yaml) är firmware för dörrklockan, ett
valfritt tillägg: en Seeed XIAO ESP32S3 Sense med kamera och en
tryckknapp mellan D1 (GPIO2) och GND.

Dörrklockan finns inte i webbinstalleraren. Den behöver hemligheter (Wi-Fi,
MQTT och kamerans nyckel), så du bygger den själv:

1. Installera ESPHome i den version som projektet testas med:

   ```sh
   python3 -m venv .venv && .venv/bin/pip install --require-hashes -r firmware/requirements.txt
   ```

2. Kopiera hemligheterna och fyll i dem. `secrets.yaml` är gitignorerad.
   Kommandot skriver över en `secrets.yaml` som redan finns.

   ```sh
   cp firmware/dorrklocka-secrets.example.yaml firmware/secrets.yaml
   ```

   `mqtt_password` och `api_key` ska vara samma som `MQTT_DOORBELL_PASSWORD`
   och `DOORBELL_CAMERA_KEY` i `.env`.
3. Anslut kortet med USB och bygg och installera:

   ```sh
   .venv/bin/esphome run firmware/dorrklocka.yaml
   ```

   Senare uppdateringar kan göras över nätverket med samma kommando. De
   kräver kamerans nyckel.

För den som vill veta detaljerna gör dörrklockan så här:

- Ett tryck (avstudsat, 50 ms) publicerar `{"tryck": "<id>"}` på
  `dorr/ringer` med QoS 1 och utan retain. Id:t är unikt för varje tryck.
- Kameran tar bilder bara när kärnan ber om det över det krypterade API:t
  (`idle_framerate: 0`). Ingen webbserver, ingen captive portal.
- MQTT-meddelanden ligger bara under `dorr/` (status under `dorr/klocka`).
  Loggen skickas inte över MQTT.

CI validerar och bygger dörrklockan med påhittade hemligheter och
kontrollerar inställningarna ovan (se `.github/workflows/firmware.yml`).

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

## Hårdvarutest: radarn och ESPectre på samma kort i 24 h

Radarn och CSI delar processor och avbrott. Testet visar att de fungerar
tillsammans över tid. Gör det innan ett nytt kort, en ny ESPHome-version
eller en ny ESPectre-version används på riktigt.

**Förberedelse**

1. Installera firmware på ett kort med radarn (LD2450 eller LD2461) och BH1750 inkopplade och
   koppla det till kärnan (eller till `esphome logs`).
2. Med LD2450: kontrollera att `Radar Firmware` inte är 2.14.x.
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
| Loggen | Inga upprepade UART-fel från radarn, inga varningar om full buffert |

**Så här mäts radarns takt:** gå runt i rummet i en minut och räkna hur
många gånger `Radar Target 1 X` uppdateras (i kärnans logg eller med
`esphome logs`). Omkring 600 betyder 10 Hz. Med LD2461 skickar firmware
målen 10 gånger per sekund oavsett, så räkna i stället hur ofta läget
ändras när du går.

**Rapportera** resultatet i ett ärende: kortmodell, firmware-versioner
(Homecontrol, ESPHome, ESPectre, radarn), tabellen ovan och eventuella
avvikelser.
