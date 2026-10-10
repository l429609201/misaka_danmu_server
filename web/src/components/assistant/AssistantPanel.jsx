/**
 * 助手聊天面板（AssistantPanel）
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 AgentAssistantPanel.vue。
 * 用 antd Drawer 承载：顶部形象展示区 + 消息列表 + 输入框。
 * 回复内容用 react-markdown + remark-gfm 渲染。
 * why remark-gfm：表格、删除线、任务列表属于 GFM 扩展语法，
 * 原版 Markdown 规范不含表格。缺这个插件时 LLM 输出的 | 表格 | 会原样显示成
 * 一堆竖线纯文本（加粗等基础语法却正常），故必须显式注入。
 * 这也是 personas.py 里 supports_table=True 能成立的前提。
 *
 * 对话通过 SSE 接收增量消息，写操作通过服务端令牌确认卡单次执行。
 */
import { useRef, useState, useCallback, useEffect, useMemo } from 'react'
import { Drawer, Input, Button, Avatar, Dropdown, Collapse, message as antdMessage } from 'antd'
import { SendOutlined, HistoryOutlined, PlusOutlined, DeleteOutlined, PaperClipOutlined, CopyOutlined, DownloadOutlined, LoadingOutlined, CheckCircleOutlined, CloseCircleOutlined, CloseOutlined, ToolOutlined } from '@ant-design/icons'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import Cookies from 'js-cookie'
import { AVATAR_IMG, getPetLabel } from './pet/petActions'
import { useAssistantChat } from './useAssistantChat'
import { useAssistantSessions, createSessionId } from './useAssistantSessions'
import { isSharedLibraryFileName } from './assistantCodePolicy'
import { restoreMessages, safeMessageContent } from './assistantDisplaySanitizer'
import { groupAssistantRounds, isAssistantWaiting, summarizeRoundTools } from './assistantTimeline'

const { TextArea } = Input

// 发送给后端的最大历史轮数（控制 token），只取最近 N 条 user/assistant
const MAX_HISTORY = 20

const elapsedSeconds = ms => `${(Math.max(0, ms || 0) / 1000).toFixed(1)}s`

// 只读取服务端给出的 UTC epoch 毫秒；不解析可能无时区的日期文本。
const choiceExpired = (choice, now) => choice?.expires_at_ms != null
  && Number.isFinite(Number(choice.expires_at_ms))
  && now >= Number(choice.expires_at_ms)

const codePreviewPassed = preview => preview?.validation?.validated === true && preview.validation.status === 'passed'

function AssistantCodePreview({ preview }) {
  const passed = codePreviewPassed(preview)
  const status = preview.validation?.status
  const label = passed ? '隔离验证通过' : status === 'not_run' || status === 'unavailable' ? '尚未完成隔离验证' : '隔离验证未通过'
  return (
    <div className="assistant-code-preview">
      <div className={`assistant-code-status ${passed ? 'is-passed' : 'is-blocked'}`}>
        {passed ? <CheckCircleOutlined /> : <CloseCircleOutlined />} {label}
        {preview.validation?.profile && <small>{preview.validation.profile}</small>}
      </div>
      <ul className="assistant-code-files">
        {(preview.files || []).map(file => <li key={file.path}>{file.action === 'add' ? '新增' : '修改'} <code>{file.path}</code></li>)}
      </ul>
      <Collapse ghost size="small" items={[{ key: 'diff', label: preview.diffTruncated ? '补丁差异（展示已截断）' : '补丁差异', children: <pre className="assistant-code-diff" tabIndex={0}>{preview.diff || '暂无差异'}</pre> }]} />
      <small>仅应用源码补丁，不自动部署或重启。语法检查不等同于行为测试。</small>
    </div>
  )
}

function ProgressIcon({ status }) {
  return status === 'running' ? <LoadingOutlined className="assistant-loading-ring" spin />
    : status === 'error' || status === 'interrupted' ? <CloseCircleOutlined /> : <CheckCircleOutlined />
}

