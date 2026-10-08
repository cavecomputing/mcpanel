/** What the page knows, shared by the modules that read or change it, and the route the address names. */
const shell = document.getElementById('shell');

export const state = {
    username: shell.dataset.username,
    admin: shell.dataset.role === 'admin',
    servers: [],       // as /api/servers sends them, sorted by name
    loaded: false,     // whether the list has arrived at least once
    firstLoad: null,   // the first fetch of the list, which the Users page waits for to name servers
    dockerError: null, // the message while Docker can't be reached
    busy: new Map(),   // server id -> the action under way: 'start', 'stop' or 'restart'
};

/**
 * The page the hash names: #/servers/<id>, #/users or #/account. Anything else is home, which
 * opens the first server.
 */
export function route() {
    const [, page = '', id = ''] = location.hash.split('/'); // '#/servers/x' -> ['#', 'servers', 'x']
    if (page === 'servers' && id) return { page: 'server', id };
    if (page === 'account' || (page === 'users' && state.admin)) return { page };
    return { page: 'home' };
}

export const serverHash = (id) => `#/servers/${id}`;
