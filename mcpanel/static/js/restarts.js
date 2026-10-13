/** The Settings tab's scheduled restarts: a server's cron lines, when each next happens, add and remove. */
import * as api from './api.js';
import { $, esc, formatDate, toast } from './ui.js';

const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone; // a new line is read in this time zone
let serverId = '';

const url = (restartId = '') => `/api/servers/${serverId}/restarts${restartId === '' ? '' : `/${restartId}`}`;

export function showRestarts(server) {
    serverId = server.id;
    $('restartRows').innerHTML = '';
    load();
}

async function load() {
    const id = serverId;
    let restarts;
    try {
        ({ restarts } = await api.get(url()));
    } catch {
        return;
    }
    if (id !== serverId) return;
    // The next time in the browser's own time zone; a line set in another zone says which.
    $('restartRows').innerHTML = restarts.map((restart) => `
        <div class="list-row" data-id="${esc(restart.id)}">
            <span class="list-row__main"><b>${esc(restart.cron)}</b>
                <span class="sub">Next: ${esc(formatDate(restart.next))}${restart.timezone === ZONE ? '' : ` · read in ${esc(restart.timezone)} time`}</span></span>
            <button class="cc-icon-btn danger" type="button" data-remove title="Remove" aria-label="Remove ${esc(restart.cron)}"><svg><use href="#i-trash"/></svg></button>
        </div>`).join('') || '<div class="list-row"><span class="list-row__main"><span class="sub">None yet.</span></span></div>';
}

async function add(event) {
    event.preventDefault();
    const button = $('restartAdd');
    button.disabled = true;
    try {
        const restart = await api.post(url(), { cron: $('restartCron').value, timezone: ZONE });
        $('restartCron').value = '';
        toast(`Restarts next at ${esc(formatDate(restart.next))}`);
        load();
    } catch { /* api.js showed why; the line stays to fix */ }
    button.disabled = false;
}

async function remove(restartId) {
    try {
        await api.del(url(restartId));
    } catch {
        return;
    }
    load();
}

export function initRestarts() {
    $('restartZone').textContent = ZONE;
    $('restartForm').addEventListener('submit', add);
    $('restartRows').addEventListener('click', (event) => {
        if (event.target.closest('[data-remove]')) remove(event.target.closest('[data-id]').dataset.id);
    });
}
