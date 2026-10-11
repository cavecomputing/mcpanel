/**
 * The Settings tab: the game settings in server.properties; the server's name, type, version, Java,
 * memory and CPU (only admins change those two), changed while it is stopped; and, for admins, Delete.
 */
import * as api from './api.js';
import { cpusOf, drawHost, fillOptions, poll } from './servers.js';
import { state } from './state.js';
import { $, ask, esc, showError, toast } from './ui.js';

let server = null; // the dict of the server the tab shows, fresh from each poll
let saved = {}; // its game settings as last read or saved, so Save sends only what changed

const stopped = () => ['stopped', 'crashed'].includes(server.status);

export async function showSettings(fresh) {
    server = fresh;
    saved = {};
    $('gameForm').reset();
    drawServerForm();
    try {
        await fillOptions($('editType'), $('editJava'), $('editHeap'), $('editCpus'));
    } catch { /* api.js showed why */ }
    drawServerForm();
    if (state.admin) drawHost($('editHostNote'), $('editCpus'), fresh.id);
    await loadGame(fresh.id);
}

/** A poll's fresh dict: the server form follows whether the server is stopped, keeping what's typed.
 * Once it stops, the game settings it can no longer say are greyed out; while it runs, those it
 * couldn't say before are asked for again. */
export function updateSettings(fresh) {
    const was = server && stopped();
    server = fresh;
    if (was !== stopped()) drawServerForm(false);
    if ((was === false && stopped()) || (fresh.status === 'running' && Object.values(saved).includes(null))) {
        loadGame(fresh.id, true);
    }
}

async function loadGame(id, onlyUnread = false) {
    let values;
    try {
        values = await api.get(`/api/servers/${id}/properties`, { quiet: onlyUnread });
    } catch {
        return;
    }
    if (server.id === id) fillGame(values, onlyUnread);
}

/** Put game settings in the form: all of them, or with onlyUnread those it couldn't say before or
 * can't say now. One the server can't say now (null: PvP's game rule from 1.21.9, while it's
 * stopped) is greyed out. */
function fillGame(values, onlyUnread = false) {
    for (const input of $('gameForm').elements) {
        if (!(input.name in values) || (onlyUnread && saved[input.name] !== null && values[input.name] !== null)) continue;
        if (input.type === 'checkbox') input.checked = values[input.name];
        else input.value = values[input.name];
        input.disabled = values[input.name] === null;
        saved[input.name] = values[input.name];
    }
}

function drawServerForm(fill = true) {
    if (fill) {
        $('editName').value = server.name;
        $('editType').value = server.type;
        $('editVersion').value = server.version;
        $('editJava').value = server.java;
        $('editHeap').value = server.heap_gb;
        $('editCpus').value = server.cpus ?? '';
    }
    for (const input of $('serverForm').elements) input.disabled = !stopped();
    $('editHeap').disabled ||= !state.admin;
    $('editCpus').disabled ||= !state.admin;
    $('editHeapHint').textContent = state.admin ? 'GB of Java heap' : 'Only admins change memory';
    $('editCpusHint').textContent = state.admin ? 'Cores it may use; empty for no limit' : 'Only admins change CPU';
    $('serverFormNote').textContent = stopped()
        ? 'Saving makes its container again with these settings; its world and files stay.'
        : 'Stop the server to change these.';
}

/** Whether the server keeps PvP in the pvp game rule, like properties.pvp_is_game_rule(): 1.21.9 on. */
function pvpIsGameRule() {
    if (server.version === 'LATEST') return true;
    const [major, minor = 0, patch = 0] = server.version.split('.').map(Number);
    return major > 1 || minor > 21 || (minor === 21 && patch >= 9);
}

async function saveGame(event) {
    event.preventDefault();
    const changes = {};
    for (const input of $('gameForm').elements) {
        if (!input.name || input.disabled) continue;
        const value = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
        if (value !== saved[input.name]) changes[input.name] = value;
    }
    $('gameSave').disabled = true;
    try {
        fillGame(await api.put(`/api/servers/${server.id}/properties`, changes));
        // PvP as a game rule took effect already; everything else waits for the server's next start.
        const forNextStart = Object.keys(changes).some((key) => key !== 'pvp' || !pvpIsGameRule());
        if (!forNextStart) toast('Saved.');
        else toast(stopped() ? 'Saved. They apply when the server starts.' : 'Saved. Restart the server to apply them.');
    } catch { /* api.js showed why */ }
    $('gameSave').disabled = false;
}

async function saveServer(event) {
    event.preventDefault();
    $('serverSave').disabled = true;
    try {
        const fresh = await api.put(`/api/servers/${server.id}`, {
            name: $('editName').value,
            type: $('editType').value,
            version: $('editVersion').value,
            java: $('editJava').value,
            heap_gb: Number($('editHeap').value),
            cpus: cpusOf($('editCpus')),
        });
        server = fresh;
        state.servers = state.servers.map((each) => (each.id === fresh.id ? fresh : each));
        toast(`Saved <b>${esc(fresh.name)}</b>`);
        drawServerForm();
        poll();
    } catch { /* api.js showed why */ }
    $('serverSave').disabled = !stopped();
}

async function deleteServer() {
    if (!stopped()) {
        showError('Stop the server before deleting it');
        return;
    }
    const { id, name } = server;
    const done = await ask({
        title: 'Delete server?', iconName: 'alert', value: '', ok: 'Delete', danger: true,
        text: `${name}, its world, every file in its folder and its backups will be deleted. This can't be undone. Type its name to confirm.`,
        action: (typed) => {
            if (typed.trim() !== name) {
                showError(`Type ${name} to delete it`);
                throw new Error('name');
            }
            return api.del(`/api/servers/${id}`);
        },
    });
    if (done === null) return;
    toast(`Deleted <b>${esc(name)}</b>`);
    state.servers = state.servers.filter((each) => each.id !== id);
    location.hash = '#/';
    poll();
}

export function initSettings() {
    $('gameForm').addEventListener('submit', saveGame);
    $('serverForm').addEventListener('submit', saveServer);
    $('deleteServer')?.addEventListener('click', deleteServer);
}
