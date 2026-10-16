# Homecontrol

Närvarostyrd belysning för hemmet. Radar (LD2450) och Wi-Fi-CSI på ESP32
känner av var någon befinner sig, och lamporna (IKEA via Zigbee2MQTT) tänds
och släcks efter det.

> Projektet är i ett tidigt skede. Grunden startar (kärnan, databasen, MQTT och
> Zigbee2MQTT) och det finns en första livevy som ritar hemmet från
> databasen, en våning i taget. Första gången visas ett påhittat exempelhem.
> Dra en låtsasperson in i ett rum så tänds de virtuella lamporna när det är
> mörkt, och de släcks när rummet har varit tomt en stund. I kartredigeraren
> ritar du dina egna rum ovanpå en bild av planritningen. Riktiga sensorer
> och lampor kopplas in i senare steg.

## Snabbstart

Du behöver [Docker](https://docs.docker.com/get-docker/) med Compose.
Det fungerar på vanliga datorer (amd64) och på Raspberry Pi 4/5 (arm64).

```sh
git clone https://github.com/YXHFR/Homecontrol.git
cd Homecontrol
cp .env.example .env
```

Öppna filen `.env` och byt ut alla `byt-mig` mot egna lösenord. Filnamnet
börjar med en punkt, så den kan vara dold i filhanteraren. Öppna den till
exempel med `nano .env` (spara med Ctrl+O och avsluta med Ctrl+X).

Starta sedan:

```sh
docker compose up -d
```

Första gången tar det några minuter. Därefter finns:

| Vad                                   | Adress                       |
| ------------------------------------- | ---------------------------- |
| Homecontrol (livevyn)                 | http://localhost:8000        |
| Kartredigeraren                       | http://localhost:8000/#redigera |
| Sida som visar att kärnan är igång    | http://localhost:8000/health |
| Zigbee2MQTT (lösenord: `ZIGBEE2MQTT_AUTH_TOKEN` i `.env`) | http://localhost:8080 |

Surfar du från en annan enhet, byt `localhost` mot datorns adress i
hemnätet. Den hittar du till exempel i routerns lista över anslutna enheter.
Använder du ett namn i stället för adressen, till exempel
`homecontrol.local`, skriv in det som `ALLOWED_HOSTS` i `.env`. Annars
kopplas livevyn bort.

### Zigbee-adapter

Koppla in Zigbee-adaptern (USB-stickan) och ta reda på dess sökväg:

```sh
ls /dev/serial/by-id/
```

Skriv in hela sökvägen i `.env`, till exempel:

```sh
ZIGBEE_ADAPTER=/dev/serial/by-id/usb-...
```

Har du en adapter som ansluts via nätverket i stället sätter du
`ZIGBEE_SERIAL_PORT` (se `.env.example`).

Starta om med `docker compose up -d`. Öppna sedan Zigbee2MQTT i webbläsaren
och följ guiden. MQTT-uppgifterna är redan ifyllda.

### Om något inte fungerar

```sh
docker compose ps      # alla fyra ska stå som "Up", de flesta även "healthy"
docker compose logs    # visar vad tjänsterna skriver, leta efter fel
```

Ett vanligt fel är att `.env` saknas eller att ett värde är tomt. Då säger
`docker compose up` vilken inställning som saknas.

### Rita hemmet i kartredigeraren

Öppna **Kartredigerare** högst upp på sidan. Verktygen finns till vänster,
kartan i mitten och en instruktion för det verktyg du har valt till höger. På
mobilen ligger verktygen ovanför kartan och instruktionen under.

1. **Ladda upp bild** av planritningen, till exempel från mäklarannonsen.
   Bilden förminskas och rensas från extra uppgifter (som var bilden togs)
   innan den sparas i databasen. Den följer inte med när hemmet exporteras.
2. **Skala bilden**: klicka på två punkter som du vet avståndet mellan, till
   exempel båda ändarna av en vägg, och skriv in avståndet i meter. Gör det
   innan du ritar rummen.
3. Rita rummen. **Rum, fri form**: klicka ut hörnen ett i taget och stäng
   rummet genom att klicka på det första hörnet igen (passar L-formade rum).
   **Rum, rektangel**: dra från ett hörn till hörnet snett emot. Ett klick
   nära ett hörn som redan finns hamnar exakt på det, så att rummen möts.
4. **Välj och flytta**: klicka på ett rum för att byta namn på det eller ta
   bort det, och dra det för att flytta det.

Allt sparas direkt, och livevyn visar ändringen utan omstart. Under
**Hemmet** står hur många rum, zoner, rumskort, routrar och lampor hemmet
har. Väggarnas material, rumskort, routrar, lampor och zoner ritas i kommande
versioner; tills dess får nya rum gipsväggar.

### Hemmet: spara, flytta och läsa in

Hemmet (våningar, rum, väggar, dörröppningar, zoner, rumskort, routrar och
lampor) sparas i databasen. Första gången kärnan startar läggs ett påhittat
exempelhem in. Hemmet kan sparas till en läsbar fil och läsas in igen, till
exempel som säkerhetskopia eller för att flytta det till en annan dator:

```sh
docker compose exec -T core homecontrol exportera > hem.json      # spara till fil
docker compose exec -T core homecontrol importera - < hem.json     # läs in från fil
docker compose exec -T core homecontrol importera --exempelhem     # börja om med exempelhemmet
docker compose restart core                                        # efter en import
```

Filen kontrolleras innan något ändras, så en trasig fil lämnar hemmet som det
var, och felmeddelandet säger var i filen felet finns. En egen hemfil
innehåller din planlösning, så lägg den inte i repot eller på nätet.

Så läser du filen:

- Alla mått är i meter, räknat från övre vänstra hörnet av ritningen: `x`
  växer åt höger och `y` nedåt. En punkt skrivs `[x, y]`.
- `id` är ett kort namn som programmet använder. Det får bara innehålla
  små bokstäver a–z, siffror, `-` och `_` (alltså inte å, ä, ö). `name` är
  namnet som visas och får vara vad som helst.
- `level` är våningsplanet: `0` är entréplanet, `1` en trappa upp och `-1`
  källaren.
- Ett rum är en yta med hörnen i `polygon`. `walls` har en vägg per sida:
  den första väggen går från första till andra hörnet, den andra från
  andra till tredje, och den sista tillbaka till första hörnet. En vägg är
  `gips`, `betong`, `trä`, `glas` eller `dörröppning`. Två rum som delar en
  vägg måste ange samma sort. Byter väggen sort på vägen läggs ett extra
  hörn där.
- `settings` per rum: under `dark_below_lux` räknas rummet som mörkt, och
  lamporna släcks när rummet har varit tomt i `off_after_empty_seconds`
  sekunder.
- `zones` är delar av ett rum: `vanlig`, `stillhet` (soffa, säng),
  `ignorera` (störkällor som en fläkt) eller `entré` (vid en dörr).
- `nodes` är rumskorten: var de sitter (`position`), monteringshöjd i
  meter (`height`), åt vilket håll radarn tittar i grader medurs där 0 är
  åt höger (`rotation`), om kortet sitter upp och ned (`mirrored`) och vad
  det kan mäta (`radar`, `csi`, `lux`).
- `signal_sources` är routrar och andra Wi-Fi-sändare.
- `plan_image` säger var bilden av planritningen ligger (`position`, bildens
  övre vänstra hörn) och hur många meter en bildpunkt är
  (`meters_per_pixel`). Bilden själv finns bara i databasen.
- `lamps` har en plats, kan höra till en zon (`zone_id`) och har en
  `profile` som säger hur lampan styrs (`generisk` eller `ikea-tradfri`).

Zonerna, lampornas zon och profil, `bright_above_lux` och rumskortens
placering sparas redan men används först i kommande versioner.

Livevyn och kartredigeraren kräver ingen inloggning än, så alla i
hemnätverket som når Homecontrol kan se och ändra planlösningen och bilden av
planritningen. Vill du att bara den här datorn ska nå den
sätter du `BIND_ADDRESS=127.0.0.1` i `.env`.

### Stoppa och uppdatera

```sh
docker compose down                        # stoppa (data sparas)
git pull && docker compose up -d --build   # uppdatera
```

## Delar

| Mapp         | Innehåll                                                   |
| ------------ | ---------------------------------------------------------- |
| `core/`      | Kärnan i Python. Svarar på `/health` och serverar webbgränssnittet. |
| `web/`       | Webbgränssnittet i TypeScript.                             |
| `firmware/` | ESPHome-firmware för rumskorten och webbinstalleraren, se [firmware/README.md](firmware/README.md). |
| `mosquitto/` | Inställningar för MQTT-brokern.                            |
| `scripts/`   | Hjälpskript, t.ex. för att klassa ändringar inför granskning. |
| `.claude/agents/` | Granskningsagenter för Claude Code.                   |

## För utvecklare

Kärnan (kräver [uv](https://docs.astral.sh/uv/)):

```sh
cd core
uv sync
uv run pytest tests ../scripts/tests
uv run ruff check .. && uv run ruff format --check .. && uv run mypy
HOMECONTROL_WEB_DIR=../web/dist uv run homecontrol
```

Utan `HOMECONTROL_DATABASE` visar kärnan exempelhemmet och sparar inget.
Testerna av databasdelen behöver en Postgres och hoppas annars över:

```sh
HOMECONTROL_TEST_DATABASE="host=localhost dbname=homecontrol_test user=homecontrol" \
  uv run pytest tests
```

Webbgränssnittet (kräver Node 22):

```sh
cd web
npm ci
npm run lint && npm test && npm run build
npm run dev    # utvecklingsserver som skickar API-anrop till kärnan
```

Webbläsartesterna (Playwright) bygger webben och startar en egen kärna på
port 8123. Med `HOMECONTROL_DATABASE` sparar den i databasen, annars i minnet:

```sh
npx playwright install chromium   # första gången
npm run test:e2e
```

Nya API-vägar i kärnan läggs under `/api/...` så att de inte krockar med
webbgränssnittets filer. Livevyn får sin realtidsström från `/api/live`.
Kartredigeraren läser och sparar hemmet med `/api/home` och bilden av
planritningen med `/api/floors/{id}/plan-image` (se `editor.py`). Ett sparat
hem går till kärnan med `Controller.replace_home`, så livevyn får det direkt.

Kärnans flöde: källkopplingar (`connectors.Source`) lämnar observationer,
närvarobedömningen avgör vilka rum som är upptagna, regelmotorn
(`rules.evaluate`, en ren funktion) ger lampkommandon med förklaringar och
lamputgångar (`connectors.LampOutput`) tar emot dem. Simuleringen
(`simulation.py`) är både en källa och en utgång.

Hemmodellen finns i `home.py` och kontrolleras där. `homefile.py` gör om den
till och från ett JSON-dokument, och `store.py` sparar samma dokument i
Postgres (tabellerna skapas av `migrations/`). Formen beskrivs alltså bara i
`home.py`, och filen och databasen har samma version. Exempelhemmet är
`example_home.json`. Kärnan läser hemmet när den startar; en import kräver
därför en omstart. Kärnan startar inte utan databasen.

Scenariotester skrivs som läsbara steg med `homecontrol.scenario.Scenario`
och körs med en styrd klocka, så att minuter av väntan tar ingen tid. Se
`core/tests/test_scenarios.py`.

### Granskning

Fyra granskningsagenter finns i `.claude/agents/`: korrekthet/tester/realtid,
säkerhet/integritet, arkitektur och läsbarhet. Kör först

```sh
python3 scripts/classify_change.py
```

Skriptet säger om ändringen är **större**: mer än cirka 200 rader, minst
5 filer, en ny modul, ett nytt beroende eller ett känsligt område. Större
ändringar granskas av alla fyra agenterna; mindre av korrekthet och
läsbarhet.

### Regler

- Commit-meddelanden följer [conventional commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `chore:` …). Versioner sätts automatiskt utifrån dem.
- Hemligheter läggs bara i `.env`, aldrig i repot.
- Inga personliga uppgifter (planritningar, nätverksadresser, nycklar)
  får checkas in.

## Licens

[Apache-2.0](LICENSE)

Undantag: firmware-binären för rumskorten innehåller ESPectre och ESPHome
och blir därför GPL-3.0. Konfigurationen i `firmware/` är Apache-2.0. Se
[firmware/README.md](firmware/README.md#licens).
