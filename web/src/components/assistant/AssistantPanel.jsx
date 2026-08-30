/**
 * 助手聊天面板（AssistantPanel）
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 AgentAssistantPanel.vue。
 * 用 antd Drawer 承载：顶部形象展示区 + 消息列表 + 输入框。
 * 回复内容用 react-markdown 渲染（项目已依赖）。
 *
 * 当前为纯 UI 外壳：sendMessage 走"假回复占位"，
 * 真正接 LLM 时只需替换 requestReply 的实现即可（已预留 TODO 口子）。
 */
import { useRef, useState, useCallback, useEffect } from 'react'
import { Drawer, Input, Button, Avatar, Dropdown, message as antdMessage } from 'antd'
import { SendOutlined, HistoryOutlined, PlusOutlined, DeleteOutlined, PaperClipOutlined } from '@ant-design/icons'
import Markdown from 'react-markdown'
import { AVATAR_IMG, getPetLabel } from './pet/petActions'
import { useAssistantChat } from './useAssistantChat'
import { useAssistantSessions, createSessionId } from './useAssistantSessions'

const { TextArea } = Input

// 发送给后端的最大历史轮数（控制 token），只取最近 N 条 user/assistant
const MAX_HISTORY = 20
const WELCOME = { role: 'bot', content: '御坂御坂在此待命，随时可以帮忙哦，御坂御坂精神满满地打招呼道！有什么想问的吗？' }

