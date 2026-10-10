/**
 * The Files tab: a server's folder, one level at a time, with the path as crumbs; new folders and
 * files, uploads, downloads, rename and delete; and an editor for text files.
 */
import * as api from './api.js';
import { $, ask, esc, formatDate, showError, toast } from './ui.js';

const MAX_EDIT = 1024 * 1024; // what the server opens in the editor; bigger files are downloaded
let serverId = '';
let path = '';      // the folder shown, '' for the server's own
let entries = [];
let editing = null; // the path of the file in the editor

const join = (...parts) => parts.filter(Boolean).join('/');
const url = (what, filePath) => `/api/servers/${serverId}/files${what}?${new URLSearchParams({ path: filePath })}`;

/** A size in bytes as "12.3 MB". */
export function formatSize(bytes) {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let unit = 0;
    while (bytes >= 1024 && unit < units.length - 1) {
        bytes /= 1024;
        unit += 1;
    }
    return `${unit ? bytes.toFixed(bytes < 10 ? 1 : 0) : bytes} ${units[unit]}`;
}

export function showFiles(server) {
    if (server.id !== serverId) {
        serverId = server.id;
        path = '';
    }
    closeEditor();
    load();
}

async function load() {
    const id = serverId;
    let data;
    try {
        data = await api.get(url('', path));
    } catch {
        return;
    }
    if (id !== serverId) return;
    entries = data.entries;
    drawCrumbs();
    const icon = { dir: 'folder', link: 'link' };
    $('fileRows').innerHTML = entries.map((entry) => {
        const name = `<svg class="i"><use href="#i-${icon[entry.type] ?? 'file'}"/></svg>${esc(entry.name)}`;
        const opens = entry.type === 'dir' || entry.type === 'file';
        return `
        <tr data-name="${esc(entry.name)}" data-type="${esc(entry.type)}">
            <td>${opens ? `<button class="fname fname--${esc(entry.type)}" type="button" data-open>${name}</button>`
                : `<span class="fname" title="${entry.type === 'link' ? "A link, which the panel doesn't follow" : 'Not a file the panel opens'}">${name}</span>`}</td>
            <td class="num muted">${entry.size === null ? '' : esc(formatSize(entry.size))}</td>
            <td class="muted hide-phone">${esc(formatDate(entry.modified))}</td>
            <td class="act">
                ${entry.type === 'file' ? `<a class="cc-icon-btn" href="${esc(url('/download', join(path, entry.name)))}" download title="Download" aria-label="Download ${esc(entry.name)}"><svg><use href="#i-download"/></svg></a>` : ''}
                <button class="cc-icon-btn" type="button" data-rename title="Rename or move" aria-label="Rename ${esc(entry.name)}"><svg><use href="#i-edit"/></svg></button>
                <button class="cc-icon-btn danger" type="button" data-delete title="Delete" aria-label="Delete ${esc(entry.name)}"><svg><use href="#i-trash"/></svg></button>
            </td>
        </tr>`;
    }).join('') || '<tr class="empty-row"><td colspan="4">This folder is empty.</td></tr>';
}

function drawCrumbs() {
    const parts = path ? path.split('/') : [];
    $('crumbs').innerHTML = `<button type="button" data-path="">${esc(serverId)}</button>`
        + parts.map((part, i) => `<span>/</span><button type="button" data-path="${esc(parts.slice(0, i + 1).join('/'))}">${esc(part)}</button>`).join('');
}

function openFolder(folder) {
    path = folder;
    closeEditor();
    load();
}

async function openFile(name, size) {
    const filePath = join(path, name);
    if (size > MAX_EDIT) {
        location.assign(url('/download', filePath));
        return;
    }
    let text;
    try {
        ({ text } = await api.get(url('/content', filePath)));
    } catch {
        return;
    }
    editing = filePath;
    $('editorName').textContent = filePath;
    $('editorText').value = text;
    $('fileList').hidden = true;
    $('editor').hidden = false;
    $('editorText').focus();
}

function closeEditor() {
    editing = null;
    $('editor').hidden = true;
    $('fileList').hidden = false;
    $('editorText').value = '';
}

async function save() {
    if (!editing) return;
    const button = $('editorSave');
    button.disabled = true;
    try {
        await api.upload(url('/content', editing), $('editorText').value);
        toast(`Saved <b>${esc(editing)}</b>`);
    } catch { /* api.js showed why */ }
    button.disabled = false;
}

/** A name for something new in this folder, from the question dialog; then what action does with its path. */
function askName(title, iconName, ok, action) {
    return ask({ title, iconName, value: '', ok, action: (name) => {
        if (name.includes('/')) {
            showError("Names can't contain /");
            throw new Error('slash');
        }
        return action(join(path, name.trim()));
    } });
}

async function newFolder() {
    if (await askName('New folder', 'folder', 'Create', (folder) => api.post(`/api/servers/${serverId}/files/folder`, { path: folder })) !== null) load();
}

async function newFile() {
    const name = await askName('New file', 'file', 'Create', async (filePath) => {
        if (entries.some((entry) => join(path, entry.name) === filePath)) {
            showError('Something with that name is already here');
            throw new Error('exists');
        }
        await api.upload(url('/content', filePath), '');
    });
    if (name === null) return;
    await load();
    openFile(name.trim(), 0);
}

async function uploadFiles(fileList) {
    for (const file of fileList) {
        if (entries.some((entry) => entry.name === file.name)) {
            const replace = await ask({ title: 'Replace it?', iconName: 'upload', text: `${file.name} is already in this folder. Upload this one in its place?`, ok: 'Replace', danger: true });
            if (!replace) continue;
        }
        toast(`Uploading <b>${esc(file.name)}</b>…`);
        try {
            await api.upload(url('/content', join(path, file.name)), file);
            toast(`Uploaded <b>${esc(file.name)}</b>`);
        } catch { /* api.js showed why */ }
    }
    load();
}

async function rename(name) {
    const from = join(path, name);
    const to = await ask({ title: 'Rename', iconName: 'file', text: 'A new name, or a path from the server folder to move it, like plugins/old/' + name, value: from, ok: 'Rename',
        action: (target) => api.post(`/api/servers/${serverId}/files/rename`, { path: from, to: target.trim() }) });
    if (to !== null) load();
}

async function remove(name, type) {
    const target = join(path, name);
    const done = await ask({ title: `Delete ${type === 'dir' ? 'folder' : 'file'}?`, iconName: type === 'dir' ? 'folder' : 'file',
        text: type === 'dir' ? `${target} and everything in it will be deleted. This can't be undone.` : `${target} will be deleted. This can't be undone.`,
        ok: 'Delete', danger: true, action: () => api.del(url('', target)) });
    if (done) {
        toast(`Deleted <b>${esc(target)}</b>`);
        load();
    }
}

export function initFiles() {
    $('crumbs').addEventListener('click', (event) => {
        const crumb = event.target.closest('[data-path]');
        if (crumb) openFolder(crumb.dataset.path);
    });
    $('fileRows').addEventListener('click', (event) => {
        const row = event.target.closest('tr[data-name]');
        if (!row) return;
        const { name, type } = row.dataset;
        if (event.target.closest('[data-open]')) {
            if (type === 'dir') openFolder(join(path, name));
            else openFile(name, entries.find((entry) => entry.name === name)?.size ?? 0);
        } else if (event.target.closest('[data-rename]')) rename(name);
        else if (event.target.closest('[data-delete]')) remove(name, type);
    });
    $('newFolderBtn').addEventListener('click', newFolder);
    $('newFileBtn').addEventListener('click', newFile);
    $('uploadBtn').addEventListener('click', () => $('uploadInput').click());
    $('uploadInput').addEventListener('change', async (event) => {
        await uploadFiles([...event.target.files]);
        event.target.value = '';
    });
    $('editorClose').addEventListener('click', closeEditor);
    $('editorSave').addEventListener('click', save);
    $('editorText').addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === 's') {
            event.preventDefault();
            save();
        }
    });
}
