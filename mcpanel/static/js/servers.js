/**
 * Servers: the sidebar list (a drawer on phones), polled every 5 seconds while the page is in view;
 * the open server's header, Start/Stop/Restart and Overview; the empty states; the new-server dialog.
 */
import * as api from './api.js';
import { route, serverHash, state } from './state.js';
import { $, copyText, esc, formatDate, setHtml, showError, showView, toast } from './ui.js';

const POLL_MS = 5000;
const STATUS = { running: 'Running', starting: 'Starting', restarting: 'Restarting', unresponsive: 'Not responding', stopped: 'Stopped', crashed: 'Crashed' };
const BADGE = { running: 'cc-badge--done', starting: 'cc-badge--open', restarting: 'cc-badge--open', unresponsive: 'cc-badge--warn', crashed: 'cc-badge--warn' };
const TYPE_NAMES = { VANILLA: 'Vanilla', PAPER: 'Paper', PURPUR: 'Purpur', FABRIC: 'Fabric', FORGE: 'Forge', NEOFORGE: 'NeoForge', QUILT: 'Quilt' };
// Each action's button: the statuses it works from, and what it says while it runs and when it's done.
const ACTIONS = {
    start: { label: 'Start', when: ['stopped', 'crashed'], doing: 'Starting…', done: 'Started' },
    stop: { label: 'Stop', when: ['running', 'starting', 'restarting', 'unresponsive'], doing: 'Stopping…', done: 'Stopped' },
    restart: { label: 'Restart', when: ['running', 'starting', 'unresponsive'], doing: 'Restarting…', done: 'Restarted' },
};
let latest = 0;     // only the newest list gets drawn
let polling = null; // the poll's list call while it runs: one at a time, so a slow Docker's answers still get drawn
let options = null; // /api/servers/options, fetched when a form first needs it
let shownTab = '';  // '<server id>/<tab>' last opened, so a poll redraws a tab without reloading it
// tab -> what it does when it opens (show) and when a poll brings the server's fresh dict (update).
const TAB_VIEWS = { overview: { show: loadStats, update: loadStats } };
let stats = null;   // the Overview's stats call while it runs

/** Give a tab its view: main.js does, so the tabs' modules can use this one. */
export function addTab(tab, view) {
    TAB_VIEWS[tab] = view;
}

export const typeName = (type) => TYPE_NAMES[type] ?? type;
const versionName = (version) => (version === 'LATEST' ? 'Latest' : version);
export const javaName = (java) => java.replace(/^java/, 'Java ');
const statusName = (status) => STATUS[status] ?? status;
const serverWithId = (id) => state.servers.find((server) => server.id === id);

/** Fetch the list and redraw what shows it. Quiet, since a failed poll tries again in 5 seconds. */
async function loadServers() {
    const request = ++latest;
    try {
        const { servers } = await api.get('/api/servers', { quiet: true });
        if (request !== latest) return;
        state.servers = servers;
        state.dockerError = null;
    } catch (error) {
        if (request !== latest) return;
        if (error.status !== 503) {
            if (!state.loaded) showError(error.message); // the first try: say why nothing shows
            return; // otherwise keep what's on screen
        }
        state.servers = [];
        state.dockerError = error.message;
    }
    state.loaded = true;
    drawSidebar();
    const where = route();
    if (where.page === 'server') showServer(where.id);
    else if (where.page === 'home') showHome();
}

/** The poll: load the list unless the last poll's call hasn't come back yet. Also for a change that moves the list. */
export function poll() {
    polling ??= loadServers().finally(() => { polling = null; });
    return polling;
}

/** The sidebar's server rows, each with its status in a dot and in words. Keeps focus on the row it was on. */
export function drawSidebar() {
    const nav = $('serverNav');
    const { id: current, tab } = route(); // other servers open on the same tab
    const focused = document.activeElement.closest('#serverNav [data-id]')?.dataset.id;
    let html = '';
    if (state.dockerError) html = `<p class="side-note">Can't reach Docker</p>`;
    else if (state.loaded && !state.servers.length) html = '<p class="side-note">No servers yet</p>';
    else html = state.servers.map((server) => `
        <a class="cc-shell__row server-row" href="${esc(serverHash(server.id, tab))}" data-id="${esc(server.id)}"${server.id === current ? ' aria-current="page"' : ''}>
            <span class="dot dot--${esc(server.status)}"></span><span class="server-row__name">${esc(server.name)}</span>
            <span class="server-row__meta">${esc(statusName(server.status))} · ${esc(typeName(server.type))} ${esc(versionName(server.version))}</span>
        </a>`).join('');
    setHtml(nav, html);
    if (focused) [...nav.querySelectorAll('[data-id]')].find((row) => row.dataset.id === focused)?.focus();
}

