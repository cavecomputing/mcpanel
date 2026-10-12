/**
 * The Files tab: a server's folder, one level at a time, with the path as crumbs; new folders and
 * files, uploads (the button, or files dropped on the tab) with their progress, downloads, rename
 * and delete; and an editor for text files.
 */
import * as api from './api.js';
import { serverHash } from './state.js';
import { $, ask, esc, formatDate, showError, toast } from './ui.js';

const MAX_EDIT = 1024 * 1024; // what the server opens in the editor; bigger files are downloaded
let serverId = '';
let path = '';      // the folder shown, '' for the server's own
let entries = [];
let editing = null; // the path of the file in the editor
let original = '';  // its text as opened or last saved, to tell when there are changes to lose
let uploading = false;

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

/** Open the tab. Coming back to the same server's files keeps a file open in the editor. */
export async function showFiles(server) {
    if (server.id === serverId && editing) {
        load();
        return;
    }
    if (server.id !== serverId) {
        if (!await mayCloseEditor()) {
            location.replace(serverHash(serverId, 'files')); // back to the file with the changes
            return;
        }
        serverId = server.id;
        path = '';
    }
    closeEditor();
    load();
}

const unsaved = () => editing !== null && $('editorText').value !== original;

/** Whether the editor may close: nothing in it is unsaved, or the user says to discard it. */
async function mayCloseEditor() {
    if (!unsaved()) return true;
    return Boolean(await ask({ title: 'Discard your changes?', iconName: 'file', ok: 'Discard', danger: true,
        text: `Your changes to ${editing} aren't saved, and will be lost.` }));
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
    }).join('') || '<tr class="empty-row"><td colspan="4">This folder is empty. Drop files here to upload them.</td></tr>';
}

function drawCrumbs() {
    const parts = path ? path.split('/') : [];
    $('crumbs').innerHTML = `<button type="button" data-path="">${esc(serverId)}</button>`
        + parts.map((part, i) => `<span>/</span><button type="button" data-path="${esc(parts.slice(0, i + 1).join('/'))}">${esc(part)}</button>`).join('');
}

async function openFolder(folder) {
    if (!await mayCloseEditor()) return;
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
    original = text;
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
    const text = $('editorText').value;
    button.disabled = true;
    try {
        await api.upload(url('/content', editing), text);
        original = text;
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

/** Upload files into the folder shown, one at a time, with the progress of each under the toolbar. */
async function uploadFiles(fileList) {
    if (uploading || !fileList.length) return;
    uploading = true;
    $('uploadBtn').disabled = true;
    const folder = path; // where they go, even if another folder is opened meanwhile
    for (const [i, file] of fileList.entries()) {
        if (folder === path && entries.some((entry) => entry.name === file.name)) {
            const replace = await ask({ title: 'Replace it?', iconName: 'upload', text: `${file.name} is already in this folder. Upload this one in its place?`, ok: 'Replace', danger: true });
            if (!replace) continue;
        }
        const name = fileList.length > 1 ? `${file.name} (${i + 1} of ${fileList.length})` : file.name;
        drawUpload(name, 0);
        try {
            await api.uploadWithProgress(url('/content', join(folder, file.name)), file, (sent) => drawUpload(name, sent));
            toast(`Uploaded <b>${esc(file.name)}</b>`);
        } catch { /* api.js showed why */ }
    }
    $('uploadStatus').hidden = true;
    $('uploadBtn').disabled = false;
    uploading = false;
    load();
}

function drawUpload(name, sent) {
    $('uploadStatus').hidden = false;
    $('uploadName').textContent = `Uploading ${name}`;
    $('uploadBar').style.width = `${sent * 100}%`;
    $('uploadPercent').textContent = `${Math.floor(sent * 100)}%`;
}

/** Files dropped on the tab are uploaded; a folder can't be, so it says to zip it instead. */
function initDrop() {
    const panel = document.querySelector('[data-tab-panel="files"]');
    const dropping = (on) => $('fileList').classList.toggle('dropping', on);
    panel.addEventListener('dragover', (event) => {
        if (!event.dataTransfer.types.includes('Files') || editing) return;
        event.preventDefault();
        dropping(true);
    });
    panel.addEventListener('dragleave', (event) => !panel.contains(event.relatedTarget) && dropping(false));
    panel.addEventListener('drop', (event) => {
        event.preventDefault();
        dropping(false);
        const items = [...event.dataTransfer.items].map((item) => [item.webkitGetAsEntry(), item.getAsFile()]);
        if (items.some(([entry]) => entry?.isDirectory)) showError("Folders can't be dropped here; upload a .zip of it and extract it");
        uploadFiles(items.filter(([entry, file]) => file && !entry?.isDirectory).map(([, file]) => file));
    });
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
    initDrop();
    $('uploadInput').addEventListener('change', async (event) => {
        await uploadFiles([...event.target.files]);
        event.target.value = '';
    });
    $('editorClose').addEventListener('click', async () => (await mayCloseEditor()) && closeEditor());
    $('editorSave').addEventListener('click', save);
    $('editorText').addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === 's') {
            event.preventDefault();
            save();
        }
    });
    window.addEventListener('beforeunload', (event) => unsaved() && event.preventDefault());
}
