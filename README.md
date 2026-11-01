# Homecontrol

Närvarostyrd belysning för hemmet. Radar och Wi-Fi-CSI på ESP32 känner av
var någon befinner sig, och Zigbee-lamporna tänds och släcks efter det.

> Under arbete. Projektet byggs och testas just nu i ett enda rum och är
> inte färdigt att användas av andra än.

## Kör

```sh
cp .env.example .env    # byt ut alla byt-mig mot egna lösenord
docker compose up -d
```

Sidan finns sedan på http://localhost:8000. Utan hårdvara kan allt provas i
simuleringsläget.

**Windows 11:** följ [WINDOWS.md](WINDOWS.md), steg för steg (Ubuntu i WSL,
Docker, brandväggen och Zigbee-stickan).

**Rumskortens firmware** installeras från webbläsaren med webbinstalleraren,
se [firmware/README.md](firmware/README.md#installera).

## Delar

| Mapp         | Innehåll                                         |
| ------------ | ------------------------------------------------ |
| `core/`      | Kärnan i Python.                                 |
| `web/`       | Webbgränssnittet i TypeScript.                   |
| `firmware/`  | ESPHome-firmware för rumskorten och dörrklockan. |
| `mosquitto/` | Inställningar för MQTT-brokern.                  |
| `postgres/`  | Inloggningsregler för databasen.                 |
| `scripts/`   | Hjälpskript.                                     |

## Licens

[Apache-2.0](LICENSE). Undantag: firmware-binären för rumskorten innehåller
ESPectre och ESPHome och blir därför GPL-3.0, se
[firmware/README.md](firmware/README.md#licens).
