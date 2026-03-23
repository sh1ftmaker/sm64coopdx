# SM64CoopDX PartyKit Networking Refactor - Technical Design

## Executive Summary

This document describes the architecture refactor to replace the current PeerJS peer-to-peer WebRTC networking with PartyKit (Cloudflare Durable Objects + WebSockets) for a shared party server model.

**Current State:** PeerJS P2P with host-as-relay architecture
**Target State:** PartyKit DO as centralized relay server

**Key Benefits:**
- No more NAT traversal / PeerJS signaling complexity
- Server-authoritative relay without P2P connection management
- Automatic reconnection, global edge distribution
- "Everyone joins the same room" - no lobby required
- Simplified room discovery (just connect to party name)

---

## 1. Current Architecture Analysis

### 1.1 PeerJS Networking Model

```
┌─────────┐                     ┌─────────┐
│  Host   │◄─────── WebRTC ────►│ Client 1│
│(slot 0) │                     └─────────┘
│         │◄─────── WebRTC ────►┌─────────┐
└─────────┘                     │ Client 2│
                                 └─────────┘
```

- Host creates PeerJS ID: `sm64-{roomId}-host`
- First client to get "unavailable-id" error becomes client
- All game traffic goes through host relay (`requireServerBroadcast = true`)
- Slot IDs 1-15 assigned by host to clients
- Ring buffer format: `[u16 slotId][u16 len][data...]`

### 1.2 Key Components

| Component | File | Purpose |
|-----------|------|---------|
| `PeerNetwork` JS | `shell.html` ~1022-1324 | PeerJS connection management |
| EM_JS Bridge | `socket_websocket.c` | C ↔ JS interface |
| `NetworkSystem` | `network.h` | Transport abstraction |
| `ns_socket_*` | `socket_websocket.c` | Socket interface impl |

### 1.3 PeerJS → PartyKit Mapping

| PeerJS Concept | PartyKit Equivalent |
|----------------|-------------------|
| PeerJS signaling server | PartyKit server (partykit.dev) |
| `sm64-{roomId}-host` peer ID | Room ID directly |
| Host detection via unavailable-id | No host - all clients connect to DO |
| `isHost` boolean | `slotId === 0` (server slot) |
| `slotToPeer` / `peerToSlot` maps | DO maintains `connectionId → slotId` |
| `recvBuffer` array | PartySocket `onmessage` events |
| Reliable DataConnection | WebSocket (TCP) |

---

## 2. Proposed Architecture

### 2.1 High-Level Design

```
                    ┌─────────────────────────┐
                    │    PartyKit Server      │
                    │  (Cloudflare Durable    │
                    │      Objects)          │
                    │                         │
                    │  - Slot assignment      │
                    │  - Message relay       │
                    │  - Join/leave handling  │
                    └────────────┬────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │    WebSocket           │
                    │    Connections         │
                    └────────────┬────────────┘
                                 │
         ┌───────────┬───────────┼───────────┬───────────┐
         │           │           │           │           │
    ┌────▼────┐ ┌────▼────┐ ┌────▼────┐ ┌────▼────┐ ┌────▼────┐
    │ Client 0│ │ Client 1│ │ Client 2│ │ Client 3│ │ Client N│
    │ (slot 0)│ │ (slot 1)│ │ (slot 2)│ │ (slot 3)│ │ (slot N)│
    │ SERVER  │ │         │ │         │ │         │ │         │
    └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘
```

### 2.2 Key Architectural Decisions

**Decision 1: DO is Pure Relay, Not Game Host**
- The Durable Object maintains connections and relays packets
- It does NOT run game logic - all clients simulate the game
- This matches the current PeerJS host behavior (host was also just a relay)
- Game logic still runs identically on all clients (client-prediction model)

**Decision 2: Slot Assignment**
- DO assigns slots 0-N to connected clients on join
- Slot 0 is assigned to first connect (becomes "host" in game terms)
- Subsequent clients get next available slots (1, 2, 3, etc.)
- Slot mapping maintained in DO state: `Map<connectionId, slotId>`

