/**
 * The Players tab: everyone who has joined, who is online, the whitelist, operators and bans. Every
 * change goes to the panel, which sends it as a console command (whitelist add, op, ban, pardon...)
 * while the server runs, so the server makes it and keeps its own lists, and writes it into the
 * list's file while it's stopped. A ban asks for a reason, which the player sees when they try to join.
 */
import * as api from './api.js';
import { $, ask, esc, formatDate, relativeTime, showError, toast } from './ui.js';

const NAME = /^[A-Za-z0-9_]{1,16}$/;
// list -> its columns after the player's, and the add form's button and command
const LISTS = {
    all: { heads: ['Last on'] },
    online: { heads: [] },
    whitelist: { heads: ['Last on'], add: ['Add to whitelist', 'whitelist add'] },
    ops: { heads: ['Last on'], add: ['Make operator', 'op'] },
    banned: { heads: ['Reason', 'Banned'], add: ['Ban', 'ban'] },
};
let server = null;
let list = 'all';
let players = null;
let loading = null;

const running = () => !['stopped', 'crashed'].includes(server.status);
const isOnline = (name) => Boolean(players.online?.includes(name));
const isBanned = (name) => players.banned.some((ban) => ban.name.toLowerCase() === name.toLowerCase());
const button = (label, command, look) => `<button class="cc-btn cc-btn--${look} cc-btn--sm" type="button" data-command="${command}"><span>${label}</span></button>`;

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

/** The player's cell: a letter for a face, the name, and what else they are, leaving out the list's own. */
function playerCell(name) {
    const tags = [
        isOnline(name) && list !== 'online' && '<span class="cc-tag tag-online">online</span>',
        players.ops.includes(name) && list !== 'ops' && '<span class="cc-tag">operator</span>',
        players.whitelist.includes(name) && !['whitelist', 'banned'].includes(list) && '<span class="cc-tag">whitelisted</span>',
        list === 'all' && isBanned(name) && '<span class="cc-tag tag-banned">banned</span>',
    ].filter(Boolean).join('');
    return `<td><span class="player"><span class="face" aria-hidden="true">${esc(name[0].toUpperCase())}</span><span class="mono">${esc(name)}</span>${tags}</span></td>`;
}

function lastOn(name) {
    if (isOnline(name)) return '<td class="muted hide-phone">Now</td>';
    const at = players.all.find((player) => player.name.toLowerCase() === name.toLowerCase())?.last_on;
    return at ? `<td class="muted hide-phone" title="${esc(formatDate(at))}">${relativeTime(at)}</td>` : '<td class="muted hide-phone">Never</td>';
}

function row(entry) {
    const name = entry.name ?? entry;
    let cells;
    if (list === 'banned') {
        const when = [entry.created && formatDate(entry.created), entry.by && `by ${esc(entry.by)}`].filter(Boolean).join('<br>');
        cells = `<td>${esc(entry.reason)}</td><td class="muted hide-phone">${when}</td><td class="act">${button('Pardon', 'pardon', 'ghost')}</td>`;
    } else if (list === 'online') {
        cells = `<td class="act">${players.ops.includes(name) ? '' : button('Op', 'op', 'ghost')}${button('Kick', 'kick', 'danger')}${button('Ban…', 'ban', 'danger')}</td>`;
    } else {
        const action = { all: isBanned(name) ? button('Pardon', 'pardon', 'ghost') : button('Ban…', 'ban', 'danger'),
            whitelist: button('Remove', 'whitelist remove', 'danger'), ops: button('Remove operator', 'deop', 'danger') }[list];
        cells = `${lastOn(name)}<td class="act">${action}</td>`;
    }
    return `<tr data-name="${esc(name)}">${playerCell(name)}${cells}</tr>`;
}

