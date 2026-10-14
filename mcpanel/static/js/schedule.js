/** The Schedule tab: a server's restarts and backups by cron line, each kind's in a table with its add bar under
 * it; when each next happens, add, rename a backup line, remove. */
import * as api from './api.js';
import { $, ask, esc, formatDate, toast } from './ui.js';

const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone; // a new line is read in this time zone
const NEXT = { restart: 'Restarts next at', backup: 'Backs up next at' };
let serverId = '';

const url = (scheduleId = '') => `/api/servers/${serverId}/schedules${scheduleId === '' ? '' : `/${scheduleId}`}`;
const sections = () => document.querySelectorAll('section[data-schedule]');

export function showSchedule(server) {
    serverId = server.id;
    for (const section of sections()) section.querySelector('tbody').innerHTML = '';
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
    for (const section of sections()) {
        const backup = section.dataset.schedule === 'backup';
        section.querySelector('tbody').innerHTML = schedules.filter((schedule) => schedule.action === section.dataset.schedule).map((schedule) => `
            <tr data-id="${esc(schedule.id)}" data-name="${esc(schedule.name ?? '')}">
                ${backup ? `<td>${schedule.name ? esc(schedule.name) : '<span class="unnamed">No name</span>'}</td>` : ''}
                <td class="mono cron">${esc(schedule.cron)}${schedule.timezone === ZONE ? '' : `<span class="cc-note">read in ${esc(schedule.timezone)} time</span>`}</td>
                <td class="muted${backup ? ' hide-phone' : ''}">${esc(formatDate(schedule.next))}</td>
                ${backup ? `<td class="num">${esc(schedule.keep)}</td>` : ''}
                <td class="act">
                    ${backup ? `<button class="cc-icon-btn" type="button" data-rename title="Rename" aria-label="Rename ${esc(schedule.name ?? schedule.cron)}"><svg><use href="#i-edit"/></svg></button>` : ''}
                    <button class="cc-icon-btn danger" type="button" data-remove title="Remove" aria-label="Remove ${esc(schedule.cron)}"><svg><use href="#i-trash"/></svg></button>
                </td>
            </tr>`).join('') || `<tr class="empty-row"><td colspan="${backup ? 5 : 3}">None yet.</td></tr>`;
    }
}

async function add(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const action = form.closest('[data-schedule]').dataset.schedule;
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    try {
        const schedule = await api.post(url(), { action, cron: form.elements.cron.value, timezone: ZONE,
            keep: form.elements.keep?.valueAsNumber, name: form.elements.name?.value });
        form.elements.cron.value = '';
        if (form.elements.name) form.elements.name.value = '';
        toast(`${NEXT[schedule.action]} ${esc(formatDate(schedule.next))}`);
        load();
    } catch { /* api.js showed why; the line stays to fix */ }
    button.disabled = false;
}

async function rename(scheduleId, current) {
    const done = await ask({ title: 'Rename this schedule', iconName: 'edit', value: current, ok: 'Rename',
        text: 'Its backups on the Backups tab show this name.', action: (name) => api.put(url(scheduleId), { name }) });
    if (done) load();
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
    for (const section of sections()) {
        section.querySelector('form').addEventListener('submit', add);
        section.querySelector('tbody').addEventListener('click', (event) => {
            const { id, name } = event.target.closest('[data-id]')?.dataset ?? {};
            if (event.target.closest('[data-rename]')) rename(id, name);
            else if (event.target.closest('[data-remove]')) remove(id);
        });
    }
}
