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
