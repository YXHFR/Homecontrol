import { mountApp } from "./app";
import { mountEditor } from "./editor";

const root = document.getElementById("app");
if (root === null) {
  throw new Error("Hittade inget #app-element att rita i");
}

// Sidorna väljs med adressens #, så att kärnan bara behöver servera en sida.
const page = window.location.hash === "#redigera" ? "editor" : "live";
document.body.dataset.page = page;
for (const link of document.querySelectorAll<HTMLAnchorElement>("nav.pages a")) {
  if (link.dataset.page === page) {
    link.setAttribute("aria-current", "page");
  }
}
// Varje sida har sin egen anslutning till kärnan; byt sida genom att ladda om.
window.addEventListener("hashchange", () => window.location.reload());

if (page === "editor") {
  mountEditor(root);
} else {
  mountApp(root);
}
