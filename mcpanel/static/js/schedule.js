/** The Schedule tab: a server's restarts and backups by cron line, when each next happens, add and remove. */
import * as api from './api.js';
import { $, esc, formatDate, toast } from './ui.js';

const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone; // a new line is read in this time zone
const NEXT = { restart: 'Restarts next at', backup: 'Backs up next at' };
let serverId = '';

const url = (scheduleId = '') => `/api/servers/${serverId}/schedules${scheduleId === '' ? '' : `/${scheduleId}`}`;
const forms = () => document.querySelectorAll('form[data-schedule]');

export function showSchedule(server) {
    serverId = server.id;
    for (const form of forms()) form.querySelector('.rows').innerHTML = '';
    load();
}

async function load() {
    const id = serverId;
    let schedules;
    try {
        ({ schedules } = await api.get(url()));
    } catch {
        return;
    }
    if (id !== serverId) return;
    // The next time in the browser's own time zone; a line set in another zone says which.
    for (const form of forms()) {
        form.querySelector('.rows').innerHTML = schedules.filter((schedule) => schedule.action === form.dataset.schedule).map((schedule) => `
            <div class="list-row" data-id="${esc(schedule.id)}">
                <span class="list-row__main"><b>${esc(schedule.cron)}</b>
                    <span class="sub">Next: ${esc(formatDate(schedule.next))}${schedule.timezone === ZONE ? '' : ` · read in ${esc(schedule.timezone)} time`}</span></span>
                <button class="cc-icon-btn danger" type="button" data-remove title="Remove" aria-label="Remove ${esc(schedule.cron)}"><svg><use href="#i-trash"/></svg></button>
            </div>`).join('') || '<div class="list-row"><span class="list-row__main"><span class="sub">None yet.</span></span></div>';
    }
}

async function add(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    try {
        const schedule = await api.post(url(), { action: form.dataset.schedule, cron: form.elements.cron.value, timezone: ZONE });
        form.elements.cron.value = '';
        toast(`${NEXT[schedule.action]} ${esc(formatDate(schedule.next))}`);
        load();
    } catch { /* api.js showed why; the line stays to fix */ }
    button.disabled = false;
}

async function remove(scheduleId) {
    try {
        await api.del(url(scheduleId));
    } catch {
        return;
    }
    load();
}

export function initSchedule() {
    $('scheduleZone').textContent = ZONE;
    for (const form of forms()) {
        form.addEventListener('submit', add);
        form.querySelector('.rows').addEventListener('click', (event) => {
            if (event.target.closest('[data-remove]')) remove(event.target.closest('[data-id]').dataset.id);
        });
    }
}
