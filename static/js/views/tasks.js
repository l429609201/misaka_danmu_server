import { apiFetch } from '../api.js';
import { switchView } from '../ui.js';

let taskListUl, taskManagerSubNav, runningTasksSearchInput, runningTasksFilterButtons, taskManagerSubViews;
let deleteRunningTaskBtn, pauseResumeTaskBtn, abortRunningTaskBtn;
let scheduledTasksTableBody, addScheduledTaskBtn, editScheduledTaskView, editScheduledTaskForm, backToTasksFromEditBtn, editScheduledTaskTitle;
let taskLoadInterval = null;
let taskLoadTimeout;

function initializeElements() {
    taskListUl = document.getElementById('task-list');
    taskManagerSubNav = document.querySelector('#task-manager-view .settings-sub-nav');
    runningTasksSearchInput = document.getElementById('running-tasks-search-input');
    runningTasksFilterButtons = document.getElementById('running-tasks-filter-buttons');
    deleteRunningTaskBtn = document.getElementById('delete-running-task-btn');
    pauseResumeTaskBtn = document.getElementById('pause-resume-task-btn');
    abortRunningTaskBtn = document.getElementById('abort-running-task-btn');
    taskManagerSubViews = document.querySelectorAll('#task-manager-view .settings-subview');
    scheduledTasksTableBody = document.querySelector('#scheduled-tasks-table tbody');
    addScheduledTaskBtn = document.getElementById('add-scheduled-task-btn');
    editScheduledTaskView = document.getElementById('edit-scheduled-task-view');
    editScheduledTaskForm = document.getElementById('edit-scheduled-task-form');
    editScheduledTaskTitle = document.getElementById('edit-scheduled-task-title');
    backToTasksFromEditBtn = document.getElementById('back-to-tasks-from-edit-btn');
}

function handleTaskManagerSubNav(e) {
    const subNavBtn = e.target.closest('.sub-nav-btn');
    if (!subNavBtn) return;
    const subViewId = subNavBtn.getAttribute('data-subview');
    if (!subViewId) return;

    taskManagerSubNav.querySelectorAll('.sub-nav-btn').forEach(btn => btn.classList.remove('active'));
    subNavBtn.classList.add('active');

    taskManagerSubViews.forEach(view => view.classList.add('hidden'));
    const targetSubView = document.getElementById(subViewId);
    if (targetSubView) targetSubView.classList.remove('hidden');

    if (subViewId === 'running-tasks-subview') loadAndRenderTasks();
    else if (subViewId === 'scheduled-tasks-subview') loadAndRenderScheduledTasks();
}

function handleTaskFilterClick(e) {
    const btn = e.target.closest('.filter-btn');
    if (!btn) return;
    runningTasksFilterButtons.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    applyTaskFilters();
}

function applyTaskFilters() {
    loadAndRenderTasksWithDebounce();
    updateTaskActionButtons();
}

async function loadAndRenderTasks() {
    const runningTasksView = document.getElementById('running-tasks-subview');
    if (!localStorage.getItem('danmu_api_token') || !runningTasksView || runningTasksView.classList.contains('hidden')) return;
    
    const searchTerm = runningTasksSearchInput.value;
    const activeFilterBtn = runningTasksFilterButtons.querySelector('.filter-btn.active');
    const statusFilter = activeFilterBtn ? activeFilterBtn.dataset.statusFilter : 'all';

    const params = new URLSearchParams();
    if (searchTerm) params.append('search', searchTerm);
    if (statusFilter) params.append('status', statusFilter);

    try {
        const tasks = await apiFetch(`/api/ui/tasks?${params.toString()}`);
        renderTasks(tasks);
    } catch (error) {
        console.error("刷新任务列表失败:", error.message);
        taskListUl.innerHTML = `<li class="error">加载任务失败: ${error.message}</li>`;
    }
}