function draw() {
    for (const tab of document.querySelectorAll('[data-list]')) tab.setAttribute('aria-pressed', tab.dataset.list === list);
    for (const count of document.querySelectorAll('[data-count]')) count.textContent = players?.[count.dataset.count]?.length ?? '';
    const { heads, add } = LISTS[list];
    $('addPlayerForm').hidden = !add;
    if (add) $('addPlayerBtn').lastElementChild.textContent = add[0];
    $('addPlayerBtn').className = `cc-btn cc-btn--${list === 'banned' ? 'danger' : 'primary'} cc-btn--sm`;
    $('addPlayerReason').required = !($('addPlayerReason').hidden = list !== 'banned');
    $('playersHead').innerHTML = ['<th>Player</th>', ...heads.map((head) => `<th${head === 'Reason' ? '' : ' class="hide-phone"'}>${head}</th>`),
        '<th><span class="sr">Actions</span></th>'].join('');
    $('playersNote').hidden = running() || list === 'online';
    $('playersNote').textContent = 'The server is stopped: changes go into its saved lists, which it reads when it starts.';

    let rows = players?.[list];
    if (list === 'all' && rows) rows = [...rows].sort((a, b) => isOnline(b.name) - isOnline(a.name));
    const columns = heads.length + 2;
    let html;
    if (!players) html = `<tr class="empty-row"><td colspan="${columns}">Loading…</td></tr>`;
    else if (rows === null) html = `<tr class="empty-row"><td colspan="${columns}">${running() ? "The server can't say who is online until it has started." : "It's not running, so nobody can join."}</td></tr>`;
    else if (!rows.length) html = `<tr class="empty-row"><td colspan="${columns}">${{ all: 'Nobody has joined yet.', online: 'Nobody is online.' }[list] ?? 'Nobody here yet.'}</td></tr>`;
    else html = rows.map(row).join('');
    $('playerRows').innerHTML = html;
}

/** Make a change, say what the server said, and load the lists it rewrote. Throws when it fails. */
async function change(command, name, reason) {
    const { response } = command === 'kick'
        ? await api.post(`/api/servers/${server.id}/console`, { command: `kick ${name}` })
        : await api.post(`/api/servers/${server.id}/players`, { command, name, reason });
    toast(esc(response || `Done: ${command} ${name}`));
    setTimeout(load, 300); // the server writes its lists as it runs the command
}

function askToBan(name) {
    const whitelisted = players.whitelist.includes(name);
    ask({
        title: `Ban ${name}?`,
        iconName: 'users',
        text: `${isOnline(name) ? "They're disconnected now, and" : 'They'} can't join again until pardoned. They see the reason when they try to.`,
        value: '',
        placeholder: 'Reason',
        check: whitelisted ? 'Take them off the whitelist too' : null,
        checked: false,
        ok: 'Ban',
        danger: true,
        action: async (reason, unlist) => {
            await change('ban', name, reason);
            if (whitelisted && unlist) await change('whitelist remove', name).catch(() => {});
        },
    });
}

export function initPlayers() {
    for (const tab of document.querySelectorAll('[data-list]')) {
        tab.addEventListener('click', () => {
            list = tab.dataset.list;
            draw();
        });
    }
    $('playerRows').addEventListener('click', (event) => {
        const { command } = event.target.closest('button[data-command]')?.dataset ?? {};
        if (!command) return;
        const { name } = event.target.closest('tr').dataset;
        if (command === 'ban') askToBan(name);
        else if (command === 'kick') {
            ask({ title: `Kick ${name}?`, iconName: 'users', text: `${name} is disconnected, and can join again.`, ok: 'Kick', danger: true })
                .then((yes) => yes && change(command, name).catch(() => {}));
        } else change(command, name).catch(() => {});
    });
    $('addPlayerForm').addEventListener('submit', (event) => {
        event.preventDefault();
        const name = $('addPlayerName').value.trim();
        if (!NAME.test(name)) {
            showError('A player name is 1 to 16 letters, digits or _');
            return;
        }
        change(LISTS[list].add[1], name, list === 'banned' ? $('addPlayerReason').value : undefined).then(() => {
            $('addPlayerName').value = '';
            $('addPlayerReason').value = '';
        }, () => {});
    });
}