**Decision 3: Message Relay Semantics**
- All messages from a client go to DO
- DO relays to appropriate recipients based on slotId
- `slotId = 0` in outgoing message → broadcast to ALL clients (including sender)
- `slotId = N` in outgoing message → send only to slot N
- When DO relays, it includes sender's slotId so recipients know who sent

**Decision 4: Backward Compatibility**
- C code still uses same `NetworkSystem` interface
- EM_JS bridge functions have same signatures
- Game logic doesn't know it's talking to PartyKit instead of PeerJS

### 2.3 Connection Flow

```
1. User opens: https://game.com/?room=awesome-room

2. C code calls: peer_init("awesome-room")

3. JS PartyNetwork:
   a. Connects to wss://myproject.username.partykit.dev/parties/main/awesome-room
   b. Sends JOIN message with optional password

4. PartyKit DO (onConnect):
   a. Assigns next available slot (0, 1, 2, ...)
   b. Stores connectionId → slotId mapping
   c. Sends ASSIGN_SLOT message to new client: { slotId: N }
   d. Broadcasts PLAYER_JOIN to all clients: { slotId: N }

5. Game receives packet via peer_drain_recv():
   { slotId: assignedSlot, data: ASSIGN_SLOT packet }

6. Game initializes player with assigned slot
```

---

## 3. Detailed Component Design

### 3.1 PartyKit Server (`partykit/server.ts`)

```typescript
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
    // Assign slot
    const slotId = this.nextSlotId++;
    this.players.set(conn.id, {
      connectionId: conn.id,
      slotId,
      joinedAt: Date.now(),
    });

    // Send slot assignment to new player
    conn.send(JSON.stringify({
      type: "ASSIGN_SLOT",
      slotId,
    }));

    // Broadcast new player join to all others
    this.room.broadcast(JSON.stringify({
      type: "PLAYER_JOIN",
      slotId,
    }), [conn.id]);

    // Send current slot map to new player
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
    if (!senderState) return;

    // Parse relay message
    // Message format for RELAY: [toSlot(u8)][fromSlot(u8)][data...]
    // Or if ArrayBuffer, it's raw binary with slotId prepended

    if (typeof message === "string") {
      // Control message
      return;
    }

    // Binary relay message
    const view = new DataView(message);
    const toSlot = view.getUint8(0);
    const fromSlot = senderState.slotId;
    const data = new Uint8Array(message, 1); // skip first byte

    if (toSlot === 0) {
      // Broadcast to all
      const relayPacket = this.buildRelayPacket(fromSlot, data);
      this.room.broadcast(relayPacket, [sender.id]);
    } else {
      // Send to specific slot
      const targetConn = this.findConnectionBySlot(toSlot);
      if (targetConn) {
        const relayPacket = this.buildRelayPacket(fromSlot, data);
        targetConn.send(relayPacket);
      }
    }
  }

  onClose(conn: Party.Connection) {
    const state = this.players.get(conn.id);
    if (state) {
      this.room.broadcast(JSON.stringify({
        type: "PLAYER_LEAVE",
        slotId: state.slotId,
      }));
      this.players.delete(conn.id);
    }
  }

  buildRelayPacket(fromSlot: number, data: Uint8Array): ArrayBuffer {
    // Format: [fromSlot(u8)][data...]
    const buf = new ArrayBuffer(data.length + 1);
    const view = new Uint8Array(buf);
    view[0] = fromSlot;
    view.set(data, 1);
    return buf;
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
```

### 3.2 JavaScript PartyNetwork (`shell.html`)

