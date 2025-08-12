const contentViews = document.querySelectorAll('.content-view');
const sidebar = document.getElementById('sidebar');

export function switchView(viewId) {
    contentViews.forEach(view => view.classList.add('hidden'));
    const targetView = document.getElementById(viewId);
    if (targetView) {
        targetView.classList.remove('hidden');
    }
}

export function setActiveSidebar(viewId) {
    sidebar.querySelectorAll('.nav-link').forEach(link => link.classList.remove('active'));
    const navLink = sidebar.querySelector(`.nav-link[data-view="${viewId}"]`);
    if (navLink) {
        navLink.classList.add('active');
    }
}

export function toggleLoader(show) {
    const loader = document.getElementById('loader');
    if (!loader) return;
    loader.classList.toggle('hidden', !show);
}