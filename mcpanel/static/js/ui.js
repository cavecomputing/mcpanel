/** Small helpers every module uses: escaping, formatting, the toast, copying and the question dialog. */

export const $ = (id) => document.getElementById(id);

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (text) => String(text).replace(/[&<>"']/g, (c) => ESCAPES[c]);

export const plural = (count, word) => `${count.toLocaleString()} ${word}${count === 1 ? '' : 's'}`;

const drawn = new WeakMap(); // element -> the HTML last given to setHtml

/** Set an element's HTML only when it changed, so a poll that brings nothing new redraws nothing. */
export function setHtml(element, html) {
    if (drawn.get(element) === html) return;
    drawn.set(element, html);
    element.innerHTML = html;
}

/** Show one of the main pane's views and hide the others. */
export function showView(id) {
    for (const view of document.querySelectorAll('.view')) view.hidden = view.id !== id;
}

const relative = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
const UNITS = [['day', 86400], ['hour', 3600], ['minute', 60]];

/** A time (Unix seconds) as "3 hours ago" or "in 23 hours". */
export function relativeTime(seconds) {
    const diff = seconds - Date.now() / 1000;
    for (const [unit, size] of UNITS) if (Math.abs(diff) >= size) return relative.format(Math.round(diff / size), unit);
    return 'just now';
}

/** A time (Unix seconds) as the browser writes dates, e.g. "Oct 8, 2026, 10:15 AM". */
export const formatDate = (seconds) => new Date(seconds * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });

let toastTimer;

/** A short message at the bottom. It is a popover, so it shows above an open dialog too. */
export function toast(html, { error = false } = {}) {
    const box = $('toast');
    box.classList.toggle('toast--error', error);
    box.hidePopover();
    box.showPopover();
    box.innerHTML = html; // after it shows, so screen readers hear the change
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.hidePopover(), error ? 6000 : 3000);
}

export const showError = (message) => toast(esc(message), { error: true });

/** Put text on the clipboard. Browsers allow it only over HTTPS and on localhost. */
export async function copyText(text, what) {
    try {
        await navigator.clipboard.writeText(text);
        toast(`Copied ${esc(what)}`);
    } catch {
        showError(`Your browser wouldn't copy ${what}; select it and copy it yourself`);
    }
}

/**
 * Ask a question in the dialog. With a value it asks for text (a password with password: true)
 * and resolves to it; without, it asks for a yes and resolves to true. action(answer), if given,
 * runs on submit; when it throws, the dialog stays open so the answer can be fixed. Cancel and
 * Escape resolve to null, or, while the action runs, to what it comes to, so a change it makes
 * still gets shown.
 */
export function ask({ title, iconName, text = '', value = null, password = false, ok = 'OK', danger = false, action }) {
    const dialog = $('askDialog');
    const input = $('askInput');
    const okButton = $('askOk');
    $('askTitle').textContent = title;
    $('askIcon').setAttribute('href', `#i-${iconName}`);
    $('askText').textContent = text;
    $('askText').hidden = !text;
    input.hidden = value === null;
    input.required = value !== null;
    input.type = password ? 'password' : 'text';
    input.autocomplete = password ? 'current-password' : 'off';
    input.value = value ?? '';
    okButton.firstElementChild.textContent = ok;
    okButton.className = `cc-btn ${danger ? 'cc-btn--danger' : 'cc-btn--primary'}`;
    dialog.showModal(); // a yes/no question starts on Cancel
    if (value !== null) input.focus();

    return new Promise((resolve) => {
        let answer = null;
        let running = null; // the action's promise, once the answer is submitted
        dialog.querySelector('form').onsubmit = async (event) => {
            event.preventDefault();
            const reply = value === null ? true : input.value;
            okButton.disabled = true;
            try {
                running = action?.(reply);
                await running;
                answer = reply;
                dialog.close();
            } catch { /* api.js showed why; let the answer be fixed */ }
            okButton.disabled = false;
        };
        dialog.onclose = async () => {
            input.value = ''; // a password doesn't stay in the page
            await running?.catch(() => {});
            resolve(answer);
        };
    });
}

export function initUi() {
    // Cancel and Done buttons close their dialog; Escape closes any dialog by itself.
    document.addEventListener('click', (event) => event.target.closest('[data-close]')?.closest('dialog').close());
}
