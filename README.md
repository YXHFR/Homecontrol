# Homecontrol

Närvarostyrd belysning för hemmet. Radar (LD2450) och Wi-Fi-CSI på ESP32
känner av var någon befinner sig, och lamporna (IKEA via Zigbee2MQTT) tänds
och släcks efter det.

> Projektet är i ett tidigt skede. Grunden startar (kärnan, databasen, MQTT och
> Zigbee2MQTT) och det finns en första livevy som ritar hemmet från
> databasen, en våning i taget. Första gången visas ett påhittat exempelhem.
> Livevyn har ett simuleringsläge: lägg till låtsaspersoner och dra in dem i
> rummen med musen eller fingret. Varje rumskort blir en virtuell radar som
> ser personerna framför sig, och kärnan gör om det radarerna ser till
> prickar på kartan och upptagna rum. Då tänds de virtuella lamporna när det
> är mörkt, dimras som en varning och släcks när rummet har varit tomt en
> stund, och släcks när dagsljuset räcker. Ett klick på en lampa tänder eller
> släcker den för hand, och under **Varför hände det?** står varför lamporna
> gjorde som de gjorde. Dagsljuset ställs med
> ett reglage, och tiden kan gå 10 eller 60 gånger fortare så att du slipper
> vänta. I kartredigeraren ritar du dina egna rum ovanpå en bild av
> planritningen, väljer vad väggarna är gjorda av och var dörröppningarna är,
> och placerar rumskort, routrar och lampor. Varje rum kan få egna ljusgränser
> och en egen fördröjning. Riktiga sensorer och lampor kopplas in i senare steg.

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
   Webbläsaren förminskar bilden och rensar bort extra uppgifter i filen (som
   var bilden togs) innan den skickas. Bilden sparas i databasen och följer
   inte med när hemmet exporteras.
2. **Skala bilden**: klicka på två punkter som du vet avståndet mellan, till
   exempel båda ändarna av en vägg, och skriv in avståndet i meter. Gör det
   innan du ritar rummen.
3. Rita rummen. **Rum, fri form**: klicka ut hörnen ett i taget och stäng
   rummet genom att klicka på det första hörnet igen (passar L-formade rum).
   **Rum, rektangel**: dra från ett hörn till hörnet snett emot. Ett klick
   nära ett hörn som redan finns hamnar exakt på det, så att rummen möts.
4. **Välj och flytta**: klicka på ett rum eller en zon för att byta namn på
   det eller ta bort det, och dra ett rum för att flytta det; zonerna i
   rummet följer med. En zon kan också byta sort, och ett klick till på en
   vald zon väljer rummet under den. För ett valt rum ställer du också in
   rummets ljus (se **Ljusreglerna** nedan): under vilken ljusnivå lamporna
   tänds, över vilken nivå dagsljuset räcker och hur länge, hur många minuter
   rummet ska ha varit tomt innan de släcks, hur länge och hur mycket de dimras
   innan dess och om rummet saknar fönster. Där finns också Wi-Fi-rörelsens
   vikt (se **Närvaro** nedan).
5. **Väggar och dörrar**: klicka på en vägg för att byta material, ett steg
   per klick: gips → betong → trä → glas → dörröppning och sedan gips igen.
   En vägg som två rum delar byts i båda.
6. **Zoner**: välj sort under **Zoner** bland verktygen och klicka ut hörnen
   inne i ett rum, på samma sätt som ett rum med fri form. Varje sorts zon
   har en egen färg:
   - **Vanlig zon**: en plats som systemet ska känna till, till exempel
     skrivbordet.
   - **Stillhetszon**: där man sitter eller ligger still, till exempel
     soffan och sängen. Där hålls närvaron kvar när radarn tappar personen,
     så länge Wi-Fi känner rörelse i rummet (högst 30 minuter).
   - **Ignorera-zon**: störkällor som en fläkt, en gardin eller en växt.
   - **Entrézon**: vid en dörr, där den som försvinner ur sikte har gått ut.

   Zonen ska ligga i ett rum; ett hörn nära en vägg hamnar på väggen.
7. **Placera** rumskort, routrar och lampor genom att välja verktyget och
   klicka på kartan. Rumskort och lampor placeras inne i ett rum och hör till
   det rummet. Den ljusa tårtbiten framför ett rumskort är vad radarn ser
   (6 meter, 60° åt vardera hållet). Välj kortet med **Välj och flytta** och
   vrid det med ringen runt kortet eller med knapparna, och ange höjden och om
   det sitter spegelvänt. En lampa som dras in i ett annat rum styrs sedan av
   det rummet; du kan också byta rum i listan.

