# Homecontrol

Närvarostyrd belysning för hemmet. Radar (LD2450) och Wi-Fi-CSI på ESP32
känner av var någon befinner sig, och lamporna (IKEA via Zigbee2MQTT) tänds
och släcks efter det.

> Projektet är i ett tidigt skede. Grunden startar (kärnan, databasen, MQTT och
> Zigbee2MQTT) och det finns en första livevy som ritar hemmet från
> databasen, en våning i taget. Första gången visas ett påhittat exempelhem.
> Dra en låtsasperson in i ett rum så tänds de virtuella lamporna när det är
> mörkt, och de släcks när rummet har varit tomt en stund. Riktiga sensorer
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
var. I filen är alla mått i meter, räknat från övre vänstra hörnet av
ritningen (x åt höger, y nedåt), och vridning anges i grader medurs. En egen
hemfil innehåller din planlösning, så lägg den inte i repot eller på nätet.

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

Nya API-vägar i kärnan läggs under `/api/...` så att de inte krockar med
webbgränssnittets filer. Livevyn får sin realtidsström från `/api/live`.

Kärnans flöde: källkopplingar (`connectors.Source`) lämnar observationer,
närvarobedömningen avgör vilka rum som är upptagna, regelmotorn
(`rules.evaluate`, en ren funktion) ger lampkommandon med förklaringar och
lamputgångar (`connectors.LampOutput`) tar emot dem. Simuleringen
(`simulation.py`) är både en källa och en utgång.

Hemmodellen finns i `home.py`. Den sparas i Postgres av `store.py`, med
tabellerna i `migrations/`, och skrivs till fil av `homefile.py`. Exempelhemmet
är `example_home.json`.

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