function renderTasks(tasksToRender) {
    if (!taskListUl) return;
    if (tasksToRender.length === 0) {
        taskListUl.innerHTML = '<li>没有符合条件的任务。</li>';
        return;
    }
    
    const noTasksLi = taskListUl.querySelector('li:not(.task-item)');
    if (noTasksLi) {
        taskListUl.innerHTML = '';
    }

    const existingTaskElements = new Map([...taskListUl.querySelectorAll('.task-item')].map(el => [el.dataset.taskId, el]));
    const incomingTaskIds = new Set(tasksToRender.map(t => t.task_id));

    for (const [taskId, element] of existingTaskElements.entries()) {
        if (!incomingTaskIds.has(taskId)) {
            element.remove();
        }
    }

    tasksToRender.forEach(task => {
        const statusColor = {
            "已完成": "var(--success-color)", "失败": "var(--error-color)",
            "排队中": "#909399", "运行中": "var(--primary-color)"
        }[task.status] || "var(--primary-color)";

        let taskElement = existingTaskElements.get(task.task_id);

        if (taskElement) {
            if (taskElement.dataset.status !== task.status) {
                taskElement.dataset.status = task.status; // e.g., "运行中", "已暂停"
                taskElement.querySelector('.task-status').textContent = task.status;
            }
            taskElement.querySelector('.task-description').textContent = task.description;
            const progressBar = taskElement.querySelector('.task-progress-bar');
            progressBar.style.width = `${task.progress}%`;
            progressBar.style.backgroundColor = statusColor;
        } else {
            const li = document.createElement('li');
            li.className = 'task-item';
            li.dataset.taskId = task.task_id;
            li.dataset.status = task.status; // e.g., "运行中", "已暂停"
            li.innerHTML = `
                <div class="task-header">
                    <span class="task-title">${task.title}</span>
                    <span class="task-status">${task.status}</span>
                </div>
                <p class="task-description">${task.description}</p>
                <div class="task-progress-bar-container">
                    <div class="task-progress-bar" style="width: ${task.progress}%; background-color: ${statusColor};"></div>
                </div>
            `;
            taskListUl.appendChild(li);
        }
    });

    // After rendering, re-evaluate button states based on the currently selected task
    const selectedTask = taskListUl.querySelector('.task-item.selected');
    if (selectedTask) {
        updateTaskActionButtons();
    }
}

function loadAndRenderTasksWithDebounce() {
    clearTimeout(taskLoadTimeout);
    taskLoadTimeout = setTimeout(loadAndRenderTasks, 300);
}

function handleTaskSelection(e) {
    const clickedLi = e.target.closest('.task-item');
    if (!clickedLi) return;

    // Remove 'selected' from all other items
    taskListUl.querySelectorAll('.task-item.selected').forEach(item => {
        if (item !== clickedLi) {
            item.classList.remove('selected');
        }
    });
    clickedLi.classList.toggle('selected');
    updateTaskActionButtons();
}

function updateTaskActionButtons() {
    const selectedTask = taskListUl.querySelector('.task-item.selected');

    if (!selectedTask) {
        deleteRunningTaskBtn.disabled = true;
        pauseResumeTaskBtn.disabled = true;
        abortRunningTaskBtn.disabled = true;
        pauseResumeTaskBtn.textContent = '暂停/恢复';
        return;
    }

    const status = selectedTask.dataset.status;
    const isDeletable = status === '已完成' || status === '失败' || status === '已暂停' || status === '排队中';
    const isPausable = status === '运行中';
    const isResumable = status === '已暂停';
    const isAbortable = status === '运行中';

    deleteRunningTaskBtn.disabled = !isDeletable;
    pauseResumeTaskBtn.disabled = !(isPausable || isResumable);
    abortRunningTaskBtn.disabled = !isAbortable;

    if (isPausable) pauseResumeTaskBtn.textContent = '暂停';
    else if (isResumable) pauseResumeTaskBtn.textContent = '恢复';
    else pauseResumeTaskBtn.textContent = '暂停/恢复';
}

async function loadAndRenderScheduledTasks() {
    if (!scheduledTasksTableBody) return;
    scheduledTasksTableBody.innerHTML = '<tr><td colspan="7">加载中...</td></tr>';
    try {
        const tasks = await apiFetch('/api/ui/scheduled-tasks');
        renderScheduledTasks(tasks);
    } catch (error) {
        scheduledTasksTableBody.innerHTML = `<tr class="error"><td colspan="7">加载失败: ${error.message}</td></tr>`;
    }
}