Bilden och rummen hör till våningen som är vald med knapparna ovanför kartan.
Med tangentbordet avbryter Esc det du ritar, Backspace ångrar senaste hörnet
och Enter stänger rummet eller zonen.

Allt sparas direkt, och livevyn visar ändringen utan omstart. Har du
redigeraren öppen i två fönster och ändrar i båda, visar det ena fönstret
det andras ändring i stället för att skriva över den. Under
**Hemmet** står hur många rum, zoner, rumskort, routrar och lampor hemmet
har.

### Närvaro

Varje rumskort ser personer med radarn. Kärnan räknar om det radarn ser
utifrån var kortet sitter, hur högt, åt vilket håll det tittar och om det är
spegelvänt. Det radarn ser utanför kortets eget rum (genom en vägg) och i
ignorera-zoner räknas inte med. Kvar blir anonyma spår, prickar på kartan.

Tappar radarn någon räknas personen som kvar en stund, beroende på var:

- i en entrézon: inte alls, personen har gått ut
- i en stillhetszon: så länge Wi-Fi känner rörelse i rummet, högst 30 minuter
- annars i rummet: cirka 10 sekunder

En person som hålls kvar visas som en streckad ring i livevyn. Under
**Just nu** står om varje rum är upptaget, tomt eller okänt. Okänt betyder att
ett rumskort i rummet har slutat svara; då lämnas lamporna som de är.

Wi-Fi-rörelsens vikt ställs per rum, från 0 till 1. Vikten gånger hur säker
mätningen är (också 0–1) avgör vad rörelsen räcker till. Från en fjärdedel
förlänger den närvaron i stillhetszoner, och vid 1 räcker Wi-Fi-rörelse
ensam för att rummet ska räknas som upptaget. Med 0 används inte Wi-Fi alls,
och med standardvärdet 0,5 förlänger tydlig rörelse närvaron men startar
den aldrig.

Livevyn visar alltså också när någon sitter eller ligger still, i vilka
zoner, och vilka sensorer som inte svarar. Tänk på det när du bestämmer vem
som når livevyn (se nedan).

### Ljusreglerna

Reglerna gäller ett rum i taget, och alla lampor i rummet följer varandra.
Värdena inom parentes är standard och ändras per rum i kartredigeraren.

- **Tänd** när någon är i rummet och det är mörkt (under 30 lux).
- **Varningsdimring**: när rummet har varit tomt i 2 minuter och 30
  sekunder dimras lamporna till hälften (50 %) i 30 sekunder. Rör sig någon
  i rummet går ljuset upp igen.
- **Släck** när rummet har varit tomt i 3 minuter.
- **Släck vid dagsljus** när någon är i rummet men det har varit ljust (över
  100 lux) i 10 minuter i sträck. Blir det mörkare emellan börjar tiden om.
- **Rum utan fönster** räknas alltid som mörka och behöver ingen ljussensor.
- **Okänt läge**: har ett rumskort slutat svara görs ingenting med lamporna
  i rummet; de lämnas som de är.

Ljusnivån som reglerna använder är den uppmätta minus det ljus som rummets
tända lampor själva ger vid ljussensorn (mindre när de är dimrade). Därför
släcker en lampa aldrig sig själv för att den lyser upp rummet. Hur mycket
varje lampa lyser upp sensorn mäts av lampkalibreringen, som kommer i ett
senare steg; tills dess är bidraget 0. Under **Just nu** står båda värdena
när de skiljer sig.

Kärnan antar inte att lamporna är släckta när den startar. Tills en lampa
har berättat hur den är, eller kärnan själv har styrt den, är läget okänt.
En lampa med okänt läge tänds när rummet ska tändas, men släcks eller dimras
aldrig på chans. De virtuella lamporna i simuleringen är släckta från början.

**Manuell styrning**: klicka på en lampa på kartan, eller på lampans knapp
under **Just nu**, så tänds eller släcks den. Automatiken i rummet pausar
då, så att den inte går emot ditt val (till exempel när du släcker för att
sova), tills rummet har varit tomt i hela släckfördröjningen (3 minuter).
Rummet står som **manuellt** på kartan och under **Just nu**, och med
**Automatik igen** tar automatiken över direkt.

**Varför hände det?** listar de senaste besluten med klockslag och värden,
till exempel "19:02 Vardagsrum: Tändes: någon är i Vardagsrum och det var
mörkt (9 lux, under 30)." Listan finns bara i kärnans minne och töms när den
startar om.

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

