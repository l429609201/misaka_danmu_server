/* eslint-env node */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import { parse } from 'espree'

// 从实际页面提取回调，用本地替身验证轮询与提交，不访问服务或真实弹幕库。
const source = readFileSync(new URL('./index.jsx', import.meta.url), 'utf8')
const tree = parse(source, { ecmaVersion: 'latest', sourceType: 'module', range: true, ecmaFeatures: { jsx: true } })
const nodes = []
function walk(node) {
  if (!node || typeof node !== 'object') return
  if (node.type) nodes.push(node)
  for (const [key, value] of Object.entries(node)) {
    if (key === 'range') continue
    if (Array.isArray(value)) value.forEach(walk)
    else if (value && typeof value === 'object') walk(value)
  }
}
walk(tree)
const text = node => source.slice(...node.range)
const effect = nodes.find(node => node.type === 'CallExpression' && node.callee.name === 'useEffect'
  && text(node.arguments[0]).includes('const fetchActiveTasks'))
const handler = nodes.find(node => node.type === 'VariableDeclarator' && node.id.name === 'handleDelete')
const onOk = nodes.find(node => node.type === 'Property' && node.key.name === 'onOk'
  && node.range[0] >= handler.range[0] && node.range[1] <= handler.range[1])
assert.ok(effect && onOk)
const settle = async () => { await new Promise(resolve => setImmediate(resolve)) }

function harness({ active = [], detail, listFailure = false } = {}) {
  const timers = new Map()
  let timerId = 0
  const state = { ids: [], refresh: 0, groups: 0, errors: [], warnings: [], submits: 0, navigationPrompts: [] }
  const context = {
    Set, Map, Promise,
    deletingAnimeIdsRef: { current: new Set() },
    submittingDeleteIdsRef: { current: new Set() },
    pendingDeleteSyncIdsRef: { current: new Set() },
    deleteTasksRef: { current: new Map() },
    deleteRefreshRequestedRef: { current: false },
    deletePollingMountedRef: { current: false },
    setDeletingAnimeIds: ids => { state.ids = [...ids] },
    deletePollingCallbacksRef: { current: {
      getList: async () => { state.refresh += 1 },
      loadGroups: async () => { state.groups += 1 },
      messageApi: { error: msg => state.errors.push(msg) },
      t: (key, data) => `${key}:${data?.error || ''}`,
    } },
    window: {
      setTimeout: fn => { timers.set(++timerId, fn); return timerId },
      clearTimeout: id => timers.delete(id),
    },
    getTaskList: async ({ page, pageSize }) => {
      if (listFailure) throw new Error('offline')
      return { data: { list: active.slice((page - 1) * pageSize, page * pageSize), total: active.length } }
    },
    getTaskDetail: async id => {
      if (detail instanceof Error) throw detail
      if (!detail) throw { code: 404 }
      return { data: { taskId: id, ...detail } }
    },
    id: 42, deleteFiles: true,
    messageApi: { warning: msg => state.warnings.push(msg), error: msg => state.errors.push(msg) },
    t: key => key,
    deleteAnime: async () => { state.submits += 1; return { data: { taskId: 'fast', message: 'submitted' } } },
    goTask: data => state.navigationPrompts.push(data),
  }
  vm.createContext(context)
  const cleanup = vm.runInContext(`(${text(effect.arguments[0])})()`, context)
  const submit = vm.runInContext(`(${text(onOk.value)})`, context)
  return { state, context, cleanup, submit, timers,
    async tick() { const [id, callback] = timers.entries().next().value; timers.delete(id); await callback() },
  }
}

test('恢复超过一页的删除任务；中文活跃状态保持禁用', async () => {
  const active = Array.from({ length: 105 }, (_, i) => ({ taskId: `task-${i}`, status: '运行中', uniqueKey: `delete-anime-${i}` }))
  const h = harness({ active })
  await settle()
  assert.equal(h.state.ids.length, 105)
  assert.ok(h.state.ids.includes(104))
  assert.equal(h.state.refresh, 0)
  h.cleanup()
  assert.equal(h.timers.size, 0)
})

test('本页快速删除在活跃列表中未出现也能按ID确认并刷新', async () => {
  const h = harness({ detail: { status: '已完成' } })
  await settle()
  await h.submit()
  assert.equal(h.state.navigationPrompts[0].taskId, 'fast')
  assert.ok(h.context.deletingAnimeIdsRef.current.has(42))
  await h.tick()
  assert.equal(h.state.refresh, 1)
  assert.equal(h.state.groups, 1)
  assert.equal(h.state.ids.length, 0)
  h.cleanup()
})

test('删除失败刷新残留项目并显示失败，而非假装已删除', async () => {
  const h = harness({ detail: { status: '失败', description: 'fixture-error' } })
  await settle()
  await h.submit()
  await h.tick()
  assert.equal(h.state.refresh, 1)
  assert.equal(h.state.errors.length, 1)
  assert.match(h.state.errors[0], /fixture-error/)
  h.cleanup()
})

test('任务查询网络异常保持资源锁，下轮继续查询', async () => {
  const h = harness({ detail: new Error('offline') })
  await settle()
  await h.submit()
  await h.tick()
  assert.ok(h.context.deletingAnimeIdsRef.current.has(42))
  assert.equal(h.state.refresh, 0)
  assert.equal(h.timers.size, 1)
  h.cleanup()
})

test('提交期间同步锁挡住React渲染前的连续确认', async () => {
  const h = harness()
  await settle()
  let resolve
  h.context.deleteAnime = () => { h.state.submits += 1; return new Promise(done => { resolve = done }) }
  const first = h.submit()
  await h.submit()
  assert.equal(h.state.submits, 1)
  resolve({ data: { taskId: 'large' } })
  await first
  assert.ok(h.context.deletingAnimeIdsRef.current.has(42))
  h.cleanup()
})

test('后端409使用拦截器code提示，不当作普通提交失败', async () => {
  const h = harness()
  await settle()
  h.context.deleteAnime = async () => { throw { code: 409 } }
  await h.submit()
  assert.ok(h.context.deletingAnimeIdsRef.current.has(42))
  assert.equal(h.state.warnings[0], 'libraryPage.deleteAlreadyRunning')
  assert.equal(h.state.errors.length, 0)
  h.cleanup()
})

test('409发生于旧活跃快照返回前，旧快照不能解除新锁', async () => {
  const h = harness()
  await settle()
  let resolve
  h.context.getTaskList = () => new Promise(done => { resolve = done })
  const tick = h.tick()
  h.context.deleteAnime = async () => { throw { code: 409 } }
  await h.submit()
  resolve({ data: { list: [], total: 0 } })
  await tick
  assert.ok(h.context.deletingAnimeIdsRef.current.has(42))
  h.cleanup()
})

test('任务历史404重新拉取列表但不假报删除成功', async () => {
  const h = harness()
  await settle()
  await h.submit()
  await h.tick()
  assert.equal(h.state.refresh, 1)
  assert.equal(h.state.errors.length, 0)
  assert.equal(h.state.ids.length, 0)
  h.cleanup()
})

test('卸载期间返回的请求不更新状态或留下计时器', async () => {
  const h = harness()
  await settle()
  let resolve
  h.context.getTaskList = () => new Promise(done => { resolve = done })
  const tick = h.tick()
  h.cleanup()
  resolve({ data: { list: [{ taskId: 'late', uniqueKey: 'delete-anime-42' }], total: 1 } })
  await tick
  assert.equal(h.state.ids.length, 0)
  assert.equal(h.timers.size, 0)
})
