/** The Backups tab: a server's backups in one list, newest first, each one a schedule made marked with that
 * schedule; made on demand, downloaded, restored or deleted. */
import * as api from './api.js';
import { formatSize } from './files.js';
import { $, ask, esc, showError, toast } from './ui.js';

let server = null;

const stopped = () => ['stopped', 'crashed'].includes(server.status);
const url = (name = '') => `/api/servers/${server.id}/backups${name ? `/${name}` : ''}`;

const two = (number) => String(number).padStart(2, '0');

/** A Unix time as ISO 8601 in the browser's time zone, with its offset: 2026-10-14T02:49:04+02:00. */
function isoTime(seconds) {
    const date = new Date(seconds * 1000);
    const offset = -date.getTimezoneOffset();
    return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())}T${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}`
        + `${offset < 0 ? '-' : '+'}${two(Math.floor(Math.abs(offset) / 60))}:${two(Math.abs(offset) % 60)}`;
}

export function showBackups(fresh) {
    server = fresh;
    $('backupRows').innerHTML = '';
    load();
}

export function updateBackups(fresh) {
    server = fresh;
    drawRestore();
}

/** Restore works only while the server is stopped: say so on its buttons otherwise. */
function drawRestore() {
    for (const button of $('backupsPage').querySelectorAll('[data-restore]')) {
        button.disabled = !stopped();
        button.title = stopped() ? 'Restore' : 'Stop the server to restore a backup';
    }
}

async function load() {
    const id = server.id;
    let backups, schedules;
    try {
        ({ backups, schedules } = await api.get(url()));
    } catch {
        return;
    }
    if (id !== server.id) return;
    // A scheduled backup is marked with its line's name, or its cron line when it has none.
    const madeBy = (backup) => {
        if (backup.schedule === null) return '';
        const schedule = schedules.find((each) => each.id === backup.schedule);
        const about = schedule ? `Made by the schedule ${schedule.cron}, which keeps its newest ${schedule.keep}`
            : 'Made by a schedule that has since been removed, so nothing deletes it but you';
        return `<span class="made-by${schedule ? '' : ' made-by--removed'}" title="${esc(about)}"><svg class="i"><use href="#i-clock"/></svg>${esc(schedule ? schedule.name ?? schedule.cron : 'Removed schedule')}</span>`;
    };
    $('backupRows').innerHTML = backups.map((backup) => `
        <tr data-name="${esc(backup.name)}" data-label="${esc(isoTime(backup.created))}">
            <td><span class="mono backup-time" title="${esc(backup.name)}">${esc(isoTime(backup.created))}</span>${madeBy(backup)}</td>
            <td class="num muted hide-phone">${esc(formatSize(backup.size))}</td>
            <td class="act">
                <a class="cc-icon-btn" href="${esc(url(backup.name))}" download title="Download" aria-label="Download ${esc(backup.name)}"><svg><use href="#i-download"/></svg></a>
                <button class="cc-icon-btn" type="button" data-restore title="Restore" aria-label="Restore ${esc(backup.name)}"><svg><use href="#i-restore"/></svg></button>
                <button class="cc-icon-btn danger" type="button" data-delete title="Delete" aria-label="Delete ${esc(backup.name)}"><svg><use href="#i-trash"/></svg></button>
            </td>
        </tr>`).join('') || '<tr class="empty-row"><td colspan="3">None yet. Back up now makes one, and the Schedule tab can make them at set times.</td></tr>';
    drawRestore();
}

async function backUp() {
    const button = $('backupNow');
    button.disabled = true;
    button.lastElementChild.textContent = 'Backing up…';
    try {
        const backup = await api.post(url());
        toast(`Backed up at <b>${esc(isoTime(backup.created))}</b>`);
        load();
    } catch { /* api.js showed why */ }
    button.disabled = false;
    button.lastElementChild.textContent = 'Back up now';
}

async function restore(name, label) {
    if (!stopped()) {
        showError('Stop the server before restoring a backup');
        return;
    }
    const done = await ask({ title: 'Restore this backup?', iconName: 'restore', ok: 'Restore', danger: true,
        text: `Everything in ${server.name}'s folder is replaced by the backup taken at ${label}.`,
        check: 'Back up the folder as it is now first',
        action: async (yes, backUpFirst) => {
            if (backUpFirst) await api.post(url());
            await api.post(`${url(name)}/restore`);
        } });
    load(); // with the backup made first, even when the restore then failed
    if (done) toast(`Restored the backup taken at <b>${esc(label)}</b>`);
}

async function remove(name, label) {
    const done = await ask({ title: 'Delete this backup?', iconName: 'archive', ok: 'Delete', danger: true,
        text: `The backup taken at ${label} will be deleted. This can't be undone.`, action: () => api.del(url(name)) });
    if (done) load();
}

export function initBackups() {
    $('backupNow').addEventListener('click', backUp);
    $('backupsPage').addEventListener('click', (event) => {
        const { name, label } = event.target.closest('tr[data-name]')?.dataset ?? {};
        if (event.target.closest('[data-restore]')) restore(name, label);
        else if (event.target.closest('[data-delete]')) remove(name, label);
    });
}
