import { defineConfig, devices } from "@playwright/test";

/**
 * Webbläsartesterna kör den byggda webben mot en riktig kärna.
 *
 * Kärnan använder databasen i HOMECONTROL_DATABASE om den är satt (som i CI),
 * annars sparar den bara i minnet medan testerna körs.
 */
const PORT = 8123;

export default defineConfig({
  testDir: "e2e",
  // Testerna delar en kärna och ändrar samma hem, så de körs ett i taget.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    launchOptions: {
      // En förinstallerad Chromium kan användas i stället för Playwrights egen.
      executablePath: process.env.CHROMIUM_PATH || undefined,
    },
  },
  projects: [
    {
      name: "dator",
      // Verktygslådan i kartredigeraren är högre än standardfönstret (720
      // punkter); i ett högre fönster behöver testerna inte rulla sidan.
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 1000 } },
      testIgnore: /mobil/,
    },
    { name: "mobil", use: { ...devices["Pixel 7"] }, testMatch: /mobil/ },
  ],
  webServer: {
    command: "npm run build && uv run --project ../core homecontrol",
    url: `http://127.0.0.1:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      HOMECONTROL_PORT: String(PORT),
      HOMECONTROL_WEB_DIR: "dist",
    },
  },
});
