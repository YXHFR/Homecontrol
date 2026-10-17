import { mountApp } from "./app";
import { hasDoorbell, mountDoor } from "./door";
import { mountEditor } from "./editor";

const root = document.getElementById("app");
if (root === null) {
  throw new Error("Hittade inget #app-element att rita i");
}

// Sidorna väljs med adressens #, så att kärnan bara behöver servera en sida.
const pages: Record<string, string> = { "#redigera": "editor", "#dorren": "door" };
const page = pages[window.location.hash] ?? "live";
document.body.dataset.page = page;
for (const link of document.querySelectorAll<HTMLAnchorElement>("nav.pages a")) {
  if (link.dataset.page === page) {
    link.setAttribute("aria-current", "page");
  }
}
// Varje sida har sin egen anslutning till kärnan; byt sida genom att ladda om.
window.addEventListener("hashchange", () => window.location.reload());

// Länken till Dörren visas bara när dörrklockan är inställd i kärnan.
const doorLink = document.querySelector<HTMLAnchorElement>('nav.pages a[data-page="door"]');
if (doorLink !== null) {
  void hasDoorbell().then((found) => {
    doorLink.hidden = !found && page !== "door";
  });
}

if (page === "editor") {
  mountEditor(root);
} else if (page === "door") {
  mountDoor(root);
} else {
  mountApp(root);
}
