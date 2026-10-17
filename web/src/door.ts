/**
 * Sidan "Dörren": dörrklockans kamera, genom kärnan (se core/src/homecontrol/doorbell_api.py).
 *
 * Kameran visas bara i 2 minuter efter att någon har ringt, eller efter att
 * någon har tryckt på Visa dörren. Kärnan bestämmer fönstret; sidan frågar
 * bara hur länge det är öppet och visar strömmen så länge. När fönstret
 * stängs tas bilden bort, så att webbläsaren slutar hämta strömmen.
 */
import { button, element } from "./dom";
import { ensureOk } from "./homeApi";

/** Dörrklockans läge från kärnan. */
export interface DoorState {
  /** Om dörrklockan har en kamera inställd. */
  camera: boolean;
  /** Hur många sekunder till kameran får visas. 0 när fönstret är stängt. */
  open_seconds: number;
}

/** Det sidan behöver av kärnan. Testerna byter ut det. */
export interface DoorApi {
  /** Dörrklockans läge, eller null om ingen dörrklocka är inställd. */
  state(): Promise<DoorState | null>;
  /** Visa dörren: öppnar fönstret i 2 minuter. */
  show(): Promise<DoorState>;
  /** Adressen till kamerans ström. */
  streamUrl(): string;
}

const NOT_FOUND = 404;

export const coreDoorApi: DoorApi = {
  async state() {
    let response: Response;
    try {
      response = await fetch("/api/dorr", { cache: "no-store" });
    } catch {
      throw new Error("Får ingen kontakt med Homecontrol.");
    }
    if (response.status === NOT_FOUND) {
      return null;
    }
    return (await (await ensureOk(Promise.resolve(response))).json()) as DoorState;
  },
  async show() {
    const response = await ensureOk(fetch("/api/dorr/visa", { method: "POST" }));
    return (await response.json()) as DoorState;
  },
  streamUrl: () => "/api/dorr/strom",
};

/** Hur ofta sidan frågar kärnan om någon har ringt. */
export const POLL_MS = 3000;
/** Så länge sidan väntar innan den försöker visa en bruten ström igen. */
export const RETRY_MS = 3000;

/** Tiden kvar som minuter och sekunder, till exempel 1:05. */
export function timeLeft(seconds: number): string {
  const whole = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** Om det finns en dörrklocka att visa länken till. */
export async function hasDoorbell(api: DoorApi = coreDoorApi): Promise<boolean> {
  try {
    return (await api.state()) !== null;
  } catch {
    return false;
  }
}

/**
 * Ritar sidan Dörren. Returnerar en funktion som stänger sidan (slutar fråga
 * kärnan och tar bort strömmen).
 */
export function mountDoor(root: HTMLElement, api: DoorApi = coreDoorApi): () => void {
  const status = element("p", { className: "status" }, "Frågar Homecontrol …");
  status.setAttribute("aria-live", "polite");
  const image = element("img", { className: "door-stream", alt: "Kameran vid dörren", hidden: true });
  const showButton = button("Visa dörren", () => void show());
  root.replaceChildren(
    element("h1", {}, "Dörren"),
    element(
      "p",
      { className: "help" },
      "Kameran visas bara en kort stund efter att någon har ringt på, eller efter att du har " +
        "tryckt på Visa dörren. Annars filmas och sparas ingenting.",
    ),
    showButton,
    status,
    image,
  );

  let closesAt = 0;
  let streaming = false;
  let stopped = false;
  let retryAt = 0;

  // Bryts strömmen (kameran hickar, eller kärnan svarar att den inte går att
  // visa just nu) försöker sidan igen om en stund, så länge fönstret är öppet.
  image.addEventListener("error", () => {
    if (streaming) {
      streaming = false;
      image.removeAttribute("src");
      retryAt = Date.now() + RETRY_MS;
    }
  });

  const startStream = () => {
    if (!streaming && Date.now() >= retryAt) {
      streaming = true;
      // Ny adress varje gång, så att webbläsaren inte visar en gammal bild.
      image.src = `${api.streamUrl()}?t=${Date.now()}`;
      image.hidden = false;
    }
  };
  const stopStream = () => {
    if (streaming) {
      streaming = false;
      image.removeAttribute("src");
      image.hidden = true;
    }
  };
  const render = () => {
    const left = (closesAt - Date.now()) / 1000;
    if (left > 0) {
      status.textContent = `Kameran visas i ${timeLeft(left)} till.`;
      startStream();
    } else {
      status.textContent = "Kameran är avstängd.";
      stopStream();
    }
  };
  const applyState = (state: DoorState | null) => {
    if (state === null) {
      status.textContent = "Ingen dörrklocka är inställd. Hur den ställs in står i README.";
      showButton.hidden = true;
      closesAt = 0;
      stopStream();
      return;
    }
    showButton.hidden = !state.camera;
    if (!state.camera) {
      status.textContent = "Dörrklockan har ingen kamera inställd.";
      return;
    }
    closesAt = Date.now() + state.open_seconds * 1000;
    render();
  };
  const report = (error: unknown) => {
    status.textContent = error instanceof Error ? error.message : "Något gick fel i Homecontrol.";
  };
  async function show() {
    showButton.disabled = true;
    try {
      applyState(await api.show());
    } catch (error) {
      report(error);
    } finally {
      showButton.disabled = false;
    }
  }
  const poll = async () => {
    try {
      applyState(await api.state());
    } catch (error) {
      report(error);
    }
  };

  void poll();
  const pollTimer = window.setInterval(() => void poll(), POLL_MS);
  const countdownTimer = window.setInterval(() => {
    if (closesAt > 0) {
      render();
    }
  }, 1000);
  return () => {
    if (!stopped) {
      stopped = true;
      window.clearInterval(pollTimer);
      window.clearInterval(countdownTimer);
      stopStream();
    }
  };
}
