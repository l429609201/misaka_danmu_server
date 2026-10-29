import assert from 'node:assert/strict'
import test from 'node:test'
import { AssistantDisplaySanitizer, restoreMessages, safeMessageContent, sanitizeAssistantText, sanitizeAssistantTimeline } from './assistantDisplaySanitizer.js'

const protocol = '<|DSML|calls><|DSML|invoke name="query"><|DSML|parameter name="sql">SELECT secret</|DSML|parameter></|DSML|invoke></|DSML|calls>'

test('所有二段切分与逐字符增量都不闪协议，保留前后正文', () => {
  const source = `之前${protocol}之后`
  for (let split = 0; split <= source.length; split += 1) {
    const parser = new AssistantDisplaySanitizer()
    const first = parser.feed(source.slice(0, split))
    const second = parser.feed(source.slice(split))
    assert.ok(['', '之', '之前', '之前之', '之前之后'].includes(first), `切分 ${split}: ${first}`)
    assert.equal(first + second + parser.finish(), '之前之后')
  }
  const parser = new AssistantDisplaySanitizer()
  let visible = ''
  for (const char of source) {
    visible += parser.feed(char)
    assert.ok('之前之后'.startsWith(visible), visible)
  }
  assert.equal(visible + parser.finish(), '之前之后')
})

test('前后两种 slash 闭标签与大小写混合在所有切分下保持一致', () => {
  for (const body of [
    protocol.replaceAll('</|DSML|', '<|DSML|/'),
    protocol.toLowerCase(),
    protocol.replaceAll('</|DSML|', '<|DSML|/').toLowerCase(),
    '<｜dsml｜calls><｜DsMl｜invoke>secret<｜DSML｜/invoke><｜dsml｜/calls>',
    '< dsml calls >< dsml parameter >secret< dsml /parameter >< dsml /calls >',
  ]) {
    const source = `前${body}后`
    for (let split = 0; split <= source.length; split += 1) {
      const parser = new AssistantDisplaySanitizer()
      const first = parser.feed(source.slice(0, split))
      assert.ok('前后'.startsWith(first), first)
      assert.equal(first + parser.feed(source.slice(split)) + parser.finish(), '前后')
    }
    const parser = new AssistantDisplaySanitizer()
    let visible = ''
    for (const char of source) {
      visible += parser.feed(char)
      assert.ok('前后'.startsWith(visible), visible)
    }
    assert.equal(visible + parser.finish(), '前后')
    assert.equal(restoreMessages([{ role: 'bot', content: source }])[0].content, '前后')
  }
  assert.equal(sanitizeAssistantText('<different>普通</different><D>普通</D>'), '<different>普通</different><D>普通</D>')
})

test('支持空格分隔、全角 pipe、单独 invoke/parameter 与重复 calls', () => {
  for (const body of [
    '< DSML calls >< DSML invoke name="x" >< DSML parameter name="y" >hidden</ DSML parameter ></ DSML invoke ></ DSML calls >',
    '<｜DSML｜calls><｜DSML｜invoke name="x">hidden</｜DSML｜invoke></｜DSML｜calls>',
    '< | DSML | calls >hidden</ | DSML | calls >',
    '<|DSML|invoke name="x">hidden</|DSML|invoke>',
    '<|DSML|parameter name="x">hidden</|DSML|parameter>',
    protocol + protocol,
  ]) {
    assert.equal(sanitizeAssistantText(`a${body}b`), 'ab')
    const parser = new AssistantDisplaySanitizer()
    assert.equal([...`a${body}b`].map(char => parser.feed(char)).join('') + parser.finish(), 'ab')
  }
})

test('普通 Markdown、代码、JSON、XML 和非协议 calls 完整保留', () => {
  const source = '正常回复\n```js\nconst ok = 2 < 3; console.log({ calls: true })\n```\n```xml\n<root><calls><invoke name="x"><parameter>ok</parameter></invoke></calls></root>\n```\n{"sql":"SELECT * FROM table"}\n<DSMLother>普通 XML</DSMLother>\n<|OTHER|calls>普通文本</|OTHER|calls>\n尾部 <'
  assert.equal(sanitizeAssistantText(source), source)
  const parser = new AssistantDisplaySanitizer()
  assert.equal([...source].map(char => parser.feed(char)).join('') + parser.finish(), source)
})