Starta om kärnan direkt efter en import. Annars kan en ändring i
kartredigeraren skriva över det du just importerade.

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
- `settings` per rum (se **Ljusreglerna**): under `dark_below_lux` räknas
  rummet som mörkt och över `bright_above_lux` som ljust av dagsljus; efter
  `daylight_off_after_seconds` sekunder av dagsljus i sträck släcks lamporna.
  De släcks också när rummet har varit tomt i `off_after_empty_seconds`
  sekunder, efter att ha dimrats till `dim_percent` procent i
  `dim_before_off_seconds` sekunder (0 = ingen dimring). `windowless: true`
  betyder att rummet saknar fönster och alltid är mörkt. Saknas ett värde i
  filen gäller standardvärdet.
- `zones` är delar av ett rum: `vanlig`, `stillhet` (soffa, säng),
  `ignorera` (störkällor som en fläkt) eller `entré` (vid en dörr). En zon
  ska ligga i sitt rum och får inte gå ut genom någon vägg (längs väggen går
  bra). Har ett sparat hem en zon som går utanför, startar kärnan inte utan
  säger vilken zon det gäller. Rätta filen och läs in den igen.
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
  `lux_contribution` är hur många lux lampan ger vid rummets ljussensor på
  full styrka (0 om inget anges, se **Ljusreglerna**).

- `wifi_weight` i `settings` är hur mycket Wi-Fi-rörelse väger mot radarn,
  från 0 till 1 (0,5 om inget anges, se **Närvaro**).

Lampornas zon och profil sparas redan men används först i kommande versioner.

### Vem som når Homecontrol

Homecontrol har inga konton och ingen inloggning: alla i hushållet har samma
åtkomst. Den som når livevyn kan alltså se var någon är, tända och släcka
lamporna och ändra planlösningen och bilden av planritningen.

- Som standard (`BIND_ADDRESS=0.0.0.0`) nås Homecontrol från alla nätverk
  som datorn är ansluten till: hemnätverket, och Tailscale om det är
  installerat. Med `BIND_ADDRESS=127.0.0.1` i `.env` nås den bara från den
  här datorn.
- Utanför hemmet når du livevyn med [Tailscale](https://tailscale.com/):
  installera det på datorn som kör Homecontrol och på din telefon, och surfa
  till datorns Tailscale-adress (100.x.y.z) och port 8000. Använder du
  datorns Tailscale-namn (MagicDNS), skriv in namnet i `ALLOWED_HOSTS`.
- Öppna aldrig port 8000 (eller någon annan av portarna) mot internet i
  routern.
- Släpper du in gäster på hemnätverket kan de nå Homecontrol. Ett gästnätverk
  i routern, skilt från hemnätverket, hindrar det.

Kärnan svarar bara på anrop till de adresser och namn den känner till
(`localhost`, IP-adresser och `ALLOWED_HOSTS`), och bara från sina egna
sidor. Det gäller även lampstyrningen. Det hindrar att en främmande webbsida
som någon i hemmet besöker styr lamporna eller läser kartan genom
webbläsaren.

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

Utan `HOMECONTROL_DATABASE` visar kärnan exempelhemmet, och ändringar från
kartredigeraren sparas bara så länge kärnan är igång.
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
planritningen med `/api/floors/{id}/plan-image` (se `core/src/homecontrol/editor.py`
och klienten i `web/src/homeApi.ts`). Varje
ändring ger en ny revision (`ETag`), och en ändring som utgår från en gammal
revision (`If-Match`) nekas. Ett sparat hem går till kärnan med
`Controller.replace_home`, så livevyn får det direkt.

Kärnans flöde: källkopplingar (`connectors.Source`) lämnar observationer,
sammanvägningen (`fusion.py`) gör om dem till spår och avgör vilka rum som
är upptagna, regelmotorn (`rules.evaluate`, en ren funktion) ger
lampkommandon med förklaringar och lamputgångar (`connectors.LampOutput`)
tar emot dem. Lamputgångarna berättar hur lamporna är med
`LampStateReported` genom samma ingång som källorna. Varje regel i
regelmotorn är en egen klass med metoden `decide`; en ny sorts regel läggs
till i listan `rules.DEFAULT_RULES` utan att de andra ändras (se
`core/src/homecontrol/rules.py`). Manuell styrning från livevyn går till
`Controller.set_lamp`. Simuleringen
(`simulation.py`) är både en källa och en utgång. Simuleringsläget i livevyn
styr låtsaskällan (personer och dagsljus) och klockans tidsfart; personerna
når kärnan genom samma ingång som en riktig källa, som radarmål i varje
rumskorts egna koordinater.

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
