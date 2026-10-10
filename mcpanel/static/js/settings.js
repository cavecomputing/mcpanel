/**
 * The Settings tab: the game settings in server.properties; the server's name, type, version, Java
 * and memory (only admins change memory), changed while it is stopped; and, for admins, Delete.
 */
import * as api from './api.js';
import { fillOptions, poll } from './servers.js';
import { state } from './state.js';
import { $, ask, esc, showError, toast } from './ui.js';

let server = null; // the dict of the server the tab shows, fresh from each poll

const stopped = () => ['stopped', 'crashed'].includes(server.status);

export async function showSettings(fresh) {
    server = fresh;
    $('gameForm').reset();
    drawServerForm();
    try {
        await fillOptions($('editType'), $('editJava'), $('editHeap'));
    } catch { /* api.js showed why */ }
    drawServerForm();
    let values;
    try {
        values = await api.get(`/api/servers/${fresh.id}/properties`);
    } catch {
        return;
    }
    if (server.id !== fresh.id) return;
    for (const input of $('gameForm').elements) {
        if (!(input.name in values)) continue;
        if (input.type === 'checkbox') input.checked = values[input.name];
        else input.value = values[input.name];
    }
}

/** A poll's fresh dict: the server form follows whether the server is stopped, keeping what's typed. */
export function updateSettings(fresh) {
    const was = server && stopped();
    server = fresh;
    if (was !== stopped()) drawServerForm(false);
}

function drawServerForm(fill = true) {
    if (fill) {
        $('editName').value = server.name;
        $('editType').value = server.type;
        $('editVersion').value = server.version;
        $('editJava').value = server.java;
        $('editHeap').value = server.heap_gb;
    }
    for (const input of $('serverForm').elements) input.disabled = !stopped();
    $('editHeap').disabled ||= !state.admin;
    $('editHeapHint').textContent = state.admin ? 'GB for the server' : 'Only admins change memory';
    $('serverFormNote').textContent = stopped()
        ? 'Saving makes its container again with these settings; its world and files stay.'
        : 'Stop the server to change these.';
}

async function saveGame(event) {
    event.preventDefault();
    const changes = {};
    for (const input of $('gameForm').elements) {
        if (!input.name) continue;
        changes[input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
    }
    $('gameSave').disabled = true;
    try {
        await api.put(`/api/servers/${server.id}/properties`, changes);
        toast(stopped() ? 'Saved. They apply when the server starts.' : 'Saved. Restart the server to apply them.');
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