test('done/error 的 finish 丢弃未闭合协议与分段 marker，并幂等', () => {
  for (const suffix of ['<|DSML|cal', '<｜DSML｜invoke name="', '<DSML parameter>secret', protocol.slice(0, -8)]) {
    const parser = new AssistantDisplaySanitizer()
    assert.equal(parser.feed(`安全${suffix}`) + parser.finish(), '安全')
    assert.equal(parser.finish(), '')
    assert.equal(parser.feed('secret'), '')
  }
  const parser = new AssistantDisplaySanitizer()
  assert.equal(parser.feed('普通 <'), '普通 ')
  assert.equal(parser.finish(), '<')
})

test('旧会话清理 content，复制/导出/context 只清 bot/assistant，不改用户引用', () => {
  const user = { role: 'user', content: `请解释${protocol}`, images: ['data:image/png;base64,x'] }
  const old = { role: 'bot', content: `说明${protocol}结束`, timestamp: 123 }
  const restored = restoreMessages([user, old])
  assert.equal(restored[0], user)
  assert.equal(restored[1].content, '说明结束')
  assert.equal(restored[1].timestamp, 123)
  for (const role of ['bot', 'assistant']) {
    assert.equal(safeMessageContent({ ...old, role }), '说明结束')
  }
  assert.equal(safeMessageContent(user), user.content)
  assert.equal(old.content, `说明${protocol}结束`)
})

test('时间线跨文本/真实工具事件清理，不改工具时间/count/status/error', () => {
  const tool = { type: 'tool', tool_id: 'real', status: 'error', count: 7, elapsed_ms: 456, started_at: 'utc', error_message: '查询失败', error_code: 'FAILED' }
  const timeline = [
    { type: 'text', content: '前<|DSML|cal' }, tool,
    { type: 'text', content: 'ls><|DSML|invoke name="fake">secret</|DSML|invoke></|DSML|calls>后' },
  ]
  const cleaned = sanitizeAssistantTimeline(timeline)
  assert.deepEqual(cleaned.map(part => part.type === 'text' ? part.content : part), ['前', tool, '后'])
  assert.equal(cleaned[1], tool)
  const messages = restoreMessages([{ role: 'bot', content: `前${protocol}后`, events: [
    { type: 'thinking', status: 'done', elapsed_ms: 99 }, ...timeline.map(part => part.type === 'text' ? { ...part, type: 'delta' } : part),
  ] }])
  assert.equal(messages[0].content, '前后')
  assert.equal(messages[0].toolCount, 7)
  assert.equal(messages[0].thinking.elapsed_ms, 99)
  assert.deepEqual(messages[0].timeline, cleaned)
  assert.equal(messages[0].timeline.filter(part => part.type === 'tool').length, 1)
})

test('历史恢复保留轮次和正文归属，旧运行态不能继续旋转', () => {
  const [message] = restoreMessages([{ role: 'assistant', content: '第一轮第二轮', streaming: true, events: [
    { type: 'round', round_id: 'r1', status: 'running', started_at: '2026-10-10T01:00:00Z' },
    { type: 'delta', round_id: 'r1', content: '第一轮' },
    { type: 'round', round_id: 'r1', status: 'done', elapsed_ms: 100 },
    { type: 'round', round_id: 'r2', status: 'running', started_at: '2026-10-10T01:00:01Z' },
    { type: 'delta', round_id: 'r2', content: '第二轮' },
  ] }])
  assert.equal(message.role, 'bot')
  assert.equal(message.streaming, false)
  assert.deepEqual(message.timeline.filter(part => part.type === 'text').map(part => [part.round_id, part.content]), [['r1', '第一轮'], ['r2', '第二轮']])
  assert.equal(message.timeline.find(part => part.type === 'round' && part.round_id === 'r1').status, 'done')
})

test('无真实工具事件的 DSML 不产生工具或伪造调用次数', () => {
  const [message] = restoreMessages([{ role: 'bot', content: protocol, events: [{ type: 'delta', content: protocol }] }])
  assert.equal(message.content, '')
  assert.deepEqual(message.timeline, [])
  assert.equal(message.toolCount, 0)
})