/** Home: the first server, or why there is none to show. */
export function showHome() {
    document.title = 'mcpanel';
    if (state.loaded && !state.dockerError && state.servers.length) {
        location.replace(serverHash(state.servers[0].id));
        return;
    }
    const none = state.admin ? 'No servers yet. Make the first one.' : "An admin hasn't given you a server yet.";
    drawHome(state.loaded && !state.dockerError ? none : '');
}

/** The home view with text, or with nothing while the list loads; the Docker callout when it can't be reached. */
function drawHome(text, offerNew = true) {
    showView('homeView');
    $('dockerError').hidden = !state.dockerError;
    $('dockerErrorText').textContent = state.dockerError ?? '';
    $('homeEmpty').hidden = !text;
    $('homeText').textContent = text;
    if ($('homeNew')) $('homeNew').hidden = !offerNew;
}

export function showServer(id) {
    const server = serverWithId(id);
    if (!server) {
        shownTab = '';
        document.title = 'mcpanel';
        const gone = "There's no server here. It may have been removed, or you may no longer have access to it.";
        drawHome(state.loaded && !state.dockerError ? gone : '', false);
        return;
    }
    showView('serverView');
    drawServer(server);
    showTab(server);
}

/** The tab the address names: its link marked, its page shown, and opened when it wasn't open already. */
function showTab(server) {
    const { tab } = route();
    for (const link of $('serverTabs').querySelectorAll('[data-tab]')) {
        link.href = serverHash(server.id, link.dataset.tab);
        if (link.dataset.tab === tab) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
    }
    for (const panel of $('serverView').querySelectorAll('[data-tab-panel]')) panel.hidden = panel.dataset.tabPanel !== tab;
    const view = TAB_VIEWS[tab];
    if (shownTab === `${server.id}/${tab}`) {
        view.update?.(server);
        return;
    }
    shownTab = `${server.id}/${tab}`;
    view.show?.(server);
}

/** The server view's header and Overview, changing only what changed, so a poll keeps focus and selection. */
function drawServer(server) {
    document.title = `${server.name} · mcpanel`;
    $('serverName').textContent = server.name;
    $('serverBadge').className = `cc-badge ${BADGE[server.status] ?? ''}`;
    $('serverBadge').textContent = statusName(server.status);
    $('serverAddress').textContent = server.address;
    const tags = [typeName(server.type), versionName(server.version), javaName(server.java), `${server.heap_gb} GB`];
    setHtml($('serverTags'), tags.map((tag) => `<span class="cc-tag">${esc(tag)}</span>`).join(''));
    const busy = state.busy.get(server.id);
    for (const button of $('serverView').querySelectorAll('[data-action]')) {
        const action = ACTIONS[button.dataset.action];
        button.disabled = Boolean(busy) || !action.when.includes(server.status);
        button.lastElementChild.textContent = busy === button.dataset.action ? action.doing : action.label;
    }
    setHtml($('serverDetails'), [
        ['Address', `<code class="cc-code">${esc(server.address)}</code>`],
        ['Port', esc(server.port)],
        ['Type', esc(typeName(server.type))],
        ['Version', esc(versionName(server.version))],
        ['Java', esc(javaName(server.java))],
        ['Memory', `${esc(server.heap_gb)} GB`],
        ['Created', esc(formatDate(server.created))],
    ].map(([term, value]) => `<dt>${term}</dt><dd>${value}</dd>`).join(''));
}

/** The Overview's players, memory and CPU, fetched on opening it and on each poll: a dash while it can't say. */
function loadStats(server) {
    if (['stopped', 'crashed'].includes(server.status)) {
        drawStats(server, null);
        return;
    }
    stats ??= api.get(`/api/servers/${server.id}/stats`, { quiet: true })
        .then((data) => route().id === server.id && drawStats(server, data))
        .catch(() => {})
        .finally(() => { stats = null; });
}

