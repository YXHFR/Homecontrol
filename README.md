# Homecontrol

Närvarostyrd belysning för hemmet. Radar (LD2450, eller LD2461 som
alternativ) och Wi-Fi-CSI på ESP32 känner av var någon befinner sig, och
lamporna (IKEA via Zigbee2MQTT) tänds och släcks efter det.

> Projektet är i ett tidigt skede men fungerar från början till slut: du
> ritar hemmet ovanpå en planritning, rumskorten (ESP32) ser var personerna
> är, och kärnan tänder, dimrar och släcker Zigbee-lamporna efter ljusregler
> som går att ändra per rum. Livevyn visar prickar på kartan och förklarar
> under **Varför hände det?** varför lamporna gjorde som de gjorde. Utan
> hårdvara kan allt provas i simuleringsläget, med låtsaspersoner som du
> drar runt i hemmet.

## Omfattning just nu: ett rum

Tills vidare byggs och testas Homecontrol i **ett enda rum**. Mjukvaran
klarar flera rum och våningar, men hårdvaran och firmware-bygget är
nedskalade tills ett rum fungerar hela vägen:

| Del | Antal | Kommentar |
| --- | --- | --- |
| HLK-LD2450 | 1 | Radarn i rummet. |
| ESP32-S3 (till exempel ESP32-S3-Zero) | 1 | Rumskortet. Kort med inbyggd kretskortsantenn ger sämre Wi-Fi-CSI, så CSI är parkerat tills vidare och radarn är huvudkällan. Kontrollera att kortet har stiften i [kopplingen](firmware/README.md#koppling), annars ändras de med `substitutions`. |
| BH1750 | 1 | Ljussensorn. |
| Zigbee-sticka | 1 | Sonoff Dongle Plus MG24 med Zigbee2MQTT. |
| IKEA KAJPLATS | 1 | Lampan. KAJPLATS säljs som Matter over Thread men har ett dolt Zigbee-läge som [Zigbee2MQTT stöder](https://www.zigbee2mqtt.io/devices/KAJPLATS_WS.html): slå av och på lampan 12 gånger (15 på vissa modeller) för att para den. Fungerar det inte, använd en TRÅDFRI-lampa. |
| Dator som hjärna | 1 | En gammal laptop eller dator som står på räcker. |

Webbinstalleraren bygger därför bara firmware för rummet i
[`firmware/rooms.txt`](firmware/rooms.txt). Rita bara det rummet i
kartredigeraren (steg 4). Fler rum, Wi-Fi-CSI som andra källa och egna
kretskort läggs till igen när det här fungerar: lägg då till rummen i
`firmware/rooms.txt` och följ resten av guiden som den står.

## Installera steg för steg

Guiden kräver ingen programmering. Du skriver av några kommandon i ett
terminalfönster och gör resten i webbläsaren. Stegen i ordning:

1. [Köp hårdvaran](#steg-1-köp-hårdvaran)
2. [Gör i ordning datorn](#steg-2-gör-i-ordning-datorn)
3. [Starta Homecontrol](#steg-3-starta-homecontrol)
4. [Rita hemmet i kartredigeraren](#steg-4-rita-hemmet-i-kartredigeraren)
5. [Koppla in Zigbee-lamporna](#steg-5-koppla-in-zigbee-lamporna)
6. [Installera rumskorten](#steg-6-installera-rumskorten)
7. [Kalibrera lamporna](#steg-7-kalibrera-lamporna)
8. [Nå livevyn utanför hemmet med Tailscale](#steg-8-nå-livevyn-utanför-hemmet-med-tailscale)

Vill du först bara prova? Gör steg 2–4. Utan rumskort och lampor startar
Homecontrol i simuleringsläget, där du drar låtsaspersoner runt i hemmet
och ser virtuella lampor tändas och släckas.

### Steg 1: Köp hårdvaran

**En dator som står på dygnet runt** och kör Homecontrol:

| Del | Kommentar |
| --- | --- |
| Raspberry Pi 4 eller 5 med minst 4 GB minne | Eller en vanlig dator eller minidator med Linux (64 bitar). |
| SSD-disk som ansluts med USB (eller via M.2 på Pi 5) | Rekommenderas starkt. Ett minneskort blir långsamt och slits ut av historiken. |
| Den officiella strömadaptern till Pi:n | En svag adapter ger konstiga fel. |
| Nätverkskabel till routern | Wi-Fi fungerar också, men kabel är stabilare. |

**För lamporna:**

| Del | Kommentar |
| --- | --- |
| Zigbee-adapter (USB-sticka) | En som [Zigbee2MQTT stöder](https://www.zigbee2mqtt.io/guide/adapters/). Sätt den gärna på en USB-förlängningskabel, en bit från datorn, så störs den mindre. |
| Zigbee-lampor | I första hand IKEA TRÅDFRI. Serien håller på att utgå, så köp dem i god tid. |

**Ett rumskort per rum** där lamporna ska styras av närvaro:

| Del | Kommentar |
| --- | --- |
| ESP32-S3-kort med extern antenn | Till exempel ESP32-S3-DevKitC-1U med en U.FL-antenn. |
| HLK-LD2450 | Radarn som ser var personerna är, upp till 3 åt gången. |
| eller HLK-LD2461 | Alternativ till LD2450 som följer upp till 5 personer. Antalet 5 och precisionen är tillverkarens uppgifter och inte provade än (se [firmware/README.md](firmware/README.md#ld2461-i-stället-för-ld2450)). |
| BH1750 | Ljussensorn. Behövs inte i rum utan fönster. |
| Kopplingskablar (dupont, hona–hona) | För att koppla ihop delarna. |
| USB-kabel som klarar data, och en USB-laddare | En ren laddkabel fungerar inte för installationen. |

Hur delarna kopplas ihop står i [firmware/README.md](firmware/README.md#koppling).

Du behöver också en dator med Chrome eller Edge för att installera
rumskorten (steg 6), och ett gratis [Tailscale](https://tailscale.com/)-konto
om du vill nå livevyn när du inte är hemma (steg 8).

### Steg 2: Gör i ordning datorn

På en Raspberry Pi:

1. Installera **Raspberry Pi OS Lite (64-bit)** på SSD-disken med
   [Raspberry Pi Imager](https://www.raspberrypi.com/software/). I Imagerns
   inställningar väljer du ett användarnamn och ett starkt lösenord, slår
   på **SSH** och anger Wi-Fi om du inte använder kabel. Kan du, välj
   inloggning med nyckel (public-key) för SSH i stället för lösenord.
   Homecontrol kräver 64 bitar.
2. Starta Pi:n från disken och logga in från din vanliga dator med
   `ssh användarnamn@datornamn.local` (namnet du valde i Imagern), eller
   med skärm och tangentbord direkt på Pi:n.

Installera sedan Docker med Compose enligt
[Dockers guide för Debian](https://docs.docker.com/engine/install/debian/).
Raspberry Pi OS är Debian, så den guiden gäller. Installera också git:

```sh
sudo apt install git
```

Följ också Dockers steg
[Manage Docker as a non-root user](https://docs.docker.com/engine/install/linux-postinstall/),
så slipper du skriva `sudo` före varje `docker`. Kontrollera att allt finns:

```sh
docker compose version
```

Versionen ska vara 2.20 eller senare.

Homecontrol fungerar på samma sätt på andra Linux-datorer (amd64 eller
arm64). Docker Desktop på Mac och Windows går att använda för att prova, men
rumskorten kräver en inställning där (se **Steg 6**).

### Steg 3: Starta Homecontrol

Hämta Homecontrol och skapa inställningsfilen:

```sh
git clone https://github.com/YXHFR/Homecontrol.git
cd Homecontrol
git checkout "$(git describe --tags --abbrev=0)"   # den senaste versionen
cp .env.example .env
```

Raden med `git checkout` väljer den senaste versionen i stället för det som
är under arbete. Finns det ingen version än svarar den med två rader som
börjar med `fatal:`; gå då bara vidare.

Öppna filen `.env` och byt ut alla `byt-mig` mot egna lösenord. Filnamnet
börjar med en punkt, så den kan vara dold i filhanteraren. Öppna den till
exempel med `nano .env` (spara med Ctrl+O och avsluta med Ctrl+X).
Hitta inte på lösenorden själv; låt datorn slumpa fram ett starkt lösenord
för vart och ett:

```sh
openssl rand -hex 24   # kör en gång per lösenord och klistra in resultatet
```

Varje lösenord ska vara olika. Lösenorden behöver du inte komma ihåg,
utom `ZIGBEE2MQTT_AUTH_TOKEN` som du loggar in på Zigbee2MQTT med. Filen
stannar på datorn och ska inte delas med någon. Vad varje lösenord används
till står under **Lösenorden och vem som får vad**.

Starta sedan:

```sh
docker compose up -d
```

Datorn hämtar då färdiga avbilder (färdigpackade versioner av programmen)
för sin sorts processor (amd64 eller arm64), med den version som står i
`compose.yaml`. Går kärnans avbild inte
att hämta, byggs den i stället från koden. Det tar längre tid, upp till en
halvtimme på en Raspberry Pi. Därefter finns:

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
öppnas sidan, men livevyn tappar genast anslutningen.

Homecontrol startar av sig själv igen efter ett strömavbrott och behöver
inte internet för att styra lamporna. Läs **Vem som når Homecontrol** längre
ned innan du släpper in fler i nätverket.

### Steg 4: Rita hemmet i kartredigeraren

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
   det sitter spegelvänt. Under **Radar i simuleringen** väljer du om
   kortet ska låtsas vara en LD2450 (3 personer) eller en LD2461 (5 personer)
   i simuleringsläget. Ett riktigt kort säger själv vilken radar det har.
   En lampa som dras in i ett annat rum styrs sedan av
   det rummet; du kan också byta rum i listan.

Bilden och rummen hör till våningen som är vald med knapparna ovanför kartan.
Med tangentbordet avbryter Esc det du ritar, Backspace ångrar senaste hörnet
och Enter stänger rummet eller zonen.

Allt sparas direkt, och livevyn visar ändringen utan omstart. Har du
redigeraren öppen i två fönster och ändrar i båda, visar det ena fönstret
det andras ändring i stället för att skriva över den. Under
**Hemmet** står hur många rum, zoner, rumskort, routrar och lampor hemmet
har.

### Steg 5: Koppla in Zigbee-lamporna

Lamporna placeras i rummen du ritade i steg 4.

**Zigbee-adaptern.** Koppla in adaptern (USB-stickan) och ta reda på dess
sökväg:

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

**Lamporna.** Gör så här för en lampa i taget:

1. Para ihop lampan med Zigbee2MQTT: tryck på **Permit join** i Zigbee2MQTT
   och nollställ lampan (en IKEA TRÅDFRI nollställs genom att slå av och på
   strömmen sex gånger i rad).
2. Öppna kartredigeraren i Homecontrol. Under **Placera** står lampan som en
   ny lampa. Välj den och klicka där den sitter, inne i ett rum.

Homecontrol använder bara lampor som är placerade på kartan; andra
Zigbee-enheter rörs inte. När två eller fler Zigbee-lampor sitter i samma rum
skapar Homecontrol en grupp i Zigbee2MQTT som heter `homecontrol-` följt av
rummets id (till exempel `homecontrol-vardagsrum`), så
att rummets lampor tänds och släcks samtidigt med en mjuk övergång. Ändra
inte i de grupperna själv; Homecontrol håller dem i takt med kartan.

Tänder, släcker eller dimrar du en lampa med en fjärrkontroll eller i en app
räknas det som manuell styrning, precis som ett klick i livevyn. En lampa som
inte svarar (till exempel när strömbrytaren är av) visas med ⚠ på kartan, och
rummet får läget okänt tills lampan svarar igen.

När den första riktiga lampan är placerad stängs simuleringsläget av. Så länge
riktiga lampor är placerade kan tiden inte gå fortare än vanligt, så att de
aldrig släcks för tidigt. De virtuella lamporna i simuleringen styr aldrig
riktiga lampor.

### Steg 6: Installera rumskorten

Ett rumskort (ESP32 med radar, Wi-Fi-mätning och ljussensor) sitter i varje
rum där lamporna ska styras av närvaro. Koppla ihop delarna enligt
[firmware/README.md](firmware/README.md#koppling).

**Installera firmware med webbinstalleraren.** Webbinstalleraren är en
webbsida där du installerar kortets mjukvara med ett klick, utan att bygga
något själv. Den byggs automatiskt på GitHub men är inte publicerad på
webben än; det kommer senare. Tills dess hämtar du den och öppnar den på
din egen dator:

1. Logga in på GitHub och öppna
   [Firmware-körningarna på main](https://github.com/YXHFR/Homecontrol/actions/workflows/firmware.yml?query=branch%3Amain+event%3Apush+is%3Asuccess).
   Välj den översta och ladda ner **webbinstallerare** under
   **Artifacts**. Packa upp zip-filen till en egen mapp.
2. Öppna ett terminalfönster i den mappen och starta en liten webbserver
   (Python finns på Mac och Linux; på Windows installeras det från
   [python.org](https://www.python.org/downloads/)):

   ```sh
   python3 -m http.server 8001 --bind 127.0.0.1
   ```

3. Öppna <http://localhost:8001> i Chrome eller Edge på samma dator.
   Stäng webbservern med Ctrl+C när du är klar.

Artefakterna sparas i 90 dagar. Finns ingen körning med artefakten kvar
kan du bygga firmware själv med ESPHome (se
[firmware/README.md](firmware/README.md#rummet-finns-inte-i-listan)).

Installera sedan kortet:

1. Öppna webbinstalleraren i Chrome eller Edge.
2. Välj rummet där kortet ska sitta. Kortet får namnet `homecontrol-<rum>`.
   Välj också radarn som sitter på kortet: LD2450 eller LD2461.
3. Anslut kortet med USB. På kort med två USB-uttag: använd det som är
   märkt **USB** (inte **UART**).
4. Tryck på **Connect**, välj kortet i listan och sedan **Install**. Första
   gången kryssar du i **Erase device**.
5. När installationen är klar frågar sidan efter Wi-Fi. Nätverk och
   lösenord skickas bara till kortet och sparas där.

Mer om installationen, uppdateringar och licensen för firmware finns i
[firmware/README.md](firmware/README.md).

**Koppla kortet till Homecontrol.** Kärnan behöver en hemlighet som
kortens krypteringsnycklar räknas fram ur. Skapa den en gång, innan du
placerar det första kortet, och skriv in den i `.env`:

```sh
openssl rand -hex 32     # skriv resultatet efter ESPHOME_SECRET= i .env
docker compose up -d     # starta om kärnan med hemligheten
```

Byt aldrig hemligheten; då får varje kort en ny nyckel och måste raderas och
installeras om. När det första kortet är placerat stängs simuleringsläget av.

1. Låt Homecontrol vara igång (från **Steg 3**) när kortet får Wi-Fi.
   Kärnan hittar korten i hemnätet av sig själv (med mDNS). Ett nytt kort
   syns i livevyn under **Rumskort** som ett nytt kort att placera.
2. Öppna kartredigeraren och välj **Placera Homecontrol <rum>** under
   **Placera**. Klicka där kortet sitter och vrid det åt rätt håll. Först nu
   ansluter kärnan till kortet och ger det dess nyckel. Gör det snart: tills
   kortet har fått kärnans nyckel kan vem som helst i hemnätet ge det en
   egen. Kortet måste ha ström; kärnan erbjuder nyckeln bara under tio
   minuter efter placeringen. Ett kort som inte är placerat används aldrig.
3. I livevyn syns det kortets radar ser som prickar, och Wi-Fi-rörelsen och
   ljusnivån påverkar rummet.

Svarar ett kort inte, eller bara en del av det (till exempel radarn), blir
kortet rött med ⚠ på kartan, varningen står under **Rumskort** och rummet
får läget okänt. Lamporna i rummet lämnas då som de är.

Finns en ny version av kortets firmware visas knappen **Godkänn uppdatering**
under **Rumskort**. Kortet uppdateras bara när någon trycker på den; det
hämtar då själv filen från webbinstalleraren och startar om.

Kärnan använder datorns eget nätverk (`network_mode: host` i
`compose.yaml`), eftersom mDNS inte når in i Dockers nät. Det fungerar på
Linux, som Raspberry Pi. I Docker Desktop (Mac och Windows) måste
värdnätverket slås på, se [Dockers beskrivning](https://docs.docker.com/engine/network/drivers/host/#docker-desktop).
Kärnan lyssnar då direkt på datorn, så sätt gärna `BIND_ADDRESS` i `.env`
till datorns adress i hemnätet, så att den bara nås därifrån. Då når du den
inte via Tailscale (se **Steg 8**).

### Steg 7: Kalibrera lamporna

En lampa lyser också på rummets ljussensor. För att en lampa inte ska
släcka sig själv när den gör rummet ljust drar Homecontrol bort lampornas
eget ljus från det som sensorn mäter (se **Ljusreglerna**). Hur mycket varje
lampa ger mäts av lampkalibreringen, ett rum i taget.

1. Öppna livevyn. Under **Just nu** har varje rum med lampor knappen
   **Mät lampornas ljus**. Tryck på den för rummet du vill kalibrera.
2. Läs råden i rutan **Lampkalibrering**: gör det när ljuset är jämnt,
   helst när det är mörkt ute, och stå inte mellan lamporna och rumskortet.
3. Tryck på **Starta kalibreringen**. Alla lampor i rummet släcks, och
   sedan tänds och släcks de en i taget medan ljuset mäts. Varje mätning
   tar ungefär en kvarts minut, så ett rum med en lampa tar ungefär en
   minut. Automatiken i rummet pausar under tiden.
4. När det är klart står varje lampas ljus i lux i rutan. Värdena sparas,
   lamporna blir som de var innan och automatiken tar över igen.

Kalibrera igen (knappen **Kalibrera igen**) när en lampa har bytts eller
flyttats. Kalibreringen avbryts, och lamporna ställs tillbaka, om dagsljuset
ändras mycket under tiden (till exempel av ett moln), om ljussensorn eller en
lampa slutar svara, om rummet ändras i kartredigeraren eller om du trycker
**Avbryt**. Tänder eller släcker du själv en lampa under tiden avbryts den
också, och den lampan får vara som du ville. Ett rum utan fönster behöver
ingen kalibrering, eftersom det alltid räknas som mörkt.

Du kan prova kalibreringen i simuleringsläget: där lyser de virtuella
lamporna upp de simulerade sensorerna, och med tidsfarten 60× går den på
några sekunder.

### Steg 8: Nå livevyn utanför hemmet med Tailscale

Homecontrol ska aldrig öppnas mot internet. Utanför hemmet når du livevyn
med [Tailscale](https://tailscale.com/), som kopplar ihop dina egna enheter
i ett eget krypterat nätverk.

1. Skapa ett gratis konto på [tailscale.com](https://tailscale.com/).
2. Installera Tailscale på datorn som kör Homecontrol enligt
   [Tailscales guide för Linux](https://tailscale.com/download/linux) och
   starta det:

   ```sh
   sudo tailscale up
   ```

   Öppna länken som visas och logga in med ditt konto.
3. Ta reda på datorns Tailscale-adress. Den börjar med 100:

   ```sh
   tailscale ip -4
   ```

4. Installera Tailscale-appen på telefonen och logga in med samma konto.
5. Surfa på telefonen till `http://` följt av adressen och `:8000`, till
   exempel `http://100.x.y.z:8000`. Spara gärna sidan på hemskärmen.

Vill du använda datorns namn i Tailscale (MagicDNS) i stället för adressen,
skriv in namnet som `ALLOWED_HOSTS` i `.env` och starta om med
`docker compose up -d`. Annars öppnas sidan, men livevyn tappar genast
anslutningen.

Tänk på:

- Alla enheter i ditt Tailscale-nätverk når Homecontrol, och med
  standardinställningen även MQTT (port 1883) och Zigbee2MQTT (port 8080),
  som båda kräver lösenord. Dela inte datorn med andra i Tailscale om de
  inte ska kunna se var ni är och styra lamporna.
- Slå inte på **Funnel** i Tailscale för datorn. Funnel öppnar den mot hela
  internet.
- Har du satt `BIND_ADDRESS` till datorns adress i hemnätet når du inte
  Homecontrol via Tailscale. Se **Vem som når Homecontrol**.

## Så fungerar det

### Närvaro

Varje rumskort ser personer med radarn: upp till 3 med LD2450 och upp till
5 med LD2461 (tillverkarens uppgift, inte provad än). Kärnan räknar om det radarn ser
utifrån var kortet sitter, hur högt, åt vilket håll det tittar och om det är
spegelvänt. Det radarn ser utanför kortets eget rum (genom en vägg) och i
ignorera-zoner räknas inte med. Kvar blir anonyma spår, prickar på kartan.

Tappar radarn någon räknas personen som kvar en stund, beroende på var:

- i en entrézon: inte alls, personen har gått ut
- i en stillhetszon: så länge Wi-Fi känner rörelse i rummet, högst 30 minuter
- annars i rummet: cirka 10 sekunder

En person som hålls kvar visas som en streckad ring i livevyn. Under
**Just nu** står om varje rum är upptaget, tomt eller okänt. Okänt betyder att
radarn på ett rumskort i rummet har slutat svara, eller Wi-Fi-mätningen när
rummet använder Wi-Fi (vikten är över 0), eller en av rummets lampor; då
lämnas lamporna som de är.
Wi-Fi-mätningen kalibrerar sig några minuter efter att kortet har startat,
och under tiden räknas den som tyst.

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
- **Okänt läge**: har ett rumskort eller en lampa slutat svara görs
  ingenting med lamporna i rummet; de lämnas som de är. När lampan eller
  kortet svarar igen tar automatiken över.

Ljusnivån som reglerna använder är den uppmätta minus det ljus som rummets
tända lampor själva ger vid ljussensorn (mindre när de är dimrade). Därför
släcker en lampa aldrig sig själv för att den lyser upp rummet. Hur mycket
varje lampa lyser upp sensorn mäts av lampkalibreringen (se **Steg 7**);
innan en lampa är mätt är bidraget 0. Under **Just nu** står båda värdena
när de skiljer sig.

Kärnan antar inte att lamporna är släckta när den startar. Tills en lampa
har berättat hur den är, eller kärnan själv har styrt den, är läget okänt.
En lampa med okänt läge tänds när rummet ska tändas, men släcks eller dimras
aldrig på chans. Zigbee-lamporna frågas om sitt läge när kärnan startar, och
de styrs först när de har svarat. De virtuella lamporna i simuleringen är
släckta från början.

**Manuell styrning**: klicka på en lampa på kartan, eller på lampans knapp
under **Just nu**, så tänds eller släcks den. Automatiken i rummet pausar
då, så att den inte går emot ditt val (till exempel när du släcker för att
sova), tills rummet har varit tomt i hela släckfördröjningen (3 minuter).
Rummet står som **manuellt** på kartan och under **Just nu**, och med
**Automatik igen** tar automatiken över direkt. En lampa som ändras med
fjärrkontroll eller app pausar automatiken på samma sätt.

**Varför hände det?** listar de senaste besluten med klockslag och värden,
till exempel "19:02 Tändes: någon är i Vardagsrum och det var mörkt (9 lux,
under 30)." Listan visar de senaste besluten och töms när kärnan startar
om, men besluten sparas också i historiken (se nedan).

### Historik

Kärnan sparar var personerna på kartan har rört sig och vad som har hänt.
Allt sparas i databasen på datorn där Homecontrol körs. Inget av det skickas
någonstans, och inget exporteras av sig självt.

- **Var personerna har rört sig:** ungefär tio mätningar i sekunden sparas
  i ett dygn. Sedan finns bara en mätning per sekund kvar (medelvärdet), och
  den packas ihop så att den tar mindre plats.
- **Händelseloggen:** när ett rum blir upptaget, tomt eller okänt, och
  besluten från **Varför hände det?**.

Prickarna på kartan är anonyma. Varje prick får ett slumpat nummer, och
historiken säger inget om vem det var. Låtsaspersonerna i simuleringsläget
sparas inte: ingen historik sparas medan simuleringsläget är på. Utan
`ESPHOME_SECRET` (se **Steg 6**) är simuleringsläget påslaget från början; stäng
av det i livevyn för att historiken ska börja sparas.

Som standard sparas historiken utan tidsgräns, så den växer sakta med
tiden. Båda tiderna går att ändra i `.env`. Ta bort `#` framför raden och
starta om kärnan med `docker compose up -d`:

```sh
HISTORY_FULL_HOURS=48   # alla mätningar sparas i 48 timmar (minst 1)
HISTORY_KEEP_DAYS=365   # all historik tas bort efter ungefär ett år
```

`HISTORY_KEEP_DAYS` ska vara längre än `HISTORY_FULL_HOURS`. Det gamla tas
bort i omgångar, så det kan ligga kvar upp till en vecka längre.

Hinner databasen inte med, till exempel på ett långsamt minneskort, tappas
mätningar i historiken i stället för att lamporna får vänta.

Historiken visar när någon har varit hemma och i vilka rum. Tänk på det när
du bestämmer vem som når datorn och databasen, och var säkerhetskopior av
databasen hamnar.

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
  En Zigbee-lampa har id:t `zigbee-` följt av lampans IEEE-adress (lampans
  unika adress i Zigbee-nätet; kartredigeraren fyller i den själv). Andra
  lampor är virtuella.
  `lux_contribution` är hur många lux lampan ger vid rummets ljussensor på
  full styrka (0 om inget anges, se **Ljusreglerna**).

- `wifi_weight` i `settings` är hur mycket Wi-Fi-rörelse väger mot radarn,
  från 0 till 1 (0,5 om inget anges, se **Närvaro**).

Lampornas zon sparas redan men används först i kommande versioner.

### När databasen är nere

Lamporna fungerar även när databasen krånglar. Kärnan har en egen kopia av
hemmet i volymen `core-data`, i samma form som exportfilen ovan. Kopian
skrivs varje gång kärnan har läst hemmet från databasen och varje gång
hemmet sparas.

- **Vid start:** svarar inte databasen inom några sekunder startar kärnan
  med kopian. Lamporna styrs som vanligt, och livevyn visar en varning:
  "Databasen svarar inte". Har kärnan ingen kopia än, till exempel första
  gången, väntar den på databasen som förut. Svarar databasen men något
  annat är fel, till exempel ett fel lösenord i `.env`, startar kärnan inte
  (se **Om något inte fungerar**).
  Har du ändrat hur länge historiken sparas (eller uppdaterat Homecontrol)
  medan databasen var nere, kör `docker compose up -d` igen när den är
  igång, så att ändringen görs i databasen.
- **Medan databasen är nere:** ändringar i kartredigeraren och resultatet
  av en lampkalibrering nekas med ett meddelande om varför. Historiken
  sparas inte; det som inte hinner skrivas tappas (se **Historik**).
  Planritningens bild finns bara i databasen och syns inte heller.
- **När databasen svarar igen:** kärnan försöker ansluta var tionde sekund.
  Lyckas det läses hemmet från databasen, varningen försvinner och
  ändringar tas emot igen. Databasen bestämmer alltid: en kopia som är
  äldre än databasen skriver aldrig över den. Bara om databasen är helt tom
  (till exempel efter att volymen `db-data` har tagits bort) läggs kopian in
  där.
- Misslyckas ett sparande medan kärnan är igång gäller samma sak: varningen
  visas, och hemmet läses från databasen igen när den svarar.

Kopian innehåller inga lösenord eller nycklar, bara det som finns i en
exporterad hemfil, och kan bara läsas av kärnan (filen
`/var/lib/homecontrol/hem.json` i kärnans container).

Vill du börja om från början, eller ta bort dina uppgifter innan datorn
lämnas bort, räcker det inte att ta bort databasen: då läggs hemmet in igen
från kopian. Ta bort båda volymerna. De har mappens namn först, oftast
`homecontrol`:

```sh
docker compose down
docker volume rm homecontrol_db-data homecontrol_core-data
```

(`docker compose down --volumes` tar också bort MQTT:s och Zigbee2MQTT:s
data, och då måste lamporna paras ihop igen.)

### Vem som når Homecontrol

Homecontrol har inga konton och ingen inloggning: alla i hushållet har samma
åtkomst. Den som når livevyn kan alltså se var någon är, tända och släcka
lamporna, ändra planlösningen och bilden av planritningen, placera nya
rumskort och godkänna uppdateringar av dem.

- Som standard (`BIND_ADDRESS=0.0.0.0`) nås Homecontrol från alla nätverk
  som datorn är ansluten till: hemnätverket, och Tailscale om det är
  installerat. Med `BIND_ADDRESS=127.0.0.1` i `.env` nås den bara från den
  här datorn.
- Utanför hemmet når du livevyn med [Tailscale](https://tailscale.com/):
  installera det på datorn som kör Homecontrol och på din telefon, och surfa
  till datorns Tailscale-adress (100.x.y.z) och port 8000. Använder du
  datorns Tailscale-namn (MagicDNS), skriv in namnet som `ALLOWED_HOSTS` i
  `.env`. Se **Steg 8** för hur det går till.
- Öppna aldrig port 8000 (eller någon annan av portarna) mot internet i
  routern.
- Släpper du in gäster på hemnätverket kan de nå Homecontrol. Ett gästnätverk
  i routern, skilt från hemnätverket, hindrar det.
- Kärnan lyssnar direkt på datorn (se **Steg 6**), så datorns egen
  brandvägg (till exempel ufw) gäller för port 8000. Portarna som Docker
  öppnar för MQTT och Zigbee2MQTT går däremot förbi brandväggen. Använd
  `BIND_ADDRESS` eller routern för dem.

### Lösenorden och vem som får vad

Delarna har var sitt lösenord i `.env` och får bara göra det de behöver.
Blir en del angripen eller går fel kommer den inte åt resten.

| Lösenord i `.env` | Vem som använder det | Vad det ger |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | Databasens ägare (`homecontrol`) | Allt i databasen. Används bara av `db-setup`, som körs en kort stund vid varje `docker compose up` och sedan avslutas. |
| `CORE_DATABASE_PASSWORD` | Kärnan (databasrollen `homecontrol_core`) | Läsa och skriva hemmet, historiken och händelseloggen, men inte skapa, ändra eller ta bort tabeller. |
| `MQTT_CORE_PASSWORD` | Kärnan (MQTT-användaren `homecontrol_core`) | Läsa allt från Zigbee2MQTT och tända, släcka och gruppera lamporna. Inte släppa in eller ta bort enheter eller ändra Zigbee2MQTT:s inställningar. |
| `MQTT_ZIGBEE2MQTT_PASSWORD` | Zigbee2MQTT (MQTT-användaren `zigbee2mqtt`) | Läsa och skriva sina egna ämnen (`zigbee2mqtt/...`). |
| `ZIGBEE2MQTT_AUTH_TOKEN` | Du, i webbläsaren | Logga in på Zigbee2MQTT:s webbsida. |

- **Databasen:** `db-setup` skapar och uppdaterar tabellerna, ställer in
  hur länge historiken sparas och skapar kärnans roll om den saknas. Först
  därefter startar kärnan. Kommer databasen inte igång alls startar kärnan
  ändå, med sin kopia av hemmet (se **När databasen är nere**). Alla
  inloggningar i databasen kräver lösenord, även inifrån datorn (se
  `postgres/pg_hba.conf`), och databasen nås inte från nätverket.
- **MQTT:** vilka ämnen varje användare får läsa och skriva står i
  `mosquitto/acl`. Allt annat är förbjudet, och utan lösenord kommer ingen in.
  Mosquittos hälsokontroll har en egen användare med ett lösenord som
  slumpas fram vid varje start.
- Kärnan kan bara styra lampor vars namn i Zigbee2MQTT har högst två
  snedstreck (`/`). Det förvalda namnet (lampans adress) har inga.

Kärnan svarar bara när den nås på de adresser och namn den känner till
(`localhost`, IP-adresser och `ALLOWED_HOSTS`), och inte till andra
webbsidor. Det gäller även lampstyrningen och uppdateringen av korten. Det hindrar att en främmande
webbsida som någon i hemmet besöker styr lamporna eller läser kartan genom
webbläsaren. Program och skript i nätverket släpps däremot in, eftersom det
inte finns några konton.

Varje lampa tar emot högst fem kommandon i rad och sedan ett i sekunden, så
att ett trasigt eller elakt program i nätverket inte kan få lamporna att
blinka. Det märks aldrig när automatiken, lampkalibreringen eller någon som
klickar i livevyn styr lamporna. Kommandon utöver gränsen skjuts upp, och
lampan får till slut det senaste. Kärnans logg säger när kommandon till en
lampa skjuts upp.

Kärnans logg säger inte när lamporna tänds och släcks, eftersom det ungefär
säger när någon kom och gick.

## Om något inte fungerar

```sh
docker compose ps      # alla fyra ska stå som "Up", de flesta även "healthy"
docker compose logs    # visar vad tjänsterna skriver, leta efter fel
```

`docker compose ps -a` visar också `db-setup`, som ska stå som
"Exited (0)": den gör i ordning databasen och avslutas sedan.

Ett vanligt fel är att `.env` saknas eller att ett värde är tomt. Då säger
`docker compose up` vilken inställning som saknas.

Står kärnan inte som "Up", titta i `docker compose logs db-setup`. Står
det `password authentication failed for user "homecontrol"` är
`POSTGRES_PASSWORD` i `.env` inte samma som när databasen skapades första
gången; databasen minns det första lösenordet. Sätt tillbaka det. Ge inte
`POSTGRES_PASSWORD` samma lösenord som någon annan rad i `.env`.

Visar livevyn "Databasen svarar inte"? Lamporna styrs ändå, men ändringar
sparas inte. Titta i `docker compose ps` och `docker compose logs db`. När
databasen är igång igen försvinner varningen av sig själv inom en halv minut.

Syns inte en ny lampa under **Placera** i kartredigeraren? Kontrollera i
Zigbee2MQTT att den är ihopparad och klar (inte "interviewing"). Står en lampa
med ⚠ på kartan svarar den inte: kontrollera strömbrytaren, eller att
Zigbee2MQTT når lampan (den är för långt bort eller saknar ström).

Syns inte ett nytt rumskort under **Rumskort** i livevyn? Kortet och datorn
måste vara i samma nätverk. Ett gästnätverk, eller en router som håller
isär enheterna i nätverket, stoppar mDNS som kärnan hittar korten med.

Kopplas livevyn bort direkt när du surfar till ett namn i stället för en
adress? Skriv in namnet som `ALLOWED_HOSTS` i `.env` och starta om med
`docker compose up -d`.

## Nya versioner

Nya versioner av Homecontrol publiceras under
[Releases](https://github.com/YXHFR/Homecontrol/releases), med en lista över
vad som är nytt och rättat. Med varje version följer färdiga avbilder av
kärnan för amd64 och arm64.

Uppdatera så här (hemmet, inställningarna och historiken finns kvar):

```sh
cd Homecontrol
docker compose exec -T core homecontrol exportera > ~/hem.json   # säkerhetskopia av hemmet
git fetch --tags
git checkout "$(git describe --tags --abbrev=0 origin/main)"     # den senaste versionen
docker compose pull core
docker compose up -d
```

`git checkout` byter till den senaste versionens filer, med en
`compose.yaml` som pekar på samma versions avbild. `docker compose pull core`
hämtar avbilden, och `docker compose up -d` startar om det som har ändrats.
Säger `pull` att avbilden inte finns har versionen nyss släppts och byggs
fortfarande; vänta en halvtimme och försök igen. Använd inte `git pull`:
mappen står på en version och inte på en gren, så git svarar då bara att
du inte är på någon gren. Säkerhetskopian `hem.json`
hamnar i din hemmapp på datorn. Dela den inte; den innehåller din
planlösning.

Säger `docker compose up -d` att en inställning saknas, har den nya
versionen fått en ny inställning. Kopiera raden från `.env.example` till
`.env` och fyll i ett eget lösenord på varje ny rad med
`openssl rand -hex 24`, som i **Steg 3**.

Kommer du från en version där kärnan och Zigbee2MQTT delade på
`MQTT_USER` och `MQTT_PASSWORD`, behöver du lägga till
`CORE_DATABASE_PASSWORD`, `MQTT_CORE_PASSWORD` och
`MQTT_ZIGBEE2MQTT_PASSWORD` (se **Lösenorden och vem som får vad**).
`MQTT_USER` och `MQTT_PASSWORD` används inte längre och kan tas bort.
Databasen behåller allt; kärnans nya roll skapas av `db-setup` vid nästa
`docker compose up -d`.

Stoppa Homecontrol med `docker compose down`. Inget sparat försvinner;
`docker compose up -d` startar allt igen.

## Delar

| Mapp         | Innehåll                                                   |
| ------------ | ---------------------------------------------------------- |
| `core/`      | Kärnan i Python. Svarar på `/health` och serverar webbgränssnittet. |
| `web/`       | Webbgränssnittet i TypeScript.                             |
| `firmware/` | ESPHome-firmware för rumskorten och webbinstalleraren, se [firmware/README.md](firmware/README.md). |
| `mosquitto/` | Inställningar för MQTT-brokern, med användarnas rättigheter (`acl`). |
| `postgres/`  | Vilka som får logga in i databasen och hur (`pg_hba.conf`). |
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
kartredigeraren sparas bara så länge kärnan är igång. Utanför Docker Compose
skapar och uppdaterar kärnan själv tabellerna. I `compose.yaml` gör
`homecontrol förbered-databasen` (tjänsten `db-setup`) det i stället, och
kärnan har `HOMECONTROL_PREPARE_DATABASE=false` och en roll som bara får
läsa och skriva.
Testerna av databasdelen behöver en Postgres med TimescaleDB och hoppas
annars över. En sådan startar du till exempel så här (den nås bara från
den här datorn och försvinner när den stoppas):

```sh
docker run -d --rm -p 127.0.0.1:5432:5432 -e POSTGRES_DB=homecontrol_test \
  -e POSTGRES_USER=homecontrol -e POSTGRES_HOST_AUTH_METHOD=trust \
  timescale/timescaledb:2.30.2-pg17
```

Kör sedan testerna:

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
Lampkalibreringen startas med `POST /api/rooms/{id}/calibration` och
avbryts med `DELETE /api/calibration` (se `core/src/homecontrol/calibration_api.py`).
Själva mätningen ligger i `calibration.py`, och läget strömmas i livevyn.
Bidragen sparas på samma sätt som en ändring i kartredigeraren
(`editor.HomeChanges`).

Kärnans flöde: källkopplingar (`connectors.Source`) lämnar observationer,
sammanvägningen (`fusion.py`) gör om dem till spår och avgör vilka rum som
är upptagna, regelmotorn (`rules.evaluate`, en ren funktion) ger
lampkommandon med förklaringar och lamputgångar (`connectors.LampOutput`)
tar emot dem. Lamputgångarna berättar hur lamporna är med
`LampStateReported` genom samma ingång som källorna. Varje regel i
regelmotorn är en egen klass med metoden `decide`; en ny sorts regel läggs
till i listan `rules.DEFAULT_RULES` utan att de andra ändras (se
`core/src/homecontrol/rules.py`). Ett beslut gäller i dag alltid alla lampor i
ett rum. Manuell styrning från livevyn går till
`Controller.set_lamp`; en ändring med fjärrkontroll eller app kommer som
`LampStateReported` med `external=True` och behandlas likadant. Lamputgången
Zigbee2MQTT (`zigbee.py`) skiljer lampornas svar på kärnans kommandon från
sådana ändringar, läser lampornas läge innan den styr dem och skickar
kommandona genom lampans profil (till exempel IKEA TRÅDFRI:s egenheter).
Kopplingstesterna kör den mot exempelmeddelanden i `core/tests/zigbee_exempel/`.
Simuleringen (`simulation.py`) är både en källa och en utgång. Simuleringsläget
i livevyn styr låtsaskällan (personer och dagsljus) och klockans tidsfart; personerna
når kärnan genom samma ingång som en riktig källa, som radarmål i varje
rumskorts egna koordinater. Tidsfarten gäller hela kärnan och är därför
spärrad till 1× så länge hemmet har riktiga rumskort eller lampor.

Rumskorten ansluts av källkopplingen ESPHome (`esphome.py`). Den hittar nya
kort med mDNS, ansluter krypterat med aioesphomeapi till de kort som är
placerade (en nod vars id är kortets namn, `homecontrol-<rum>`) och tolkar
entiteterna från firmware-paketet till observationer. Radarn,
Wi-Fi-mätningen och ljussensorn på ett kort är var sin källa, så att en del
som tystnar ger `SourceLost` för just den delen. Kopplingen ser på kortets
entiteter vilken radar det har (LD2461 har mål 4 och 5) och skickar det med
radarmålen, så att sammanvägningen vet hur många mål som är rimligt.
Kopplingen testas mot exempelmeddelanden i `core/tests/esphome_exempel/`, i
den form aioesphomeapi lämnar dem. De innehåller inga nycklar, nätverksnamn
eller adresser. Exemplen för LD2461 är påhittade, inte inspelade. Spelas
nya exempel in från ett riktigt kort ska nycklar, nätverksnamn och adresser
tas bort ur dem först.

Hemmodellen finns i `home.py` och kontrolleras där. `homefile.py` gör om den
till och från ett JSON-dokument, och `store.py` sparar samma dokument i
Postgres (tabellerna skapas av `migrations/`). Formen beskrivs alltså bara i
`home.py`, och filen och databasen har samma version. Exempelhemmet är
`example_home.json`. Kärnan läser hemmet när den startar; en import kräver
därför en omstart. Med `HOMECONTROL_HOME_COPY` har kärnan en kopia av hemmet
(`homecopy.py`) och startar med den när databasen inte svarar;
`databaselink.py` håller reda på kontakten och ansluter igen. Utan
inställningen väntar kärnan på databasen vid start.

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
- Versionsnummer ändras aldrig för hand. På main håller release-please
  (`.github/workflows/release.yml`, inställningar i
  `release-please-config.json`) en PR öppen med nästa version och listan
  över ändringar: `feat` ger en ny mindre version (0.1.0 → 0.2.0) och `fix`
  en rättningsversion (0.1.0 → 0.1.1). Före 1.0 ger även en brytande
  ändring (`feat!:`) bara en ny mindre version. PR:en byter versionen i `compose.yaml`,
  `core/pyproject.toml`, `web/package.json` och låsfilerna. När den mergas
  skapas en tagg och en GitHub Release, och kärnans avbild publiceras som
  `ghcr.io/yxhfr/homecontrol` för amd64 och arm64. Ändringslistan finns
  bara i releaserna; det finns ingen CHANGELOG-fil. Dependabot använder
  `fix(deps)` för beroenden som hamnar i avbilden eller i `compose.yaml`, så
  att de ger en ny version. Webbinstalleraren för rumskorten byggs däremot
  från main vid varje ändring i `firmware/`, inte vid en release, och
  sparas som artefakten `webbinstallerare`. Den publiceras inte på webben
  än.
- Hemligheter läggs bara i `.env`, aldrig i repot.
- Inga personliga uppgifter (planritningar, nätverksadresser, nycklar)
  får checkas in.

## Licens

[Apache-2.0](LICENSE)

Undantag: firmware-binären för rumskorten innehåller ESPectre och ESPHome
och blir därför GPL-3.0. Konfigurationen i `firmware/` är Apache-2.0. Se
[firmware/README.md](firmware/README.md#licens).
