/** Entry point: wires up each module, then shows the page the address names, and again on every change. */
import { initAccount, showAccount } from './account.js';
import { initConsole, showConsole, updateConsole } from './console.js';
import { initFiles, showFiles } from './files.js';
import { initPlayers, showPlayers, updatePlayers } from './players.js';
import { addTab, drawSidebar, initServers, showHome, showServer } from './servers.js';
import { initSettings, showSettings, updateSettings } from './settings.js';
import { route } from './state.js';
import { initTheme } from './theme.js';
import { initUi } from './ui.js';
import { initUsers, showUsers } from './users.js';

const PAGES = { home: showHome, server: showServer, users: showUsers, account: showAccount };

function show() {
    const where = route();
    drawSidebar();
    for (const link of document.querySelectorAll('[data-page]')) {
        if (link.dataset.page === where.page) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
    }
    PAGES[where.page](where.id);
}

initTheme();
initUi();
initAccount();
initUsers();
initConsole();
initPlayers();
initFiles();
initSettings();
addTab('players', { show: showPlayers, update: updatePlayers });
addTab('console', { show: showConsole, update: updateConsole });
addTab('files', { show: showFiles });
addTab('settings', { show: showSettings, update: updateSettings });
initServers();
window.addEventListener('hashchange', show);
show();
