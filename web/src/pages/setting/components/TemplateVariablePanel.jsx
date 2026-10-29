import { useState } from 'react'
import { Card, Input, Tag, Collapse, Tooltip, Empty, Space } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

const { Panel } = Collapse

/**
 * 模板变量面板 - 显示可用变量并支持搜索和插入
 */
export const TemplateVariablePanel = ({ variables = [], onInsert }) => {
  const { t } = useTranslation()
  const [searchText, setSearchText] = useState('')

  // 按类别分组
  const groupedVariables = variables.reduce((acc, variable) => {
    const category = variable.category || 'other'
    if (!acc[category]) {
      acc[category] = []
    }
    acc[category].push(variable)
    return acc
  }, {})

  // 类别显示名称映射
  const categoryNames = {
    status: t('notificationTemplate.categoryStatus'),
    media: t('notificationTemplate.categoryMedia'),
    source: t('notificationTemplate.categorySource'),
    result: t('notificationTemplate.categoryResult'),
    extra: t('notificationTemplate.categoryExtra'),
    other: t('notificationTemplate.categoryOther'),
  }

  // 过滤变量（按名称、标签、描述、示例）
  const filterVariables = (vars) => {
    if (!searchText) return vars
    const lowerSearch = searchText.toLowerCase()
    return vars.filter(v =>
      v.name.toLowerCase().includes(lowerSearch) ||
      v.label.toLowerCase().includes(lowerSearch) ||
      (v.description && v.description.toLowerCase().includes(lowerSearch)) ||
      (v.example && String(v.example).toLowerCase().includes(lowerSearch))
    )
  }

  // 渲染变量 Tag
  const renderVariableTag = (variable) => (
    <Tooltip
      key={variable.name}
      title={
        <div>
          <div><strong>{variable.label}</strong></div>
          {variable.description && <div>{variable.description}</div>}
          {variable.example && (
            <div style={{ marginTop: 4, fontSize: 12, opacity: 0.8 }}>
              {t('notificationTemplate.exampleLabel')}: {String(variable.example)}
            </div>
          )}
        </div>
      }
    >
      <Tag
        color="blue"
        style={{ cursor: 'pointer', marginBottom: 8 }}
        onClick={() => onInsert?.(variable.name)}
      >
        {variable.name}
      </Tag>
    </Tooltip>
  )

  const filteredGroupedVariables = Object.entries(groupedVariables)
    .map(([category, vars]) => ({
      category,
      variables: filterVariables(vars),
    }))
    .filter(group => group.variables.length > 0)

  return (
    <Card
      title={t('notificationTemplate.variablesTitle')}
      size="small"
      style={{ maxHeight: 400, overflow: 'auto' }}
    >
      {/* 搜索框 */}
      <Input
        prefix={<SearchOutlined />}
        placeholder={t('notificationTemplate.searchVariables')}
        value={searchText}
        onChange={(e) => setSearchText(e.target.value)}
        style={{ marginBottom: 16 }}
        allowClear
      />

      {/* 变量列表 */}
      {filteredGroupedVariables.length === 0 ? (
        <Empty
          description={searchText ? t('notificationTemplate.noMatchVariables') : t('notificationTemplate.noVariables')}
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      ) : (
        <Collapse
          defaultActiveKey={filteredGroupedVariables.map(g => g.category)}
          ghost
          size="small"
        >
          {filteredGroupedVariables.map(({ category, variables: vars }) => (
            <Panel
              key={category}
              header={
                <Space>
                  {categoryNames[category] || category}
                  <span style={{ color: '#999', fontSize: 12 }}>({vars.length})</span>
                </Space>
              }
            >
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {vars.map(renderVariableTag)}
              </div>
            </Panel>
          ))}
        </Collapse>
      )}
    </Card>
  )
}
