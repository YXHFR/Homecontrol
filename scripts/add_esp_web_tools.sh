#!/bin/sh
# Lägger ESP Web Tools (Apache-2.0, med licensfilen) i webbinstalleraren, så
# att sidan inte laddar något från ett CDN och fungerar utan internet.
#
#   scripts/add_esp_web_tools.sh SITE
#
# Versionen är låst här, med paketets sha256 från registry.npmjs.org.
set -eu
site=$1
ESP_WEB_TOOLS_VERSION=10.4.0
ESP_WEB_TOOLS_SHA256=f18da75335d2f0dca044c4bb052848c0696e7b03cc23d61d8530dd6eba0a9008
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
archive="$work/esp-web-tools-${ESP_WEB_TOOLS_VERSION}.tgz"
curl -sSfL -o "$archive" "https://registry.npmjs.org/esp-web-tools/-/esp-web-tools-${ESP_WEB_TOOLS_VERSION}.tgz"
echo "${ESP_WEB_TOOLS_SHA256}  ${archive}" | sha256sum --check --quiet
tar -xzf "$archive" -C "$work"
rm -rf "$site/esp-web-tools"
cp -r "$work/package/dist/web" "$site/esp-web-tools"
cp "$work/package/LICENSE" "$site/esp-web-tools/LICENSE"
