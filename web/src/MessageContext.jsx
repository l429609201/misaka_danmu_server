import { message } from 'antd'
import { createContext, useContext, useMemo } from 'react' // 从 React 导入

// 创建 Context
const MessageContext = createContext(null)

// 提供 Context 的组件
export function MessageProvider({ children }) {
  const [messageApi, contextHolder] = message.useMessage()

  // 包装常用方法
  const messageMethods = useMemo(
    () => ({
      success: (content, duration) => messageApi.success(content, duration),
      error: (content, duration) => messageApi.error(content, duration),
      info: (content, duration) => messageApi.info(content, duration),
      warning: (content, duration) => messageApi.warning(content, duration),
      loading: (content, duration) => messageApi.loading(content, duration),
      destroy: () => messageApi.destroy(),
    }),
    [messageApi]
  )

  return (
    <MessageContext.Provider value={messageMethods}>
      {contextHolder}
      {children}
    </MessageContext.Provider>
  )
}

// 自定义 Hook 用于获取 message 方法
export function useMessage() {
  const context = useContext(MessageContext)
  if (!context) {
    throw new Error('useMessage 必须在 MessageProvider 内部使用')
  }
  return context
}
