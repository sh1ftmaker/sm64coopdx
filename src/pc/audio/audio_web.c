#ifdef TARGET_WEB

#include "audio_api.h"
#include <emscripten.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

// Ring buffer for audio samples: 64KB (~0.5s at 32kHz stereo)
#define WEB_AUDIO_BUF_SIZE (1 << 16)
static int16_t sWebAudioBuf[WEB_AUDIO_BUF_SIZE];
static volatile uint32_t sWebAudioWritePos = 0;
static volatile uint32_t sWebAudioReadPos = 0;

// JS-side initialization of Web Audio API
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

    // Ring buffer params (must match C side)
    var BUF_SIZE = 1 << 16; // 65536 samples

    // ScriptProcessorNode: simple, works everywhere
    // Buffer size 1024 = ~32ms latency at 32kHz
    var node = ctx.createScriptProcessor(1024, 0, 2);
    Module._webAudioNode = node;

    node.onaudioprocess = function(e) {
        var outL = e.outputBuffer.getChannelData(0);
        var outR = e.outputBuffer.getChannelData(1);
        var bufPtr = Module._web_audio_get_buffer();
        var readPtr = Module._web_audio_get_read_pos();
        var writePos = Module._web_audio_get_write_pos();
        var readPos = HEAPU32[readPtr >> 2];

        for (var i = 0; i < outL.length; i++) {
            if (readPos === writePos) {
                // Buffer underrun — output silence
                outL[i] = 0;
                outR[i] = 0;
            } else {
                var idx = (readPos & (BUF_SIZE - 1)) * 2; // stereo: L, R interleaved
                // Wait — the C side writes interleaved s16 pairs but our ring buffer
                // index is in samples (each sample = one L + one R).
                // Actually the C buffer is int16_t with stereo interleaved.
                // So sWebAudioBuf[n*2] = L, sWebAudioBuf[n*2+1] = R
                // But we store sample count, not byte offset.
                // bufPtr points to sWebAudioBuf which is int16_t[]
                var base = (bufPtr >> 1) + (readPos & (BUF_SIZE - 1)) * 2;
                outL[i] = HEAP16[base] / 32768.0;
                outR[i] = HEAP16[base + 1] / 32768.0;
                readPos++;
            }
        }
        HEAPU32[readPtr >> 2] = readPos;
    };

    // Don't connect yet — wait for user gesture
    console.log('[WebAudio] Initialized (suspended, waiting for user gesture)');

    // Resume on user gesture
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

// Export buffer pointers so JS can read them
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
    // Number of stereo sample pairs buffered
    return (int)(sWebAudioWritePos - sWebAudioReadPos);
}

static int audio_web_get_desired_buffered(void) {
    // Target ~2 frames worth of audio at 32kHz/30fps ≈ 2133 samples
    return 2 * 1024;
}

static void audio_web_play(const uint8_t *buf, size_t len) {
    // buf is interleaved s16 stereo: [L0, R0, L1, R1, ...]
    // len is in bytes, so num_sample_pairs = len / 4
    const int16_t *samples = (const int16_t *)buf;
    uint32_t num_pairs = (uint32_t)(len / 4);

    for (uint32_t i = 0; i < num_pairs; i++) {
        uint32_t pos = sWebAudioWritePos & (WEB_AUDIO_BUF_SIZE - 1);
        sWebAudioBuf[pos * 2]     = samples[i * 2];     // L
        sWebAudioBuf[pos * 2 + 1] = samples[i * 2 + 1]; // R
        sWebAudioWritePos++;
    }
}

static void audio_web_shutdown(void) {
    // Nothing to do
}

struct AudioAPI audio_web = {
    audio_web_init,
    audio_web_buffered,
    audio_web_get_desired_buffered,
    audio_web_play,
    audio_web_shutdown
};

#endif // TARGET_WEB