export function AssistantPanel({ open, onClose, machine, isMobile }) {
  const [messages, setMessages] = useState([WELCOME])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sessionId, setSessionId] = useState(() => createSessionId())
  const [sessions, setSessions] = useState([])
  const [pendingImages, setPendingImages] = useState([]) // 待发送图片 data URL
  const listRef = useRef(null)
  const fileInputRef = useRef(null)
  const { send: streamChat, abort } = useAssistantChat()
  const { listSessions, loadSession, saveSession, deleteSession } = useAssistantSessions()

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

  // 新建会话：清空消息、生成新 sessionId
  const newSession = useCallback(() => {
    abort()
    setSessionId(createSessionId())
    setMessages([WELCOME])
    setInput('')
    setSending(false)
  }, [abort])

  // 切换到历史会话：加载其消息
  const switchSession = useCallback(async sid => {
    if (sid === sessionId) return
    abort()
    try {
      const data = await loadSession(sid)
      setSessionId(data.sessionId)
      setMessages(data.messages?.length ? data.messages : [WELCOME])
      setSending(false)
    } catch {
      antdMessage.error('加载会话失败')
    }
  }, [sessionId, abort, loadSession])

  // 保存当前会话（对话结束后调用，只在有真实内容时）
  const persist = useCallback((msgs) => {
    const real = msgs.filter(m => m.content && !(m.role === 'bot' && m === WELCOME))
    if (real.length <= 1) return // 只有欢迎语不保存
    saveSession(sessionId, real.map(m => ({ role: m.role, content: m.content })))
      .then(refreshSessions)
  }, [sessionId, saveSession, refreshSessions])

  // 选择附件：图片转 base64 预览待发；文本文件读内容拼进输入框
  const handleFiles = useCallback(async (fileList) => {
    const files = Array.from(fileList || [])
    for (const f of files) {
      const isImage = f.type.startsWith('image/')
      if (isImage) {
        if (f.size > 4 * 1024 * 1024) { antdMessage.warning(`图片 ${f.name} 超过 4MB`); continue }
        if (pendingImages.length >= 3) { antdMessage.warning('最多 3 张图片'); break }
        const dataUrl = await new Promise(res => {
          const r = new FileReader()
          r.onload = () => res(r.result)
          r.readAsDataURL(f)
        })
        setPendingImages(prev => [...prev, dataUrl])
      } else {
        // 文本文件：限 256KB，读内容拼进输入
        if (f.size > 256 * 1024) { antdMessage.warning(`文件 ${f.name} 超过 256KB`); continue }
        try {
          const text = await f.text()
          // 简单二进制探测：含 NUL 字节视为二进制，拒绝
          if (text.includes('\u0000')) { antdMessage.warning(`${f.name} 疑似二进制文件，已忽略`); continue }
          setInput(prev => `${prev ? prev + '\n' : ''}【文件 ${f.name}】\n${text}`)
        } catch {
          antdMessage.error(`读取 ${f.name} 失败`)
        }
      }
    }
    if (fileInputRef.current) fileInputRef.current.value = ''
  }, [pendingImages])

  const removeImage = useCallback(idx => {
    setPendingImages(prev => prev.filter((_, i) => i !== idx))
  }, [])

  // 断流恢复：SSE 意外中断后，后端任务仍会跑完并存快照。
  // 这里带退避轮询会话详情，isProcessing 变 false 后用服务端消息恢复；多次失败则回退到 onFail。
  const recoverFromServer = useCallback((sid, onFail) => {
    let attempts = 0
    const poll = async () => {
      attempts += 1
      try {
        const data = await loadSession(sid)
        if (!data.isProcessing && data.messages?.length) {
          setMessages(data.messages) // 用服务端最终快照恢复
          machine.happy()
          setSending(false)
          refreshSessions()
          return
        }
      } catch {
        // 快照可能还没写，继续等
      }
      if (attempts >= 6) {
        onFail?.()
        return
      }
      setTimeout(poll, Math.min(6000, 1000 + attempts * 800))
    }
    setTimeout(poll, 1200)
  }, [loadSession, machine, refreshSessions])

  const send = useCallback(async (overrideText) => {
    // 允许传入文本（用于确认卡自动回复），否则取输入框内容
    const text = (typeof overrideText === 'string' ? overrideText : input).trim()
    // 允许"仅图片无文字"发送
    if ((!text && pendingImages.length === 0) || sending) return
    setInput('')
    setSending(true)
    machine.thinking() // 发送即进入思考态

    // 取出本轮图片附件并清空待发区
    const imgs = pendingImages
    setPendingImages([])

    // 先落用户消息（带图片），再追加一条空的 bot 消息用于流式填充
    let firstDelta = true
    setMessages(prev => [
      ...prev,
      { role: 'user', content: text, images: imgs },
      { role: 'bot', content: '', streaming: true },
    ])

    // 组装发给后端的历史（含本轮 user，排除正在流式的空 bot；role 映射 bot→assistant）
    const history = [...messages, { role: 'user', content: text, images: imgs }]
      .filter(m => m.content || (m.images && m.images.length))
      .slice(-MAX_HISTORY)
      .map(m => ({
        role: m.role === 'bot' ? 'assistant' : 'user',
        content: m.content,
        images: m.role === 'user' ? (m.images || []) : [],
      }))

    // 增量写入最后一条 bot 消息
    const appendToLastBot = (chunk, done = false, isErr = false) => {
      setMessages(prev => {
        const next = [...prev]
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].role === 'bot') {
            next[i] = {
              ...next[i],
              content: isErr ? chunk : next[i].content + chunk,
              streaming: !done,
            }
            break
          }
        }
        return next
      })
    }

    // 更新最后一条 bot 消息的工具状态标签（"御坂正在查询…"）
    const setLastBotTool = (label) => {
      setMessages(prev => {
        const next = [...prev]
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].role === 'bot') {
            next[i] = { ...next[i], toolLabel: label }
            break
          }
        }
        return next
      })
    }

    await streamChat(history, undefined, {
      onTool: ev => {
        // running 显示标签，done 清除
        setLastBotTool(ev.status === 'running' ? (ev.label || '正在处理') : '')
      },
      onConfirm: ev => {
        // 写类工具需二次确认：把确认卡挂到最后一条 bot 消息
        setMessages(prev => {
          const next = [...prev]
          for (let i = next.length - 1; i >= 0; i--) {
            if (next[i].role === 'bot') {
              next[i] = {
                ...next[i],
                streaming: false,
                toolLabel: '',
                confirm: ev, // {name,label,description,arguments}
                content: next[i].content || `御坂御坂需要你确认是否执行「${ev.label}」哦，御坂御坂谨慎地询问道。`,
              }
              break
            }
          }
          return next
        })
        machine.idle?.()
        setSending(false)
      },
      onDelta: piece => {
        if (firstDelta) {
          firstDelta = false
          machine.talking() // 首个增量到达 → 说话态
        }
        appendToLastBot(piece)
      },
      onDone: () => {
        appendToLastBot('', true)
        machine.happy() // 完成 → 表演后自动回落 idle
        setSending(false)
        // 用最新消息快照持久化会话
        setMessages(cur => { persist(cur); return cur })
      },
      onError: msg => {
        // 断流恢复：SSE 中断时后端任务仍会跑完并存快照，尝试轮询拉取最终结果
        recoverFromServer(sessionId, () => {
          appendToLastBot(msg || '对话出错了', true, true)
          machine.sad()
          setSending(false)
        })
      },
    }, sessionId)
  }, [input, sending, messages, machine, streamChat, persist, sessionId, recoverFromServer, pendingImages])

  // 停止：中断流，把当前流式 bot 消息定格，回落待命
  const handleStop = useCallback(() => {
    abort()
    setSending(false)
    setMessages(prev => {
      const next = [...prev]
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].role === 'bot' && next[i].streaming) {
          next[i] = {
            ...next[i],
            content: next[i].content || '（已停止）',
            streaming: false,
          }
          break
        }
      }
      return next
    })
    machine.idle?.()
  }, [abort, machine])

  // 用户对写类工具确认卡做出选择：确认=回一句同意让御坂继续执行；取消=回一句放弃
  const respondConfirm = useCallback((msgIndex, agree) => {
    setMessages(prev => {
      const next = [...prev]
      if (next[msgIndex]) next[msgIndex] = { ...next[msgIndex], confirm: null }
      return next
    })
    // 把用户的决定作为一条普通消息发出，御坂据此决定是否真正调用写工具
    const decision = agree ? '确认执行，请继续。' : '算了，取消吧。'
    send(decision)
  }, [send])

  return (
    <Drawer
      open={open}
      onClose={onClose}
      placement="right"
      width={isMobile ? '100%' : 380}
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Avatar className="assistant-title-avatar" src={AVATAR_IMG} size={36} />
          <div style={{ lineHeight: 1.2 }}>
            <div style={{ fontWeight: 600 }}>御坂助手</div>
            <div style={{ fontSize: 12, opacity: 0.6 }}>在线 · {getPetLabel(machine.state)}</div>
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
                : [{ key: 'empty', label: '暂无历史会话', disabled: true }],
            }}
          >
            <Button type="text" icon={<HistoryOutlined />} title="历史会话" />
          </Dropdown>
          <Button type="text" icon={<PlusOutlined />} title="新建对话" onClick={newSession} />
        </div>
      }
      styles={{ body: { display: 'flex', flexDirection: 'column', padding: 12 } }}
    >
      {/* 消息列表（顶部大立绘展示区已移除，只保留标题栏小头像） */}
      <div ref={listRef} className="assistant-msg-list" style={{ flex: 1, overflowY: 'auto' }}>
        {messages.map((m, i) => (
          <div key={i} className={`assistant-msg ${m.role === 'user' ? 'user' : 'bot'}`}>
            {m.role === 'bot' ? (
              <>
                {/* 工具调用进度卡：御坂正在查询… */}
                {m.toolLabel && (
                  <div className="assistant-tool-chip">🔧 {m.toolLabel}…</div>
                )}
                <Markdown>{m.content || ''}</Markdown>
                {/* 流式中且暂无内容也无工具时显示等待光标 */}
                {m.streaming && !m.content && !m.toolLabel && (
                  <span className="assistant-typing-dot">▍</span>
                )}
                {/* 写类工具二次确认卡 */}
                {m.confirm && (
                  <div className="assistant-confirm-card">
                    <div className="assistant-confirm-desc">
                      操作：{m.confirm.label}
                      {m.confirm.arguments && Object.keys(m.confirm.arguments).length > 0 && (
                        <span className="assistant-confirm-args">
                          （{Object.entries(m.confirm.arguments).map(([k, v]) => `${k}=${v}`).join(', ')}）
                        </span>
                      )}
                    </div>
                    <div className="assistant-confirm-btns">
                      <Button size="small" type="primary" onClick={() => respondConfirm(i, true)}>
                        确认执行
                      </Button>
                      <Button size="small" onClick={() => respondConfirm(i, false)}>
                        取消
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
                      <img key={k} src={src} alt="附件" className="assistant-msg-image" />
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
              <img src={src} alt="待发送" />
              <span className="assistant-pending-remove" onClick={() => removeImage(k)}>×</span>
            </div>
          ))}
        </div>
      )}

      {/* 输入区 */}
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
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
          title="添加附件（图片/文本文件）"
          onClick={() => fileInputRef.current?.click()}
        />
        <TextArea
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="输入消息，Enter 发送…"
          autoSize={{ minRows: 1, maxRows: 3 }}
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
          {sending ? '停止' : ''}
        </Button>
      </div>
    </Drawer>
  )
}