function renderScheduledTasks(tasks) {
    scheduledTasksTableBody.innerHTML = '';
    if (tasks.length === 0) {
        scheduledTasksTableBody.innerHTML = '<tr><td colspan="7">没有定时任务。</td></tr>';
        return;
    }

    tasks.forEach(task => {
        const row = scheduledTasksTableBody.insertRow();
        row.innerHTML = `
            <td>${task.name}</td>
            <td>${task.job_type === 'tmdb_auto_map' ? 'TMDB自动映射' : task.job_type}</td>
            <td>${task.cron_expression}</td>
            <td>${task.is_enabled ? '✅' : '❌'}</td>
            <td>${task.last_run_at ? new Date(task.last_run_at).toLocaleString() : '从未'}</td>
            <td>${task.next_run_at ? new Date(task.next_run_at).toLocaleString() : 'N/A'}</td>
            <td class="actions-cell">
                <div class="action-buttons-wrapper">
                    <button class="action-btn" data-action="run" data-task-id="${task.id}" title="立即运行">▶️</button>
                    <button class="action-btn" data-action="edit" data-task-id="${task.id}" title="编辑">✏️</button>
                    <button class="action-btn" data-action="delete" data-task-id="${task.id}" title="删除">🗑️</button>
                </div>
            </td>
        `;
        row.querySelector('[data-action="edit"]').addEventListener('click', () => showEditScheduledTaskView(task));
        row.querySelector('[data-action="run"]').addEventListener('click', () => handleScheduledTaskAction('run', task.id));
        row.querySelector('[data-action="delete"]').addEventListener('click', () => handleScheduledTaskAction('delete', task.id));
    });
}

function showEditScheduledTaskView(task = null) {
    switchView('edit-scheduled-task-view');
    editScheduledTaskForm.reset();
    const taskTypeSelect = document.getElementById('edit-scheduled-task-type');
    taskTypeSelect.innerHTML = '<option value="">加载中...</option>';
    taskTypeSelect.disabled = true;

    apiFetch('/api/ui/scheduled-tasks/available').then(jobs => {
        taskTypeSelect.innerHTML = '';
        if (jobs.length === 0) {
            taskTypeSelect.innerHTML = '<option value="">无可用任务</option>';
        } else {
            jobs.forEach(job => {
                const option = document.createElement('option');
                option.value = job.type;
                option.textContent = job.name;
                taskTypeSelect.appendChild(option);
            });
            taskTypeSelect.disabled = false;
        }
        if (task && typeof task.id !== 'undefined') {
            editScheduledTaskTitle.textContent = '编辑定时任务';
            document.getElementById('edit-scheduled-task-id').value = task.id;
            document.getElementById('edit-scheduled-task-name').value = task.name;
            taskTypeSelect.value = task.job_type;
            document.getElementById('edit-scheduled-task-cron').value = task.cron_expression;
            document.getElementById('edit-scheduled-task-enabled').checked = task.is_enabled;
        } else {
            editScheduledTaskTitle.textContent = '添加定时任务';
            document.getElementById('edit-scheduled-task-id').value = '';
        }
    }).catch(err => {
        taskTypeSelect.innerHTML = `<option value="">加载失败: ${err.message}</option>`;
    });
}

async function handleScheduledTaskFormSubmit(e) {
    e.preventDefault();
    const id = document.getElementById('edit-scheduled-task-id').value;
    const payload = {
        name: document.getElementById('edit-scheduled-task-name').value,
        job_type: document.getElementById('edit-scheduled-task-type').value,
        cron_expression: document.getElementById('edit-scheduled-task-cron').value,
        is_enabled: document.getElementById('edit-scheduled-task-enabled').checked,
    };
    const url = id ? `/api/ui/scheduled-tasks/${id}` : '/api/ui/scheduled-tasks';
    const method = id ? 'PUT' : 'POST';
    try {
        await apiFetch(url, { method, body: JSON.stringify(payload) });
        backToTasksFromEditBtn.click();
        loadAndRenderScheduledTasks();
    } catch (error) {
        alert(`保存失败: ${error.message}`);
    }
}

