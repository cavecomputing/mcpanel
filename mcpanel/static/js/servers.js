/**
 * Servers: the sidebar list (a drawer on phones), polled every 5 seconds while the page is in view;
 * the open server's header, Start/Stop/Restart and Overview; the empty states; the new-server dialog.
 */
import * as api from './api.js';
import { formatSize } from './files.js';
import { route, serverHash, state } from './state.js';
import { $, ask, copyText, esc, formatDate, plural, setHtml, showError, showView, toast } from './ui.js';

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
let troubleFor = ''; // '<server id>/<status>' whose last lines the Overview shows, while it is crashed or not responding
const TROUBLE_LINES = 15;
const DISK_MS = 60 * 1000; // the Overview asks for disk use at most this often, since it walks the whole folder
let disk = { id: '', at: 0, use: null }; // the last disk use fetched: for which server, when, and {folder, backups, free}
const TROUBLE_TITLES = { crashed: 'It stopped with an error', unresponsive: "It isn't answering", restarting: 'It keeps stopping with an error, and Docker keeps starting it again' };
// What a crash's lines most often mean, in plain words: the first match among its last lines wins.
const MEMORY_HINT = 'It used all the memory it may use. Give it more memory in Settings, or run fewer mods.';
const CRASH_HINTS = [
    [/OutOfMemoryError/, MEMORY_HINT],
    [/UnsupportedClassVersionError|compiled by a more recent version of the Java Runtime|Unsupported Java detected/,
        'Something in it needs a newer Java than it runs. Choose a newer Java in Settings.'],
    [/incompatible mods? (found|set)|Mod resolution failed|Missing or unsupported mandatory dependencies|Mixin apply .*failed/i,
        "A mod is missing another mod it needs, or isn't made for this version or loader. The Console says which."],
];

/** Give a tab its view: main.js does, so the tabs' modules can use this one. */
export function addTab(tab, view) {
    TAB_VIEWS[tab] = view;
}

export const typeName = (type) => TYPE_NAMES[type] ?? type;
const versionName = (version) => (version === 'LATEST' ? 'Latest' : version);
// LATEST with the release it got at its last start, which players need to know to join.
const runningVersion = (server) => (server.version === 'LATEST' && server.game_version ? `Latest (${server.game_version})` : versionName(server.version));
export const javaName = (java) => java.replace(/^java/, 'Java ');
const statusName = (status) => STATUS[status] ?? status;
const serverWithId = (id) => state.servers.find((server) => server.id === id);

/** How long since a time (Unix seconds), as "45 min", "3 h 12 min" or "2 days 4 h". */
function formatUptime(seconds) {
    const minutes = Math.floor((Date.now() / 1000 - seconds) / 60);
    if (minutes < 1) return 'under a minute';
    if (minutes < 60) return `${minutes} min`;
    if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
    return `${plural(Math.floor(minutes / (24 * 60)), 'day')} ${Math.floor(minutes / 60) % 24} h`;
}

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
    $('serverTabs').querySelector('[aria-current]').scrollIntoView({ block: 'nearest', inline: 'center' });
    view.show?.(server);
}

/** On a narrow screen the tabs scroll sideways: fade the side that has more of them. */
function markMoreTabs() {
    const tabs = $('serverTabs');
    tabs.toggleAttribute('data-more-before', tabs.scrollLeft > 1);
    tabs.toggleAttribute('data-more-after', tabs.scrollLeft + tabs.clientWidth < tabs.scrollWidth - 1);
}

