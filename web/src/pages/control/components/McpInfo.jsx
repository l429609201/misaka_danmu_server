import { Card, Collapse, Typography, Alert } from 'antd'
import { ApiOutlined, ThunderboltOutlined, SafetyOutlined, ToolOutlined } from '@ant-design/icons'

export const McpInfo = () => {
  return (
    <div className="my-6 space-y-4">
      <Card title="MCP Server" size="small">
        <Alert
          type="info"
          showIcon
          icon={<ApiOutlined />}
          message="Model Context Protocol (MCP)"
          description="弹幕库内置 MCP Server，AI Agent（如 Claude、Cursor、Cline 等）可通过 MCP 协议直接调用弹幕库的所有外部控制 API，实现自然语言驱动的弹幕管理。"
          className="mb-4"
        />

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
          <Card size="small" className="text-center">
            <ThunderboltOutlined className="text-2xl text-blue-500 mb-2" />
            <div className="text-sm font-medium">传输协议</div>
            <div className="text-xs text-gray-500">Streamable HTTP</div>
          </Card>
          <Card size="small" className="text-center">
            <SafetyOutlined className="text-2xl text-green-500 mb-2" />
            <div className="text-sm font-medium">认证方式</div>
            <div className="text-xs text-gray-500">X-API-KEY 请求头</div>
          </Card>
          <Card size="small" className="text-center">
            <ToolOutlined className="text-2xl text-orange-500 mb-2" />
            <div className="text-sm font-medium">端点地址</div>
            <Typography.Text code className="text-xs">/api/mcp</Typography.Text>
          </Card>
        </div>
      </Card>

      <Card title="📋 客户端配置" size="small">
        <div className="mb-2 text-sm text-gray-600 dark:text-gray-400">
          将以下 JSON 添加到你的 AI 客户端 MCP 配置文件中，替换地址和密钥即可连接：
        </div>
        <pre className="text-xs bg-gray-50 dark:bg-gray-800 rounded p-3 overflow-x-auto whitespace-pre-wrap break-all m-0">
{`{
  "mcpServers": {
    "misaka-danmu": {
      "type": "http",
      "url": "http://<你的地址>:7768/api/mcp",
      "headers": {
        "X-API-KEY": "<API密钥页面获取>"
      }
    }
  }
}`}
        </pre>
      </Card>

      <Card title="🔧 可用工具一览" size="small">
        <div className="mb-2 text-sm text-gray-600 dark:text-gray-400">
          连接成功后，AI Agent 可使用以下类别的工具（具体列表取决于已注册的外部控制 API）：
        </div>
        <Collapse size="small" items={[
          {
            key: 'search-import',
            label: '🔍 搜索与导入',
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>搜索媒体 — 从所有启用的弹幕源搜索</li>
                <li>全自动搜索并导入 — 一键搜索+导入</li>
                <li>直接导入搜索结果 / 编辑后导入 / XML导入 / URL导入</li>
                <li>获取搜索结果的分集列表</li>
              </ul>
            ),
          },
          {
            key: 'library',
            label: '📚 媒体库管理',
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>获取/搜索媒体库 — 浏览已收录作品</li>
                <li>获取作品详情、数据源、分集列表</li>
                <li>创建/编辑/删除作品、数据源、分集</li>
                <li>刷新分集弹幕、精确标记数据源</li>
              </ul>
            ),
          },
          {
            key: 'token',
            label: '🔑 Token 管理',
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>获取/创建/删除 Token</li>
                <li>启用/禁用 Token、重置调用次数</li>
                <li>查看 Token 访问日志</li>
              </ul>
            ),
          },
          {
            key: 'task',
            label: '⚙️ 任务与定时任务',
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>获取后台任务列表与状态</li>
                <li>中止/暂停/恢复任务</li>
                <li>获取所有定时任务与最近运行结果</li>
              </ul>
            ),
          },
          {
            key: 'config',
            label: '🛠️ 配置与弹幕源',
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>获取/更新弹幕输出设置、白名单配置项</li>
                <li>获取所有弹幕源配置（代理、黑名单、超时等）</li>
                <li>更新单个弹幕源配置</li>
              </ul>
            ),
          },
          {
            key: 'logs',
            label: '📄 日志与监控',
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>获取实时日志 / 历史日志文件列表</li>
                <li>读取指定日志文件内容</li>
                <li>获取流控状态</li>
              </ul>
            ),
          },
        ]} />
      </Card>

      <Card size="small">
        <div className="text-xs text-gray-500 dark:text-gray-400">
          <strong>提示：</strong>MCP 使用的 API 密钥与外部控制 API 相同，请在「API密钥」页面获取。
          所有通过 MCP 的访问都会记录在「API访问日志」中，带有 <Typography.Text code>MCP:</Typography.Text> 前缀。
        </div>
      </Card>
    </div>
  )
}
