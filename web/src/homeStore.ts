/**
 * Hemmet som sidan visar och ändrar, och kön som sparar ändringarna i kärnan
 * en i taget (se homeApi.ts).
 *
 * Det som visas är det som kärnan senast tog emot, med de ändringar som
 * väntar på att sparas ovanpå. Säger kärnan nej ångras ändringen och allt
 * som byggde på den, och hemmet läses om från kärnan, så att sidan aldrig
 * visar något som inte är sparat.
 */
import { CoreError, type HomeApi } from "./homeApi";
import type { HomeDocument } from "./homefile";

/** En ändring av hemmet, som görs på det sparade hemmet när den står på tur. */
export type Edit = (document: HomeDocument) => HomeDocument;

/** Något som ska skickas till kärnan: en ändring av hemmet eller av en bild. */
export type Change =
  /**
   * `failed` körs om ändringen inte sparas, innan sidan visar hemmet igen,
   * och `done` när den är sparad.
   */
  | { kind: "edit"; edit: Edit; failed?: () => void; done?: () => void }
  /** `send` skickar bilden med revisionen den utgår från och ger den nya. */
  | { kind: "image"; send: (revision: string) => Promise<string>; done?: () => void };

export interface StoreEvents {
  /** Det som visas har ändrats. */
  changed(): void;
  /** Kön har börjat eller slutat skicka. */
  busy(busy: boolean): void;
  /** En ändring sparades inte. `conflict` när hemmet hade ändrats någon annanstans. */
  failed(error: unknown, conflict: boolean): void;
}

export class HomeStore {
  /** Det som kärnan senast tog emot, och dess revision. */
  private saved: HomeDocument | null = null;
  private revision = "";
  /** Ändringar som väntar på att skickas till kärnan, i den ordning de gjordes. */
  private waiting: Change[] = [];
  /** Ändringarna skickas en i taget. */
  private queue: Promise<void> = Promise.resolve();
  private current: HomeDocument | null = null;
  /** Hur många ändringar som har sparats sedan sidan öppnades. */
  savedCount = 0;

  constructor(
    private readonly api: HomeApi,
    private readonly events: StoreEvents,
  ) {}

  /** Det som visas: det sparade med de väntande ändringarna ovanpå. */
  get shown(): HomeDocument | null {
    return this.current;
  }

  /** Om något väntar på att sparas. */
  get busy(): boolean {
    return this.waiting.length > 0;
  }

  /** Om en bild håller på att laddas upp eller tas bort. */
  get imageChanging(): boolean {
    return this.waiting.some((change) => change.kind === "image");
  }

  /** Läser hemmet från kärnan första gången. */
  async load(): Promise<void> {
    await this.reloadSaved();
    this.refresh();
    this.events.changed();
  }

  /**
   * Läser om hemmet från kärnan, till exempel när någon annan har ändrat det.
   * Görs inte medan ändringar väntar; då läses det om när de är sparade eller
   * om kärnan säger nej.
   */
  async reload(): Promise<void> {
    if (this.busy) {
      return;
    }
    await this.reloadSaved();
    if (this.busy) {
      return; // en ändring hann göras medan hemmet lästes; den utgår från det nya
    }
    this.refresh();
    this.events.changed();
  }

  /** Ändrar hemmet. `edit` görs på det sparade hemmet när ändringen står på tur. */
  commit(edit: Edit, hooks: { failed?: () => void; done?: () => void } = {}): void {
    this.enqueue({ kind: "edit", edit, ...hooks });
  }

  /** Skickar en bild till kärnan, som ändrar hemmet själv. */
  sendImage(send: (revision: string) => Promise<string>, done?: () => void): void {
    this.enqueue({ kind: "image", send, done });
  }

  private async reloadSaved(): Promise<void> {
    const latest = await this.api.load();
    this.saved = latest.document;
    this.revision = latest.revision;
  }

  private refresh() {
    let shown = this.saved;
    for (const change of this.waiting) {
      if (shown !== null && change.kind === "edit") {
        shown = change.edit(shown);
      }
    }
    this.current = shown;
  }

  private enqueue(change: Change) {
    this.waiting.push(change);
    this.refresh();
    this.events.busy(true);
    this.events.changed();
    // Ett oväntat fel får inte stoppa kön, för då sparas inget mer förrän sidan laddas om.
    this.queue = this.queue
      .then(() => this.run(change))
      .catch((error: unknown) => this.events.failed(error, false));
  }

  private async run(change: Change) {
    if (!this.waiting.includes(change)) {
      return; // en tidigare ändring misslyckades, och den här byggde på den
    }
    let succeeded = false;
    let failure: unknown = null;
    try {
      if (change.kind === "edit" && this.saved !== null) {
        // Ändringen görs på det kärnan har nu, inte på det som visades när
        // användaren gjorde den. Då försvinner inget som hann sparas emellan.
        const next = change.edit(this.saved);
        this.revision = await this.api.save(next, this.revision);
        this.saved = next;
      } else if (change.kind === "image") {
        this.revision = await change.send(this.revision);
        await this.reloadSaved(); // kärnan har ändrat hemmet; läs det som blev
      }
      this.waiting = this.waiting.filter((other) => other !== change);
      succeeded = true;
      this.savedCount++;
    } catch (error) {
      failure = error;
      // Resten av kön byggde på den här ändringen och skickas inte heller.
      // De ångras alla, den senaste först.
      for (const dropped of [...this.waiting].reverse()) {
        if (dropped.kind === "edit") {
          dropped.failed?.();
        }
      }
      this.waiting = [];
      try {
        await this.reloadSaved();
      } catch {
        // Kärnan går inte att nå; visa det som senast sparades. Det kan vara
        // inaktuellt, så nästa ändring får skickas utan giltig revision. Då
        // nekar kärnan den (409) och hemmet läses om i stället för att skrivas över.
        this.revision = "";
      }
    }
    // Bilder och fel ändrar det som visas; en sparad ändring visades redan.
    if (failure !== null || change.kind === "image") {
      this.refresh();
    }
    if (failure !== null) {
      this.events.failed(failure, failure instanceof CoreError && failure.conflict);
    }
    if (succeeded) {
      change.done?.();
    }
    if (this.waiting.length === 0) {
      this.events.busy(false);
    }
    this.events.changed();
  }
}

/** Felet i klartext, för att visas för användaren. */
export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
