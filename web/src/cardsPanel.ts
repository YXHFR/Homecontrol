import { element } from "./dom";
import type { Card, ClientMessage } from "./protocol";

export interface CardsPanel {
  /** Panelen som läggs in på sidan. Den är dold när kärnan inte ansluter några kort. */
  element: HTMLElement;
  /** Visar korten från kärnan. Utan kort döljs panelen. */
  show(cards: Card[] | null): void;
}

/** Signalstyrka i klartext, som i en mobiltelefon. */
export function signalText(dbm: number): string {
  const quality = dbm >= -60 ? "bra" : dbm >= -70 ? "godkänd" : "svag";
  return `Wi-Fi ${Math.round(dbm)} dBm (${quality})`;
}

/** Läget för ett placerat kort i en rad: varningarna, eller att allt fungerar. */
export function cardStatus(card: Card): string {
  if (card.problems.length > 0) {
    return card.problems.join(" ");
  }
  const parts = ["Fungerar."];
  if (card.calibrating) {
    parts.push("Wi-Fi-mätningen kalibrerar; håll rummet stilla en stund.");
  }
  if (card.wifi_signal !== null) {
    parts.push(`${signalText(card.wifi_signal)}.`);
  }
  if (card.firmware_version !== null) {
    parts.push(`Version ${card.firmware_version}.`);
  }
  return parts.join(" ");
}

/**
 * Panelen "Rumskort": varningar för kort som inte fungerar, nya kort som
 * väntar på att placeras, och knappen som godkänner en uppdatering. Kärnan
 * uppdaterar aldrig ett kort utan att någon har tryckt på knappen.
 */
export function createCardsPanel(send: (message: ClientMessage) => void): CardsPanel {
  const placed = element("ul", { className: "cards" });
  const newCards = element("ul", { className: "new-cards" });
  const newSection = element(
    "div",
    { hidden: true },
    element("h3", {}, "Nya rumskort att placera"),
    element(
      "p",
      { className: "help" },
      "Kärnan har hittat de här korten men använder dem inte förrän de är placerade. " +
        "Öppna kartredigeraren och välj kortet under Placera.",
    ),
    newCards,
  );
  const panel = element(
    "section",
    { className: "cards-panel", hidden: true },
    element("h2", {}, "Rumskort"),
    placed,
    newSection,
  );
  panel.setAttribute("aria-label", "Rumskort");

  const updateButton = (card: Card) => {
    const update = card.update;
    if (update === null || !card.online) {
      return [];
    }
    if (update.in_progress) {
      const progress = update.progress === null ? "" : ` ${Math.round(update.progress)} %`;
      return [element("span", { className: "status" }, ` Uppdaterar till ${update.version} …${progress}`)];
    }
    const button = element("button", { type: "button" }, `Godkänn uppdatering till ${update.version}`);
    button.dataset.card = card.id;
    button.addEventListener("click", () => {
      button.disabled = true;
      // Ritas om med nästa läge, så att knappen går att trycka på igen om
      // kärnan inte kunde starta uppdateringen.
      shown = "";
      send({ type: "install_update", card: card.id });
    });
    return [
      " ",
      element("span", { className: "help" }, "Kortet startar om och är borta en stund. "),
      button,
    ];
  };

  // Läget kommer tio gånger i sekunden. Listan ritas bara om när korten har
  // ändrats, så att knappen inte byts ut under fingret.
  let shown = "";

  return {
    element: panel,
    show(cards) {
      const next = JSON.stringify(cards);
      if (next === shown) {
        return;
      }
      shown = next;
      panel.hidden = cards === null || cards.length === 0;
      const all = cards ?? [];
      placed.replaceChildren(
        ...all
          .filter((card) => card.placed)
          .map((card) => {
            const item = element(
              "li",
              {},
              element("strong", {}, card.problems.length > 0 ? `⚠ ${card.name}` : card.name),
              `: ${cardStatus(card)}`,
              ...updateButton(card),
            );
            item.dataset.card = card.id;
            item.dataset.warning = String(card.problems.length > 0);
            return item;
          }),
      );
      const waiting = all.filter((card) => !card.placed);
      newSection.hidden = waiting.length === 0;
      newCards.replaceChildren(...waiting.map((card) => element("li", {}, `${card.name} (${card.id})`)));
    },
  };
}
