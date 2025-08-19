import { checkLogin, setupAuthEventListeners } from './auth.js';
import { setupHomeEventListeners } from './views/home.js';
import { setupLibraryEventListeners } from './views/library.js';
import { setupEditAnimeEventListeners } from './views/editAnime.js';
import { setupTasksEventListeners } from './views/tasks.js';
import { setupTokensEventListeners } from './views/tokens.js';
import { setupSourcesEventListeners } from './views/sources.js';
import { setupExternalApiEventListeners } from './views/externalApi.js';
import { setupSettingsEventListeners } from './views/settings.js';
import { switchView, setActiveSidebar } from './ui.js';

document.addEventListener('DOMContentLoaded', () => {
    // Setup all event listeners from different modules
    setupAuthEventListeners();
    setupHomeEventListeners();
    setupLibraryEventListeners();
    setupEditAnimeEventListeners();
    setupTasksEventListeners();
    setupTokensEventListeners();
    setupSourcesEventListeners();
    setupExternalApiEventListeners();
    setupSettingsEventListeners();

    // Sidebar navigation
    document.getElementById('sidebar').addEventListener('click', (e) => {
        const navLink = e.target.closest('.nav-link');
        if (!navLink) return;
        
        e.preventDefault();
        const viewId = navLink.getAttribute('data-view');
        if (!viewId) return;

        setActiveSidebar(viewId);
        switchView(viewId);

        // Trigger data loading for the new view
        // This uses a custom event system for loose coupling
        document.dispatchEvent(new CustomEvent('viewchange', { detail: { viewId } }));
    });

    // Initial load
    checkLogin();
});