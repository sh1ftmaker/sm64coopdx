# SM64CoopDX PartyKit Refactor Progress

## Status Overview
Converting from PeerJS P2P to PartyKit (Cloudflare Durable Objects) client-server architecture.

---

## ✅ Completed

### Phase 1: Server Implementation
- Created `partykit/` directory structure
- `partykit/package.json` - PartyKit v0.0.115, compat-date 2024-01-01
- `partykit/tsconfig.json`
- `partykit/partykit.json` (entry: `src/server.ts`)
- Implemented `partykit/src/server.ts` with:
  - Durable Object room handling
  - Slot assignment (first player = **host, slot 0**) — corrected from slot 1
  - Message types: ASSIGN_SLOT, PLAYER_JOIN, PLAYER_LEAVE, RELAY
  - Binary frame format: `[toSlot(u8)][data...]` client→server, `[fromSlot][payload]` server→client
  - Connection tracking: `connection.id → slotId`
- Verified TypeScript: `npx tsc --noEmit` ✓

---

### Phase 2: Client Implementation (shell.html)
- ✓ Removed PeerJS script tag, added PartySocket CDN (`https://cdn.jsdelivr.net/npm/partysocket@1.1.16/dist/index.min.js`)
- ✓ Replaced `PeerNetwork` object with PartySocket-based implementation (kept global name `PeerNetwork` for C compatibility)
- ✓ Binary protocol: send `[toSlot(u8)][payload]`, receive `{slotId, data}` packets
- ✓ Slot assignment: host gets slot 0, clients 1+
- ✓ Connection state: `slotId = -1` (unassigned), `isConnected()` returns `slotId >= 0`
- ✓ Removed BackgroundGuard keepalive logic entirely
- ✓ `kickSlot()` no-op implementation
- ✓ Fixed join check: removed `PeerNetwork.peer` check, now `PeerNetwork.isConnected()` sufficient

---

### Phase 3: C-side Bridge
- EM_JS functions in `src/pc/network/socket/socket_websocket.c` already call `PeerNetwork.*` methods
- No changes needed — JS `PeerNetwork` API matches exactly:
  - `peer_init`, `peer_send`, `peer_is_connected`, `peer_is_host`, `peer_drain_recv`, `peer_kick_slot`, `peer_shutdown`
- `src/pc/pc_main.c` and `src/pc/djui/djui_panel_join_direct.c` EM_ASM calls remain valid
- Slot numbering consistent: host = 0, clients = 1+

---

## ⏳ Pending

### Phase 4: Testing & Validation
- [ ] Trigger GitHub Actions build to verify end-to-end integration (Emscripten compile + TypeScript)
- [ ] Deploy PartyKit server to Cloudflare (or use higher-RAM machine for `npx partykit dev`)
- [ ] Set `PARTYKIT_SERVER_URL` in HTML build config or runtime
- [ ] Test full flow: host via `?room=NAME`, join from another browser
- [ ] Validate binary packet routing and slot assignment with real game traffic
- [ ] Ensure save/ROM persistence still works (unaffected by networking change)
- [ ] Verify player leave events propagate reliably
- [ ] Test with 3+ players to confirm slot assignment and broadcast behavior
- [ ] Investigate MMO-style "one big room" scalability (>10 players) and potential optimizations

---

### Optional Cleanup (documentation/comments)
- [ ] Update log messages/comments from "PeerJS" to "PartyKit" for accuracy (currently `PeerNetwork` kept as alias)
- [ ] Update README.md and CLAUDE.md to reflect new networking model
- [ ] Remove deprecated references if doing full API break (currently compatible)

---

## 🔧 Implementation Notes

### Protocol Changes
- **Server**: PartyKit Durable Object (DO) relays all messages; no P2P
- **Client → Server**: `Uint8Array` = `[targetSlot(u8)][payload]`
- **Server → Client**: `Uint8Array` = `[fromSlot(u8)][payload]`
- **C Bridge**: Ring buffer format unchanged: `[u16 slot][u16 len][data...]`
- **Connection Lifecycle**: On connect, server sends `ASSIGN_SLOT` JSON; client considers connected when `slotId >= 0` and socket open
- **Heartbeats**: Built into PartySocket; BackgroundGuard removed

### Known Issues / Risks
- Local `npx partykit dev` crashes on Raspberry Pi (4GB RAM) with `tcmalloc: large alloc 1GB` — must use CI/CD for builds
- `web_auto_network` C polling loop expects `peer_is_connected()` to eventually return true — should work with new implementation
- Need to handle server URL configuration (`.partykit.dev` domain)
- Slot ID overflow (wrap at 255) not yet handled if >255 players (unlikely for now)

### File Structure
```
partykit/
├── package.json
├── tsconfig.json
├── partykit.json
└── src/
    └── server.ts  (Durable Object relay with slot management)

src/pc/web/
├── shell.html          (PeerNetwork → PartySocket wrapper)
└── socket_websocket.c  (EM_JS bridge — no changes)

src/pc/
├── pc_main.c           (EM_ASM calls — unchanged)
└── djui/
    └── djui_panel_join_direct.c  (unchanged)
```

---

## 📂 Repository Changes (uncommitted)
- **Modified**: `src/pc/web/shell.html`
- **Added**: `partykit/` (new directory)
- **Added**: `progress.md` (this file, now updated)
- **Added**: `docs/PARTYKIT_REFACTOR_DESIGN.md` (technical design)

---

**Last updated**: 2026-03-23  
**Status**: Phase 1–3 complete, ready for CI build and multiplayer testing.
