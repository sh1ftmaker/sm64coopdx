# Web Audio: JS ROM Extraction Approach

## Status: ACTIVE (updated 2026-03-15)

---

## Strategy

**C audio engine does NOT work on web** (confirmed: bank data has corrupt pointers
under WASM32, synthesis outputs silence). Instead, extract audio data from WASM
linear memory in JavaScript, decode ADPCM samples to PCM, and play via Web Audio API.

Follow the Shipwright/OTR pattern: extraction happens at runtime when the user
loads the ROM, decoded audio is cached in IndexedDB.

## What's Already In Memory

The build system loads ROM audio data into three C arrays at compile time:
- `gSoundDataRaw[]` (TBL) — ~5.7MB of raw ADPCM sample frames
- `gSoundDataADSR[]` (CTL) — instrument definitions, codebook pointers, envelopes
- `gMusicData[]` (SEQ) — 35+ music sequence tracks

These arrays are in WASM linear memory and accessible from JS via `HEAP8/HEAP16`.

## Architecture

### Phase 1: Export Sample Metadata to JS

Create `src/pc/web/web_audio_export.c` (EMSCRIPTEN_KEEPALIVE functions):
- `web_audio_get_sample_table()` → pointer to `gSoundDataRaw`
- `web_audio_get_sample_count()` → number of unique samples
- `web_audio_get_sample_info(index)` → offset, size, codebook, loop info
- Parse CTL to build a flat sample table accessible from JS

### Phase 2: JS VADPCM Decoder

SM64 VADPCM format (per frame = 9 bytes → 16 PCM samples):
- Byte 0: [scale 4 bits][predictor_index 4 bits]
- Bytes 1-8: 16 × 4-bit signed residuals
- Decode uses codebook (order=2, typically 2 predictors)
- Inner product with 11-bit fixed-point arithmetic
- State carries across frames (16 s16 values)

Reference implementations:
- `tools/aifc_decode.c` (lines 239-276)
- `tools/sdk-tools/adpcm/vdecode.c`

### Phase 3: Play SFX via Web Audio API

Hook `play_sound()` in C → EM_ASM call to JS:
- JS maintains decoded sample AudioBuffers
- On play_sound(soundBits, pos):
  - Look up sample from soundBits (bank + sound ID)
  - Create AudioBufferSourceNode
  - Apply position-based panning
  - Play immediately (low latency)

### Phase 4: Music Sequences (deferred)

Sequences are a harder problem — they need a synthesizer.
Options (in order of priority):
1. Pre-render each sequence offline and include as OGG files
2. Port the sequence player to JS
3. Convert to MIDI + use a JS MIDI library

## Sample Data Format

From `samples_assets.c`:
```c
ROM_ASSET_LOAD_SAMPLE(name, &gSoundDataRaw[OFFSET], ROM_ADDR, SIZE, 0, SIZE);
```

~300 unique samples, each stored as raw ADPCM frames at known offsets
in `gSoundDataRaw`. Codebook stored in CTL alongside instrument definitions.

## Key Files

| File | Role |
|------|------|
| `sound/sound_data.c` | Arrays: gSoundDataRaw, gSoundDataADSR, gMusicData |
| `sound/samples_assets.c` | ROM_ASSET_LOAD_SAMPLE with offsets and sizes |
| `sound/samples_offsets.h` | Offset constants for each sample |
| `sound/sound_banks/*.json` | Instrument → sample mappings |
| `tools/aifc_decode.c` | Reference VADPCM decoder |
| `src/audio/internal.h` | AudioBankSample, AdpcmBook, AdpcmLoop structs |
| `src/audio/external.c` | play_sound(), play_sequence() — hooks for JS bridge |