function AssistantTimeline({ message, t, now, active }) {
  const rounds = groupAssistantRounds(message, { active })
  const statusLabel = status => t(`assistant.${status === 'running' ? 'progressWaiting' : status === 'error' ? 'toolError' : status === 'interrupted' ? 'progressInterrupted' : 'toolDone'}`)
  const toolLabel = tool => tool.name === 'code_search' ? t('assistant.toolSourceSearch')
    : tool.name === 'code_read' || tool.name === 'code_read_file' ? t('assistant.toolSourceRead')
      : tool.label || tool.name || t('assistant.processingTool')
  return rounds.map(round => {
    const timing = round.boundary || round.thinking
    const started = typeof timing?.started_at === 'number' ? timing.started_at : Date.parse(timing?.started_at)
    const elapsed = round.status === 'running' && Number.isFinite(started) ? now - started : timing?.elapsed_ms
    const summary = summarizeRoundTools(round.tools, toolLabel).map(group => t('assistant.toolSummaryCount', group)).join(t('assistant.toolSummarySeparator'))
    const showStatus = round.tools.length > 0 || timing || round.status !== 'done'
    return <div key={round.id} className="assistant-round">
      {round.content && <div className="assistant-timeline-text assistant-round-bubble"><Markdown remarkPlugins={[remarkGfm]}>{round.content}</Markdown></div>}
      {showStatus && (round.tools.length ? <details className={`assistant-round-details ${round.status}`}>
        <summary className={`assistant-round-status ${round.status}`}>
          <ProgressIcon status={round.status} />
          <span>{statusLabel(round.status)}{elapsed != null && ` · ${elapsedSeconds(elapsed)}`} <span className="assistant-round-tool-summary">({summary})</span></span>
        </summary>
        <div className="assistant-round-tools">
          {round.tools.map((tool, index) => <div key={tool.tool_id || index} className={`assistant-tool-entry ${tool.status || 'done'}`}>
            <ProgressIcon status={tool.status || 'done'} />
            <span>{tool.label || tool.name || t('assistant.processingTool')}
              {tool.error_message && <div className="assistant-tool-failure">{tool.error_message}</div>}
            </span>
            <small>{statusLabel(tool.status || 'done')}</small>
          </div>)}
        </div>
      </details> : <div className={`assistant-round-status ${round.status}`} role={round.status === 'running' ? 'status' : undefined}>
        <ProgressIcon status={round.status} /><span>{statusLabel(round.status)}{elapsed != null && ` · ${elapsedSeconds(elapsed)}`}</span>
      </div>)}
    </div>
  })
}

