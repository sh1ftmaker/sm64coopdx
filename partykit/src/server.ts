import type * as Party from "partykit/server";

interface PlayerState {
  connectionId: string;
  slotId: number;
  joinedAt: number;
}

interface RelayMessage {
  type: "RELAY";
  fromSlot: number;
  toSlot: number;  // 0 = broadcast
  data: Uint8Array;
}

interface ControlMessage {
  type: "ASSIGN_SLOT" | "PLAYER_JOIN" | "PLAYER_LEAVE" | "SLOT_MAP";
  slotId?: number;
  slots?: Array<{ connectionId: string; slotId: number }>;
}

export default class SM64Server implements Party.Server {
  players: Map<string, PlayerState> = new Map();
  nextSlotId: number = 0;
  room: Party.Room;

  constructor(room: Party.Room) {
    this.room = room;
  }

  onConnect(conn: Party.Connection, ctx: Party.ConnectionContext) {
    console.log("[SM64Server] New connection:", conn.id);

    // Assign slot
    const slotId = this.nextSlotId++;
    this.players.set(conn.id, {
      connectionId: conn.id,
      slotId,
      joinedAt: Date.now(),
    });

    console.log("[SM64Server] Assigned slot", slotId, "to connection", conn.id);

    // Send slot assignment to new player
    conn.send(JSON.stringify({
      type: "ASSIGN_SLOT",
      slotId,
    }));

    // Broadcast new player join to all others (exclude sender)
    this.room.broadcast(JSON.stringify({
      type: "PLAYER_JOIN",
      slotId,
    }), [conn.id]);

    // Send current slot map to new player so they know about existing players
    const slots = Array.from(this.players.values()).map(p => ({
      connectionId: p.connectionId,
      slotId: p.slotId,
    }));
    conn.send(JSON.stringify({
      type: "SLOT_MAP",
      slots,
    }));
  }

  onMessage(message: string | ArrayBuffer, sender: Party.Connection) {
    const senderState = this.players.get(sender.id);
    if (!senderState) {
      console.warn("[SM64Server] Message from unknown connection:", sender.id);
      return;
    }

    // Control messages (JSON strings) are ignored at DO level - handled by clients directly
    if (typeof message === "string") {
      // Could be used for chat or other control in future
      return;
    }

    // Binary relay message
    // Expected format from client: [toSlot(u8)][data...]
    const view = new DataView(message);
    if (message.byteLength < 1) {
      console.warn("[SM64Server] Empty binary message from", sender.id);
      return;
    }

    const toSlot = view.getUint8(0);
    const fromSlot = senderState.slotId;
    const data = new Uint8Array(message, 1); // skip first byte

    // Build relay packet: [fromSlot(u8)][data...]
    const relayPacket = new Uint8Array(data.length + 1);
    relayPacket[0] = fromSlot;
    relayPacket.set(data, 1);

    if (toSlot === 0) {
      // Broadcast to all clients except sender
      console.log(`[SM64Server] Broadcast from slot ${fromSlot} to all`);
      this.room.broadcast(relayPacket, [sender.id]);
    } else {
      // Send to specific slot
      const targetConn = this.findConnectionBySlot(toSlot);
      if (targetConn) {
        console.log(`[SM64Server] Relay from slot ${fromSlot} to slot ${toSlot}`);
        targetConn.send(relayPacket);
      } else {
        console.warn(`[SM64Server] Target slot ${toSlot} not found for relay from slot ${fromSlot}`);
      }
    }
  }

  onClose(conn: Party.Connection) {
    const state = this.players.get(conn.id);
    if (state) {
      console.log("[SM64Server] Connection closed:", conn.id, "slot:", state.slotId);
      // Notify all other clients that this player left
      this.room.broadcast(JSON.stringify({
        type: "PLAYER_LEAVE",
        slotId: state.slotId,
      }), [conn.id]);
      this.players.delete(conn.id);
    }
  }

  findConnectionBySlot(slotId: number): Party.Connection | null {
    for (const [connId, state] of this.players) {
      if (state.slotId === slotId) {
        return this.room.getConnection(connId) || null;
      }
    }
    return null;
  }
}
