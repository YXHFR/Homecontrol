#!/bin/sh
# Skapar Mosquittos lösenordsfil från MQTT_USER och MQTT_PASSWORD och
# startar sedan brokern. Lösenordet hamnar bara hashat på disk.
set -eu

: "${MQTT_USER:?MQTT_USER saknas, se .env.example}"
: "${MQTT_PASSWORD:?MQTT_PASSWORD saknas, se .env.example}"

passwd_file=/mosquitto/data/passwd
umask 077
printf '%s:%s\n' "$MQTT_USER" "$MQTT_PASSWORD" > "$passwd_file"
mosquitto_passwd -U "$passwd_file"
chown mosquitto:mosquitto "$passwd_file"

exec /docker-entrypoint.sh "$@"