export function AssistantPanel({ open, onClose, machine, isMobile }) {
  const { t } = useTranslation()
  // 欢迎语随语言变化（人设文案已 i18n）
  const WELCOME = useMemo(() => ({ role: 'bot', content: t('assistant.welcome') }), [t])
  const [messages, setMessages] = useState([WELCOME])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  // 授权只保存在当前面板状态，不写入浏览器存储。
  const [codeRepair, setCodeRepair] = useState(false)
  const [now, setNow] = useState(Date.now())
  const ticking = open && sending
  const pendingChoice = open && messages.some(m => m.choice?.selectedOptionId == null && !m.choice?.invalid
    && m.choice?.expires_at_ms != null && !choiceExpired(m.choice, now))
  useEffect(() => {
    if (open) setNow(Date.now())
  }, [open])
  useEffect(() => {
    if (!ticking && !pendingChoice) return undefined
    const timer = setInterval(() => setNow(Date.now()), ticking ? 100 : 1000)
    return () => clearInterval(timer)
  }, [ticking, pendingChoice])
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem('assistantSessionId') || createSessionId())
  const [sessions, setSessions] = useState([])
  const [pendingImages, setPendingImages] = useState([]) // 待发送图片 data URL
  const listRef = useRef(null)
  const turnRef = useRef(0)
  const fileInputRef = useRef(null)
  const { send: streamChat, abort } = useAssistantChat()
  const { listSessions, loadSession, deleteSession } = useAssistantSessions()

  // 新消息自动滚到底部
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, open])

  // 组件卸载时中断流
  useEffect(() => () => abort(), [abort])

  // 打开面板时刷新会话列表
  const refreshSessions = useCallback(async () => {
    setSessions(await listSessions())
  }, [listSessions])
  useEffect(() => {
    if (open) refreshSessions()
  }, [open, refreshSessions])

  useEffect(() => {
    sessionStorage.setItem('assistantSessionId', sessionId)
  }, [sessionId])
  useEffect(() => {
    if (!open) return undefined
    const sid = sessionStorage.getItem('assistantSessionId')
    if (!sid) return undefined
    let cancelled = false
    loadSession(sid).then(data => {
      if (!cancelled && turnRef.current === 0 && data.messages?.length) setMessages(restoreMessages(data.messages))
    }).catch(error => {
      // 登录用户变化或会话已删除时，不再复用不可访问的旧 ID。
      if (!cancelled && turnRef.current === 0 && error?.status === 404) {
        const fresh = createSessionId()
        setSessionId(fresh)
        setMessages([WELCOME])
      }
    })
    return () => { cancelled = true }
  }, [open, loadSession, WELCOME])

  // 新建会话：清空消息、生成新 sessionId
  const newSession = useCallback(() => {
    turnRef.current += 1
    abort()
    setSessionId(createSessionId())
    setMessages([WELCOME])
    setInput('')
    setSending(false)
  }, [abort, WELCOME])

  // 切换到历史会话：加载其消息
  const switchSession = useCallback(async sid => {
    if (sid === sessionId) return
    const turn = ++turnRef.current
    abort()
    setSending(false)
    try {
      const data = await loadSession(sid)
      if (turnRef.current !== turn) return
      setSessionId(data.sessionId)
      setMessages(data.messages?.length ? restoreMessages(data.messages) : [WELCOME])
    } catch {
      if (turnRef.current === turn) antdMessage.error(t('assistant.loadSessionFailed'))
    }
  }, [sessionId, abort, loadSession, WELCOME, t])

  // 导出当前对话为纯文本文件下载
  const exportChat = useCallback(() => {
    // 导出也在出口清理，用户消息及其 DSML 引用不受影响。
    const real = messages.map(m => ({ ...m, content: safeMessageContent(m) })).filter(m => m.content)
    if (real.length === 0) { antdMessage.info(t('assistant.noExportContent')); return }
    const lines = real.map(m => `【${m.role === 'user' ? t('assistant.roleMe') : t('assistant.roleBot')}】\n${m.content}\n`)
    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `misaka_chat_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }, [messages, t])

  // 选择附件：图片转 base64 预览待发；文本文件读内容拼进输入框
  const handleFiles = useCallback(async (fileList) => {
    const files = Array.from(fileList || [])
    for (const f of files) {
      // 文件名策略先于 MIME 分支，伪装成图片的共享库也不得读取。
      if (isSharedLibraryFileName(f.name)) {
        antdMessage.warning(`禁止读取 .so 文件：${f.name}`)
        continue
      }
      const isImage = f.type.startsWith('image/')
      if (isImage) {
        if (f.size > 4 * 1024 * 1024) { antdMessage.warning(t('assistant.imgTooLarge', { name: f.name })); continue }
        if (pendingImages.length >= 3) { antdMessage.warning(t('assistant.imgMax')); break }
        const dataUrl = await new Promise(res => {
          const r = new FileReader()
          r.onload = () => res(r.result)
          r.readAsDataURL(f)
        })
        setPendingImages(prev => [...prev, dataUrl])
      } else {
        // 文本文件：限 256KB，读内容拼进输入
        if (f.size > 256 * 1024) { antdMessage.warning(t('assistant.fileTooLarge', { name: f.name })); continue }
        try {
          const text = await f.text()
          // 简单二进制探测：含 NUL 字节视为二进制，拒绝
          if (text.includes('\u0000')) { antdMessage.warning(t('assistant.fileBinary', { name: f.name })); continue }
          setInput(prev => `${prev ? prev + '\n' : ''}【${f.name}】\n${text}`)
        } catch {
          antdMessage.error(t('assistant.fileReadFailed', { name: f.name }))
        }
      }
    }
    if (fileInputRef.current) fileInputRef.current.value = ''
  }, [pendingImages, t])

  const removeImage = useCallback(idx => {
    setPendingImages(prev => prev.filter((_, i) => i !== idx))
  }, [])

  // 处理粘贴事件（支持粘贴图片）
  const handlePaste = useCallback(async (e) => {
    const items = e.clipboardData?.items
    if (!items) return

    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      const file = item.kind === 'file' ? item.getAsFile() : null
      if (file && isSharedLibraryFileName(file.name)) {
        e.preventDefault()
        antdMessage.warning(`禁止读取 .so 文件：${file.name}`)
        continue
      }
      if (item.type.startsWith('image/')) {
        e.preventDefault()
        if (pendingImages.length >= 3) {
          antdMessage.warning(t('assistant.imgMax'))
          break
        }
        if (!file) continue
        const reader = new FileReader()
        reader.onload = ev => {
          const dataUrl = ev.target?.result
          if (dataUrl) setPendingImages(prev => [...prev, dataUrl])
        }
        reader.readAsDataURL(file)
      }
    }
  }, [pendingImages, t])

  // 断流恢复：SSE 意外中断后，后端任务仍会跑完并存快照。
  // 这里带退避轮询会话详情，isProcessing 变 false 后用服务端消息恢复；多次失败则回退到 onFail。
  const recoverFromServer = useCallback((sid, turn, onFail) => {
    let attempts = 0
    const poll = async () => {
      if (turnRef.current !== turn) return
      attempts += 1
      try {
        const data = await loadSession(sid)
        if (turnRef.current !== turn) return
        if (!data.isProcessing && data.messages?.length) {
          setMessages(restoreMessages(data.messages)) // 用服务端最终快照恢复
          machine.chatTo('happy')
          setSending(false)
          refreshSessions()
          return
        }
      } catch {
        // 快照可能还没写，继续等
      }
      if (turnRef.current !== turn) return
      if (attempts >= 6) {
        onFail?.()
        return
      }
      setTimeout(poll, Math.min(6000, 1000 + attempts * 800))
    }
    setTimeout(poll, 1200)
  }, [loadSession, machine, refreshSessions])

  const send = useCallback(async (overrideText, options = {}) => {
    // 选项回调只提供服务端返回的后续文本；界面显示选中的安全标签。
    const text = (typeof overrideText === 'string' ? overrideText : input).trim()
    if ((!text && pendingImages.length === 0) || (sending && !options.resume)) return
    const baseMessages = options.baseMessages || messages
    const turn = ++turnRef.current
    const active = () => turnRef.current === turn
    setInput('')
    setSending(true)
    machine.chatTo('thinking') // 发送即进入思考态

    // 取出本轮图片附件并清空待发区
    const imgs = pendingImages
    setPendingImages([])

    // 先落用户消息（带图片），再追加一条空的 bot 消息用于流式填充
    let firstDelta = true
    const activeTools = new Set()
    const userMessage = { role: 'user', content: options.displayText || text, images: imgs }
    setMessages([
      ...baseMessages,
      userMessage,
      // 首个 SSE 事件前也显示思考中的转圈，避免短暂空白。
      { role: 'bot', content: '', timeline: [], streaming: true, thinking: { status: 'running', started_at: new Date().toISOString() } },
    ])

    // 后续文本由服务端生成；历史里的展示标签不得被模型误认作原始选择值。
    const history = [...baseMessages, { role: 'user', content: text, images: imgs }]
      .map(m => ({ ...m, content: safeMessageContent(m) }))
      .filter(m => m.content || (m.images && m.images.length))
      .slice(-MAX_HISTORY)
      .map(m => ({
        role: m.role === 'bot' ? 'assistant' : 'user',
        content: m.content,
        images: m.role === 'user' ? (m.images || []) : [],
      }))

    // 工具状态更新原时间线条目，正文只合并相邻的文本段。
    const updateBot = updater => setMessages(prev => {
      const next = [...prev]
      const index = next.findLastIndex(m => m.role === 'bot')
      if (index >= 0) next[index] = updater(next[index])
      return next
    })
    let currentRoundId
    const appendToLastBot = (chunk, done = false, isErr = false, roundId = currentRoundId) => updateBot(bot => {
      const timeline = [...(bot.timeline || [])]
      if (chunk) {
        if (!isErr && timeline.at(-1)?.type === 'text' && timeline.at(-1)?.round_id === roundId) {
          timeline[timeline.length - 1] = { ...timeline.at(-1), content: timeline.at(-1).content + chunk }
        } else timeline.push({ type: 'text', content: chunk, round_id: roundId })
      }
      return { ...bot, content: isErr ? chunk : bot.content + chunk, timeline, streaming: !done, ...(isErr ? { error: true } : {}) }
    })
    const finishThinking = status => updateBot(bot => bot.thinking?.status === 'running' ? {
      ...bot,
      thinking: { ...bot.thinking, status, elapsed_ms: Date.now() - (Date.parse(bot.thinking.started_at) || Date.now()) },
    } : bot)

    await streamChat(history, undefined, {
      onRound: ev => {
        if (!active()) return
        currentRoundId = ev.round_id
        updateBot(bot => {
          const timeline = [...(bot.timeline || [])]
          const index = timeline.findIndex(part => part.type === 'round' && part.round_id === ev.round_id)
          if (index >= 0) timeline[index] = { ...timeline[index], ...ev }
          else timeline.push(ev)
          return { ...bot, timeline }
        })
      },
      onTool: ev => {
        if (!active()) return
        if (ev.status === 'running') {
          activeTools.add(ev.tool_id)
          machine.chatTo('tool')
        } else {
          activeTools.delete(ev.tool_id)
          if (activeTools.size === 0) machine.chatTo(firstDelta ? 'thinking' : 'talking')
        }
        updateBot(bot => {
          const timeline = [...(bot.timeline || [])]
          const index = timeline.findIndex(part => part.type === 'tool' && part.tool_id === ev.tool_id)
          // 保留正规事件的计时与错误字段，绝不从正文协议补造状态。
          const entry = { ...ev, type: 'tool' }
          if (index >= 0) timeline[index] = { ...timeline[index], ...entry }
          else timeline.push(entry)
          return { ...bot, timeline, toolCount: Math.max(bot.toolCount || 0, ev.count || 0, timeline.filter(part => part.type === 'tool').length) }
        })
      },
      onThinking: ev => {
        if (!active()) return
        updateBot(bot => {
          const thinking = { ...bot.thinking, ...ev, started_at: ev.started_at || bot.thinking?.started_at || new Date().toISOString() }
          const timeline = [...(bot.timeline || [])]
          const index = timeline.findIndex(part => part.type === 'thinking' && part.round_id === ev.round_id)
          if (index >= 0) timeline[index] = { ...timeline[index], ...ev }
          else timeline.push({ ...ev, type: 'thinking' })
          return { ...bot, thinking, timeline }
        })
      },
      onChoice: ev => {
        if (!active()) return
        finishThinking('done')
        updateBot(bot => ({ ...bot, streaming: false, choice: { id: ev.id, title: ev.title, prompt: ev.prompt, expires_at_ms: ev.expires_at_ms, options: (ev.options || []).map(option => ({ id: option.id, label: option.label, description: option.description })), selectedOptionId: null } }))
        machine.chatTo('idle')
        setSending(false)
        refreshSessions()
      },
      onConfirm: ev => {
        if (!active()) return
        // 写类工具需二次确认：把确认卡挂到最后一条 bot 消息
        setMessages(prev => {
          const next = [...prev]
          for (let i = next.length - 1; i >= 0; i--) {
            if (next[i].role === 'bot') {
              next[i] = {
                ...next[i],
                streaming: false,
                confirm: ev, // {name,label,description,arguments}
                content: next[i].content || t('assistant.confirmPrompt', { label: ev.label }),
              }
              break
            }
          }
          return next
        })
        machine.chatTo('idle')
        setSending(false)
      },
      onDelta: (piece, ev) => {
        if (!active()) return
        if (firstDelta) {
          firstDelta = false
          if (activeTools.size === 0) machine.chatTo('talking') // 首个增量到达后，非工具阶段进入回复态
        }
        appendToLastBot(piece, false, false, ev?.round_id ?? currentRoundId)
      },
      onDone: () => {
        if (!active()) return
        appendToLastBot('', true)
        finishThinking('done')
        machine.chatTo('happy') // 完成 → 表演后自动回落 idle
        setSending(false)
        refreshSessions()
      },
      onServerError: msg => {
        if (!active()) return
        appendToLastBot(msg || t('assistant.replyError'), true, true)
        finishThinking('error')
        machine.chatTo('sad')
        setSending(false)
      },
      onError: msg => {
        if (!active()) return
        // 断流恢复只允许该轮更新当前面板。
        recoverFromServer(sessionId, turn, () => {
          if (!active()) return
          appendToLastBot(msg || t('assistant.replyError'), true, true)
          finishThinking('error')
          machine.chatTo('sad')
          setSending(false)
        })
      },
    }, sessionId, { codeRepair })
  }, [input, sending, codeRepair, messages, machine, streamChat, sessionId, recoverFromServer, refreshSessions, pendingImages, t])

  // 停止：中断流，把当前流式 bot 消息定格，回落待命
  const handleStop = useCallback(() => {
    turnRef.current += 1
    abort()
    setSending(false)
    setMessages(prev => {
      const next = [...prev]
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].role === 'bot' && next[i].streaming) {
          next[i] = {
            ...next[i],
            content: next[i].content || t('assistant.stopped'),
            timeline: next[i].content ? next[i].timeline : [...(next[i].timeline || []), { type: 'text', content: t('assistant.stopped') }],
            streaming: false,
            stopped: true,
            thinking: next[i].thinking?.status === 'running' ? {
              ...next[i].thinking, status: 'done', elapsed_ms: Date.now() - (Date.parse(next[i].thinking.started_at) || Date.now()),
            } : next[i].thinking,
          }
          break
        }
      }
      return next
    })
    machine.chatTo('idle')
  }, [abort, machine, t])

  // 确认卡只提交服务端签发的单次令牌；动作和参数从不由浏览器指定。
  const respondConfirm = useCallback(async (msgIndex, agree) => {
    const confirmation = messages[msgIndex]?.confirm
    if (!confirmation?.confirmationToken || confirmation.sessionId !== sessionId || sending) return
    if (agree && confirmation.codePreview && !codePreviewPassed(confirmation.codePreview)) return
    const turn = ++turnRef.current
    const active = () => turnRef.current === turn

    setSending(true)
    try {
      const res = await fetch('/api/ui/assistant/tool/execute', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${Cookies.get('danmu_token') || ''}`,
        },
        body: JSON.stringify({
          sessionId,
          confirmationToken: confirmation.confirmationToken,
          approved: agree,
        }),
      })
      const result = await res.json()
      if (!active()) return
      if (!res.ok && res.status === 409) {
        setMessages(prev => {
          const next = [...prev]
          if (next[msgIndex]) next[msgIndex] = { ...next[msgIndex], confirm: null }
          next.push({ role: 'bot', content: t('assistant.confirmExpired'), timestamp: Date.now() })
          return next
        })
        return
      }
      if (!res.ok) throw new Error(result.detail || t('assistant.execFailed'))

      setMessages(prev => {
        const next = [...prev]
        if (next[msgIndex]) next[msgIndex] = { ...next[msgIndex], confirm: null }
        next.push({
          role: 'bot',
          content: !agree
            ? t('assistant.operationCancelled')
            : result.ok
              ? (result.message || t('assistant.operationSubmitted'))
              : t('assistant.operationFailed', { error: result.error || t('assistant.unknownResult') }),
          timestamp: Date.now(),
        })
        return next
      })
    } catch (error) {
      if (active()) antdMessage.error(error.message || t('assistant.execFailed'))
    } finally {
      if (active()) {
        setSending(false)
        machine.chatTo('idle')
      }
    }
  }, [messages, sessionId, sending, machine, t])

  // 选项只提交服务端签发的标识；原始值与一次性校验均留在服务端。
  const respondChoice = useCallback(async (msgIndex, optionId) => {
    const choice = messages[msgIndex]?.choice
    if (!choice?.id || !optionId || choice.selectedOptionId != null || choice.invalid || choiceExpired(choice, Date.now()) || sending) return
    const turn = ++turnRef.current
    setSending(true)
    try {
      const res = await fetch('/api/ui/assistant/choice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${Cookies.get('danmu_token') || ''}` },
        body: JSON.stringify({ sessionId, choiceId: choice.id, optionId }),
      })
      const result = await res.json()
      if (turnRef.current !== turn) return
      if (!res.ok) {
        if (res.status === 409) setMessages(prev => prev.map((item, index) => index === msgIndex ? {
          ...item, choice: { ...item.choice, invalid: true },
        } : item))
        throw new Error(result.detail || t('assistant.choiceFailed', { defaultValue: '选项已失效，请重新提问' }))
      }
      const content = result.message?.content
      if (!result.ok || typeof content !== 'string' || !content.trim()) throw new Error(t('assistant.choiceFailed', { defaultValue: '选项已失效，请重新提问' }))
      const next = messages.map((item, index) => index === msgIndex ? {
        ...item, choice: { ...item.choice, selectedOptionId: optionId },
      } : item)
      setMessages(next)
      await send(content, { resume: true, baseMessages: next })
    } catch (error) {
      if (turnRef.current === turn) {
        setSending(false)
        antdMessage.error(error.message || t('assistant.choiceFailed', { defaultValue: '选择失败' }))
      }
    }
  }, [messages, sending, sessionId, send, t])

  return (
    <Drawer
      open={open}
      onClose={onClose}
      placement="right"
      width={isMobile ? '100vw' : 475}
      rootClassName={`assistant-panel${isMobile ? ' assistant-panel-mobile' : ''}`}
      closable={!isMobile}
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Avatar className="assistant-title-avatar" src={AVATAR_IMG} size={36} />
          <div className="assistant-header-text" style={{ lineHeight: 1.2 }}>
            <div style={{ fontWeight: 600 }}>{t('assistant.title')}</div>
            <div className="assistant-header-status" style={{ fontSize: 12, opacity: 0.6 }}>{sending ? t('assistant.progressWaiting') : t('assistant.online')} · {getPetLabel(machine.state, t)}</div>
          </div>
        </div>
      }
      extra={
        <div style={{ display: 'flex', gap: 4 }}>
          <Dropdown
            trigger={['click']}
            menu={{
              items: sessions.length
                ? sessions.map(s => ({
                    key: s.sessionId,
                    label: (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, maxWidth: 240 }}>
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {s.sessionId === sessionId ? '● ' : ''}{s.title}
                        </span>
                        <DeleteOutlined
                          onClick={async e => {
                            e.stopPropagation()
                            await deleteSession(s.sessionId)
                            if (s.sessionId === sessionId) newSession()
                            refreshSessions()
                          }}
                        />
                      </div>
                    ),
                    onClick: () => switchSession(s.sessionId),
                  }))
                : [{ key: 'empty', label: t('assistant.noHistory'), disabled: true }],
            }}
          >
            <Button type="text" icon={<HistoryOutlined />} title={t('assistant.historyTitle')} />
          </Dropdown>
          <Button type="text" icon={<PlusOutlined />} title={t('assistant.newChat')} onClick={newSession} />
          <Button type="text" icon={<DownloadOutlined />} title={t('assistant.exportChat')} onClick={exportChat} />
          {isMobile && <Button type="text" icon={<CloseOutlined />} aria-label={t('common.close')} title={t('common.close')} onClick={onClose} />}
        </div>
      }
      styles={{ body: { display: 'flex', flexDirection: 'column', minHeight: 0, padding: isMobile ? '8px 12px max(12px, env(safe-area-inset-bottom))' : 12 } }}
    >
      {/* 消息列表（顶部大立绘展示区已移除，只保留标题栏小头像） */}
      <div ref={listRef} className="assistant-msg-list" style={{ flex: 1, overflowY: 'auto' }}>
        {messages.map((m, i) => (
          <div key={i} className={`assistant-msg ${m.role === 'user' ? 'user' : 'bot'} ${m.choice || m.timeline?.some(part => part.type === 'tool') ? 'structured' : ''}`}>
            {m.role === 'bot' ? (
              <>
                <AssistantTimeline message={m} t={t} now={now} active={isAssistantWaiting(m, sending) && i === messages.length - 1} />
                {/* 复制按钮：非流式且有内容时显示（hover 出现） */}
                {!m.streaming && m.content && (
                  <span
                    className="assistant-msg-copy"
                    title={t('assistant.copy')}
                    onClick={() => {
                      navigator.clipboard?.writeText(safeMessageContent(m))
                        .then(() => antdMessage.success(t('assistant.copied')))
                        .catch(() => antdMessage.error(t('assistant.copyFailed')))
                    }}
                  >
                    <CopyOutlined />
                  </span>
                )}
                {/* 流式中且暂无内容也无工具时显示"正在输入"三点动画 */}
                {m.streaming && !m.content && !m.timeline?.length && !m.thinking && (
                  <span className="assistant-typing-dots" aria-label={t('assistant.typing')}>
                    <i></i><i></i><i></i>
                  </span>
                )}
                {/* 用户歧义选项只提交服务端签发的标识。 */}
                {m.choice && (
                  <div className="assistant-choice">
                    <strong>{m.choice.title || t('assistant.choiceTitle', { defaultValue: '请选择' })}</strong>
                    {m.choice.selectedOptionId == null && (m.choice.invalid || choiceExpired(m.choice, now)) && (
                      <span className="assistant-choice-expired">{m.choice.invalid
                        ? t('assistant.choiceInvalid', { defaultValue: '已失效' })
                        : t('assistant.choiceExpired', { defaultValue: '已过期' })}</span>
                    )}
                    {m.choice.prompt && <p>{m.choice.prompt}</p>}
                    <div className="assistant-choice-options">
                      {(m.choice.options || []).map((option, optionIndex) => (
                        <Button key={option.id || optionIndex} size="small" disabled={m.choice.selectedOptionId != null || m.choice.invalid || choiceExpired(m.choice, now) || sending} type={m.choice.selectedOptionId === option.id ? 'primary' : 'default'} title={option.description || option.label} onClick={() => respondChoice(i, option.id)}>
                          {option.label}
                        </Button>
                      ))}
                    </div>
                  </div>
                )}
                {/* 写类工具二次确认卡 */}
                {m.confirm && (
                  <div className="assistant-confirm-card">
                    <div className="assistant-confirm-desc">
                      {t('assistant.operation')}：{m.confirm.description || m.confirm.label}
                      {m.confirm.irreversible && <strong className="assistant-confirm-warning">{t('assistant.irreversibleAction')}</strong>}
                      {m.confirm.arguments && Object.keys(m.confirm.arguments).length > 0 && (
                        <span className="assistant-confirm-args">
                          {Object.entries(m.confirm.arguments).map(([key, value]) => (
                            <span key={key}>
                              {key}: {typeof value === 'object' && value !== null
                                ? Object.entries(value).map(([field, item]) => `${field}=${item}`).join(', ')
                                : value}
                              {' '}
                            </span>
                          ))}
                        </span>
                      )}
                    </div>
                    {m.confirm.codePreview && <AssistantCodePreview preview={m.confirm.codePreview} />}
                    <div className="assistant-confirm-btns">
                      <Button size="small" type="primary" disabled={!!m.confirm.codePreview && !codePreviewPassed(m.confirm.codePreview)} onClick={() => respondConfirm(i, true)}>
                        {t('assistant.confirmExec')}
                      </Button>
                      <Button size="small" onClick={() => respondConfirm(i, false)}>
                        {t('assistant.cancel')}
                      </Button>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <>
                {/* 用户发送的图片附件 */}
                {m.images && m.images.length > 0 && (
                  <div className="assistant-msg-images">
                    {m.images.map((src, k) => (
                      <img key={k} src={src} alt="" className="assistant-msg-image" />
                    ))}
                  </div>
                )}
                {m.content}
              </>
            )}
          </div>
        ))}
      </div>

      {/* 待发送图片预览 */}
      {pendingImages.length > 0 && (
        <div className="assistant-pending-images">
          {pendingImages.map((src, k) => (
            <div key={k} className="assistant-pending-image">
              <img src={src} alt="" />
              <span className="assistant-pending-remove" onClick={() => removeImage(k)}>×</span>
            </div>
          ))}
        </div>
      )}

      {/* 输入区 */}
      <div className="assistant-input-tools">
        <Button
          className="assistant-code-repair-toggle"
          size="small"
          type={codeRepair ? 'primary' : 'default'}
          icon={<ToolOutlined />}
          aria-pressed={codeRepair}
          disabled={sending}
          title="启用后，当前请求可自行修改隔离验证通过的源码；.so 文件禁止读取，未部署的容器不可应用；不会自动部署或重启，普通业务写操作仍需确认。"
          onClick={() => setCodeRepair(prev => !prev)}
        >
          代码修复
        </Button>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*,.txt,.md,.srt,.xml,.ass,.vtt,.json,.log"
          multiple
          style={{ display: 'none' }}
          onChange={e => handleFiles(e.target.files)}
        />
        <Button
          icon={<PaperClipOutlined />}
          title={t('assistant.attach')}
          onClick={() => fileInputRef.current?.click()}
        />
        <TextArea
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder={t('assistant.inputPlaceholder')}
          autoSize={{ minRows: 1, maxRows: 3 }}
          onPaste={handlePaste}
          onPressEnter={e => {
            if (!e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={sending ? handleStop : send}
          danger={sending}
        >
          {sending ? t('assistant.stop') : ''}
        </Button>
      </div>
    </Drawer>
  )
}