```javascript
var PartyNetwork = {
  socket: null,
  roomId: null,
  mySlotId: -1,
  isHost: false,  // true if mySlotId === 0
  slotMap: {},    // connectionId → slotId (local copy)
  recvBuffer: [], // Array of {slotId, data} for C code

  // Connect to PartyKit room
  init: function(roomId) {
    this.roomId = roomId;
    this.recvBuffer = [];
    this.slotMap = {};

    // PartySocket from partysocket package
    this.socket = new PartySocket({
      host: "sm64coopdx.username.partykit.dev",
      room: roomId,
      party: "main",
    });

    this.socket.addEventListener("open", () => {
      console.log("[PartyKit] Connected to room:", roomId);
    });

    this.socket.addEventListener("message", (event) => {
      this.handleMessage(event.data);
    });

    this.socket.addEventListener("close", () => {
      console.log("[PartyKit] Disconnected");
    });
  },

  handleMessage: function(data) {
    if (typeof data === "string") {
      // Control message
      const msg = JSON.parse(data);
      switch (msg.type) {
        case "ASSIGN_SLOT":
          this.mySlotId = msg.slotId;
          this.isHost = (msg.slotId === 0);
          console.log("[PartyKit] Assigned slot:", msg.slotId, "isHost:", this.isHost);
          break;
        case "PLAYER_JOIN":
          console.log("[PartyKit] Player joined slot:", msg.slotId);
          break;
        case "PLAYER_LEAVE":
          console.log("[PartyKit] Player left slot:", msg.slotId);
          break;
        case "SLOT_MAP":
          this.slotMap = {};
          msg.slots.forEach(s => { this.slotMap[s.connectionId] = s.slotId; });
          break;
      }
    } else {
      // Binary relay message: [fromSlot(u8)][data...]
      const arr = new Uint8Array(data);
      const fromSlot = arr[0];
      const packetData = arr.slice(1);
      this.recvBuffer.push({ slotId: fromSlot, data: packetData });
    }
  },

  // Send to PartyKit server
  // slotId = 0 means broadcast
  // slotId = N means send to specific slot
  send: function(slotId, data) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return;
    }

    // Prepend destination slot for server routing
    // Protocol: [toSlot(u8)][data...]
    const buf = new ArrayBuffer(data.byteLength + 1);
    const view = new Uint8Array(buf);
    view[0] = slotId;
    view.set(new Uint8Array(data), 1);
    this.socket.send(buf);
  },

  // Called from C via EM_JS peer_drain_recv
  drainRecvBuffer: function() {
    const packets = this.recvBuffer;
    this.recvBuffer = [];
    return packets;
  },

  isConnected: function() {
    return this.socket && this.socket.readyState === WebSocket.OPEN;
  },

  shutdown: function() {
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.mySlotId = -1;
    this.isHost = false;
    this.slotMap = {};
    this.recvBuffer = [];
  },
};
```

### 3.3 EM_JS Bridge Changes (`socket_websocket.c`)

**Changes to existing EM_JS functions:**

```c
// Updated: Initialize PartyKit connection instead of PeerJS
EM_JS(int, peer_init, (const char* roomId), {
    var id = UTF8ToString(roomId);
    PartyNetwork.init(id);
    return 0;
});

// Unchanged: Same signature, same semantics
EM_JS(int, peer_send, (int slotId, const uint8_t* data, int len), {
    var buf = HEAPU8.subarray(data, data + len);
    var copy = new Uint8Array(buf);
    PartyNetwork.send(slotId, copy.buffer);
    return 0;
});

// Unchanged: Same function, different internal check
EM_JS(int, peer_is_connected, (), {
    return PartyNetwork.isConnected() ? 1 : 0;
});

// Changed: Check if our slotId is 0 (we're the "host" in game terms)
EM_JS(int, peer_is_host, (), {
    return PartyNetwork.isHost ? 1 : 0;
});

// New: Get our assigned slot ID
EM_JS(int, peer_get_slot_id, (), {
    return PartyNetwork.mySlotId;
});

// Unchanged: Same drain logic
EM_JS(int, peer_drain_recv, (uint8_t* ringBuf, int ringBufSize, int* headPtr, int tailVal), {
    var packets = PartyNetwork.drainRecvBuffer();
    // ... rest unchanged
});

// Updated: Shutdown PartyKit instead of PeerJS
EM_JS(void, peer_shutdown, (), {
    PartyNetwork.shutdown();
});
```

