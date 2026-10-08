/**
 * The Users page, for admins: everyone who can sign in, the invites not used yet, and one dialog that
 * either invites someone or changes a user's role and servers.
 */
import * as api from './api.js';
import { state } from './state.js';
import { $, ask, copyText, esc, relativeTime, showView, toast } from './ui.js';

let users = [];
let editing = null; // the user the dialog changes, or null while it invites

const roleBadge = (role) => (role === 'admin' ? '<span class="cc-badge cc-badge--info">Admin</span>' : '<span class="cc-badge">Member</span>');
const serverName = (id) => state.servers.find((server) => server.id === id)?.name ?? id;

/** What a user or invite may use, in words. */
function serversLine(role, serverIds) {
    if (role === 'admin') return 'Every server';
    if (!serverIds.length) return 'No servers yet';
    return serverIds.map((id) => `<span class="cc-tag">${esc(serverName(id))}</span>`).join(' ');
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
                <div class="sub">${user.last_seen ? `Last seen ${relativeTime(user.last_seen)}` : 'Not signed in on any device'}</div>
            </div>
            <div class="list-row__actions">${user.username === state.username ? '<span class="cc-tag">You</span>' : `
                <button class="cc-btn cc-btn--ghost cc-btn--sm" type="button" data-edit="${user.id}"><span>Edit</span></button>
                <button class="cc-btn cc-btn--danger cc-btn--sm" type="button" data-remove="${user.id}"><span>Remove</span></button>`}
            </div>
        </div>`).join('');
    $('invitesCard').hidden = !data.invites.length;
    $('inviteRows').innerHTML = data.invites.map((invite) => `
        <div class="list-row">
            <div class="list-row__main">
                <div class="list-row__title"><b>${esc(invite.username)}</b>${roleBadge(invite.role)}</div>
                <div class="sub">${serversLine(invite.role, invite.servers)}</div>
                <div class="sub">Expires ${relativeTime(invite.expires)}</div>
            </div>
            <div class="list-row__actions"><button class="cc-btn cc-btn--danger cc-btn--sm" type="button" data-revoke="${esc(invite.username)}"><span>Revoke</span></button></div>
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
    $('inviteDone').hidden = true;
    $('userTitle').textContent = user ? `Change ${user.username}` : 'Invite someone';
    $('userIntro').hidden = false;
    $('userIntro').textContent = user
        ? 'A new role or server list applies to their next click.'
        : 'They get a one-time link to choose a password and set up two-factor sign-in.';
    $('inviteNameField').hidden = Boolean(user);
    $('inviteName').required = !user;
    $('userRole').value = user?.role ?? 'member';
    drawPicks(user?.servers ?? []);
    syncRole();
    $('userSubmit').firstElementChild.textContent = user ? 'Save' : 'Create link';
    $('userDialog').showModal();
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
            const username = $('inviteName').value.trim().toLowerCase();
            const { link } = await api.post('/api/users/invite', { username, role, servers });
            $('userTitle').textContent = `Invite link for ${username}`;
            $('userIntro').hidden = true;
            $('inviteLink').textContent = location.origin + link;
            $('userForm').hidden = true;
            $('inviteDone').hidden = false;
            $('copyInvite').focus();
        }
        showUsers();
    } catch { /* api.js showed why; the form stays open to fix */ }
    button.disabled = false;
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

async function revoke(username) {
    try {
        await api.del(`/api/users/invites/${encodeURIComponent(username)}`);
    } catch {
        return;
    }
    toast(`Revoked the invite for <b>${esc(username)}</b>`);
    showUsers();
}

export function initUsers() {
    if (!state.admin) return;
    $('inviteBtn').addEventListener('click', () => openDialog());
    $('userRows').addEventListener('click', (event) => {
        const button = event.target.closest('[data-edit], [data-remove]');
        const user = users.find((u) => String(u.id) === (button?.dataset.edit ?? button?.dataset.remove));
        if (user) (button.dataset.edit ? openDialog : remove)(user);
    });
    $('inviteRows').addEventListener('click', (event) => {
        const button = event.target.closest('[data-revoke]');
        if (button) revoke(button.dataset.revoke);
    });
    $('userRole').addEventListener('change', syncRole);
    $('userForm').addEventListener('submit', submit);
    $('copyInvite').addEventListener('click', () => copyText($('inviteLink').textContent, 'the link'));
    $('userDialog').addEventListener('close', () => { $('inviteLink').textContent = ''; }); // shown once
}
