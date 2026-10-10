/**
 * 仅清理助手正文中的 DeepSeek DSML 协议，不解释参数、不生成工具事件。
 * 每条流只喂新增 delta；普通 XML/代码在确认不是协议后原样释放。
 */
// 两种闭标签（</|DSML|calls> 与 <|DSML|/calls>）仅作为正文协议边界。
const HEADS = ['calls', 'invoke', 'parameter'].flatMap(name =>
  ['|dsml|', 'dsml'].flatMap(namespace => [`<${namespace}${name}`, `</${namespace}${name}`, `<${namespace}/${name}`]))
const compact = text => text.replace(/\s/g, '').replace(/｜/g, '|').toLowerCase()

export class AssistantDisplaySanitizer {
  constructor() {
    this.pending = ''
    this.stack = []
    this.marker = null
    this.finished = false
  }

  /** 增量输出已确认安全的正文，协议候选前缀不进入界面。 */
  feed(delta) {
    if (this.finished) return ''
    let output = ''
    const emit = text => { if (!this.stack.length) output += text }
    for (const char of String(delta || '')) {
      if (!this.pending) {
        if (char === '<') this.pending = char
        else emit(char)
        continue
      }
      if (this.marker) {
        // 参数内容始终丢弃；这里只等标签结束，不解析或执行属性。
        if (char === '>') {
          const { name, closing } = this.marker
          if (closing) {
            const index = this.stack.lastIndexOf(name)
            if (index >= 0) this.stack.length = index
          } else if (!this.pending.trimEnd().endsWith('/')) this.stack.push(name)
          this.pending = ''
          this.marker = null
        } else {
          // 无需保存任意长的属性，只保留自闭合标记判断所需的末字符。
          if (!/\s/.test(char)) this.pending = char
        }
        continue
      }
      const head = compact(this.pending)
      if (HEADS.includes(head) && (/[\s/>]/.test(char))) {
        this.marker = { name: head.match(/(calls|invoke|parameter)$/)[0], closing: head.includes('/') }
        if (char === '>') {
          if (this.marker.closing) {
            const index = this.stack.lastIndexOf(this.marker.name)
            if (index >= 0) this.stack.length = index
          } else this.stack.push(this.marker.name)
          this.pending = ''
          this.marker = null
        } else this.pending = char
        continue
      }
      const candidate = this.pending + char
      if (HEADS.some(value => value.startsWith(compact(candidate)))) this.pending = candidate
      else {
        // 普通标签不吞掉；新的左括号重新参与协议前缀检测。
        if (char === '<') { emit(this.pending); this.pending = '<' }
        else { emit(candidate); this.pending = '' }
      }
    }
    return output
  }

  /** 正常结束/错误结束均丢弃未闭合协议；普通比较符仍可输出。 */
  finish() {
    if (this.finished) return ''
    this.finished = true
    const head = compact(this.pending)
    const unsafe = this.marker || /^<\/?(?:\||d)/.test(head)
    const output = !this.stack.length && !unsafe ? this.pending : ''
    this.pending = ''
    this.stack = []
    this.marker = null
    return output
  }
}

/** 完整历史正文与流式正文使用同一解析器，避免不同入口漏清理。 */
export function sanitizeAssistantText(content) {
  const parser = new AssistantDisplaySanitizer()
  return parser.feed(content) + parser.finish()
}

/** 复制、导出及模型历史只清理 bot/assistant；用户引用保持原样。 */
export function safeMessageContent(message) {
  return message.role === 'bot' || message.role === 'assistant'
    ? sanitizeAssistantText(message.content)
    : message.content
}

/** 时间线跨 delta/真实工具事件共享解析状态；工具事件及错误完整保留。 */
export function sanitizeAssistantTimeline(timeline) {
  const parser = new AssistantDisplaySanitizer()
  const cleaned = (timeline || []).map(part => part.type === 'text'
    ? { ...part, content: parser.feed(part.content) }
    : part)
  const tail = parser.finish()
  if (tail) {
    const last = cleaned.findLastIndex(part => part.type === 'text')
    if (last >= 0) cleaned[last] = { ...cleaned[last], content: cleaned[last].content + tail }
  }
  return cleaned.filter(part => part.type !== 'text' || part.content)
}

/** 从正规服务端事件恢复展示；旧正文同样清理，不从 DSML 假造工具。 */
export function restoreMessages(messages) {
  return (messages || []).map(message => {
    if (message.role !== 'bot' && message.role !== 'assistant') return message
    const content = safeMessageContent(message)
    if (!Array.isArray(message.events)) return {
      ...message, role: 'bot', content, streaming: false,
      ...(Array.isArray(message.timeline) ? { timeline: sanitizeAssistantTimeline(message.timeline) } : {}),
    }
    const timeline = []
    let thinking
    let choice
    let toolCount = 0
    for (const event of message.events) {
      if (event.type === 'round' || event.type === 'thinking') {
        if (event.type === 'thinking') thinking = { ...thinking, ...event }
        if (event.type === 'round' || event.round_id != null) {
          const index = timeline.findIndex(part => part.type === event.type && part.round_id === event.round_id)
          if (index >= 0) timeline[index] = { ...timeline[index], ...event }
          else timeline.push({ ...event })
        }
      }
      if (event.type === 'choice') choice = { ...event, selectedOptionId: event.selectedOptionId ?? null }
      if (event.type === 'tool') {
        const index = timeline.findIndex(part => part.type === 'tool' && part.tool_id === event.tool_id)
        if (index >= 0) timeline[index] = { ...timeline[index], ...event }
        else timeline.push(event)
        toolCount = Math.max(toolCount, event.count || 0, timeline.filter(part => part.type === 'tool').length)
      }
      if (event.type === 'delta' || event.type === 'text') {
        if (timeline.at(-1)?.type === 'text' && timeline.at(-1).round_id === event.round_id) timeline.at(-1).content += event.content || ''
        else timeline.push({ type: 'text', content: event.content || '', ...(event.round_id != null ? { round_id: event.round_id } : {}) })
      }
    }
    if (!timeline.some(part => part.type === 'text') && message.content) timeline.push({ type: 'text', content: message.content })
    return { ...message, role: 'bot', content, streaming: false, timeline: sanitizeAssistantTimeline(timeline), thinking, choice, toolCount }
  })
}
