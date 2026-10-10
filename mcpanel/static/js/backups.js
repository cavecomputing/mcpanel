/** The Backups tab: a server's backups, newest first, made on demand, downloaded, restored or deleted. */
import * as api from './api.js';
import { formatSize } from './files.js';
import { $, ask, esc, formatDate, showError, toast } from './ui.js';

let server = null;

const stopped = () => ['stopped', 'crashed'].includes(server.status);
const url = (name = '') => `/api/servers/${server.id}/backups${name ? `/${name}` : ''}`;

export function showBackups(fresh) {
    server = fresh;
    $('backupRows').innerHTML = '';
    load();
}

export function updateBackups(fresh) {
    server = fresh;
}

async function load() {
    const id = server.id;
    let backups;
    try {
        ({ backups } = await api.get(url()));
    } catch {
        return;
    }
    if (id !== server.id) return;
    $('backupRows').innerHTML = backups.map((backup) => `
        <tr data-name="${esc(backup.name)}">
            <td class="mono">${esc(backup.name)}</td>
            <td class="num muted">${esc(formatSize(backup.size))}</td>
            <td class="muted hide-phone">${esc(formatDate(backup.created))}</td>
            <td class="act">
                <a class="cc-icon-btn" href="${esc(url(backup.name))}" download title="Download" aria-label="Download ${esc(backup.name)}"><svg><use href="#i-download"/></svg></a>
                <button class="cc-icon-btn" type="button" data-restore title="Restore" aria-label="Restore ${esc(backup.name)}"><svg><use href="#i-restart"/></svg></button>
                <button class="cc-icon-btn danger" type="button" data-delete title="Delete" aria-label="Delete ${esc(backup.name)}"><svg><use href="#i-trash"/></svg></button>
            </td>
        </tr>`).join('') || '<tr class="empty-row"><td colspan="4">No backups yet.</td></tr>';
}

async function backUp() {
    const button = $('backupNow');
    button.disabled = true;
    button.lastElementChild.textContent = 'Backing up…';
    try {
        const backup = await api.post(url());
        toast(`Backed up as <b>${esc(backup.name)}</b>`);
        load();
    } catch { /* api.js showed why */ }
    button.disabled = false;
    button.lastElementChild.textContent = 'Back up now';
}

async function restore(name) {
    if (!stopped()) {
        showError('Stop the server before restoring a backup');
        return;
    }
    const done = await ask({ title: 'Restore this backup?', iconName: 'archive', ok: 'Restore', danger: true,
        text: `Everything in ${server.name}'s folder is replaced by ${name}. Anything since then is lost, unless you back up first.`,
        action: () => api.post(`${url(name)}/restore`) });
    if (done) toast(`Restored <b>${esc(name)}</b>`);
}

async function remove(name) {
    const done = await ask({ title: 'Delete this backup?', iconName: 'archive', ok: 'Delete', danger: true,
        text: `${name} will be deleted. This can't be undone.`, action: () => api.del(url(name)) });
    if (done) load();
}

export function initBackups() {
    $('backupNow').addEventListener('click', backUp);
    $('backupRows').addEventListener('click', (event) => {
        const name = event.target.closest('tr[data-name]')?.dataset.name;
        if (event.target.closest('[data-restore]')) restore(name);
        else if (event.target.closest('[data-delete]')) remove(name);
    });
}
