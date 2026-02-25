import { Empty, Spin, Tabs } from 'antd'
import { useState, useEffect } from 'react'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store'
import {
  getConfigSchema,
  getGithubToken,
  saveGithubToken,
  verifyGithubToken,
} from '../../../apis'
import { GenericConfigItem } from './GenericConfigItem'
import { DatabaseBackupManager } from './DatabaseBackupManager'
import { DatabaseInfoPanel } from './DatabaseInfoPanel'
import { SortablePriorityList } from '../../../components/SortablePriorityList'

// GitHub Token 的特殊配置（使用自定义 API）
const GITHUB_TOKEN_CONFIG = {
  key: 'github_token',
  getApi: getGithubToken,
  saveApi: saveGithubToken,
  verifyApi: verifyGithubToken,
}

// 需要渲染自定义组件的分组（旧格式兼容）
const CUSTOM_COMPONENTS_BY_KEY = {
  database: DatabaseBackupManager,
}

// 通用组件类型映射（新格式）
const CUSTOM_COMPONENT_TYPES = {
  SortablePriorityList: SortablePriorityList,
  DatabaseBackupManager: DatabaseBackupManager,
}

export const Parameters = () => {
  const [schema, setSchema] = useState([])
  const [loading, setLoading] = useState(true)
  const isMobile = useAtomValue(isMobileAtom)

  useEffect(() => {
    loadSchema()
  }, [])

  const loadSchema = async () => {
    try {
      setLoading(true)
      const res = await getConfigSchema()
      setSchema(res.data || [])
    } catch (err) {
      console.error('加载配置 Schema 失败:', err)
      setSchema([])
    } finally {
      setLoading(false)
    }
  }

  // 为特殊配置项注入自定义 API
  const enrichConfig = (config) => {
    if (config.key === 'github_token') {
      return { ...config, ...GITHUB_TOKEN_CONFIG }
    }
    return config
  }

  if (loading) {
    return (
      <div className="flex justify-center items-center py-12">
        <Spin tip="加载中..." />
      </div>
    )
  }

  if (!schema || schema.length === 0) {
    return (
      <Empty
        description="暂无可调整的配置项"
        className="py-12"
      />
    )
  }

  // 构建 Tabs 的 items
  const tabItems = schema.map((group) => {
    // 解析自定义组件配置
    let CustomComponent = null
    let customComponentProps = {}

    // 新格式：customComponent 是对象 { type, props }
    if (group.customComponent && typeof group.customComponent === 'object') {
      CustomComponent = CUSTOM_COMPONENT_TYPES[group.customComponent.type]
      customComponentProps = group.customComponent.props || {}
    }
    // 旧格式兼容：通过 group.key 匹配
    else if (CUSTOM_COMPONENTS_BY_KEY[group.key]) {
      CustomComponent = CUSTOM_COMPONENTS_BY_KEY[group.key]
    }
    // 旧格式兼容：customComponent 是字符串
    else if (typeof group.customComponent === 'string') {
      CustomComponent = CUSTOM_COMPONENT_TYPES[group.customComponent]
    }

    return {
      key: group.key,
      label: group.label,
      children: (
        <div className="py-2 pb-4">
          {/* 数据库标签页顶部显示连接信息 */}
          {group.key === 'database' && <DatabaseInfoPanel />}
          {group.items?.map((item) => (
            <GenericConfigItem key={item.key} config={enrichConfig(item)} />
          ))}
          {/* 渲染自定义组件 */}
          {CustomComponent && <CustomComponent {...customComponentProps} />}
        </div>
      ),
    }
  })

  return (
    <Tabs
      defaultActiveKey={schema[0]?.key}
      items={tabItems}
      tabPosition={isMobile ? 'top' : 'left'}
      className="parameters-tabs"
    />
  )
}

