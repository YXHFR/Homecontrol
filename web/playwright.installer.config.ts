import { defineConfig, devices } from "@playwright/test";

/**
 * Webbläsartestet av webbinstalleraren, som den startas på ägarens dator:
 * mappen serveras av sin egen starta.py på http://localhost.
 *
 * INSTALLER_SITE är en byggd webbinstallerare (artefakten "webbinstallerare",
 * eller en med påhittad firmware, se .github/workflows/firmware.yml).
 */
const site = process.env.INSTALLER_SITE;
if (!site) throw new Error("Sätt INSTALLER_SITE till mappen med webbinstalleraren");
const PORT = 8765;

export default defineConfig({
  testDir: "e2e-installer",
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    launchOptions: {
      // En förinstallerad Chromium kan användas i stället för Playwrights egen.
      executablePath: process.env.CHROMIUM_PATH || undefined,
    },
  },
  projects: [{ name: "dator", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `python3 -I "${site}/starta.py" --no-browser --port ${PORT}`,
    url: `http://localhost:${PORT}/`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
