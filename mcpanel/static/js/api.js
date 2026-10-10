/** Calls to the JSON API under /api. A failed call shows the server's message (unless it's quiet) and throws it. */
import { showError } from './ui.js';

async function request(method, url, body, quiet = false, raw = false) {
    try {
        const response = await fetch(url, {
            method,
            headers: body === undefined || raw ? {} : { 'Content-Type': 'application/json' },
            body: body === undefined || raw ? body : JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        if (response.status === 401) { // signed out, e.g. from another device: come back here after signing in
            location.assign(`/login?${new URLSearchParams({ next: location.pathname + location.hash })}`);
        }
        if (!response.ok) {
            const error = new Error(data.error || `The server answered ${response.status}`);
            error.status = response.status;
            throw error;
        }
        return data;
    } catch (error) {
        if (!quiet) showError(error.message);
        throw error;
    }
}

/** GET url. quiet keeps a failure off the screen, for polling, where the next try comes soon anyway. */
export const get = (url, { quiet = false } = {}) => request('GET', url, undefined, quiet);
export const post = (url, body) => request('POST', url, body);
export const put = (url, body) => request('PUT', url, body);
export const del = (url) => request('DELETE', url);
/** PUT a file (a Blob, or text) as the request's body, as it is: an upload, or the editor's save. */
export const upload = (url, body) => request('PUT', url, body, false, true);
