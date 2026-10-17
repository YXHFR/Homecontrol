import "./styles.css";
import { mountDoor } from "./door";
import { element } from "./dom";
import { mountPage } from "./page";

const root = document.getElementById("app");
if (root === null) {
  throw new Error("Hittade inget #app-element att rita i");
}

// Homecontrol är en sida. Dörrklockans kamera har en egen sida (#dorren),
// som nås från toppraden när en dörrklocka är inställd.
const page = window.location.hash === "#dorren" ? "door" : "home";
document.body.dataset.page = page;
// Varje sida har sin egen anslutning till kärnan; byt sida genom att ladda om.
window.addEventListener("hashchange", () => window.location.reload());

if (page === "door") {
  const content = element("div", {});
  root.classList.add("simple-page");
  root.replaceChildren(element("a", { className: "back-link", href: "#" }, "‹ Homecontrol"), content);
  mountDoor(content);
} else {
  mountPage(root);
}