### 3.4 C-Side Interface (`ns_socket_update`)

```c
static void ns_socket_update(void) {
    if (gNetworkType == NT_NONE) { return; }

    // Update host flag: slotId 0 = "host" in game terms
    // In PartyKit model, slotId 0 is first player to join
    // This matches PeerJS semantics where isHost means "we're the relay"
    sIsHostMode = peer_is_host();

    // For GUI-initiated joins, detect when PartyKit connects and send mod list
    if (!sSentModListRequest && !sIsHostMode && peer_is_connected()) {
        sSentModListRequest = true;
        printf("[PartyKit C] Connection ready — sending mod list request\n");
        network_send_mod_list_request();
    }

    // Drain PartyKit received packets into ring buffer
    peer_drain_recv(sRecvBuf, WS_RECV_BUF_SIZE, (int*)&sRecvBufHead, sRecvBufTail);

    // Process ring buffer: each entry is [u16 slotId][u16 packetLen][packetData...]
    while (ringbuf_available() >= 4) {
        // ... same packet processing as before
    }
}

static int ns_socket_send(u8 localIndex, void* address, u8* data, u16 dataLength) {
    (void)address;

    if (!peer_is_connected()) {
        return SOCKET_ERROR;
    }

    if (sIsHostMode) {
        // HOST MODE: send to specific slot or broadcast (slot 0)
        u16 slotId = 0;
        if (localIndex > 0 && localIndex < MAX_PLAYERS) {
            slotId = sAddr[localIndex].sin6_port;
        }
        peer_send(slotId, data, dataLength);
    } else {
        // CLIENT MODE: send to server (DO handles routing)
        peer_send(0, data, dataLength);
    }
    return 0;
}
```

---

## 4. Packet Flow Diagrams

### 4.1 Client Join Flow

```
Client                    DO                       Other Clients
  |                        |                            |
  |---- TCP WS connect --->|                            |
  |                        |                            |
  |                        |<--- broadcast JOIN -------|
  |                        |                            |
  |<-- ASSIGN_SLOT (N) ---|                            |
  |<-- SLOT_MAP --------- |                            |
  |                        |                            |
  |                        |<--- broadcast JOIN -------|
  |                        |   (通知其他人)              |
```

### 4.2 Game Packet Relay (Broadcast)

```
Client A                  DO                       Client B, C, D
  |                        |                            |
  |--- RELAY [0][data] --->|  (toSlot=0 means broadcast)|
  |                        |                            |
  |                        |--- RELAY [A][data] ------->|
  |                        |--- RELAY [A][data] ------->|
  |                        |--- RELAY [A][data] ------->|
```

### 4.3 Game Packet Relay (Directed)

```
Client A                  DO                       Client B
  |                        |                            |
  |--- RELAY [B][data] --->|  (toSlot=B means to B)    |
  |                        |                            |
  |                        |--- RELAY [A][data] ------->|
```

### 4.4 Player Leave Flow

```
Client B                  DO                       Client A, C, D
  |                        |                            |
  |---- TCP WS close ----->|                            |
  |                        |                            |
  |                        |--- PLAYER_LEAVE --------->|
```

---

## 5. Protocol Specification

### 5.1 Message Types

| Type | Direction | Format | Description |
|------|-----------|--------|-------------|
| `ASSIGN_SLOT` | Server → Client | JSON | Assigns player's slot |
| `PLAYER_JOIN` | Server → All | JSON | New player joined |
| `PLAYER_LEAVE` | Server → All | JSON | Player disconnected |
| `SLOT_MAP` | Server → Client | JSON | Full slot mapping |
| `RELAY` | Any → Server → Others | Binary | Game packet relay |

### 5.2 Binary Relay Format

```
Byte 0:      Destination slot (0 = broadcast, N = specific slot)
Bytes 1-N:   Raw game packet data

When server relays:
Byte 0:      Sender's slot (so recipients know who sent)
Bytes 1-N:   Raw game packet data
```

### 5.3 Ring Buffer Format (C Side)

