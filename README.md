# Homecontrol

Närvarostyrd belysning för hemmet. Radar (LD2450) och Wi-Fi-CSI på ESP32
känner av var någon befinner sig, och lamporna (IKEA via Zigbee2MQTT) tänds
och släcks efter det.

> Projektet är i ett tidigt skede. Just nu startar grunden: kärnan, databasen,
> MQTT och Zigbee2MQTT. Själva närvarostyrningen kommer i senare steg.

## Snabbstart

Du behöver [Docker](https://docs.docker.com/get-docker/) med Compose.
Det fungerar på vanliga datorer (amd64) och på Raspberry Pi 4/5 (arm64).

```sh
git clone https://github.com/YXHFR/Homecontrol.git
cd Homecontrol
cp .env.example .env
```

Öppna `.env` och byt ut alla `byt-mig` mot egna lösenord. Starta sedan:

```sh
docker compose up -d
```

Första gången tar det några minuter. Därefter finns:

| Vad                 | Adress                       |
| ------------------- | ---------------------------- |
| Homecontrol         | http://localhost:8000        |
| Hälsokontroll       | http://localhost:8000/health |
| Zigbee2MQTT         | http://localhost:8080        |

Byt `localhost` mot datorns adress om du surfar från en annan enhet.

### Zigbee-adapter

Koppla in Zigbee-adaptern och skriv in dess sökväg i `.env`, till exempel:

```sh
ZIGBEE_ADAPTER=/dev/serial/by-id/usb-...
```

Starta om med `docker compose up -d`. Öppna sedan Zigbee2MQTT i webbläsaren
och följ guiden. MQTT-uppgifterna är redan ifyllda.

### Stoppa och uppdatera

```sh
docker compose down            # stoppa (data sparas)
git pull && docker compose up -d --build   # uppdatera
```

## Delar

| Mapp         | Innehåll                                                   |
| ------------ | ---------------------------------------------------------- |
| `core/`      | Kärnan i Python. Hälsokontroll och serverar webbgränssnittet. |
| `web/`       | Webbgränssnittet i TypeScript.                             |
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

Webbgränssnittet (kräver Node 22):

```sh
cd web
npm ci
npm run lint && npm test && npm run build
npm run dev    # utvecklingsserver som skickar API-anrop till kärnan
```

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
