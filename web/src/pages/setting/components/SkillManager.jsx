import { Button, Card, Collapse, Input, Modal, Space, Switch, Table, Tag, message } from 'antd'
import { useEffect, useState } from 'react'
import { PlusOutlined, EditOutlined, DeleteOutlined, ReloadOutlined, EyeOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

const { TextArea } = Input

/**
 * 技能管理组件（参考 MoviePilot v3 设计）
 * 用户可自制 skill 到持久化目录 config/skills/
 * 支持 CRUD + 启停 + 热重载
 */
export const SkillManager = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [skills, setSkills] = useState([])
  const [editModalOpen, setEditModalOpen] = useState(false)
  const [viewModalOpen, setViewModalOpen] = useState(false)
  const [currentSkill, setCurrentSkill] = useState(null)
  const [formData, setFormData] = useState({
    skillId: '',
    name: '',
    description: '',
    content: '',
    allowedTools: '',
  })

  useEffect(() => {
    loadSkills()
  }, [])

  const loadSkills = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/ui/assistant/skills')
      const data = await res.json()
      setSkills(data.skills || [])
    } catch (error) {
      message.error('加载技能列表失败')
    } finally {
      setLoading(false)
    }
  }

  const handleCreate = () => {
    setCurrentSkill(null)
    setFormData({ skillId: '', name: '', description: '', content: '', allowedTools: '' })
    setEditModalOpen(true)
  }

  const handleEdit = async (skillId) => {
    try {
      const res = await fetch(`/api/ui/assistant/skills/${skillId}`)
      const skill = await res.json()
      setCurrentSkill(skill)
      setFormData({
        skillId: skill.skillId,
        name: skill.name,
        description: skill.description,
        content: skill.content,
        allowedTools: (skill.allowedTools || []).join(' '),
      })
      setEditModalOpen(true)
    } catch (error) {
      message.error('加载技能详情失败')
    }
  }

  const handleView = async (skillId) => {
    try {
      const res = await fetch(`/api/ui/assistant/skills/${skillId}`)
      const skill = await res.json()
      setCurrentSkill(skill)
      setViewModalOpen(true)
    } catch (error) {
      message.error('加载技能详情失败')
    }
  }

  const handleSave = async () => {
    if (!formData.name || !formData.description || !formData.content) {
      message.warning('请填写必填项（名称、触发描述、正文）')
      return
    }
    if (!currentSkill && !formData.skillId) {
      message.warning('创建时必须填写技能 ID（小写字母/数字/短横线）')
      return
    }

    const payload = {
      ...formData,
      allowedTools: formData.allowedTools.trim().split(/\s+/).filter(Boolean),
    }

    try {
      let res
      if (currentSkill) {
        // 更新
        res = await fetch(`/api/ui/assistant/skills/${currentSkill.skillId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
      } else {
        // 创建
        res = await fetch('/api/ui/assistant/skills', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
      }
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || '操作失败')
      }
      message.success(currentSkill ? '技能已更新' : '技能已创建')
      setEditModalOpen(false)
      loadSkills()
    } catch (error) {
      message.error(error.message || '保存失败')
    }
  }

  const handleDelete = (skillId) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除技能「${skillId}」吗？此操作不可恢复。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await fetch(`/api/ui/assistant/skills/${skillId}`, { method: 'DELETE' })
          if (!res.ok) throw new Error('删除失败')
          message.success('已删除')
          loadSkills()
        } catch (error) {
          message.error(error.message || '删除失败')
        }
      },
    })
  }

  const handleToggle = async (skillId, enabled) => {
    try {
      const res = await fetch(`/api/ui/assistant/skills/${skillId}/toggle`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      if (!res.ok) throw new Error('切换失败')
      message.success(enabled ? '已启用' : '已停用')
      loadSkills()
    } catch (error) {
      message.error(error.message || '操作失败')
    }
  }

  const handleReload = async () => {
    try {
      const res = await fetch('/api/ui/assistant/skills/reload', { method: 'POST' })
      if (!res.ok) throw new Error('重载失败')
      const data = await res.json()
      message.success(data.message || '已重载')
      loadSkills()
    } catch (error) {
      message.error(error.message || '重载失败')
    }
  }

  const columns = [
    { title: '技能 ID', dataIndex: 'skillId', key: 'skillId', width: 200 },
    { title: '名称', dataIndex: 'name', key: 'name', width: 150 },
    {
      title: '触发描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (text) => text || <span style={{ color: '#999' }}>无</span>,
    },
    { title: '版本', dataIndex: 'version', key: 'version', width: 80, render: (v) => `v${v}` },
    {
      title: '推荐工具',
      dataIndex: 'allowedTools',
      key: 'allowedTools',
      width: 200,
      render: (tools) => (
        <Space size={[0, 4]} wrap>
          {(tools || []).slice(0, 3).map((tool) => (
            <Tag key={tool} style={{ fontSize: 11 }}>{tool}</Tag>
          ))}
          {(tools || []).length > 3 && <Tag style={{ fontSize: 11 }}>+{tools.length - 3}</Tag>}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 100,
      render: (enabled, record) => (
        <Switch
          checked={enabled}
          size="small"
          onChange={(checked) => handleToggle(record.skillId, checked)}
        />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_, record) => (
        <Space size="small">
          <Button type="link" size="small" icon={<EyeOutlined />}
            onClick={() => handleView(record.skillId)}>查看</Button>
          <Button type="link" size="small" icon={<EditOutlined />}
            onClick={() => handleEdit(record.skillId)}>编辑</Button>
          <Button type="link" size="small" danger icon={<DeleteOutlined />}
            onClick={() => handleDelete(record.skillId)}>删除</Button>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Card
        title="御坂技能管理"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={handleReload}>重载目录</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新建技能</Button>
          </Space>
        }
      >
        <div style={{ marginBottom: 16, color: '#666', fontSize: 13, lineHeight: 1.8 }}>
          技能是针对特定场景的操作手册（含步骤与注意事项），存放在 <code>config/skills/&lt;技能ID&gt;/SKILL.md</code>。
          御坂在 system prompt 里只看到技能的名称与触发描述，判断需要时才读取正文，以此节省 token。
          你也可以直接把 SKILL.md 放进目录，再点「重载目录」生效。
        </div>
        <Table
          rowKey="skillId"
          columns={columns}
          dataSource={skills}
          loading={loading}
          size="middle"
          pagination={false}
          scroll={{ x: 1000 }}
        />
      </Card>

      {/* 编辑/创建弹窗 */}
      <Modal
        title={currentSkill ? `编辑技能：${currentSkill.name}` : '新建技能'}
        open={editModalOpen}
        onOk={handleSave}
        onCancel={() => setEditModalOpen(false)}
        width={800}
        okText="保存"
        cancelText="取消"
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>
              技能 ID {!currentSkill && <span style={{ color: '#ff4d4f' }}>*</span>}
            </div>
            <Input
              value={formData.skillId}
              disabled={!!currentSkill}
              placeholder="小写字母、数字、短横线，如 import-variety-batch"
              onChange={(e) => setFormData({ ...formData, skillId: e.target.value })}
            />
            {currentSkill && (
              <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
                技能 ID 是目录名，创建后不可修改
              </div>
            )}
          </div>
          <div>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>
              名称 <span style={{ color: '#ff4d4f' }}>*</span>
            </div>
            <Input
              value={formData.name}
              placeholder="中文名称，如「批量导入综艺」"
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>
              触发描述 <span style={{ color: '#ff4d4f' }}>*</span>
            </div>
            <TextArea
              value={formData.description}
              rows={2}
              placeholder="告诉御坂何时该用这个技能，写清触发场景。这段会进 system prompt，是御坂判断是否读取正文的唯一依据。"
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>推荐工具</div>
            <Input
              value={formData.allowedTools}
              placeholder="空格分隔，如 search_media import_selected（仅作提示，不强制限制）"
              onChange={(e) => setFormData({ ...formData, allowedTools: e.target.value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>
              作业指导书正文 <span style={{ color: '#ff4d4f' }}>*</span>
            </div>
            <TextArea
              value={formData.content}
              rows={14}
              placeholder={'Markdown 格式。建议包含：\n\n## 触发时机\n- 用户说"..."\n\n## 工作流程\n1. 第一步：调用 xxx 工具\n2. 第二步：...\n\n## 关键注意\n- 注意事项'}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              onChange={(e) => setFormData({ ...formData, content: e.target.value })}
            />
          </div>
        </Space>
      </Modal>

      {/* 查看弹窗 */}
      <Modal
        title={currentSkill ? `${currentSkill.name}（${currentSkill.skillId}）` : '技能详情'}
        open={viewModalOpen}
        onCancel={() => setViewModalOpen(false)}
        footer={[
          <Button key="close" onClick={() => setViewModalOpen(false)}>关闭</Button>,
          <Button key="edit" type="primary" onClick={() => {
            setViewModalOpen(false)
            handleEdit(currentSkill.skillId)
          }}>编辑</Button>,
        ]}
        width={800}
      >
        {currentSkill && (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <div>
              <Tag color={currentSkill.enabled ? 'green' : 'default'}>
                {currentSkill.enabled ? '已启用' : '已停用'}
              </Tag>
              <Tag>v{currentSkill.version}</Tag>
            </div>
            <div>
              <div style={{ fontWeight: 500, marginBottom: 4 }}>触发描述</div>
              <div style={{ color: '#666' }}>{currentSkill.description || '无'}</div>
            </div>
            {(currentSkill.allowedTools || []).length > 0 && (
              <div>
                <div style={{ fontWeight: 500, marginBottom: 4 }}>推荐工具</div>
                <Space size={[0, 4]} wrap>
                  {currentSkill.allowedTools.map((tool) => <Tag key={tool}>{tool}</Tag>)}
                </Space>
              </div>
            )}
            <div>
              <div style={{ fontWeight: 500, marginBottom: 4 }}>作业指导书正文</div>
              <pre style={{
                background: 'rgba(127,127,127,0.08)', padding: 12, borderRadius: 4,
                maxHeight: 400, overflow: 'auto', fontSize: 13, whiteSpace: 'pre-wrap',
                wordBreak: 'break-word', margin: 0,
              }}>
                {currentSkill.content}
              </pre>
            </div>
          </Space>
        )}
      </Modal>
    </div>
  )
}