Unchanged from current:

```
Offset 0-1:   u16 slotId (LE) - sender's slot
Offset 2-3:   u16 packetLen (LE) - length of packet data
Offset 4+:    packetLen bytes of compressed packet data
```

---

## 6. File Changes

### 6.1 New Files

| File | Purpose |
|------|---------|
| `partykit/partykit.json` | PartyKit project config |
| `partykit/src/server.ts` | Durable Object server implementation |
| `partykit/package.json` | Node.js dependencies |
| `partykit/tsconfig.json` | TypeScript configuration |
| `partykit/.gitignore` | Ignore node_modules, dist |

### 6.2 Modified Files

| File | Changes |
|------|--------|
| `shell.html` | Replace `PeerNetwork` with `PartyNetwork` |
| `src/pc/network/socket/socket_websocket.c` | Update EM_JS calls from PeerJS to PartyKit |
| `src/pc/network/socket/socket_websocket.h` | May need new function declarations |
| `src/pc/network/network.h` | Add `NS_PARTYKIT` to `NetworkSystemType` enum |
| `src/pc/network/network.c` | Add `NS_PARTYKIT` case in `network_set_system()` |
| `Makefile` | No changes needed (partykit is separate deployment) |

### 6.3 Optional: New Socket Implementation

Instead of modifying `socket_websocket.c`, we could create a new file `socket_partykit.c` that implements the same `NetworkSystem` interface but uses PartyKit. This keeps PeerJS and PartyKit implementations separate and allows switching via the `network_set_system()` mechanism.

| File | Purpose |
|------|---------|
| `src/pc/network/socket/socket_partykit.c` | PartyKit implementation |

---

## 7. Implementation Steps

### Phase 1: PartyKit Server (1-2 hours)

1. Create `partykit/` directory structure
2. Write `partykit.json` configuration
3. Implement `server.ts` with:
   - Slot assignment on connect
   - Broadcast relay
   - Directed relay
   - Join/leave notifications
4. Test locally with `partykit dev`

### Phase 2: JavaScript Client (2-3 hours)

1. Add `partysocket` dependency (CDN or npm)
2. Create `PartyNetwork` object in `shell.html`
3. Implement:
   - `init(roomId)` - connect to PartyKit
   - `send(slotId, data)` - relay message
   - `drainRecvBuffer()` - return packets to C
   - `isConnected()`, `isHost` - state checks
4. Replace `PeerNetwork` with `PartyNetwork`

### Phase 3: C-Side Bridge (1-2 hours)

1. Update EM_JS functions to call `PartyNetwork` instead of `PeerNetwork`
2. Add `peer_get_slot_id()` function
3. Test compilation with `TARGET_WEB=1`
4. Verify packet flow end-to-end

### Phase 4: Testing & Polish (2-4 hours)

1. Local testing with `partykit dev`
2. Deploy to staging PartyKit project
3. Test:
   - Multiple browser tabs (simulating multiple players)
   - Join/leave during gameplay
   - Reconnection behavior
   - Cross-browser compatibility
4. Performance profiling (compare latency to PeerJS)
5. Documentation update

### Phase 5: Production Deployment (1 hour)

1. Create production PartyKit project
2. Configure custom domain if desired
3. Deploy server code
4. Update `PartyNetwork` host URL
5. Monitor connections and errors

---

## 8. Migration Considerations

### 8.1 Backward Compatibility

- Keep PeerJS implementation intact initially (feature flag)
- Allow switching between PeerJS and PartyKit via config or build flag
- `?transport=peerjs` or `?transport=partykit` URL param

### 8.2 Data Migration

- No persistent game state in PeerJS (stateless relay)
- PartyKit Durable Object can optionally persist:
  - Slot mappings (on disconnect/reconnect)
  - Chat history
  - Game settings

### 8.3 Breaking Changes

- Users who bookmark `?room=X` will need to reconnect (new server)
- Old PeerJS room codes won't work with PartyKit
- No migration path between PeerJS and PartyKit rooms

---

