/**
 * The Console tab: the server's output, polled every 2 seconds while the tab is open and in view, as
 * rows of time, level and message that All / Chat / Problems and a search narrow down; a command line
 * that runs commands over RCON, with the answers shown under them; and latest.log to download.
 */
import * as api from './api.js';
import { route } from './state.js';
import { $, ask, esc, plural, showError } from './ui.js';

const POLL_MS = 2000;
const MAX_LINES = 2000; // kept on screen
let server = null;
let since = null;   // the time of the last line shown, which the next poll asks from
let timer = null;
let loading = null; // the poll's call while it runs
let rows = [];      // the output, oldest first: {time, text, level, kind, trace, reply, el}
let filter = 'all'; // all, chat or problems
let query = '';     // the search
let unseen = 0;     // lines that came while someone reads further up
const history = []; // commands sent, newest last, for the arrow keys
let back = 0;       // how far back in history the input is

const running = () => !['stopped', 'crashed'].includes(server.status);
const open = () => route().tab === 'console' && route().id === server?.id && document.visibilityState === 'visible';

// The server's own time and level in front of a line, which the row's columns replace: Paper's
// "[12:00:00 INFO]: ", vanilla's and Fabric's "[12:00:00] [Server thread/INFO]: ", and Forge's
// "[14Oct2026 12:00:00.123] [Server thread/INFO] [minecraft/DedicatedServer]: ".
const PREFIX = /^\[(?:[^\]]* )?\d\d:\d\d:\d\d(?:\.\d+)?(?: (\w+))?\](?: \[[^\]]*\/(\w+)\])?(?: \[[^\]]*\])?:? /;
const TRACE = /^(\s+at |\s*\.\.\. \d+ more|Caused by: |[\w.$]+(Exception|Error)(: |$))/;
const CHAT_KINDS = ['chat', 'say', 'join', 'left', 'adv'];

