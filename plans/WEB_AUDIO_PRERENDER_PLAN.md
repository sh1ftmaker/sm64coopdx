# Web Audio: Pre-Render Approach

## Status: ACTIVE (created 2026-03-15)

---

## Strategy

When the user loads their SM64 ROM in the browser, we extract the audio data
(CTL, TBL, SEQ) and use the **existing C audio engine** (compiled to WASM) to
pre-render every sequence and sound effect to PCM audio files. These are stored
in IndexedDB and played back via the Web Audio API.

**Key insight from main branch research**: The SM64 audio synthesis pipeline
already runs on web — it just outputs to `audio_null` (discarded). We can
capture that output instead of throwing it away.

## Why Pre-Render

- **Highest chance of success**: Reuses the existing, proven C audio engine
- **Perfect fidelity**: Exact same synthesis code as native (reverb, ADPCM, etc.)
- **No format conversion needed**: Engine already outputs PCM S16 stereo @ 32kHz
- **Decoupled from game loop**: No real-time synthesis pressure on the main thread

## Architecture

### Phase 1: ROM Audio Extraction (in browser, at ROM load time)

The ROM contains three zlib-compressed audio segments:
- **CTL** (~53KB compressed) — instrument definitions, ADPCM codebooks
- **TBL** (~2.4MB compressed) — raw ADPCM sample data
- **SEQ** (~11KB compressed) — 35+ music sequence tracks

**Steps:**
1. User loads ROM → we have the full ROM as an ArrayBuffer in JS
2. Locate audio segment offsets in the ROM (known for US version)
3. Extract & decompress (zlib) the three segments
4. Write them to Emscripten MEMFS so the C audio engine can access them

**Key question:** Where are the segment offsets? Check:
- `build/us_pc/` build artifacts for segment addresses
- The ROM segment table (first ~64 bytes of ROM)
- `src/audio/load.c` — how `gAlCtlHeader`, `gAlTbl`, `gSeqFileHeader` are set

### Phase 2: Pre-Render Sequences to WAV/OGG

**Approach:** Call the existing synthesis pipeline in a tight loop to render each
sequence to a PCM buffer, then encode to a compressed format.

**Implementation:**
1. Create `web_prerender_audio()` C function (EMSCRIPTEN_KEEPALIVE)
2. For each sequence ID (0x00 - 0x22, ~35 tracks):
   a. Call `sequence_player_init()` to load the sequence
   b. Loop `create_next_audio_buffer()` until sequence completes
   c. Collect PCM output (s16 stereo @ 32kHz) into a buffer
   d. Detect silence at end, trim
   e. Encode to OGG Vorbis (or just store as PCM if size permits)
3. Store rendered audio in Emscripten MEMFS
4. JS reads the files and caches to IndexedDB

**Challenges:**
- Need to detect when a sequence has "finished" (many loop forever)
  - For looping tracks: render one full loop + a few seconds, then rely on
    JS-side looping with crossfade
  - Track the sequence player's loop counter
- Some sequences are triggered dynamically (star collect, death, etc.)
  - These are short jingles — easy to pre-render
- Sound effects: there are hundreds — may need different approach

### Phase 3: Sound Effect Rendering

Sound effects are NOT sequences — they're direct sample triggers via `play_sound()`.

**Two options:**

**Option A: Extract raw samples (preferred)**
- Parse CTL to build sample lookup table
- Decode ADPCM samples from TBL → PCM using existing `synthesis.c` code
- Store each unique sample as a short audio clip
- Game triggers → JS plays the matching sample via AudioBufferSourceNode

**Option B: Pre-render SFX through engine**
- Programmatically trigger each `play_sound()` call
- Capture audio output
- More complete (gets reverb, envelope) but complex to enumerate all sounds

### Phase 4: Browser Playback Integration

**Music (sequences):**
- JS `AudioManager` class manages playback
- `play_sequence(seqId)` → load pre-rendered audio from IndexedDB → play via Web Audio API
- Handle looping, fading, crossfading between tracks
- Bridge: C calls JS via EM_JS when game triggers `play_sequence()`

**Sound Effects:**
- Pre-decoded samples loaded into AudioBuffers on startup
- `play_sound(soundBits, pos)` → JS finds sample → plays with:
  - Position-based panning (from `pos` parameter)
  - Volume based on distance
  - Low-latency via AudioBufferSourceNode

**Integration seam (from main branch research):**
- `src/audio/external.c` → `play_sound()` and `play_sequence()` are the public APIs
- Add `#ifdef TARGET_WEB` to bridge these to JS instead of the C synth engine
- Or: keep `audio_null` backend but add EM_JS callbacks at the trigger points

### Phase 5: Storage & Caching

**Budget estimate:**
- 35 sequences × ~2 min avg × 32kHz × 2ch × 2 bytes = ~270MB raw PCM (too big!)
- With OGG Vorbis q5: ~30-50MB total (acceptable for IndexedDB)
- SFX samples: ~5MB decoded PCM
- Total: ~35-55MB in IndexedDB

**Caching strategy (follow Shipwright pattern):**
- First visit: extract → render → compress → store in IndexedDB
- Subsequent visits: load from IndexedDB directly
- Version key: hash of ROM CRC + renderer version → invalidate on updates

## Key Native Audio Facts (from main branch)

| Property | Value |
|----------|-------|
| Sample rate | 32,000 Hz |
| Channels | 2 (stereo) |
| Bit depth | 16-bit signed PCM |
| Buffer per update | 528-560 samples |
| Updates per frame | 4-5 |
| Sequence players | 3 (level music, env, SFX) |
| Max simultaneous notes | 128 |
| Audio backend | `audio_null` on web (all no-ops) |
| Audio currently runs | YES — synthesis executes, output is just discarded |

## Key Files

| File | Role |
|------|------|
| `src/audio/external.c` | `play_sound()`, `play_sequence()` — trigger points to intercept |
| `src/audio/external.h` | Public audio API |
| `src/audio/synthesis.c` | PCM synthesis — already runs on web, output discarded |
| `src/audio/load.c` | `audio_init()`, segment loading — need to feed ROM data here |
| `src/audio/seqplayer.c` | Sequence playback — need to detect loop/end |
| `src/audio/internal.h` | All data structures |
| `src/pc/pc_main.c:872-877` | Where `audio_null` is forced on web |
| `src/pc/audio/audio_api.h` | AudioAPI struct — could make a capture backend |
| `src/pc/web/shell.html` | JS side — ROM loading, Web Audio API playback |
| `src/pc/web/web_main.c` | Emscripten bridge functions |
| `tools/disassemble_sound.py` | Parses CTL/TBL — reference for format |

## Implementation Order

1. **Prove extraction works**: Get CTL/TBL/SEQ out of ROM in browser
2. **Prove rendering works**: Render one sequence (e.g. title screen music) to PCM
3. **Add playback**: Play the rendered PCM via Web Audio API
4. **Scale up**: Render all sequences, add progress UI
5. **Sound effects**: Extract and play SFX samples
6. **Polish**: Caching, crossfading, volume control, loading states
