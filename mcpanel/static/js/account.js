/** The account menu in the top bar, and the Your account page: password, recovery codes, signed-in devices. */
import * as api from './api.js';
import { $, ask, copyText, esc, plural, relativeTime, showError, showView, toast } from './ui.js';

const BROWSERS = [['Edg/', 'Edge'], ['OPR/', 'Opera'], ['Firefox/', 'Firefox'], ['FxiOS/', 'Firefox'], ['CriOS/', 'Chrome'], ['Chrome/', 'Chrome'], ['Safari/', 'Safari']];
const SYSTEMS = [['iPhone', 'iPhone'], ['iPad', 'iPad'], ['Android', 'Android'], ['CrOS', 'ChromeOS'], ['Windows', 'Windows'], ['Mac OS X', 'macOS'], ['Linux', 'Linux']];

/** A user agent as "Firefox on Windows", or its first word ("curl/8.5.0") when it isn't a known browser. */
function deviceName(userAgent) {
    const browser = BROWSERS.find(([mark]) => userAgent.includes(mark))?.[1];
    const system = SYSTEMS.find(([mark]) => userAgent.includes(mark))?.[1];
    if (browser) return system ? `${browser} on ${system}` : browser;
    return userAgent.split(' ')[0] || 'Unknown device';
}

function setMenu(open) {
    $('userMenu').hidden = !open;
    $('userBtn').setAttribute('aria-expanded', open);
}

export function showAccount() {
    showView('accountView');
    document.title = 'Your account · mcpanel';
    $('newCodes').hidden = true; // shown once, so not again on coming back
    $('codeList').innerHTML = '';
    loadCodesLeft();
    loadSessions();
}

async function loadCodesLeft() {
    let left;
    try {
        ({ recovery_codes_left: left } = await api.get('/api/me'));
    } catch {
        return;
    }
    $('codesLeft').textContent = left ? `${plural(left, 'code')} left.` : "None left. Make new ones, so a lost phone doesn't lock you out.";
}

async function loadSessions() {
    let sessions;
    try {
        ({ sessions } = await api.get('/api/account/sessions'));
    } catch {
        return;
    }
    $('sessionRows').innerHTML = sessions.map((session) => `
        <div class="list-row">
            <div class="list-row__main">
                <b>${esc(deviceName(session.user_agent))}</b>
                <span class="sub">${session.current ? `Signed in ${relativeTime(session.created)}` : `Last seen ${relativeTime(session.last_seen)}`}${session.ip ? ` · <code class="cc-code">${esc(session.ip)}</code>` : ''}</span>
            </div>
            ${session.current ? '<span class="cc-tag">This device</span>'
                : `<button class="cc-btn cc-btn--ghost cc-btn--sm" type="button" data-session="${esc(session.id)}" data-device="${esc(deviceName(session.user_agent))}"><span>Sign out</span></button>`}
        </div>`).join('');
    $('signOutOthers').disabled = sessions.length < 2;
}

async function changePassword(event) {
    event.preventDefault();
    const form = event.target;
    if ($('newPassword').value !== $('againPassword').value) {
        showError("The two new passwords aren't the same");
        return;
    }
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    try {
        await api.post('/api/account/password', { current: $('currentPassword').value, new: $('newPassword').value });
        form.reset();
        toast('Password changed. Your other devices were signed out.');
        loadSessions();
    } catch { /* api.js showed why */ }
    button.disabled = false;
}

async function makeNewCodes() {
    let codes;
    const answer = await ask({
        title: 'Make new recovery codes',
        iconName: 'shield',
        text: 'Your old codes stop working. Enter your password to go on.',
        value: '',
        password: true,
        ok: 'Make new codes',
        action: async (password) => { ({ codes } = await api.post('/api/account/recovery-codes', { password })); },
    });
    if (answer === null) return;
    $('codeList').innerHTML = codes.map((code) => `<li>${esc(code)}</li>`).join('');
    $('newCodes').hidden = false;
    $('codesLeft').textContent = `${plural(codes.length, 'code')} left.`;
    $('copyCodes').focus();
}

async function signOutDevice(button) {
    try {
        await api.del(`/api/account/sessions/${encodeURIComponent(button.dataset.session)}`);
    } catch {
        return;
    }
    toast(`Signed out <b>${esc(button.dataset.device)}</b>`);
    loadSessions();
}

async function signOutOthers() {
    try {
        await api.post('/api/account/sessions/sign-out-others');
    } catch {
        return;
    }
    toast('Signed out every other device');
    loadSessions();
}

export function initAccount() {
    // The account menu: opens under its button, closes on a choice, a click elsewhere or Escape.
    $('userBtn').addEventListener('click', () => setMenu($('userMenu').hidden));
    $('userMenu').addEventListener('click', (event) => event.target.closest('a') && setMenu(false));
    document.addEventListener('click', (event) => !event.target.closest('.account-menu') && setMenu(false));
    document.querySelector('.account-menu').addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || $('userMenu').hidden) return;
        setMenu(false);
        $('userBtn').focus();
    });

    $('passwordForm').addEventListener('submit', changePassword);
    $('newCodesBtn').addEventListener('click', makeNewCodes);
    $('copyCodes').addEventListener('click', () => copyText([...$('codeList').children].map((item) => item.textContent).join('\n'), 'the codes'));
    $('signOutOthers').addEventListener('click', signOutOthers);
    $('sessionRows').addEventListener('click', (event) => {
        const button = event.target.closest('[data-session]');
        if (button) signOutDevice(button);
    });
}
