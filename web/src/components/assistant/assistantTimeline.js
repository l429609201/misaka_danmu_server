import { sanitizeAssistantTimeline } from './assistantDisplaySanitizer.js'

/** 只在当前流仍等待服务端时显示加载，历史运行态不能自行复活。 */
export function isAssistantWaiting(message, sending) {
  return Boolean(sending && message.streaming && !message.choice && !message.confirm && !message.error && !message.stopped)
}

/** 折叠正规轮次事件；旧时间线按相邻正文及随后工具保持原来的顺序。 */
export function groupAssistantRounds(message, { active = false } = {}) {
  const source = message.timeline?.length ? message.timeline : message.content ? [{ type: 'text', content: message.content }] : []
  const rounds = []
  const explicit = new Map()
  const tools = new Map()
  let current
  const create = id => {
    const round = { id: id ?? `legacy-${rounds.length}`, content: '', tools: [], thinking: null, boundary: null }
    rounds.push(round)
    if (id != null) explicit.set(String(id), round)
    return round
  }
  for (const part of sanitizeAssistantTimeline(source)) {
    if (part.type === 'tool' && part.tool_id != null && tools.has(String(part.tool_id))) {
      const previous = tools.get(String(part.tool_id))
      Object.assign(previous, part)
      continue
    }
    if (part.round_id != null) current = explicit.get(String(part.round_id)) || create(part.round_id)
    else if (!current || (part.type === 'text' && current.tools.length)) current = create()
    if (part.type === 'round') current.boundary = { ...current.boundary, ...part }
    if (part.type === 'thinking') current.thinking = { ...current.thinking, ...part }
    if (part.type === 'text') current.content += part.content || ''
    if (part.type === 'tool') {
      const tool = { ...part }
      current.tools.push(tool)
      if (part.tool_id != null) tools.set(String(part.tool_id), tool)
    }
  }
  if (!rounds.length && (active || message.thinking)) create()
  if (rounds.length && message.thinking && !rounds.some(round => round.thinking)) rounds.at(-1).thinking = message.thinking
  return rounds.map((round, index) => {
    const pending = round.tools.some(tool => tool.status === 'running') || round.boundary?.status === 'running' || round.thinking?.status === 'running'
    const failed = round.tools.some(tool => tool.status === 'error') || round.boundary?.status === 'error' || round.thinking?.status === 'error' || (index === rounds.length - 1 && message.error)
    const waiting = active && index === rounds.length - 1
    const status = waiting ? 'running' : failed ? 'error' : pending || (index === rounds.length - 1 && message.stopped) ? 'interrupted' : 'done'
    return { ...round, status, tools: round.tools.map(tool => ({ ...tool, status: tool.status === 'running' && !waiting ? 'interrupted' : tool.status })) }
  })
}

/** 按真实工具名称计次，同一个工具调用的状态更新不增加次数。 */
export function summarizeRoundTools(tools, labelForTool) {
  const groups = new Map()
  for (const tool of tools) {
    const name = tool.name || tool.label || 'tool'
    const group = groups.get(name) || { label: labelForTool(tool), count: 0 }
    group.count += 1
    groups.set(name, group)
  }
  return [...groups.values()]
}
