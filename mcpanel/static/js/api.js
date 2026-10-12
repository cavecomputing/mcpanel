/** Calls to the JSON API under /api. A failed call shows the server's message (unless it's quiet) and throws it. */
import { showError } from './ui.js';

/** The data of an answer, or its error thrown. A 401 is a sign-out, e.g. from another device: come
 * back here after signing in. */
function answer(status, data) {
    if (status === 401) location.assign(`/login?${new URLSearchParams({ next: location.pathname + location.hash })}`);
    if (status >= 200 && status < 300) return data;
    const error = new Error(data.error || `The server answered ${status}`);
    error.status = status;
    throw error;
}

async function request(method, url, body, quiet = false, raw = false) {
    try {
        const response = await fetch(url, {
            method,
            headers: body === undefined || raw ? {} : { 'Content-Type': 'application/json' },
            body: body === undefined || raw ? body : JSON.stringify(body),
        });
        return answer(response.status, await response.json().catch(() => ({})));
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

/** upload() telling onProgress(fraction sent) as it goes, which fetch can't, so XMLHttpRequest. */
export function uploadWithProgress(url, body, onProgress) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        const fail = (error) => {
            showError(error.message);
            reject(error);
        };
        xhr.open('PUT', url);
        xhr.responseType = 'json';
        xhr.upload.onprogress = (event) => event.lengthComputable && onProgress(event.loaded / event.total);
        xhr.onload = () => {
            try {
                resolve(answer(xhr.status, xhr.response ?? {}));
            } catch (error) {
                fail(error);
            }
        };
        xhr.onerror = () => fail(new Error("The upload didn't reach the panel"));
        xhr.send(body);
    });
}
