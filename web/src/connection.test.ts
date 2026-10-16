import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveConnection, liveUrl, type ConnectionStatus, type SocketLike } from "./connection";
import type { ServerMessage } from "./protocol";

class FakeSocket implements SocketLike {
  readyState: SocketLike["readyState"] = 0;
  onopen: SocketLike["onopen"] = null;
  onclose: SocketLike["onclose"] = null;
  onmessage: SocketLike["onmessage"] = null;
  sent: string[] = [];

  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
    this.onclose?.call(this.asWebSocket(), new CloseEvent("close"));
  }
  open() {
    this.readyState = 1;
    this.onopen?.call(this.asWebSocket(), new Event("open"));
  }
  receive(data: string) {
    this.onmessage?.call(this.asWebSocket(), new MessageEvent("message", { data }));
  }
  private asWebSocket(): WebSocket {
    return this as unknown as WebSocket;
  }
}

function setup() {
  const sockets: FakeSocket[] = [];
  const statuses: ConnectionStatus[] = [];
  const messages: ServerMessage[] = [];
  const connection = new LiveConnection(
    "ws://x/api/live",
    { onStatus: (s) => statuses.push(s), onMessage: (m) => messages.push(m) },
    () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    },
  );
  return { connection, sockets, statuses, messages };
}

describe("liveUrl", () => {
  it("använder wss på https och ws annars", () => {
    expect(liveUrl({ protocol: "https:", host: "hem:8000" })).toBe("wss://hem:8000/api/live");
    expect(liveUrl({ protocol: "http:", host: "hem:8000" })).toBe("ws://hem:8000/api/live");
  });
});

describe("LiveConnection", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("lämnar vidare giltiga meddelanden och släpper trasiga", () => {
    const { connection, sockets, messages } = setup();
    connection.start();
    sockets[0].open();

    sockets[0].receive("inte json");
    sockets[0].receive(
      '{"type":"state","tracks":[],"rooms":[],"zones":[],"lamps":[],"simulation":null,"cards":null}',
    );

    expect(messages).toEqual([
      { type: "state", tracks: [], rooms: [], zones: [], lamps: [], simulation: null, cards: null },
    ]);
  });

  it("skickar bara när anslutningen är öppen", () => {
    const { connection, sockets } = setup();
    connection.start();

    connection.send({ type: "set_daylight", lux: 1 });
    sockets[0].open();
    connection.send({ type: "set_daylight", lux: 2 });

    expect(sockets[0].sent).toEqual(['{"type":"set_daylight","lux":2}']);
  });

  it("ansluter igen med växande väntetid när kontakten bryts", () => {
    const { connection, sockets, statuses } = setup();
    connection.start();
    sockets[0].close();

    vi.advanceTimersByTime(499);
    expect(sockets).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(2);

    sockets[1].close();
    vi.advanceTimersByTime(999);
    expect(sockets).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(3);

    sockets[2].open();
    expect(statuses).toEqual(["connecting", "closed", "connecting", "closed", "connecting", "open"]);
  });

  it("slutar försöka när den stoppas", () => {
    const { connection, sockets } = setup();
    connection.start();
    sockets[0].open();

    connection.stop();
    vi.advanceTimersByTime(60_000);

    expect(sockets).toHaveLength(1);
  });
});
