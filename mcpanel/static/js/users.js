/**
 * The Users page, for admins: everyone who can sign in, and one dialog that either adds an account
 * or changes a user's role and servers, and shows a new one-time password once.
 */
import * as api from './api.js';
import { state } from './state.js';
import { $, ask, copyText, esc, relativeTime, showView, toast } from './ui.js';

let users = [];
let editing = null; // the user the dialog changes, or null while it adds one

const roleBadge = (role) => (role === 'admin' ? '<span class="cc-badge cc-badge--info">Admin</span>' : '<span class="cc-badge">Member</span>');
const serverName = (id) => state.servers.find((server) => server.id === id)?.name ?? id;

/** What a user may use, in words. */
function serversLine(role, serverIds) {
    if (role === 'admin') return 'Every server';
    if (!serverIds.length) return 'No servers yet';
    return serverIds.map((id) => `<span class="cc-tag">${esc(serverName(id))}</span>`).join(' ');
}

/** Where a user is with signing in: waiting for their first sign-in, or when they were last seen. */
function signInLine(user) {
    const expires = user.one_time_password_expires;
    if (expires === null) return user.last_seen ? `Last seen ${relativeTime(user.last_seen)}` : 'Not signed in on any device';
    if (expires > Date.now() / 1000) return `Waiting for first sign-in · the one-time password expires ${relativeTime(expires)}`;
    return `Waiting for first sign-in · the one-time password expired ${relativeTime(expires)}; reset sign-in for a new one`;
}

export async function showUsers() {
    showView('usersView');
    document.title = 'Users · mcpanel';
    let data;
    try {
        [data] = await Promise.all([api.get('/api/users'), state.firstLoad]); // so a fresh load names servers, not ids
    } catch {
        return;
    }
    users = data.users;
    $('userRows').innerHTML = users.map((user) => `
        <div class="list-row">
            <div class="list-row__main">
                <div class="list-row__title"><b>${esc(user.username)}</b>${roleBadge(user.role)}</div>
                <div class="sub">${serversLine(user.role, user.servers)}</div>
                <div class="sub">${signInLine(user)}</div>
            </div>
            <div class="list-row__actions">${user.username === state.username ? '<span class="cc-tag">You</span>' : `
                <button class="cc-btn cc-btn--ghost cc-btn--sm" type="button" data-action="edit" data-user="${user.id}"><span>Edit</span></button>
                <button class="cc-btn cc-btn--ghost cc-btn--sm" type="button" data-action="reset" data-user="${user.id}"><span>Reset sign-in</span></button>
                <button class="cc-btn cc-btn--danger cc-btn--sm" type="button" data-action="remove" data-user="${user.id}"><span>Remove</span></button>`}
            </div>
        </div>`).join('');
}

/** The servers' checkboxes, with the ones given already ticked; ids Docker no longer lists stay, ticked. */
function drawPicks(given) {
    const listed = state.servers.map((server) => server.id);
    const ids = [...listed, ...given.filter((id) => !listed.includes(id))];
    $('serverPickList').innerHTML = ids.length
        ? ids.map((id) => `<label class="cc-checkbox"><input type="checkbox" value="${esc(id)}"${given.includes(id) ? ' checked' : ''}><span>${esc(serverName(id))}</span></label>`).join('')
        : '<p>No servers yet. You can give them some once there are.</p>';
}

function syncRole() {
    $('serverPicks').hidden = $('userRole').value === 'admin';
}

function openDialog(user = null) {
    editing = user;
    $('userForm').reset();
    $('userForm').hidden = false;
    $('passwordDone').hidden = true;
    $('userTitle').textContent = user ? `Change ${user.username}` : 'Add someone';
    $('userIntro').hidden = false;
    $('userIntro').textContent = user
        ? 'A new role or server list applies to their next click.'
        : 'They get a one-time password to sign in with, then choose their own and set up two-factor sign-in.';
    $('newUsernameField').hidden = Boolean(user);
    $('newUsername').required = !user;
    $('userRole').value = user?.role ?? 'member';
    drawPicks(user?.servers ?? []);
    syncRole();
    $('userSubmit').firstElementChild.textContent = user ? 'Save' : 'Add';
    $('userDialog').showModal();
}

/** Show a new one-time password in the dialog, opening it if need be. */
function showPassword(username, password) {
    $('userTitle').textContent = `One-time password for ${username}`;
    $('userIntro').hidden = true;
    $('oneTimePassword').textContent = password;
    $('userForm').hidden = true;
    $('passwordDone').hidden = false;
    if (!$('userDialog').open) $('userDialog').showModal();
    $('copyPassword').focus();
}

async function submit(event) {
    event.preventDefault();
    const role = $('userRole').value;
    const servers = role === 'member' ? [...$('serverPickList').querySelectorAll('input:checked')].map((box) => box.value) : [];
    const button = $('userSubmit');
    button.disabled = true;
    try {
        if (editing) {
            await api.put(`/api/users/${editing.id}`, { role, servers });
            $('userDialog').close();
            toast(`Saved <b>${esc(editing.username)}</b>`);
        } else {
            const { user, one_time_password: password } = await api.post('/api/users', { username: $('newUsername').value.trim(), role, servers });
            showPassword(user.username, password);
        }
        showUsers();
    } catch { /* api.js showed why; the form stays open to fix */ }
    button.disabled = false;
}

async function reset(user) {
    let password;
    const answer = await ask({
        title: `Reset sign-in for ${user.username}?`,
        iconName: 'key',
        text: 'Their password, two-factor sign-in and recovery codes stop working, and they are signed out everywhere. You get a new one-time password to give them.',
        ok: 'Reset sign-in',
        danger: true,
        action: async () => { ({ one_time_password: password } = await api.post(`/api/users/${user.id}/reset`)); },
    });
    if (!answer) return;
    showPassword(user.username, password);
    showUsers();
}

async function remove(user) {
    const answer = await ask({
        title: `Remove ${user.username}?`,
        iconName: 'users',
        text: 'They are signed out everywhere at once and can no longer sign in. Their servers keep running.',
        ok: 'Remove',
        danger: true,
        action: () => api.del(`/api/users/${user.id}`),
    });
    if (!answer) return;
    toast(`Removed <b>${esc(user.username)}</b>`);
    showUsers();
}

export function initUsers() {
    if (!state.admin) return;
    $('addUserBtn').addEventListener('click', () => openDialog());
    $('userRows').addEventListener('click', (event) => {
        const button = event.target.closest('[data-action]');
        const user = users.find((u) => String(u.id) === button?.dataset.user);
        if (user) ({ edit: openDialog, reset, remove })[button.dataset.action](user);
    });
    $('userRole').addEventListener('change', syncRole);
    $('userForm').addEventListener('submit', submit);
    $('copyPassword').addEventListener('click', () => copyText($('oneTimePassword').textContent, 'the password'));
    $('userDialog').addEventListener('close', () => { $('oneTimePassword').textContent = ''; }); // shown once
}
