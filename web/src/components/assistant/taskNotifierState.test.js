/* eslint-env node */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mergeTaskSnapshot, serverTimestamp, taskEvents, noticeRemainingMs } from './taskNotifierState.js'

const baseline = serverTimestamp('2026-10-05T00:30:00')

test('单条气泡留存十秒，连续消息留存五秒', () => {
  assert.equal(noticeRemainingMs(1000, false, 1000), 10000)
  assert.equal(noticeRemainingMs(1000, true, 1000), 5000)
  assert.equal(noticeRemainingMs(1000, false, 6000), 5000)
  assert.equal(noticeRemainingMs(1000, false, 11000), 0)
})

test('新消息到达只缩短当前总时长，不追加五秒', () => {
  assert.equal(noticeRemainingMs(1000, true, 4000), 2000)
  assert.equal(noticeRemainingMs(1000, true, 8000), 0)
})
const task = (id, status, updatedAt = '2026-10-05T00:30:05') => ({
  taskId: id, status, title: '导入测试', createdAt: '2026-10-04T00:00:00', updatedAt,
})

test('首次轮询不播报历史任务', () => {
  assert.deepEqual(taskEvents(null, [task('old', '已完成')], baseline), [])
})

test('已知任务运行到完成只播报一次', () => {
  const previous = { a: task('a', '运行中') }
  const list = [task('a', '已完成')]
  assert.equal(taskEvents(previous, list, baseline)[0].kind, 'done')
  assert.deepEqual(taskEvents(mergeTaskSnapshot(previous, list), list, baseline), [])
})

test('未出现过运行态的短任务和旧任务最近完成都能播报', () => {
  const list = [task('fast', '已完成'), task('older', '失败')]
  assert.deepEqual(taskEvents({}, list, baseline).map(event => event.kind), ['done', 'failed'])
})

test('历史任务仅因分页被发现不能补播', () => {
  assert.deepEqual(taskEvents({}, [task('old', '已完成', '2026-10-04T23:00:00')], baseline), [])
})

test('任务暂时离开第一页仍保留状态避免重新进入时重复播报', () => {
  const previous = { old: task('old', '已完成') }
  const snapshot = mergeTaskSnapshot(previous, [task('new', '运行中')])
  assert.deepEqual(taskEvents(snapshot, [task('old', '已完成')], baseline), [])
  assert.equal(snapshot.old.status, '已完成')
})

test('长期监测快照容量有界且保留最近任务', () => {
  const list = Array.from({ length: 2100 }, (_, i) => task(`id-${i}`, '已完成'))
  const snapshot = mergeTaskSnapshot(null, list)
  assert.equal(Object.keys(snapshot).length, 2000)
  assert.equal(snapshot['id-2099'].status, '已完成')
  assert.equal(snapshot['id-0'], undefined)
})

test('服务器 naive 时间解析不受浏览器本地时区或时钟影响', () => {
  const original = process.env.TZ
  try {
    const values = ['UTC', 'Asia/Tokyo', 'America/New_York'].map(zone => {
      process.env.TZ = zone
      return serverTimestamp('2026-10-05T00:30:00')
    })
    assert.equal(new Set(values).size, 1)
    assert.equal(serverTimestamp('2026-10-05T00:30:00+08:00'), Date.parse('2026-10-04T16:30:00Z'))
    assert.ok(Number.isNaN(serverTimestamp(null)))
  } finally {
    if (original === undefined) delete process.env.TZ
    else process.env.TZ = original
  }
})
