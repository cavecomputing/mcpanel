/**
 * The Console tab: the server's output, polled every 2 seconds while the tab is open and in view,
 * and a command line that runs commands over RCON, with the answers shown in the output.
 */
import * as api from './api.js';
import { route } from './state.js';
import { $, esc } from './ui.js';

const POLL_MS = 2000;
const MAX_LINES = 2000; // kept on screen
let server = null;
let since = null;   // the time of the last line shown, which the next poll asks from
let timer = null;
let loading = null; // the poll's call while it runs
const history = []; // commands sent, newest last, for the arrow keys
let back = 0;       // how far back in history the input is

const running = () => !['stopped', 'crashed'].includes(server.status);
const open = () => route().tab === 'console' && route().id === server?.id && document.visibilityState === 'visible';

/** A line's colour: warnings and errors, joins and leaves, chat. */
function kind(line) {
    if (/\/(WARN|ERROR|FATAL)\]|Exception/.test(line)) return 'warn';
    if (/ (joined|left) the game$/.test(line)) return 'join';
    if (/INFO\]: (\[Not Secure\] )?<[^>]+>/.test(line)) return 'chat';
    return '';
}

function append(lines, className = null) {
    const log = $('consoleLog');
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.insertAdjacentHTML('beforeend', lines.map((line) => {
        const name = className ?? kind(line);
        return name ? `<span class="${name}">${esc(line)}</span>\n` : `${esc(line)}\n`;
    }).join(''));
    while (log.childNodes.length > MAX_LINES * 2) log.firstChild.remove();
    if (atBottom) log.scrollTop = log.scrollHeight;
}

async function load() {
    const id = server.id;
    try {
        const data = await api.get(`/api/servers/${id}/console?${since === null ? '' : new URLSearchParams({ since })}`, { quiet: true });
        if (id !== server.id) return;
        if (since === null && !data.lines.length) append(['Nothing yet. The server writes here once it starts.'], 'note');
        append(data.lines);
        since = data.since ?? since;
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
    $('consoleLog').textContent = '';
    updateConsole(fresh);
    load().then(() => { $('consoleLog').scrollTop = $('consoleLog').scrollHeight; });
    clearInterval(timer);
    timer = setInterval(poll, POLL_MS);
}

/** A poll's fresh dict: commands only while the server runs. */
export function updateConsole(fresh) {
    server = fresh;
    const input = $('commandInput');
    input.disabled = !running();
    $('commandSend').disabled = !running();
    input.placeholder = running() ? 'A command, e.g. whitelist add Steve' : 'Start the server to send it commands';
    for (const chip of document.querySelectorAll('[data-command]')) chip.disabled = !running();
}

/** Run a command and show it and its answer in the output. */
async function sendCommand(command) {
    command = command.trim();
    if (!command || !running()) return;
    history.push(command);
    back = 0;
    append([`> ${command}`], 'cmd');
    try {
        const { response } = await api.post(`/api/servers/${server.id}/console`, { command });
        if (response) append(response.split('\n'), 'reply');
    } catch (error) {
        append([error.message], 'warn');
    }
}

export function initConsole() {
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
    document.querySelector('.chips').addEventListener('click', (event) => {
        const chip = event.target.closest('[data-command]');
        if (!chip) return;
        if ('fill' in chip.dataset) {
            $('commandInput').value = chip.dataset.command;
            $('commandInput').focus();
        } else sendCommand(chip.dataset.command);
    });
    document.addEventListener('visibilitychange', () => {
        if (server && open() && !timer) {
            poll();
            timer = setInterval(poll, POLL_MS);
        }
    });
}
