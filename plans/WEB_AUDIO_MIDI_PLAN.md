# Web Audio: MIDI + SoundFont Approach

## Status: PARKED (created 2026-03-15)

This branch is reserved for a future MIDI-based approach to SM64 web audio.
The pre-render approach (`feature/web-audio`) is being pursued first as it has the
highest chance of success. If it works well, this branch may not be needed.
If the pre-render approach has issues (file size, latency), this is the fallback.

---

## Overview

Convert SM64's custom sequence format to standard MIDI, build a SoundFont from
the ROM's ADPCM samples, and use a browser-based MIDI player (e.g. WebAudioFont,
MIDI.js, or a custom Web Audio API synth) for real-time playback.

## Why This Approach

- **Small storage**: ~5MB (MIDI files + SoundFont) vs ~50MB+ for pre-rendered audio
- **Dynamic**: Music reacts to game state (tempo changes, channel muting) just like native
- **Low latency**: Sound effects play immediately from sample buffers

## Architecture

### ROM Extraction (runtime, in browser)

Same as pre-render approach — extract CTL, TBL, SEQ from ROM when user loads it.

### Step 1: ADPCM → SoundFont

1. Parse CTL to get instrument/drum definitions (ADSR envelopes, note ranges, tuning)
2. Parse TBL to get raw ADPCM sample data
3. Decode ADPCM → PCM using `vadpcm_dec` (compile to WASM or port to JS)
4. Package into SF2 (SoundFont) format or raw AudioBuffers keyed by bank+program+note

### Step 2: Sequence → MIDI

SM64 sequences use a custom format (NOT standard MIDI) defined in `src/audio/seqplayer.c`.

Key differences from MIDI:
- Custom channel commands (portamento, vibrato, reverb are inline)
- Bank switching via `chan_setbank` command
- Tempo in custom units
- Loop constructs (`seq_loop`, `chan_loop`)
- Dynamic volume/pan from game state

Conversion needs:
- Parse sequence binary format (see `src/audio/seqplayer.c` command tables)
- Map SM64 commands → MIDI events (note on/off, program change, CC)
- Handle loop unrolling or MIDI loop extensions
- Map SM64 instruments → General MIDI program numbers (or custom SoundFont programs)

### Step 3: Browser Playback

Options:
- **WebAudioFont**: JS library that plays MIDI with custom SoundFonts via Web Audio API
- **MIDI.js**: Older but proven browser MIDI playback
- **Custom synth**: Build minimal synth that reads AudioBuffers from step 1

### Step 4: Sound Effects

SFX don't use sequences — they're direct sample triggers.
- Build a lookup table: soundBits → sample buffer
- On `play_sound()`, find the sample and play via `AudioBufferSourceNode`
- Apply position-based panning from game state

## Key Files to Study

| File | Why |
|------|-----|
| `src/audio/seqplayer.c` | Sequence command format — must reverse-engineer for MIDI conversion |
| `src/audio/internal.h` | All data structures (Instrument, Drum, AudioBankSample, SequencePlayer) |
| `src/audio/external.c` | Public API (`play_sound`, `play_sequence`) — integration points |
| `src/audio/load.c` | How CTL/TBL/SEQ are loaded and patched |
| `tools/disassemble_sound.py` | Already parses CTL/TBL — reuse logic |
| `tools/aifc_decode.c` | ADPCM decoder — port to WASM |
| `sound/sequences.json` | Sequence → bank mapping |
| `sound/sound_banks/*.json` | Instrument definitions |

## Effort Estimate

- Sequence format reverse-engineering: **High** (custom format, many commands)
- ADPCM → SoundFont: **Medium** (decoder exists, need SF2 packaging)
- Browser MIDI player integration: **Medium**
- SFX playback: **Low** (direct sample → AudioBuffer)

## Risks

- SM64 sequence format has features that don't map cleanly to MIDI (dynamic bank switching, custom effects)
- Fidelity loss: reverb, portamento, vibrato may not match original
- Some sequences may use commands that are hard to convert

## References

- SM64 audio RE: https://hack64.net/wiki/doku.php?id=super_mario_64:audio_binary
- WebAudioFont: https://github.com/nicknealon/WebAudioFont
- MIDI.js: https://github.com/mudcube/MIDI.js
