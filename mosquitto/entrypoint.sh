#!/bin/sh
# Skapar Mosquittos lösenordsfil och behörighetsfil och startar sedan
# brokern. Kärnan och Zigbee2MQTT har var sin användare (se acl), och
# dörrklockan får en egen när MQTT_DOORBELL_PASSWORD är satt.
# Lösenorden hamnar bara hashade på disk.
set -eu

: "${MQTT_CORE_PASSWORD:?MQTT_CORE_PASSWORD saknas, se .env.example}"
: "${MQTT_ZIGBEE2MQTT_PASSWORD:?MQTT_ZIGBEE2MQTT_PASSWORD saknas, se .env.example}"

passwd_file=/mosquitto/data/passwd
acl_file=/mosquitto/data/acl
umask 077

# Hälsokontrollen får ett nytt slumpat lösenord vid varje start. Det läses
# av mosquitto_sub från dess inställningsfil och syns därför inte i
# processlistan.
healthcheck_password=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
mkdir -p /root/.config
printf -- '-u healthcheck\n-P %s\n' "$healthcheck_password" > /root/.config/mosquitto_sub

printf 'homecontrol_core:%s\nzigbee2mqtt:%s\nhealthcheck:%s\n' \
    "$MQTT_CORE_PASSWORD" "$MQTT_ZIGBEE2MQTT_PASSWORD" "$healthcheck_password" > "$passwd_file"
# Utan lösenord finns ingen dörrklocka, och ingen kan logga in som den.
if [ -n "${MQTT_DOORBELL_PASSWORD:-}" ]; then
    printf 'dorrklocka:%s\n' "$MQTT_DOORBELL_PASSWORD" >> "$passwd_file"
fi
mosquitto_passwd -U "$passwd_file"
cp /homecontrol/acl "$acl_file"
chown mosquitto:mosquitto "$passwd_file" "$acl_file"

exec /docker-entrypoint.sh "$@"
