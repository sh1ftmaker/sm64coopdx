#ifdef TARGET_WEB

#include "audio_api.h"
#include <emscripten.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

// Ring buffer for stereo sample pairs.
// Each "slot" = 2 int16_t (L + R). 16384 slots = ~0.5s at 32kHz.
#define WEB_AUDIO_RING_SLOTS 16384
#define WEB_AUDIO_RING_MASK  (WEB_AUDIO_RING_SLOTS - 1)
static int16_t sWebAudioBuf[WEB_AUDIO_RING_SLOTS * 2]; // interleaved L,R
static volatile uint32_t sWebAudioWritePos = 0; // in slots (stereo pairs)
static volatile uint32_t sWebAudioReadPos = 0;

EM_JS(void, web_audio_init_js, (), {
    if (Module._webAudioCtx) return;

    var AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) {
        console.warn('[WebAudio] No AudioContext available');
        return;
    }

    var ctx = new AudioContext({ sampleRate: 32000 });
    Module._webAudioCtx = ctx;
    Module._webAudioStarted = false;

    var RING_SLOTS = 16384;
    var RING_MASK = RING_SLOTS - 1;

    // 2048-sample buffer = ~64ms latency at 32kHz (good balance of
    // latency vs underrun resistance for 30Hz game tick rate)
    var node = ctx.createScriptProcessor(2048, 0, 2);
    Module._webAudioNode = node;

    node.onaudioprocess = function(e) {
        var outL = e.outputBuffer.getChannelData(0);
        var outR = e.outputBuffer.getChannelData(1);
        var bufPtr = Module._web_audio_get_buffer();
        var readPtr = Module._web_audio_get_read_pos();
        var writePtr = Module._web_audio_get_write_pos();
        var readPos = HEAPU32[readPtr >> 2];
        var writePos = HEAPU32[writePtr >> 2];

        // bufPtr is byte address of sWebAudioBuf (int16_t array)
        // Divide by 2 to get HEAP16 index base
        var heapBase = bufPtr >> 1;

        for (var i = 0; i < outL.length; i++) {
            if (readPos === writePos) {
                // Buffer underrun — output silence
                outL[i] = 0;
                outR[i] = 0;
            } else {
                var slot = (readPos & RING_MASK) * 2;
                outL[i] = HEAP16[heapBase + slot] / 32768.0;
                outR[i] = HEAP16[heapBase + slot + 1] / 32768.0;
                readPos++;
            }
        }
        HEAPU32[readPtr >> 2] = readPos;
    };

    console.log('[WebAudio] Initialized (suspended, waiting for user gesture)');

    function tryResume() {
        if (!Module._webAudioStarted && Module._webAudioCtx) {
            Module._webAudioCtx.resume().then(function() {
                if (Module._webAudioCtx.state === 'running') {
                    Module._webAudioNode.connect(Module._webAudioCtx.destination);
                    Module._webAudioStarted = true;
                    console.log('[WebAudio] Started playback');
                }
            });
        }
    }

    ['click', 'mousedown', 'keydown', 'touchstart', 'pointerdown']
        .forEach(function(evt) {
            document.addEventListener(evt, tryResume, { passive: true });
        });
});

EMSCRIPTEN_KEEPALIVE
int16_t* web_audio_get_buffer(void) {
    return sWebAudioBuf;
}

EMSCRIPTEN_KEEPALIVE
uint32_t* web_audio_get_read_pos(void) {
    return (uint32_t*)&sWebAudioReadPos;
}

EMSCRIPTEN_KEEPALIVE
uint32_t* web_audio_get_write_pos(void) {
    return (uint32_t*)&sWebAudioWritePos;
}

static bool audio_web_init(void) {
    sWebAudioWritePos = 0;
    sWebAudioReadPos = 0;
    web_audio_init_js();
    return true;
}

static int audio_web_buffered(void) {
    return (int)(sWebAudioWritePos - sWebAudioReadPos);
}

static int audio_web_get_desired_buffered(void) {
    // Target ~3 frames of audio at 32kHz/30fps ≈ 3200 samples
    // Generous buffer to absorb game tick jitter
    return 3200;
}

static uint32_t sDebugCounter = 0;

static void audio_web_play(const uint8_t *buf, size_t len) {
    // buf = interleaved s16 stereo: [L0, R0, L1, R1, ...]
    // len in bytes; each stereo pair = 4 bytes
    const int16_t *samples = (const int16_t *)buf;
    uint32_t num_pairs = (uint32_t)(len / 4);

    // Debug: log every ~2 seconds (60 calls at 30Hz)
    if (sDebugCounter % 60 == 0) {
        int16_t maxSample = 0;
        for (uint32_t i = 0; i < num_pairs * 2 && i < 100; i++) {
            int16_t s = samples[i] < 0 ? -samples[i] : samples[i];
            if (s > maxSample) maxSample = s;
        }
        EM_ASM({
            console.log('[WebAudio] play() len=' + $0 + ' pairs=' + $1 +
                        ' writePos=' + $2 + ' readPos=' + $3 +
                        ' maxSample=' + $4);
        }, (int)len, num_pairs, sWebAudioWritePos, sWebAudioReadPos, (int)maxSample);
    }
    sDebugCounter++;

    for (uint32_t i = 0; i < num_pairs; i++) {
        uint32_t slot = (sWebAudioWritePos & WEB_AUDIO_RING_MASK) * 2;
        sWebAudioBuf[slot]     = samples[i * 2];     // L
        sWebAudioBuf[slot + 1] = samples[i * 2 + 1]; // R
        sWebAudioWritePos++;
    }
}

static void audio_web_shutdown(void) {
}

struct AudioAPI audio_web = {
    audio_web_init,
    audio_web_buffered,
    audio_web_get_desired_buffered,
    audio_web_play,
    audio_web_shutdown
};

#endif // TARGET_WEB