/** The server view's header and Overview, changing only what changed, so a poll keeps focus and selection. */
function drawServer(server) {
    document.title = `${server.name} · mcpanel`;
    $('serverName').textContent = server.name;
    $('serverBadge').className = `cc-badge ${BADGE[server.status] ?? ''}`;
    $('serverBadge').textContent = statusName(server.status);
    $('serverAddress').textContent = server.address;
    const tags = [typeName(server.type), runningVersion(server), javaName(server.java), `${server.memory_gb} GB`];
    setHtml($('serverTags'), tags.map((tag) => `<span class="cc-tag">${esc(tag)}</span>`).join(''));
    // How far a start has got, since a first one downloads and generates for minutes.
    $('serverProgress').hidden = !server.progress;
    $('serverProgress').textContent = server.progress ?? '';
    $('serverProgress').title = server.progress ?? '';
    const busy = state.busy.get(server.id);
    for (const button of $('serverView').querySelectorAll('[data-action]')) {
        const action = ACTIONS[button.dataset.action];
        button.disabled = Boolean(busy) || !action.when.includes(server.status);
        button.lastElementChild.textContent = busy === button.dataset.action ? action.doing : action.label;
    }
    // Disk use, which loadDisk() fetches apart from the other stats: it shows while stopped too.
    const diskUse = disk.id === server.id ? disk.use : null;
    const [amount, unit] = diskUse ? formatSize(diskUse.folder + diskUse.backups).split(' ') : [];
    setHtml($('statDisk'), diskUse ? `${amount} <small>${unit}</small>` : '–');
    setHtml($('serverDetails'), [
        ['Address', `<code class="cc-code">${esc(server.address)}</code>`],
        ['Port', esc(server.port)],
        ['Type', esc(typeName(server.type))],
        ['Version', esc(runningVersion(server))],
        ['Java', esc(javaName(server.java))],
        ['Memory', `${esc(server.memory_gb)} GB`],
        ['CPU', server.cpus ? plural(server.cpus, 'core') : 'No limit'],
        ['Up for', server.started ? formatUptime(server.started) : 'Not running'],
        ['Disk', diskUse ? `${formatSize(diskUse.folder)} in its folder, ${formatSize(diskUse.backups)} in backups; ${formatSize(diskUse.free)} free on the host` : '–'],
        ['Created', esc(formatDate(server.created))],
    ].map(([term, value]) => `<dt>${term}</dt><dd>${value}</dd>`).join(''));
}

/** The Overview's players, TPS, memory and CPU, fetched on opening it and on each poll: a dash while it can't say. */
function loadStats(server) {
    loadTrouble(server);
    loadDisk(server);
    if (['stopped', 'crashed'].includes(server.status)) {
        drawStats(server, null);
        return;
    }
    stats ??= api.get(`/api/servers/${server.id}/stats`, { quiet: true })
        .then((data) => route().id === server.id && drawStats(server, data))
        .catch(() => {})
        .finally(() => { stats = null; });
}

/** The Overview's disk use, stopped or not: on opening a server's Overview, then once a minute. */
function loadDisk(server) {
    if (disk.id === server.id && Date.now() - disk.at < DISK_MS) return;
    disk = { id: server.id, at: Date.now(), use: disk.id === server.id ? disk.use : null };
    api.get(`/api/servers/${server.id}/disk`, { quiet: true }).then((use) => {
        if (disk.id !== server.id) return;
        disk.use = use;
        if (serverWithId(server.id) && route().id === server.id) drawServer(serverWithId(server.id));
    }).catch(() => {});
}

/** A crashed, unresponsive or crash-looping server's last lines on the Overview, with what they most
 * likely mean when that's known, fetched once each time it gets that way. */
function loadTrouble(server) {
    const key = `${server.id}/${server.status}`;
    if (!TROUBLE_TITLES[server.status]) {
        troubleFor = '';
        $('troubleCard').hidden = true;
        return;
    }
    if (troubleFor === key) return;
    troubleFor = key;
    $('troubleCard').hidden = true;
    $('troubleTitle').textContent = TROUBLE_TITLES[server.status];
    $('troubleConsole').href = serverHash(server.id, 'console');
    api.get(`/api/servers/${server.id}/console`, { quiet: true }).then(({ lines }) => {
        if (troubleFor !== key) return;
        const recent = lines.slice(-200).join('\n');
        const hint = server.oom_killed ? MEMORY_HINT : CRASH_HINTS.find(([pattern]) => pattern.test(recent))?.[1];
        $('troubleHint').hidden = !hint;
        $('troubleHint').textContent = hint ?? '';
        $('troubleLog').textContent = lines.slice(-TROUBLE_LINES).join('\n') || 'It wrote nothing.';
        $('troubleCard').hidden = false;
    }).catch(() => {});
}

function drawStats(server, data) {
    const gb = (bytes) => (bytes / 1024 ** 3).toFixed(1);
    const usage = data?.usage;
    setHtml($('statPlayers'), data?.players == null ? '–' : `${esc(data.players)} <small>/ ${esc(data.max_players)}</small>`);
    setHtml($('statTps'), data?.tps == null ? '–' : `${data.tps.toFixed(1)} <small>/ 20</small>`);
    setHtml($('statMemory'), usage ? `${gb(usage.memory)} <small>/ ${+gb(usage.memory_limit)} GB</small>` : '–');
    // In cores, like the CPU limit an admin sets: "1.5 of 2 cores", or "0.4 cores" with no limit.
    setHtml($('statCpu'), usage ? `${(usage.cpu / 100).toFixed(1)} <small>${server.cpus ? `of ${plural(server.cpus, 'core')}` : 'cores'}</small>` : '–');
}

