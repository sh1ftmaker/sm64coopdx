import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const shell = readFileSync(new URL('../src/pc/web/shell.html', import.meta.url), 'utf8');

test('browser shell scripts parse before Emscripten starts', () => {
    for (const [, attributes, script] of shell.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)) {
        if (!attributes.includes('type="module"')) new vm.Script(script);
    }
});

test('PartyKit bridge preserves binary packets and assigned roles', () => {
    const listeners = {};
    const sent = [];
    class Socket {
        readyState = 1;
        addEventListener(name, callback) { listeners[name] = callback; }
        send(data) { sent.push(new Uint8Array(data)); }
        close() { this.readyState = 3; }
    }
    const context = vm.createContext({
        window: { PARTYKIT_SERVER_URL: 'localhost:1999' },
        PartySocket: Socket, WebSocket: { OPEN: 1 },
        Uint8Array, ArrayBuffer, console,
    });
    const start = shell.indexOf('    var PeerNetwork = {');
    const end = shell.indexOf('    var Module = {', start);
    assert.ok(start >= 0 && end > start);
    vm.runInContext(shell.slice(start, end), context);
    const network = context.PeerNetwork;
    network.init('test-room');
    assert.equal(network.socket.binaryType, 'arraybuffer');
    assert.equal(network.isConnected(), false);
    listeners.message({ data: JSON.stringify({ type: 'ASSIGN_SLOT', slotId: 0 }) });
    assert.equal(network.isConnected(), true);
    assert.equal(network.isHost, true);
    network.send(2, new Uint8Array([0, 127, 255]).buffer);
    assert.deepEqual([...sent[0]], [2, 0, 127, 255]);
    listeners.message({ data: new Uint8Array([2, 255, 0, 127]).buffer });
    const packets = network.drainRecvBuffer();
    assert.equal(packets[0].slotId, 2);
    assert.deepEqual([...packets[0].data], [255, 0, 127]);
    assert.equal(network.drainRecvBuffer().length, 0);
    network.shutdown();
    assert.equal(network.isHost, false);
    assert.equal(network.slotId, -1);
    network.init('test-room');
    listeners.message({ data: JSON.stringify({ type: 'ASSIGN_SLOT', slotId: 1 }) });
    assert.equal(network.isConnected(), true);
    assert.equal(network.isHost, false);
});

function audioHarness() {
    const handlers = {};
    const registrations = [];
    const document = {
        hidden: false,
        addEventListener(event, handler, options) {
            handlers[event] = handler;
            registrations.push({ event, options });
        },
    };
    const context = vm.createContext({
        document, window: { addEventListener(event, handler) { handlers[event] = handler; } },
        Module: {},
        AudioContext() { throw new Error('Must not create an unrelated audio context'); },
    });
    const start = shell.indexOf('    // ---- Unlock and recover');
    const end = shell.indexOf('    // ---- Watch for game rendering', start);
    assert.ok(start >= 0 && end > start);
    vm.runInContext(shell.slice(start, end), context);
    return { context, handlers, registrations, document };
}

test('an early touch does not prevent unlocking SDL audio on a later touch', () => {
    const { context, handlers, registrations } = audioHarness();
    handlers.touchstart();
    let resumes = 0;
    context.Module.SDL2 = { audioContext: {
        state: 'suspended', resume() { resumes++; this.state = 'running'; return Promise.resolve(); },
    } };
    handlers.touchend();
    assert.equal(resumes, 1);
    handlers.click();
    assert.equal(resumes, 1);
    assert.ok(registrations.filter(r => ['touchstart', 'touchend', 'pointerdown'].includes(r.event))
        .every(r => r.options.capture));
});

test('failed and interrupted resumes remain retryable', async () => {
    const { context, handlers } = audioHarness();
    let resumes = 0;
    const ctx = { state: 'suspended', resume() { resumes++; return Promise.reject(new Error('gesture required')); } };
    context.Module.SDL2 = { audioContext: ctx };
    handlers.touchend();
    await Promise.resolve();
    ctx.resume = function() { resumes++; this.state = 'running'; return Promise.resolve(); };
    handlers.touchend();
    assert.equal(ctx.state, 'running');
    ctx.state = 'interrupted';
    handlers.touchend();
    assert.equal(ctx.state, 'running');
    ctx.state = 'suspended';
    handlers.touchstart();
    assert.equal(resumes, 4);
});

test('returning to the page retries audio without resuming hidden or closed contexts', () => {
    const { context, handlers, document } = audioHarness();
    let resumes = 0;
    const ctx = { state: 'interrupted', resume() { resumes++; this.state = 'running'; } };
    context.Module.SDL2 = { audioContext: ctx };
    document.hidden = true;
    handlers.visibilitychange();
    assert.equal(resumes, 0);
    document.hidden = false;
    handlers.visibilitychange();
    assert.equal(resumes, 1);
    ctx.state = 'closed';
    handlers.pageshow();
    assert.equal(resumes, 1);
    ctx.state = 'interrupted';
    ctx.resume = () => { throw new Error('temporarily unavailable'); };
    assert.doesNotThrow(() => handlers.focus());
});
