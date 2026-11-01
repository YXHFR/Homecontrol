#!/bin/sh
# Lägger ESP Web Tools i webbinstalleraren, så att sidan inte laddar något
# från ett CDN och fungerar utan internet. Versionen och dess sha256 står i
# .github/workflows/firmware.yml (ESP_WEB_TOOLS_VERSION, ESP_WEB_TOOLS_SHA256).
#
#   scripts/add_esp_web_tools.sh SITE
set -eu
site=$1
: "${ESP_WEB_TOOLS_VERSION:?}" "${ESP_WEB_TOOLS_SHA256:?}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
archive="$work/esp-web-tools-${ESP_WEB_TOOLS_VERSION}.tgz"
curl -sSfL -o "$archive" "https://registry.npmjs.org/esp-web-tools/-/esp-web-tools-${ESP_WEB_TOOLS_VERSION}.tgz"
echo "${ESP_WEB_TOOLS_SHA256}  ${archive}" | sha256sum --check --quiet
tar -xzf "$archive" -C "$work"
rm -rf "$site/esp-web-tools"
cp -r "$work/package/dist/web" "$site/esp-web-tools"
cp "$work/package/LICENSE" "$site/esp-web-tools/LICENSE"
