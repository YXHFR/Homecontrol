# Köra Homecontrol på Windows 11

Homecontrol körs i Linux. På en Windows 11-dator gör du det med WSL
(Windows Subsystem for Linux): Ubuntu körs i en liten virtuell dator inuti
Windows, och Docker körs i Ubuntu. Den här guiden tar dig från en vanlig
Windows 11-dator till en Homecontrol som syns i hela hemnätet.

Docker Desktop används **inte**. Docker installeras direkt i Ubuntu, så att
Homecontrol fungerar som på en vanlig Linuxdator (kärnan behöver dela
datorns nätverk för att hitta rumskorten).

Det här behövs:

- Windows 11, version 22H2 eller senare (Inställningar → System → Om).
- Ett administratörskonto i Windows.
- Zigbee-stickan (till exempel Sonoff Zigbee 3.0 USB Dongle) om lamporna
  ska styras.

Kommandon som börjar med `PS>` skrivs i **PowerShell** i Windows. Kommandon
som börjar med `$` skrivs i **Ubuntu**. Skriv inte med `PS>` eller `$`.

## 1. Installera Ubuntu 24.04 i WSL

Öppna PowerShell **som administratör** (högerklicka på Start →
Terminal (administratör)) och kör:

```powershell
PS> wsl --install -d Ubuntu-24.04
```

Starta om datorn om du blir ombedd. Ubuntu startar sedan av sig själv och
frågar efter ett användarnamn och ett lösenord. De är bara för Ubuntu och
behöver inte vara samma som i Windows.

Kontrollera att Ubuntu kör WSL version 2:

```powershell
PS> wsl --list --verbose
```

Kolumnen `VERSION` ska visa `2` för `Ubuntu-24.04`.

## 2. Låt hemnätet nå Ubuntu (mirrored-läget)

Som standard har WSL ett eget, dolt nätverk. Rumskorten och mobilen i
hemnätet kan då inte nå Homecontrol. I **mirrored**-läget delar Ubuntu i
stället Windows nätverkskort, och hemnätet når Ubuntu på Windows egen
adress.

Skapa (eller ändra) filen `.wslconfig` i din användarmapp i Windows. Det
enklaste är att öppna den i Anteckningar:

```powershell
PS> notepad "$env:USERPROFILE\.wslconfig"
```

Svara **Ja** om Anteckningar frågar om filen ska skapas. Hela filen ska
se ut så här:

```ini
[wsl2]
# Ubuntu delar Windows nätverkskort, så att hemnätet når Homecontrol.
networkingMode=mirrored
# Stäng inte av WSL när den är ledig.
vmIdleTimeout=-1

[general]
# Stäng inte av Ubuntu när inget fönster är öppet. Annars stannar
# Homecontrol en stund efter att du har stängt terminalen.
instanceIdleTimeout=-1
```

Spara. Stäng sedan av WSL helt, så att inställningarna läses in nästa gång
Ubuntu startar:

```powershell
PS> wsl --shutdown
```

## 3. Slå på systemd i Ubuntu

Docker startas av systemd. I Ubuntu 24.04 som har installerats med
`wsl --install` är systemd oftast redan påslaget. Öppna Ubuntu (Start →
Ubuntu 24.04) och kontrollera:

```sh
$ cat /etc/wsl.conf
```

Står det

```ini
[boot]
systemd=true
```

är du klar med det här steget. Annars lägger du till raderna:

```sh
$ printf '[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf
```

och startar om WSL från PowerShell:

```powershell
PS> wsl --shutdown
```

## 4. Installera Docker Engine i Ubuntu