async function handleScheduledTaskAction(action, taskId) {
    if (action === 'delete' && confirm('确定要删除这个定时任务吗？')) {
        await apiFetch(`/api/ui/scheduled-tasks/${taskId}`, { method: 'DELETE' });
        loadAndRenderScheduledTasks();
    } else if (action === 'run') {
        await apiFetch(`/api/ui/scheduled-tasks/${taskId}/run`, { method: 'POST' });
        alert('任务已触发运行，请稍后刷新查看运行时间。');
    }
}

async function handleDeleteRunningTask() {
    const selectedTask = taskListUl.querySelector('.task-item.selected');
    if (!selectedTask) {
        alert('请先选择一个任务。');
        return;
    }
    const taskId = selectedTask.dataset.taskId;
    const status = selectedTask.dataset.status;
    if (status === '运行中') {
        alert('不能删除正在运行的任务。');
        return;
    }
    const taskTitle = selectedTask.querySelector('.task-title').textContent;

    if (confirm(`您确定要从历史记录中删除任务 "${taskTitle}" 吗？`)) {
        try {
            await apiFetch(`/api/ui/tasks/${taskId}`, { method: 'DELETE' });
            loadAndRenderTasks();
        } catch (error) {
            alert(`删除任务失败: ${error.message}`);
        }
    }
}

async function handlePauseResumeTask() {
    const selectedTask = taskListUl.querySelector('.task-item.selected');
    if (!selectedTask) {
        alert('请先选择一个任务。');
        return;
    }
    
    const taskId = selectedTask.dataset.taskId;
    const status = selectedTask.dataset.status;
    let endpoint = '';
    if (status === '运行中') endpoint = `/api/ui/tasks/${taskId}/pause`;
    else if (status === '已暂停') endpoint = `/api/ui/tasks/${taskId}/resume`;
    else return; // Not pausable or resumable

    try {
        await apiFetch(endpoint, { method: 'POST' });
        // The task list will auto-refresh and show the new status
    } catch (error) {
        alert(`操作失败: ${error.message}`);
    }
}

async function handleAbortRunningTask() {
    const selectedTask = taskListUl.querySelector('.task-item.selected');
    if (!selectedTask) {
        alert('请先选择一个任务。');
        return;
    }
    const taskId = selectedTask.dataset.taskId;
    const taskTitle = selectedTask.querySelector('.task-title').textContent;

    if (confirm(`您确定要中止任务 "${taskTitle}" 吗？\n此操作会尝试停止任务，如果无法停止，则会将其强制标记为“失败”状态。`)) {
        try {
            await apiFetch(`/api/ui/tasks/${taskId}/abort`, { method: 'POST' });
            // The task list will auto-refresh and show the new status, but we can trigger one manually for faster feedback.
            loadAndRenderTasks();
        } catch (error) {
            alert(`中止任务失败: ${error.message}`);
        }
    }
}

export function setupTasksEventListeners() {
    initializeElements();
    taskManagerSubNav.addEventListener('click', handleTaskManagerSubNav);
    runningTasksSearchInput.addEventListener('input', applyTaskFilters);
    runningTasksFilterButtons.addEventListener('click', handleTaskFilterClick);
    addScheduledTaskBtn.addEventListener('click', () => showEditScheduledTaskView());
    deleteRunningTaskBtn.addEventListener('click', handleDeleteRunningTask);
    pauseResumeTaskBtn.addEventListener('click', handlePauseResumeTask);
    abortRunningTaskBtn.addEventListener('click', handleAbortRunningTask);
    editScheduledTaskForm.addEventListener('submit', handleScheduledTaskFormSubmit);
    backToTasksFromEditBtn.addEventListener('click', () => {
        switchView('task-manager-view');
    });

    document.addEventListener('auth:status-changed', (e) => {
        if (e.detail.loggedIn) {
            if (taskLoadInterval) clearInterval(taskLoadInterval);
            taskLoadInterval = setInterval(loadAndRenderTasks, 2000);
        } else {
            if (taskLoadInterval) clearInterval(taskLoadInterval);
        }
    });

    document.addEventListener('viewchange', (e) => {
        if (e.detail.viewId === 'task-manager-view') {
            const firstSubNavBtn = taskManagerSubNav.querySelector('.sub-nav-btn');
            if (firstSubNavBtn) firstSubNavBtn.click();
            updateTaskActionButtons();
        }
    });

    taskListUl.addEventListener('click', handleTaskSelection);
}