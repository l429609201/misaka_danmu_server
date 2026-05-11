import { useState, useEffect } from 'react'
import { Modal, Spin, Tag, Badge, Typography, Collapse, Timeline, Button } from 'antd'
import { getReleaseHistory } from '../apis'
import { useMessage } from '../MessageContext'
import dayjs from 'dayjs'
import ReactMarkdown from 'react-markdown'

const { Text } = Typography

/**
 * 预处理 GitHub Release 的 changelog 文本，使 ReactMarkdown 能正确渲染。
 */
const preprocessChangelog = (text) => {
  if (!text) return text
  return text.replace(/\r\n/g, '\n')
}

// Markdown 渲染样式
const markdownComponents = {
  // 链接
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--color-primary)' }} className="hover:underline">
      {children}
    </a>
  ),
  // 段落
  p: ({ children }) => <p className="my-2">{children}</p>,
  // 列表
  ul: ({ children }) => <ul className="list-disc list-inside my-2 space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside my-2 space-y-1">{children}</ol>,
  li: ({ children }) => <li className="ml-2">{children}</li>,
  // 代码
  code: ({ children }) => (
    <code style={{ backgroundColor: 'var(--color-hover)' }} className="px-1.5 py-0.5 rounded text-sm font-mono">
      {children}
    </code>
  ),
  // 代码块
  pre: ({ children }) => (
    <pre style={{ backgroundColor: 'var(--color-hover)' }} className="p-3 rounded-lg overflow-x-auto my-2">
      {children}
    </pre>
  ),
  // 引用块
  blockquote: ({ children }) => (
    <blockquote style={{ borderColor: 'var(--color-primary)', backgroundColor: 'var(--color-hover)' }} className="border-l-4 pl-4 py-1 my-2 rounded-r">
      {children}
    </blockquote>
  ),
  // 标题
  h1: ({ children }) => <h1 className="text-xl font-bold my-3">{children}</h1>,
  h2: ({ children }) => <h2 className="text-lg font-bold my-2">{children}</h2>,
  h3: ({ children }) => <h3 className="text-base font-bold my-2">{children}</h3>,
  // 强调
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
}

export const ReleaseHistoryModal = ({ open, onClose }) => {
  const [loading, setLoading] = useState(false)
  const [releaseHistory, setReleaseHistory] = useState([])
  const messageApi = useMessage()

  useEffect(() => {
    if (open && releaseHistory.length === 0) {
      loadReleaseHistory()
    }
  }, [open])

  const loadReleaseHistory = async () => {
    setLoading(true)
    try {
      const res = await getReleaseHistory(5)
      setReleaseHistory(res.data.releases || [])
    } catch (error) {
      console.error('加载历史版本失败:', error)
      messageApi.error('加载历史版本失败')
    } finally {
      setLoading(false)
    }
  }

  const latestVersion = releaseHistory.length > 0 ? releaseHistory[0].version : null

  return (
    <Modal
      title={
        <div className="flex items-center gap-2">
          <span>更新日志</span>
          {latestVersion && (
            <Tag color="green">最新版本: v{latestVersion}</Tag>
          )}
        </div>
      }
      open={open}
      onCancel={onClose}
      footer={null}
      width={700}
      styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}
    >
      <Spin spinning={loading}>
        {releaseHistory.length > 0 ? (
          <Timeline
            className="mt-4"
            items={releaseHistory.map((release, index) => ({
              color: index === 0 ? 'green' : 'gray',
              children: (
                <Collapse
                  size="small"
                  defaultActiveKey={index === 0 ? [release.version] : []}
                  items={[{
                    key: release.version,
                    label: (
                      <div className="flex items-center gap-2 flex-wrap">
                        <Tag color={index === 0 ? 'green' : 'default'}>
                          v{release.version}
                        </Tag>
                        {release.publishedAt && (
                          <Text type="secondary" className="text-xs">
                            {dayjs(release.publishedAt).format('YYYY-MM-DD HH:mm')}
                          </Text>
                        )}
                        {index === 0 && <Badge status="processing" text="最新" />}
                      </div>
                    ),
                    children: (
                      <div className="max-h-[300px] overflow-y-auto">
                        <div className="text-sm p-3 rounded" style={{ backgroundColor: 'var(--color-hover)' }}>
                          <ReactMarkdown components={markdownComponents}>
                            {preprocessChangelog(release.changelog) || '暂无更新说明'}
                          </ReactMarkdown>
                        </div>
                        {release.releaseUrl && (
                          <Button
                            type="link"
                            size="small"
                            href={release.releaseUrl}
                            target="_blank"
                            className="mt-2 p-0"
                          >
                            查看 GitHub Release
                          </Button>
                        )}
                      </div>
                    )
                  }]}
                />
              )
            }))}
          />
        ) : (
          !loading && <div className="text-center text-gray-500 py-8">暂无版本信息</div>
        )}
      </Spin>
    </Modal>
  )
}

export default ReleaseHistoryModal