## 9. Testing Strategy

### 9.1 Unit Tests

- PartyKit server: Test slot assignment, relay logic
- PartyNetwork: Test send/receive, buffer management

### 9.2 Integration Tests

- Multi-tab testing: Open 4 browser tabs, join same room
- Verify all game actions sync correctly (movement, star collection)
- Test disconnect/reconnect behavior

### 9.3 Load Tests

- Simulate N connections to PartyKit room
- Measure relay latency at scale
- Verify Durable Object limits

### 9.4 Comparison Testing

- Latency: PeerJS vs PartyKit
- Reliability: Reconnection behavior
- Bandwidth: Message overhead comparison

---

## 10. Potential Issues & Mitigations

### 10.1 Latency Concern

**Issue:** PartyKit relay adds ~50-100ms latency vs PeerJS P2P
**Mitigation:** 
- SM64 is 30 Hz (~33ms per tick)
- Game uses client-prediction (local simulation)
- If latency is problematic, could explore:
  - Cloudflare's fastest edge location
  - Multiple DO regions with user-affinity

### 10.2 Single Point of Failure

**Issue:** PartyKit DO is centralized relay
**Mitigation:**
- Cloudflare's 275+ edge locations provide redundancy
- DO state is ephemeral (no persistent game state lost on crash)
- Automatic reconnection clientside

### 10.3 Connection Limits

**Issue:** PartyKit free tier limits
**Mitigation:**
- Free tier: unlimited connections per room (subject to DO memory)
- 100 live connections without hibernation
- With hibernation: ~32,000 connections
- SM64CoopDX has MAX_PLAYERS = 16, well within limits

### 10.4 Cost

**Issue:** PartyKit pricing at scale
**Mitigation:**
- Free tier: 10 projects, storage cleared 24h
- Paid tiers: Pay for usage (connections, CPU time)
- Monitor usage and optimize DO code

---

## 11. Future Enhancements

### 11.1 Persistent Rooms

- Rooms that persist after all players leave
- Resume game from saved state

### 11.2 Multiple Regions

- DOs in different Cloudflare regions
- Route players to nearest DO

### 11.3 Server-Side Game Logic

- Move some game validation to DO
- Anti-cheat measures
- Server-authoritative physics (optional mode)

### 11.4 Spectator Mode

- DO supports read-only spectators
- Separate "spectator" slot type

---

## 12. Appendix: Reference Code

### 12.1 PartyKit Project Setup

```bash
mkdir partykit && cd partykit
npm init -y
npm install partykit typescript @types/node
npx tsc --init
```

### 12.2 package.json Dependencies

```json
{
  "name": "sm64coopdx-partykit",
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "partykit dev",
    "deploy": "partykit deploy"
  },
  "dependencies": {
    "partykit": "^0.0.100"
  },
  "devDependencies": {
    "typescript": "^5.0.0",
    "@types/node": "^20.0.0"
  }
}
```

### 12.3 partykit.json

```json
{
  "name": "sm64coopdx",
  "main": "src/server.ts",
  "parties": {
    "main": "src/server.ts"
  }
}
```

### 12.4 Shell HTML Script Addition

```html
<!-- PartyKit PartySocket library -->
<script src="https://unpkg.com/partysocket@1.0.2/dist/global.js"></script>
```

---

## 13. Summary

The PartyKit refactor transforms SM64CoopDX from a P2P mesh network to a client-server architecture with a hosted relay. The key changes are:

1. **Server**: New PartyKit Durable Object handles slot assignment and message relay
2. **Client**: JavaScript `PartyNetwork` replaces `PeerNetwork` using `PartySocket`
3. **Bridge**: EM_JS functions updated to call `PartyNetwork` API
4. **Game Logic**: Unchanged - `NetworkSystem` interface remains the same

The result is a simpler networking stack with:
- No NAT traversal / P2P complexity
- Centralized relay with global edge distribution
- Automatic reconnection and connection management
- "Join any room" without host/Client distinction

Total estimated implementation time: **8-12 hours** across all phases.
