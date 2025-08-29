import { Modal } from 'antd'
import { createContext, useContext, useState, useMemo, useEffect } from 'react'

// 创建 Modal Context
const ModalContext = createContext(null)

// 生成唯一 ID
const generateId = () => Math.random().toString(36).substr(2, 9)

// Modal 提供组件
export function ModalProvider({ children }) {
  // 用数组管理多个 modal 实例
  const [modals, setModals] = useState([])
  // 跟踪当前最高 zIndex
  const [maxZIndex, setMaxZIndex] = useState(1000)

  // 打开普通 Modal
  const openModal = config => {
    const modalId = generateId()
    // 新 modal 的 zIndex 自动递增，确保显示在最上层
    const zIndex = maxZIndex + 1

    setMaxZIndex(zIndex)
    setModals(prev => [
      ...prev,
      {
        id: modalId,
        visible: true,
        zIndex,
        ...config,
      },
    ])

    return modalId // 返回 modalId 用于后续操作
  }

  // 打开确认 Modal
  const confirm = config => {
    return new Promise((resolve, reject) => {
      const modalId = generateId()
      const zIndex = maxZIndex + 1

      setMaxZIndex(zIndex)
      setModals(prev => [
        ...prev,
        {
          id: modalId,
          visible: true,
          zIndex,
          title: config.title || '确认',
          content: config.content,
          okText: config.okText || '确认',
          cancelText: config.cancelText || '取消',
          onOk: async () => {
            try {
              if (config.onOk) {
                await config.onOk() // 执行用户传入的 onOk
              }
              resolve()
              closeModal(modalId) // 关闭当前 modal
            } catch (error) {
              reject(error)
            }
          },
          onCancel: () => {
            if (config.onCancel) {
              config.onCancel()
            }
            reject(new Error('用户取消'))
            closeModal(modalId) // 关闭当前 modal
          },
          confirmLoading: config.confirmLoading || false,
          maskClosable: config.maskClosable || false,
          width: config.width || 520,
        },
      ])
    })
  }

  // 关闭指定 modal
  const closeModal = modalId => {
    setModals(prev =>
      prev.map(modal =>
        modal.id === modalId ? { ...modal, visible: false } : modal
      )
    )

    // 彻底从数组中移除已关闭的 modal（延迟执行确保动画完成）
    setTimeout(() => {
      setModals(prev => prev.filter(modal => modal.id !== modalId))
    }, 300)
  }

  // 关闭所有 modal
  const closeAllModals = () => {
    setModals(prev => prev.map(modal => ({ ...modal, visible: false })))
    setTimeout(() => setModals([]), 300)
  }

  // 包装所有方法
  const modalMethods = useMemo(
    () => ({
      open: openModal,
      confirm,
      close: closeModal,
      closeAll: closeAllModals,
    }),
    []
  )

  return (
    <ModalContext.Provider value={modalMethods}>
      {children}
      {/* 渲染所有 modal 实例 */}
      {modals.map(modal => (
        <Modal
          key={modal.id}
          title={modal.title}
          open={modal.visible}
          onOk={modal.onOk}
          onCancel={modal.onCancel}
          confirmLoading={modal.confirmLoading}
          maskClosable={modal.maskClosable}
          width={modal.width}
          zIndex={modal.zIndex}
          destroyOnHidden={true}
          afterClose={() => {
            if (modal.afterClose) modal.afterClose()
          }}
        >
          {modal.content}
        </Modal>
      ))}
    </ModalContext.Provider>
  )
}

// 自定义 Hook 用于获取 Modal 方法
export function useModal() {
  const context = useContext(ModalContext)
  if (!context) {
    throw new Error('useModal 必须在 ModalProvider 内部使用')
  }
  return context
}
