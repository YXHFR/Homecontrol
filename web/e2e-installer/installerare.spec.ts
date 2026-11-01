import { expect, test } from "@playwright/test";

/**
 * Webbinstalleraren ska gå att köra på den egna datorn utan internet. Själva
 * installationen kräver ett kort och kan inte provas här.
 */
test("webbinstalleraren laddar ESP Web Tools och hittar firmware utan internet", async ({
  page,
}) => {
  const outside: string[] = [];
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "localhost") return route.continue();
    outside.push(url.href);
    return route.abort();
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/");

  // Rummen och versionen kommer från info.json.
  const room = page.locator("#room");
  await expect(room.locator("option")).not.toHaveCount(0);
  await expect(page.locator("#version")).not.toHaveText("–");
  await expect(page.locator("#error")).toBeHidden();

  // ESP Web Tools är laddat och knappen är redo (localhost räknas som säker).
  await page.waitForFunction(() => customElements.get("esp-web-install-button") !== undefined);
  const button = page.locator("esp-web-install-button");
  await expect(button.locator('[slot="unsupported"]')).toBeHidden();
  await expect(button.locator('[slot="not-allowed"]')).toBeHidden();
  await expect(button.getByRole("button")).toBeVisible();

  // Knappens manifest och firmware-filerna det pekar på finns.
  const radar = page.locator("#radar");
  for (const prefix of await radar.locator("option").evaluateAll((o) =>
    o.map((option) => (option as HTMLOptionElement).value),
  )) {
    await radar.selectOption(prefix);
    const name = `manifest-${prefix}${await room.inputValue()}.json`;
    await expect(button).toHaveAttribute("manifest", name);
    const manifest = await page.request.get(name);
    expect(manifest.ok()).toBe(true);
    for (const build of (await manifest.json()).builds) {
      for (const part of build.parts) {
        expect(part.path).not.toMatch(/^\/|:\/\//);
        expect((await page.request.head(new URL(part.path, page.url()).href)).ok()).toBe(true);
      }
    }
  }

  expect(errors).toEqual([]);
  expect(outside).toEqual([]);
});