function drawStats(server, data) {
    const gb = (bytes) => (bytes / 1024 ** 3).toFixed(1);
    const usage = data?.usage;
    setHtml($('statPlayers'), data?.players == null ? '–' : `${esc(data.players)} <small>/ ${esc(data.max_players)}</small>`);
    setHtml($('statMemory'), usage ? `${gb(usage.memory)} <small>/ ${gb(usage.memory_limit)} GB</small>` : '–');
    setHtml($('statCpu'), usage ? `${esc(usage.cpu)}<small>% of a core</small>` : '–');
}

/** Start, stop or restart the open server. Stopping can take up to a minute while the world saves. */
async function runAction(action) {
    const { id } = route();
    if (!serverWithId(id) || state.busy.has(id)) return;
    state.busy.set(id, action);
    showServer(id);
    try {
        const fresh = await api.post(`/api/servers/${id}/${action}`);
        state.servers = state.servers.map((server) => (server.id === id ? fresh : server));
        toast(`${ACTIONS[action].done} <b>${esc(fresh.name)}</b>`);
    } catch { /* api.js showed why */ }
    state.busy.delete(id);
    drawSidebar();
    if (route().id === id) showServer(id);
}

/** The type, Java and memory choices, fetched once, filled into a form's selects and memory field. */
export async function fillOptions(typeSelect, javaSelect, heapInput) {
    options ??= await api.get('/api/servers/options');
    typeSelect.innerHTML = options.types.map((type) => `<option value="${esc(type)}">${esc(typeName(type))}</option>`).join('');
    // Empty leaves it to the panel, which picks the Java the version needs.
    javaSelect.innerHTML = '<option value="">Match the version</option>'
        + options.java.map((java) => `<option value="${esc(java)}">${esc(javaName(java))}</option>`).join('');
    Object.assign(heapInput, { min: options.heap.min, max: options.heap.max, defaultValue: options.heap.default });
}

async function openNewServer() {
    try {
        await fillOptions($('serverType'), $('serverJava'), $('serverHeap'));
    } catch {
        return;
    }
    $('newServerForm').reset();
    $('createServer').disabled = true;
    $('newServerDialog').showModal();
}

async function createServer(event) {
    event.preventDefault();
    const button = $('createServer');
    button.disabled = true;
    button.lastElementChild.textContent = 'Creating…';
    try {
        const server = await api.post('/api/servers', {
            name: $('serverNameInput').value,
            type: $('serverType').value,
            version: $('serverVersion').value,
            java: $('serverJava').value,
            heap_gb: Number($('serverHeap').value),
            eula: $('serverEula').checked,
        });
        $('newServerDialog').close();
        toast(`Created <b>${esc(server.name)}</b>. Start it when it's ready.`);
        state.servers = [...state.servers, server]; // so it opens before the next poll
        location.hash = serverHash(server.id);
        loadServers();
    } catch { /* api.js showed why; the form stays open to fix */ }
    button.disabled = !$('serverEula').checked;
    button.lastElementChild.textContent = 'Create';
}

function setDrawer(open) {
    $('side').classList.toggle('open', open);
    $('sideBtn').setAttribute('aria-expanded', open);
    if (open) $('sideClose').focus();
}

export function initServers() {
    $('sideBtn').addEventListener('click', () => setDrawer(true));
    $('sideClose').addEventListener('click', () => {
        setDrawer(false);
        $('sideBtn').focus();
    });
    $('scrim').addEventListener('click', () => setDrawer(false));
    $('side').addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !$('side').classList.contains('open')) return;
        setDrawer(false);
        $('sideBtn').focus();
    });
    // A link closes the drawer even when it leads where the page already is, which changes no hash.
    $('side').addEventListener('click', (event) => event.target.closest('a[href], [data-new-server]') && setDrawer(false));
    window.addEventListener('hashchange', () => {
        setDrawer(false);
        if (route().page !== 'server') shownTab = ''; // coming back opens the tab afresh
    });

    $('serverView').addEventListener('click', (event) => {
        const action = event.target.closest('[data-action]')?.dataset.action;
        if (action) runAction(action);
    });
    $('copyAddress').addEventListener('click', () => copyText(serverWithId(route().id)?.address ?? '', 'the address'));

    if (state.admin) {
        for (const button of document.querySelectorAll('[data-new-server]')) button.addEventListener('click', openNewServer);
        $('serverEula').addEventListener('change', (event) => { $('createServer').disabled = !event.target.checked; });
        $('newServerForm').addEventListener('submit', createServer);
    }

    setInterval(() => document.visibilityState === 'visible' && poll(), POLL_MS);
    document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && poll());
    state.firstLoad = poll();
}