/** A line of output as a row: its message without the prefix, its level, and what kind of line it is. */
function parse(line, time) {
    const prefix = line.match(PREFIX);
    const text = prefix ? line.slice(prefix[0].length) : line;
    const level = prefix ? (prefix[1] ?? prefix[2] ?? '') : '';
    const row = { time, text, level: level === 'INFO' ? '' : level, kind: 'info' };
    let match;
    if (['WARN', 'ERROR', 'FATAL'].includes(level) || (!prefix && /Exception|^\s+at /.test(line))) row.kind = 'warn';
    else if ((match = text.match(/^(?:\[Not Secure\] )?<([^>]+)> (.*)$/))) Object.assign(row, { kind: 'chat', who: match[1], said: match[2] });
    else if ((match = text.match(/^\[(Server|Rcon)\] (.*)$/))) Object.assign(row, { kind: 'say', who: match[1], said: match[2] });
    else if ((match = text.match(/^\S+ (joined|left) the game$/))) row.kind = match[1] === 'joined' ? 'join' : 'left';
    else if (/ has (made the advancement|completed the challenge|reached the goal) \[/.test(text)) row.kind = 'adv';
    return row;
}

/** The text with the search's matches marked, escaped. */
function mark(text) {
    if (!query) return esc(text);
    const lower = text.toLowerCase();
    const needle = query.toLowerCase();
    let html = '';
    let from = 0;
    for (let at = lower.indexOf(needle); at !== -1; at = lower.indexOf(needle, from)) {
        html += `${esc(text.slice(from, at))}<mark>${esc(text.slice(at, at + needle.length))}</mark>`;
        from = at + needle.length;
    }
    return html + esc(text.slice(from));
}

function shown(row) {
    if (filter === 'chat' && !CHAT_KINDS.includes(row.kind) && !(row.kind === 'cmd' && row.text.startsWith('say '))) return false;
    if (filter === 'problems' && row.kind !== 'warn') return false;
    const needle = query.toLowerCase();
    return !needle || [row.text, row.reply ?? '', ...(row.trace ?? [])].some((text) => text.toLowerCase().includes(needle));
}

function rowHtml(row) {
    const date = new Date(row.time * 1000);
    const [hours, minutes, seconds] = [date.getHours(), date.getMinutes(), date.getSeconds()].map((n) => String(n).padStart(2, '0'));
    const time = `<time datetime="${date.toISOString()}" title="${esc(date.toLocaleString())}">${hours}:${minutes}<span class="sec">:${seconds}</span></time>`;
    if (row.kind === 'cmd') {
        const reply = row.reply ? `<span class="reply">${mark(row.reply)}</span>` : '';
        return `<div class="ln ln--cmd">${time}<span class="lvl">you</span><span class="msg"><b>&gt;</b> <span class="cmdtext">${mark(row.text)}</span>${reply}</span></div>`;
    }
    let message = mark(row.text);
    if (row.kind === 'chat') message = `<span class="who">${mark(row.who)}</span> ${mark(row.said)}`;
    if (row.kind === 'say') message = `<span class="who who--server">[${row.who}]</span> ${mark(row.said)}`;
    if (row.kind === 'join') message = `<span class="arrow">→</span> ${message}`;
    if (row.kind === 'left') message = `<span class="arrow">←</span> ${message}`;
    if (row.trace) {
        const found = query && row.trace.some((line) => line.toLowerCase().includes(query.toLowerCase()));
        message += `<details class="trace"${found ? ' open' : ''}><summary>${plural(row.trace.length, 'more line')}</summary><pre>${row.trace.map(mark).join('\n')}</pre></details>`;
    }
    return `<div class="ln ln--${row.kind}">${time}<span class="lvl">${esc(row.level)}</span><span class="msg">${message}</span></div>`;
}

function element(row) {
    const box = document.createElement('div');
    box.innerHTML = rowHtml(row);
    row.el = box.firstElementChild;
    return row.el;
}

const atBottom = (log) => log.scrollHeight - log.scrollTop - log.clientHeight < 40;

function showUnseen() {
    const pill = $('consoleNew');
    pill.hidden = !unseen;
    pill.innerHTML = `<svg class="i"><use href="#i-down"/></svg>${plural(unseen, 'new line')}`;
}

/** Every row the filter and search let through, from scratch, scrolled to the newest. */
function render() {
    const log = $('consoleLog');
    for (const row of rows) row.el = null;
    const fragment = document.createDocumentFragment();
    for (const row of rows.filter(shown)) fragment.append(element(row));
    log.replaceChildren(fragment);
    if (!log.children.length) {
        const empty = query ? `Nothing matches “${query}”.` : { chat: 'No chat yet.', problems: 'No warnings or errors.' }[filter]
            ?? (since === null ? 'Nothing yet. The server writes here once it starts.' : 'Nothing yet.');
        log.innerHTML = `<p class="note">${esc(empty)}</p>`;
    }
    log.scrollTop = log.scrollHeight;
    unseen = 0;
    showUnseen();
}

/** New rows, and rows that changed (a stack trace's next lines, a command's answer), drawn in place. */
function update(added, changed) {
    const log = $('consoleLog');
    if (log.querySelector('.note')) {
        render();
        return;
    }
    const follow = atBottom(log);
    for (const row of changed) row.el?.replaceWith(element(row));
    const fresh = added.filter(shown);
    log.append(...fresh.map(element));
    for (const row of rows.splice(0, Math.max(0, rows.length - MAX_LINES))) row.el?.remove();
    if (follow) {
        log.scrollTop = log.scrollHeight;
    } else {
        unseen += fresh.length;
        showUnseen();
    }
}

async function load() {
    const id = server.id;
    try {
        const first = since === null;
        const data = await api.get(`/api/servers/${id}/console?${first ? '' : new URLSearchParams({ since })}`, { quiet: true });
        if (id !== server.id) return;
        const added = [];
        const changed = new Set();
        data.lines.forEach((line, index) => {
            const last = added.at(-1) ?? rows.at(-1);
            if (last?.kind === 'warn' && TRACE.test(line) && !PREFIX.test(line)) {
                (last.trace ??= []).push(line);
                if (!added.includes(last)) changed.add(last);
            } else {
                added.push(parse(line, data.times[index]));
            }
        });
        rows.push(...added);
        since = data.since ?? since;
        if (first) render();
        else update(added, changed);
    } catch { /* the next poll tries again */ }
}

function poll() {
    if (!open()) {
        clearInterval(timer);
        timer = null;
        return;
    }
    loading ??= load().finally(() => { loading = null; });
}

export function showConsole(fresh) {
    server = fresh;
    since = null;
    rows = [];
    $('consoleLog').textContent = '';
    updateConsole(fresh);
    load();
    clearInterval(timer);
    timer = setInterval(poll, POLL_MS);
}

/** A poll's fresh dict: commands only while the server runs, and only the chips its type has. */
export function updateConsole(fresh) {
    server = fresh;
    const input = $('commandInput');
    input.disabled = !running();
    $('commandSend').disabled = !running();
    input.placeholder = running() ? 'A command, e.g. whitelist add Steve' : 'Start the server to send it commands';
    for (const chip of document.querySelectorAll('.chips [data-command]')) {
        chip.disabled = !running();
        chip.hidden = Boolean(chip.dataset.types) && !chip.dataset.types.split(' ').includes(server.type);
    }
}

/** Run a command and show it, with its answer under it, in the output. */
async function sendCommand(command) {
    command = command.trim();
    if (!command || !running()) return;
    history.push(command);
    back = 0;
    const row = { time: Date.now() / 1000, text: command, kind: 'cmd' };
    rows.push(row);
    update([row], []);
    const log = $('consoleLog');
    log.scrollTop = log.scrollHeight;
    try {
        const { response } = await api.post(`/api/servers/${server.id}/console`, { command });
        row.reply = response;
        if (response) update([], [row]);
    } catch (error) {
        const failed = { time: Date.now() / 1000, text: error.message, level: '', kind: 'warn' };
        rows.push(failed);
        update([failed], []);
    }
}

/** Download the server's logs/latest.log, the file people ask for when helping, if it has one yet. */
async function downloadLog() {
    const files = `/api/servers/${server.id}/files`;
    try {
        const { entries } = await api.get(`${files}?path=logs`, { quiet: true });
        if (entries.some((entry) => entry.name === 'latest.log' && entry.type === 'file')) {
            location.assign(`${files}/download?path=logs/latest.log`);
            return;
        }
    } catch { /* no logs folder yet */ }
    showError("There's no latest.log yet: the server writes it once it starts");
}

export function initConsole() {
    $('downloadLog').addEventListener('click', downloadLog);
    $('commandForm').addEventListener('submit', (event) => {
        event.preventDefault();
        const input = $('commandInput');
        sendCommand(input.value);
        input.value = '';
    });
    $('commandInput').addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        event.preventDefault();
        back = Math.max(0, Math.min(history.length, back + (event.key === 'ArrowUp' ? 1 : -1)));
        event.target.value = back ? history[history.length - back] : '';
    });
    document.querySelector('.term__bar .seg').addEventListener('click', (event) => {
        const pressed = event.target.closest('[data-filter]');
        if (!pressed) return;
        for (const button of pressed.parentElement.children) button.setAttribute('aria-pressed', String(button === pressed));
        filter = pressed.dataset.filter;
        render();
    });
    $('consoleSearch').addEventListener('input', (event) => {
        query = event.target.value.trim();
        render();
    });
    $('consoleLog').addEventListener('scroll', (event) => {
        if (unseen && atBottom(event.target)) {
            unseen = 0;
            showUnseen();
        }
    });
    $('consoleNew').addEventListener('click', () => {
        const log = $('consoleLog');
        log.scrollTop = log.scrollHeight;
    });
    document.querySelector('.chips').addEventListener('click', async (event) => {
        const chip = event.target.closest('[data-command]');
        if (!chip) return;
        const label = chip.textContent[0].toUpperCase() + chip.textContent.slice(1);
        if ('fill' in chip.dataset) {
            $('commandInput').value = chip.dataset.command;
            $('commandInput').focus();
        } else if (!chip.dataset.ask || await ask({ title: `${label}?`, iconName: 'trash', text: chip.dataset.ask, ok: label, danger: true })) {
            sendCommand(chip.dataset.command);
        }
    });
    document.addEventListener('visibilitychange', () => {
        if (server && open() && !timer) {
            poll();
            timer = setInterval(poll, POLL_MS);
        }
    });
}