/** Stop and Restart disconnect whoever is on, so they ask first while someone is. */
async function mayDisconnect(server, action) {
    if (action === 'start' || ['stopped', 'crashed'].includes(server.status)) return true;
    let online;
    try {
        ({ online } = await api.get(`/api/servers/${server.id}/players`, { quiet: true }));
    } catch {
        return true;
    }
    if (!online?.length) return true;
    const label = ACTIONS[action].label;
    return Boolean(await ask({ title: `${label} ${server.name}?`, iconName: action, ok: label, danger: true,
        text: `${online.length === 1 ? `${online[0]} is` : `${plural(online.length, 'player')} are`} on, and will be disconnected.` }));
}

/** Start, stop or restart the open server. Stopping can take up to a minute while the world saves. */
async function runAction(action) {
    const { id } = route();
    if (!serverWithId(id) || state.busy.has(id) || !await mayDisconnect(serverWithId(id), action)) return;
    if (state.busy.has(id)) return; // pressed again while the question was open
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

/** The type, Java, memory and CPU choices, fetched once, filled into a form's selects and fields. */
export async function fillOptions(typeSelect, javaSelect, memoryInput, cpusInput) {
    options ??= await api.get('/api/servers/options');
    typeSelect.innerHTML = options.types.map((type) => `<option value="${esc(type)}">${esc(typeName(type))}</option>`).join('');
    // Empty leaves it to the panel, which picks the Java the version needs.
    javaSelect.innerHTML = '<option value="">Match the version</option>'
        + options.java.map((java) => `<option value="${esc(java)}">${esc(javaName(java))}</option>`).join('');
    Object.assign(memoryInput, { min: options.memory.min, max: options.memory.max, defaultValue: options.memory.default });
    cpusInput.min = options.cpus.min;
}

/** For an admin's form: the host's memory and cores in note, and how much of them the servers other
 * than exceptId are given (their containers' limits, running or not). cpusInput can't go past the cores. */
export async function drawHost(note, cpusInput, exceptId = null) {
    let host;
    try {
        host = await api.get('/api/servers/host', { quiet: true });
    } catch {
        note.hidden = true;
        return;
    }
    const others = state.servers.filter((server) => server.id !== exceptId);
    const servers = (count) => `${count} ${exceptId ? 'other ' : ''}server${count === 1 ? '' : 's'}`;
    const gb = (bytes) => `${+(bytes / 1024 ** 3).toFixed(1)} GB`;
    const given = others.reduce((sum, server) => sum + (server.memory_limit ?? 0), 0);
    const left = host.memory - given;
    const limited = others.filter((server) => server.cpus);
    const cores = limited.reduce((sum, server) => sum + server.cpus, 0);
    const unlimited = others.length - limited.length;
    const memory = !others.length ? 'none given to a server yet'
        : `${gb(given)} given to ${servers(others.length)}, ${left >= 0 ? `<b>${gb(left)} left</b>` : `<b class="over">${gb(-left)} more than it has</b>`}`;
    const cpu = [limited.length && `${servers(limited.length)} limited to ${plural(cores, 'core')}${limited.length > 1 ? ' in all' : ''}`,
        unlimited && `${servers(unlimited)} with no limit`].filter(Boolean).join(', ');
    cpusInput.max = host.cpus;
    note.hidden = false;
    setHtml(note, `<span><b>Memory</b>: ${gb(host.memory)} on the host, ${memory}. A server uses no more than its memory; three quarters of it is Java's heap.</span>
        <span><b>CPU</b>: ${plural(host.cpus, 'core')} on the host${cpu ? `; ${cpu}` : ''}.</span>`);
}

/** A CPU field's cores, or null for no limit when it's empty. */
export const cpusOf = (input) => (input.value === '' ? null : Number(input.value));

async function openNewServer() {
    try {
        await fillOptions($('serverType'), $('serverJava'), $('serverMemory'), $('serverCpus'));
    } catch {
        return;
    }
    $('newServerForm').reset();
    $('createServer').disabled = true;
    $('newServerDialog').showModal();
    drawHost($('newHostNote'), $('serverCpus'));
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
            memory_gb: Number($('serverMemory').value),
            cpus: cpusOf($('serverCpus')),
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
    $('serverTabs').addEventListener('scroll', markMoreTabs, { passive: true });
    new ResizeObserver(markMoreTabs).observe($('serverTabs')); // also when the server view first shows
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
