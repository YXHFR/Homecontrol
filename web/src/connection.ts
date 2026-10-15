import { parseServerMessage, type ClientMessage, type ServerMessage } from "./protocol";

export type ConnectionStatus = "connecting" | "open" | "closed";

export interface ConnectionHandlers {
  onMessage(message: ServerMessage): void;
  onStatus(status: ConnectionStatus): void;
}

/** Det lilla av WebSocket som anslutningen använder, så att testerna kan byta ut den. */
export type SocketLike = Pick<
  WebSocket,
  "readyState" | "onopen" | "onclose" | "onmessage" | "send" | "close"
>;

export type SocketFactory = (url: string) => SocketLike;

const OPEN = 1;
const MAX_RETRY_MS = 10_000;

/** Adressen till kärnans realtidsström, på samma värd som sidan. */
export function liveUrl(location: Pick<Location, "protocol" | "host">): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/api/live`;
}

/** Håller en WebSocket mot kärnan öppen och ansluter igen om den bryts. */
export class LiveConnection {
  private socket: SocketLike | null = null;
  private retryMs = 500;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  constructor(
    private readonly url: string,
    private readonly handlers: ConnectionHandlers,
    private readonly createSocket: SocketFactory = (url) => new WebSocket(url),
  ) {}

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.socket?.close();
    this.socket = null;
  }

  /** Skickar ett kommando. Utan anslutning försvinner det; kärnans läge gäller ändå. */
  send(message: ClientMessage): void {
    if (this.socket?.readyState === OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  private connect(): void {
    this.handlers.onStatus("connecting");
    const socket = this.createSocket(this.url);
    this.socket = socket;
    socket.onopen = () => {
      this.retryMs = 500;
      this.handlers.onStatus("open");
    };
    socket.onmessage = (event) => {
      const data: unknown = event.data;
      const message = typeof data === "string" ? parseServerMessage(data) : null;
      if (message !== null) {
        this.handlers.onMessage(message);
      }
    };
    socket.onclose = () => {
      if (this.socket !== socket) {
        return;
      }
      this.socket = null;
      this.handlers.onStatus("closed");
      if (!this.stopped) {
        this.retryTimer = setTimeout(() => {
          this.retryTimer = null;
          this.connect();
        }, this.retryMs);
        this.retryMs = Math.min(this.retryMs * 2, MAX_RETRY_MS);
      }
    };
  }
}