Stegen följer [Dockers egen guide för Ubuntu](https://docs.docker.com/engine/install/ubuntu/).
Kör i Ubuntu, ett block i taget:

```sh
# Dockers nyckel, så att paketen kan kontrolleras.
$ sudo apt update
$ sudo apt install ca-certificates curl
$ sudo install -m 0755 -d /etc/apt/keyrings
$ sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
$ sudo chmod a+r /etc/apt/keyrings/docker.asc
```

```sh
# Dockers paketkälla.
$ sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
$ sudo apt update
```

```sh
# Docker och Docker Compose.
$ sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Ge din användare rätt att köra Docker utan `sudo`
([Dockers steg efter installationen](https://docs.docker.com/engine/install/linux-postinstall/)):

```sh
$ sudo usermod -aG docker $USER
```

Stäng Ubuntu-fönstret och öppna det igen, så att ändringen gäller. Prova
sedan:

```sh
$ docker run hello-world
```

Det ska skriva `Hello from Docker!`. Docker startar sedan av sig själv
varje gång Ubuntu startar.

## 5. Öppna Windows brandvägg för Homecontrol

Mirrored-läget gör att hemnätet når Ubuntu, men WSL har en egen brandvägg i
Windows (Hyper-V-brandväggen) som stoppar allt som kommer in. Du öppnar
bara de portar som Homecontrol behöver.

| Port | Vad | Behövs |
| --- | --- | --- |
| TCP 8000 | Homecontrols webbsida. Google Home-högtalaren hämtar också dörrklockans ljud här. | Alltid |
| UDP 5353 | mDNS: kärnan hittar rumskorten med det. | Med rumskort |
| TCP 1883 | MQTT (Mosquitto). Dörrklockan skickar sina tryck hit. | Med dörrklocka |
| TCP 8080 | Zigbee2MQTT:s egen webbsida (lösenord: `ZIGBEE2MQTT_AUTH_TOKEN`). | Om du vill nå den från en annan dator |

Har du ändrat `WEB_PORT`, `MQTT_PORT` eller `ZIGBEE2MQTT_PORT` i `.env`
använder du dina portar i stället. Rumskorten behöver ingen öppning: det är
kärnan som ansluter till dem.

Öppna PowerShell **som administratör** och kör de rader du behöver.
`{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}` är WSL:s id i brandväggen och är
samma på alla datorer.

```powershell
PS> $wsl = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
PS> New-NetFirewallHyperVRule -Name Homecontrol-Webb -DisplayName "Homecontrol webbsida" -Direction Inbound -VMCreatorId $wsl -Protocol TCP -LocalPorts 8000
PS> New-NetFirewallHyperVRule -Name Homecontrol-mDNS -DisplayName "Homecontrol mDNS" -Direction Inbound -VMCreatorId $wsl -Protocol UDP -LocalPorts 5353
PS> New-NetFirewallHyperVRule -Name Homecontrol-MQTT -DisplayName "Homecontrol MQTT" -Direction Inbound -VMCreatorId $wsl -Protocol TCP -LocalPorts 1883
PS> New-NetFirewallHyperVRule -Name Homecontrol-Zigbee2MQTT -DisplayName "Homecontrol Zigbee2MQTT" -Direction Inbound -VMCreatorId $wsl -Protocol TCP -LocalPorts 8080
```

Se vilka regler som finns för WSL:

```powershell
PS> Get-NetFirewallHyperVRule -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
```

Ta bort en regel (till exempel om du inte längre har dörrklockan):

```powershell
PS> Remove-NetFirewallHyperVRule -Name Homecontrol-MQTT
```

Öppna inte allt med `Set-NetFirewallHyperVVMSetting -DefaultInboundAction
Allow`. Då når hela nätverket alla portar i Ubuntu, även sådant som inte
hör till Homecontrol.

Kommandona kommer från Microsofts sidor om
[nätverk i WSL](https://learn.microsoft.com/windows/wsl/networking) och
[Hyper-V-brandväggen](https://learn.microsoft.com/windows/security/operating-system-security/network-security/windows-firewall/hyper-v-firewall).

## 6. Koppla Zigbee-stickan till Ubuntu

Ubuntu ser inte USB-enheter av sig själv. Programmet
[usbipd-win](https://github.com/dorssel/usbipd-win) lånar ut en USB-enhet
från Windows till WSL. Hoppa över det här steget om du inte har någon
Zigbee-sticka än.

1. Installera usbipd-win (i PowerShell):

   ```powershell
   PS> winget install usbipd
   ```

   Stäng PowerShell och öppna det igen efteråt.

2. Sätt i stickan och leta upp den:

   ```powershell
   PS> usbipd list
   ```

   Leta efter stickan i listan (till exempel `Silicon Labs CP210x` eller
   `USB-Enhanced-SERIAL CH9102`, beroende på modell). Skriv upp dess
   `BUSID`, till exempel `2-3`.

3. Dela stickan med WSL. Det görs en gång, **som administratör**, och
   gäller även efter omstart:

   ```powershell
   PS> usbipd bind --busid 2-3
   ```

4. Koppla stickan till Ubuntu (Ubuntu måste vara igång; administratör
   behövs inte):

   ```powershell
   PS> usbipd attach --wsl --busid 2-3
   ```

5. Kontrollera i Ubuntu att stickan syns:

   ```sh
   $ ls -l /dev/serial/by-id/
   ```

   Du ser ett långt namn, till exempel
   `usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_..._if00-port0`. Syns
   mappen inte: prova `ls /dev/ttyUSB* /dev/ttyACM*`, och uppdatera WSL med
   `wsl --update` i PowerShell.

6. Skriv in hela sökvägen i `.env` (se steg 7):

   ```ini
   ZIGBEE_ADAPTER=/dev/serial/by-id/usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_..._if00-port0
   ```

   `compose.yaml` ger Zigbee2MQTT stickan som `/dev/zigbee`; mer behöver
   inte ändras. Vid första starten visar Zigbee2MQTT en guide på port 8080
   där du väljer stickans typ.

**Efter en omstart** (av Windows, av WSL eller om stickan dras ur) är
stickan inte längre kopplad till Ubuntu, och steg 4 måste göras igen.
Låt usbipd göra det själv i stället:

```powershell
PS> usbipd attach --wsl --busid 2-3 --auto-attach
```

Kommandot ligger kvar och kopplar in stickan igen så fort den försvinner,
så länge PowerShell-fönstret är öppet.

## 7. Starta Homecontrol

I Ubuntu:

```sh
$ sudo apt install git
$ git clone https://github.com/YXHFR/Homecontrol.git
$ cd Homecontrol
$ cp .env.example .env
$ nano .env
```

Byt ut varje `byt-mig` mot ett eget lösenord (ett nytt för varje rad).
Skapa lösenord med `openssl rand -hex 24`. Fyll i `ZIGBEE_ADAPTER` från
steg 6 och `ESPHOME_SECRET` om du har rumskort (`openssl rand -hex 32`).
Spara med Ctrl+O och Enter, och stäng med Ctrl+X.

Starta:

```sh
$ docker compose up -d
```

Första gången hämtas allt från nätet, och det tar några minuter. Se att
allt är igång:

```sh
$ docker compose ps
```

Öppna sedan webbsidan:

- på samma dator: <http://localhost:8000>
- från mobilen eller en annan dator i hemnätet: `http://<datorns adress>:8000`.
  Datorns adress står under **IPv4-adress** när du kör `ipconfig` i
  PowerShell (till exempel `192.168.1.20`). Ge datorn en fast adress i
  routern, så att den inte byter adress.

## Starta allt när Windows startar

WSL startar inte av sig själv när Windows startar, först när någon öppnar
Ubuntu. Vill du att Homecontrol alltid är igång kan Windows starta Ubuntu
när du loggar in. Kör i PowerShell:

```powershell
PS> schtasks /create /tn "Homecontrol WSL" /sc onlogon /tr "wsl.exe -d Ubuntu-24.04 --exec /bin/true"
```

Ubuntu startar då vid inloggning, Docker startar Homecontrol, och med
`instanceIdleTimeout=-1` (steg 2) fortsätter allt att köra. Zigbee-stickan
behöver fortfarande kopplas in (steg 6).

## Om något inte fungerar

| Problem | Prova |
| --- | --- |
| Sidan fungerar på datorn men inte från mobilen | Kontrollera brandväggsreglerna (steg 5) och att `.wslconfig` har `networkingMode=mirrored`. Kör `wsl --shutdown` och öppna Ubuntu igen. |
| `permission denied` från `docker` | Du är inte med i gruppen `docker` än. Stäng Ubuntu-fönstret och öppna det igen (steg 4). |
| Zigbee2MQTT startar men hittar ingen sticka | Kör `usbipd attach` igen (steg 6) och sedan `docker compose restart zigbee2mqtt`. |
| Rumskorten syns inte som ny enhet | Kontrollera regeln för UDP 5353 (steg 5). |
