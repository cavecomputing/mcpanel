/**
 * The Players tab: who is online, the whitelist, operators and bans. Every change is a console
 * command (whitelist add, op, ban, pardon...), so the server makes it and keeps its own lists.
 */
import * as api from './api.js';
import { $, esc, showError, toast } from './ui.js';

const NAME = /^[A-Za-z0-9_]{1,16}$/;
// list -> the add form's button and command, the second column's heading, each row's actions
const LISTS = {
    online: { extra: '', actions: [['Op', 'op', 'ghost'], ['Kick', 'kick', 'danger'], ['Ban', 'ban', 'danger']] },
    whitelist: { add: ['Add to whitelist', 'whitelist add'], extra: '', actions: [['Remove', 'whitelist remove', 'danger']] },
    ops: { add: ['Make operator', 'op'], extra: '', actions: [['Remove operator', 'deop', 'danger']] },
    banned: { add: ['Ban', 'ban'], extra: 'Reason', actions: [['Pardon', 'pardon', 'ghost']] },
};
let server = null;
let list = 'online';
let players = null;
let loading = null;

const running = () => !['stopped', 'crashed'].includes(server.status);

export function showPlayers(fresh) {
    server = fresh;
    players = null;
    draw();
    load();
}

/** A poll's fresh dict: who's online changes, so load again (one call at a time). */
export function updatePlayers(fresh) {
    server = fresh;
    load();
}

function load() {
    const id = server.id;
    loading ??= api.get(`/api/servers/${id}/players`, { quiet: true })
        .then((data) => {
            if (id !== server.id) return;
            players = data;
            draw();
        })
        .catch(() => {})
        .finally(() => { loading = null; });
}

function draw() {
    for (const button of document.querySelectorAll('[data-list]')) button.setAttribute('aria-pressed', button.dataset.list === list);
    for (const count of document.querySelectorAll('[data-count]')) {
        const names = players?.[count.dataset.count];
        count.textContent = names ? names.length : '';
    }
    const { add, extra, actions } = LISTS[list];
    $('addPlayerForm').hidden = !add;
    if (add) $('addPlayerBtn').lastElementChild.textContent = add[0];
    for (const control of $('addPlayerForm').elements) control.disabled = !running();
    $('playersExtra').textContent = extra;
    $('playersNote').hidden = running();
    $('playersNote').textContent = 'The server is stopped: these are its saved lists. Start it to change them.';

    const rows = players?.[list];
    let html;
    if (!players) html = '<tr class="empty-row"><td colspan="3">Loading…</td></tr>';
    else if (rows === null) html = `<tr class="empty-row"><td colspan="3">${running() ? "The server can't say who is online until it has started." : 'The server is stopped.'}</td></tr>`;
    else if (!rows.length) html = `<tr class="empty-row"><td colspan="3">${list === 'online' ? 'Nobody is online.' : 'Nobody here yet.'}</td></tr>`;
    else {
        html = rows.map((row) => {
            const name = row.name ?? row;
            const buttons = actions.filter(([, command]) => !(command === 'op' && players.ops.includes(name)))
                .map(([label, command, look]) => `<button class="cc-btn cc-btn--${look} cc-btn--sm" type="button" data-command="${esc(command)}"${running() ? '' : ' disabled'}><span>${label}</span></button>`).join('');
            return `<tr data-name="${esc(name)}"><td class="mono">${esc(name)}${list === 'online' && players.ops.includes(name) ? ' <span class="cc-tag">op</span>' : ''}</td>
                <td class="muted">${esc(row.reason ?? '')}</td><td class="act">${buttons}</td></tr>`;
        }).join('');
    }
    $('playerRows').innerHTML = html;
}

async function run(command, name) {
    try {
        const { response } = await api.post(`/api/servers/${server.id}/console`, { command: `${command} ${name}` });
        toast(esc(response || `Done: ${command} ${name}`));
    } catch {
        return;
    }
    setTimeout(load, 300); // the server writes its lists as it runs the command
}

export function initPlayers() {
    for (const button of document.querySelectorAll('[data-list]')) {
        button.addEventListener('click', () => {
            list = button.dataset.list;
            draw();
        });
    }
    $('playerRows').addEventListener('click', (event) => {
        const button = event.target.closest('button[data-command]');
        if (button) run(button.dataset.command, button.closest('tr').dataset.name);
    });
    $('addPlayerForm').addEventListener('submit', (event) => {
        event.preventDefault();
        const name = $('addPlayerName').value.trim();
        if (!NAME.test(name)) {
            showError('A player name is 1 to 16 letters, digits or _');
            return;
        }
        run(LISTS[list].add[1], name);
        $('addPlayerName').value = '';
    });
}
