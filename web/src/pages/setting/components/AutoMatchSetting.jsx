import React, { useState, useEffect } from 'react'
import { Form, Input, Select, Switch, Button, message, Spin, Card, Tabs, Space, Tooltip, Row, Col, Alert, Statistic, AutoComplete } from 'antd'
const { TextArea } = Input
const { TabPane } = Tabs
const { Option } = Select
import { getConfig, setConfig, getDefaultAIPrompts, getAIBalance, getAIModels } from '@/apis'
import api from '@/apis/fetch'
import { QuestionCircleOutlined, SaveOutlined, ThunderboltOutlined, CheckCircleOutlined, CloseCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import AIMetrics from './AIMetrics'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store/index.js'

const CustomSwitch = (props) => {
  return <Switch {...props} />
}

const AutoMatchSetting = () => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [matchMode, setMatchMode] = useState('traditional')
  const [fallbackEnabled, setFallbackEnabled] = useState(false)
  const [recognitionEnabled, setRecognitionEnabled] = useState(false)
  const [aliasExpansionEnabled, setAliasExpansionEnabled] = useState(false)
  const [nameConversionEnabled, setNameConversionEnabled] = useState(false)
  const [episodeGroupEnabled, setEpisodeGroupEnabled] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [selectedMetadataSource, setSelectedMetadataSource] = useState('tmdb')
  const [balanceInfo, setBalanceInfo] = useState(null)
  const [balanceLoading, setBalanceLoading] = useState(false)
  const [aiProviders, setAiProviders] = useState([])
  const [providersLoading, setProvidersLoading] = useState(false)
  const [selectedProvider, setSelectedProvider] = useState(null) // 当前选中的提供商配置
  const [dynamicModels, setDynamicModels] = useState({}) // 动态获取的模型列表，按提供商ID存储
  const [refreshingModels, setRefreshingModels] = useState(false) // 是否正在刷新模型列表
  const [selectedPromptType, setSelectedPromptType] = useState('aiRecognitionPrompt') // 当前选中的提示词类型
  const [selectedMatchPromptType, setSelectedMatchPromptType] = useState('aiPrompt') // AI自动匹配的提示词类型
  const isMobile = useAtomValue(isMobileAtom)

  // 加载配置
  const loadSettings = async (providers) => {
    try {
      setLoading(true)
      const [
        enabledRes,
        fallbackRes,
        providerRes,
        apiKeyRes,
        baseUrlRes,
        modelRes,
        promptRes,
        recognitionEnabledRes,
        recognitionPromptRes,
        aliasValidationPromptRes,
        aliasCorrectionEnabledRes,
        aliasExpansionEnabledRes,
        aliasExpansionPromptRes,
        nameConversionEnabledRes,
        nameConversionPromptRes,
        logRawResponseRes,
        homeSearchSeasonMappingRes,
        fallbackSearchSeasonMappingRes,
        webhookSeasonMappingRes,
        matchFallbackSeasonMappingRes,
        externalSearchSeasonMappingRes,
        autoImportSeasonMappingRes,
        seasonMappingSourceRes,
        seasonMappingPromptRes,
        episodeGroupEnabledRes,
        episodeGroupPromptRes
      ] = await Promise.all([
        getConfig('aiMatchEnabled'),
        getConfig('aiFallbackEnabled'),
        getConfig('aiProvider'),
        getConfig('aiApiKey'),
        getConfig('aiBaseUrl'),
        getConfig('aiModel'),
        getConfig('aiPrompt'),
        getConfig('aiRecognitionEnabled'),
        getConfig('aiRecognitionPrompt'),
        getConfig('aiAliasValidationPrompt'),
        getConfig('aiAliasCorrectionEnabled'),
        getConfig('aiAliasExpansionEnabled'),
        getConfig('aiAliasExpansionPrompt'),
        getConfig('aiNameConversionEnabled'),
        getConfig('aiNameConversionPrompt'),
        getConfig('aiLogRawResponse'),
        getConfig('homeSearchEnableTmdbSeasonMapping'),
        getConfig('fallbackSearchEnableTmdbSeasonMapping'),
        getConfig('webhookEnableTmdbSeasonMapping'),
        getConfig('matchFallbackEnableTmdbSeasonMapping'),
        getConfig('externalSearchEnableTmdbSeasonMapping'),
        getConfig('autoImportEnableTmdbSeasonMapping'),
        getConfig('seasonMappingMetadataSource'),
        getConfig('seasonMappingPrompt'),
        getConfig('aiEpisodeGroupEnabled'),
        getConfig('aiEpisodeGroupPrompt')
      ])

      const enabled = enabledRes.data.value === 'true'
      const fallback = fallbackRes.data.value === 'true'
      const recognition = recognitionEnabledRes.data.value === 'true'
      const aliasCorrection = aliasCorrectionEnabledRes.data.value === 'true'
      const aliasExpansion = aliasExpansionEnabledRes.data.value === 'true'
      const nameConversion = nameConversionEnabledRes.data.value === 'true'
      const logRawResponse = logRawResponseRes.data.value === 'true'
      const episodeGroup = episodeGroupEnabledRes.data.value === 'true'
      setMatchMode(enabled ? 'ai' : 'traditional')
      setFallbackEnabled(fallback)
      setRecognitionEnabled(recognition)
      setAliasExpansionEnabled(aliasExpansion)
      setNameConversionEnabled(nameConversion)
      setEpisodeGroupEnabled(episodeGroup)
      setSelectedMetadataSource(seasonMappingSourceRes.data.value || 'tmdb')

      const providerValue = providerRes.data.value || 'deepseek'

      form.setFieldsValue({
        aiMatchEnabled: enabled,
        aiFallbackEnabled: fallback,
        aiProvider: providerValue,
        aiApiKey: apiKeyRes.data.value || '',
        aiBaseUrl: baseUrlRes.data.value || '',
        aiModel: modelRes.data.value || 'deepseek-chat',
        aiPrompt: promptRes.data.value || '',
        aiRecognitionEnabled: recognition,
        aiRecognitionPrompt: recognitionPromptRes.data.value || '',
        aiAliasValidationPrompt: aliasValidationPromptRes.data.value || '',
        aiAliasCorrectionEnabled: aliasCorrection,
        aiAliasExpansionEnabled: aliasExpansion,
        aiAliasExpansionPrompt: aliasExpansionPromptRes.data.value || '',
        aiNameConversionEnabled: nameConversion,
        aiNameConversionPrompt: nameConversionPromptRes.data.value || '',
        aiLogRawResponse: logRawResponse,
        homeSearchEnableTmdbSeasonMapping: homeSearchSeasonMappingRes.data.value === 'true',
        fallbackSearchEnableTmdbSeasonMapping: fallbackSearchSeasonMappingRes.data.value === 'true',
        webhookEnableTmdbSeasonMapping: webhookSeasonMappingRes.data.value === 'true',
        matchFallbackEnableTmdbSeasonMapping: matchFallbackSeasonMappingRes.data.value === 'true',
        externalSearchEnableTmdbSeasonMapping: externalSearchSeasonMappingRes.data.value === 'true',
        autoImportEnableTmdbSeasonMapping: autoImportSeasonMappingRes.data.value === 'true',
        seasonMappingMetadataSource: seasonMappingSourceRes.data.value || 'tmdb',
        seasonMappingPrompt: seasonMappingPromptRes.data.value || '',
        aiEpisodeGroupEnabled: episodeGroup,
        aiEpisodeGroupPrompt: episodeGroupPromptRes.data.value || ''
      })

      // 设置当前选中的提供商配置
      if (providers && Array.isArray(providers) && providers.length > 0) {
        const provider = providers.find(p => p.id === providerValue)
        setSelectedProvider(provider)

        // 加载完成后,如果提供商支持余额查询,自动刷新余额
        if (provider?.supportBalance) {
          fetchBalance()
        }
      } else {
        // 如果 providers 为空,尝试从 aiProviders state 中查找
        const provider = aiProviders.find(p => p.id === providerValue)
        if (provider) {
          setSelectedProvider(provider)
          if (provider.supportBalance) {
            fetchBalance()
          }
        }
      }
    } catch (error) {
      console.error('加载配置失败:', error)
      message.error(`加载配置失败: ${error?.response?.data?.message || error?.message || error?.detail || String(error) || '未知错误'}`)
    } finally {
      setLoading(false)
    }
  }

  // 加载AI提供商列表
  const loadAIProviders = async () => {
    try {
      setProvidersLoading(true)
      const res = await api.get('/api/ui/config/ai/providers')
      const providers = res.data || []
      setAiProviders(providers)
      return providers
    } catch (error) {
      console.error('加载AI提供商列表失败:', error)
      // 使用默认配置
      const defaultProviders = [
        {
          id: 'deepseek',
          displayName: 'DeepSeek (推荐)',
          defaultModel: 'deepseek-chat',
          modelPlaceholder: 'deepseek-chat',
          baseUrlPlaceholder: 'https://api.deepseek.com (默认)'
        },
        {
          id: 'siliconflow',
          displayName: 'SiliconFlow 硅基流动',
          defaultModel: 'Qwen/Qwen2.5-7B-Instruct',
          modelPlaceholder: 'Qwen/Qwen2.5-7B-Instruct, deepseek-ai/DeepSeek-V2.5',
          baseUrlPlaceholder: 'https://api.siliconflow.cn/v1 (默认)'
        },
        {
          id: 'openai',
          displayName: 'OpenAI (兼容接口)',
          defaultModel: 'gpt-4-turbo',
          modelPlaceholder: 'gpt-4, gpt-4-turbo, gpt-3.5-turbo',
          baseUrlPlaceholder: 'https://api.openai.com/v1 (默认) 或自定义兼容接口'
        }
      ]
      setAiProviders(defaultProviders)
      return defaultProviders
    } finally {
      setProvidersLoading(false)
    }
  }

  useEffect(() => {
    const init = async () => {
      const providers = await loadAIProviders()
      await loadSettings(providers || aiProviders)
      // fetchBalance() 会在 loadSettings() 中根据提供商配置自动调用
    }
    init()
  }, [])

  // 更新选中的提供商配置
  const updateSelectedProvider = (providerId) => {
    const provider = aiProviders.find(p => p.id === providerId)
    setSelectedProvider(provider)

    // 如果提供商支持余额查询,自动刷新余额
    if (provider?.supportBalance) {
      fetchBalance()
    }
  }

  // 监听提供商变化
  const handleProviderChange = (providerId) => {
    updateSelectedProvider(providerId)
  }

  // 获取余额
  const fetchBalance = async () => {
    try {
      setBalanceLoading(true)
      const res = await getAIBalance()
      setBalanceInfo(res.data)
    } catch (error) {
      console.error('获取余额失败:', error)
      // 不显示错误消息,因为可能是提供商不支持
    } finally {
      setBalanceLoading(false)
    }
  }

  // 保存 Tab 1: AI连接配置
  const handleSaveConnectionConfig = async () => {
    try {
      setSaving(true)
      const values = form.getFieldsValue()

      await Promise.all([
        setConfig('aiProvider', values.aiProvider || ''),
        setConfig('aiApiKey', values.aiApiKey || ''),
        setConfig('aiBaseUrl', values.aiBaseUrl || ''),
        setConfig('aiModel', values.aiModel || ''),
        setConfig('aiLogRawResponse', values.aiLogRawResponse ? 'true' : 'false')
      ])

      message.success('AI连接配置保存成功')

      // 保存成功后重新加载余额
      if (selectedProvider?.supportBalance) {
        fetchBalance()
      }
    } catch (error) {
      console.error('保存配置失败:', error)
      message.error(`保存失败: ${error?.response?.data?.message || error?.message || '未知错误'}`)
    } finally {
      setSaving(false)
    }
  }

  // 保存 Tab 2: AI自动匹配
  const handleSaveMatchConfig = async () => {
    try {
      setSaving(true)
      const values = form.getFieldsValue()

      await Promise.all([
        setConfig('aiMatchEnabled', values.aiMatchEnabled ? 'true' : 'false'),
        setConfig('aiFallbackEnabled', values.aiFallbackEnabled ? 'true' : 'false'),
        setConfig('aiPrompt', values.aiPrompt || ''),
        setConfig('homeSearchEnableTmdbSeasonMapping', values.homeSearchEnableTmdbSeasonMapping ? 'true' : 'false'),
        setConfig('fallbackSearchEnableTmdbSeasonMapping', values.fallbackSearchEnableTmdbSeasonMapping ? 'true' : 'false'),
        setConfig('webhookEnableTmdbSeasonMapping', values.webhookEnableTmdbSeasonMapping ? 'true' : 'false'),
        setConfig('matchFallbackEnableTmdbSeasonMapping', values.matchFallbackEnableTmdbSeasonMapping ? 'true' : 'false'),
        setConfig('externalSearchEnableTmdbSeasonMapping', values.externalSearchEnableTmdbSeasonMapping ? 'true' : 'false'),
        setConfig('autoImportEnableTmdbSeasonMapping', values.autoImportEnableTmdbSeasonMapping ? 'true' : 'false'),
        setConfig('seasonMappingMetadataSource', values.seasonMappingMetadataSource || 'tmdb'),
        setConfig('seasonMappingPrompt', values.seasonMappingPrompt || ''),
        setConfig('aiEpisodeGroupEnabled', values.aiEpisodeGroupEnabled ? 'true' : 'false'),
        setConfig('aiEpisodeGroupPrompt', values.aiEpisodeGroupPrompt || '')
      ])

      message.success('AI自动匹配配置保存成功')
    } catch (error) {
      console.error('保存配置失败:', error)
      message.error(`保存失败: ${error?.response?.data?.message || error?.message || '未知错误'}`)
    } finally {
      setSaving(false)
    }
  }

  // 保存 Tab 3: AI识别增强
  const handleSaveRecognitionConfig = async () => {
    try {
      setSaving(true)
      const values = form.getFieldsValue()

      await Promise.all([
        setConfig('aiRecognitionEnabled', values.aiRecognitionEnabled ? 'true' : 'false'),
        setConfig('aiRecognitionPrompt', values.aiRecognitionPrompt || ''),
        setConfig('aiAliasValidationPrompt', values.aiAliasValidationPrompt || ''),
        setConfig('aiAliasCorrectionEnabled', values.aiAliasCorrectionEnabled ? 'true' : 'false'),
        setConfig('aiAliasExpansionEnabled', values.aiAliasExpansionEnabled ? 'true' : 'false'),
        setConfig('aiAliasExpansionPrompt', values.aiAliasExpansionPrompt || ''),
        setConfig('aiNameConversionEnabled', values.aiNameConversionEnabled ? 'true' : 'false'),
        setConfig('aiNameConversionPrompt', values.aiNameConversionPrompt || '')
      ])

      message.success('AI识别增强配置保存成功')
    } catch (error) {
      console.error('保存配置失败:', error)
      message.error(`保存失败: ${error?.response?.data?.message || error?.message || '未知错误'}`)
    } finally {
      setSaving(false)
    }
  }

  // 获取模型名称占位符
  const getModelPlaceholder = (provider) => {
    const providerConfig = aiProviders.find(p => p.id === provider)
    return providerConfig?.modelPlaceholder || '请输入模型名称'
  }

  // 刷新模型列表
  const handleRefreshModels = async () => {
    const currentProvider = form.getFieldValue('aiProvider')
    if (!currentProvider) {
      message.warning('请先选择AI提供商')
      return
    }

    try {
      setRefreshingModels(true)
      const response = await getAIModels(currentProvider, true)

      if (response.data.error) {
        message.warning(response.data.error)
      } else {
        // 更新动态模型列表
        setDynamicModels(prev => ({
          ...prev,
          [currentProvider]: response.data.models
        }))

        const newCount = response.data.newCount || 0
        if (newCount > 0) {
          message.success(`刷新成功！发现 ${newCount} 个新模型`)
        } else {
          message.success('刷新成功！模型列表已是最新')
        }
      }
    } catch (error) {
      console.error('刷新模型列表失败:', error)
      message.error('刷新失败: ' + (error.response?.data?.detail || error.message))
    } finally {
      setRefreshingModels(false)
    }
  }

  // 获取可选模型列表
  const getAvailableModels = (provider) => {
    const providerConfig = aiProviders.find(p => p.id === provider)

    // 优先使用动态获取的模型列表，否则使用硬编码列表
    const models = dynamicModels[provider] || providerConfig?.availableModels || []

    return models.map(model => ({
      value: model.value,
      label: (
        <div>
          <div style={{ fontWeight: 500 }}>
            {model.label}
            {model.isNew && <span style={{ marginLeft: '8px', color: '#52c41a', fontSize: '12px' }}>新</span>}
          </div>
          {model.description && (
            <div style={{ fontSize: '12px', color: '#999' }}>{model.description}</div>
          )}
        </div>
      )
    }))
  }

  // 获取Base URL占位符
  const getBaseUrlPlaceholder = (provider) => {
    const providerConfig = aiProviders.find(p => p.id === provider)
    return providerConfig?.baseUrlPlaceholder || '可选,用于自定义接口地址'
  }

  // 测试AI连接
  const handleTestConnection = async () => {
    try {
      setTesting(true)
      setTestResult(null)

      const values = form.getFieldsValue(['aiProvider', 'aiApiKey', 'aiBaseUrl', 'aiModel'])

      if (!values.aiProvider || !values.aiApiKey || !values.aiModel) {
        message.warning('请先填写AI提供商、API密钥和模型名称')
        return
      }

      const response = await api.post('/api/ui/config/ai/test', {
        provider: values.aiProvider,
        apiKey: values.aiApiKey,
        baseUrl: values.aiBaseUrl || null,
        model: values.aiModel
      })

      setTestResult(response.data)

      if (response.data.success) {
        message.success(`测试成功! 响应时间: ${response.data.latency}ms`)
      } else {
        message.error('测试失败,请查看详细信息')
      }
    } catch (error) {
      setTestResult({
        success: false,
        message: '测试请求失败',
        error: error?.response?.data?.message || error?.message || error?.detail || String(error) || '未知错误'
      })
      message.error(`测试失败: ${error?.response?.data?.message || error?.message || error?.detail || String(error) || '未知错误'}`)
    } finally {
      setTesting(false)
    }
  }

  // 填充默认提示词
  const handleFillDefaultPrompt = async (promptKey) => {
    try {
      const response = await getDefaultAIPrompts()
      const defaultValue = response.data[promptKey]

      if (defaultValue) {
        form.setFieldValue(promptKey, defaultValue)
        message.success('已填充默认提示词')
      } else {
        message.error('未找到默认提示词')
      }
    } catch (error) {
      console.error('获取默认提示词失败:', error)
      message.error(`获取默认提示词失败: ${error?.response?.data?.message || error?.message || '未知错误'}`)
    }
  }

  return (
    <Spin spinning={loading}>
      <Card>
        <Form
          form={form}
          layout="vertical"
          onValuesChange={(changedValues) => {
            if ('aiMatchEnabled' in changedValues) {
              setMatchMode(changedValues.aiMatchEnabled ? 'ai' : 'traditional')
            }
            if ('aiFallbackEnabled' in changedValues) {
              setFallbackEnabled(changedValues.aiFallbackEnabled)
            }
            if ('aiRecognitionEnabled' in changedValues) {
              setRecognitionEnabled(changedValues.aiRecognitionEnabled)
            }
            if ('aiAliasExpansionEnabled' in changedValues) {
              setAliasExpansionEnabled(changedValues.aiAliasExpansionEnabled)
            }
            if ('aiEpisodeGroupEnabled' in changedValues) {
              setEpisodeGroupEnabled(changedValues.aiEpisodeGroupEnabled)
            }
          }}
        >
          <Tabs defaultActiveKey="connection">
            {/* 标签页1: AI连接配置 */}
            <TabPane tab="AI连接配置" key="connection">
              <Form.Item
                name="aiProvider"
                label={
                  <Space>
                    <span>AI提供商</span>
                    <Tooltip title="选择AI服务提供商。不同提供商支持不同的模型和功能。">
                      <QuestionCircleOutlined />
                    </Tooltip>
                  </Space>
                }
                rules={[{ required: matchMode === 'ai', message: '请选择AI提供商' }]}
              >
                <Select loading={providersLoading} onChange={handleProviderChange}>
                  {aiProviders.map(provider => (
                    <Option key={provider.id} value={provider.id}>
                      {provider.displayName}
                    </Option>
                  ))}
                </Select>
              </Form.Item>

              <Form.Item
                name="aiApiKey"
                label={
                  <Space>
                    <span>API密钥</span>
                    <Tooltip title="从AI服务提供商获取的API密钥。必填项。">
                      <QuestionCircleOutlined />
                    </Tooltip>
                  </Space>
                }
                rules={[{ required: matchMode === 'ai', message: '请输入API密钥' }]}
              >
                <Input.Password placeholder="sk-..." />
              </Form.Item>

              <Form.Item
                noStyle
                shouldUpdate={(prevValues, currentValues) =>
                  prevValues.aiProvider !== currentValues.aiProvider
                }
              >
                {({ getFieldValue }) => (
                  <Form.Item
                    name="aiBaseUrl"
                    label={
                      <Space>
                        <span>Base URL</span>
                        <Tooltip title="自定义API接口地址。通常用于第三方兼容接口或代理服务。留空使用默认地址。">
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </Space>
                    }
                  >
                    <Input
                      placeholder={getBaseUrlPlaceholder(getFieldValue('aiProvider'))}
                    />
                  </Form.Item>
                )}
              </Form.Item>

              <Form.Item
                noStyle
                shouldUpdate={(prevValues, currentValues) =>
                  prevValues.aiProvider !== currentValues.aiProvider
                }
              >
                {({ getFieldValue }) => (
                  <Form.Item
                    label={
                      <Space>
                        <span>模型名称</span>
                        <Tooltip title="AI模型的名称。不同模型有不同的性能和价格。可从下拉列表选择或自定义输入。">
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </Space>
                    }
                  >
                    <Space.Compact style={{ width: '100%' }}>
                      <Form.Item
                        name="aiModel"
                        noStyle
                        rules={[{ required: matchMode === 'ai', message: '请输入模型名称' }]}
                      >
                        <AutoComplete
                          style={{ flex: 1 }}
                          options={getAvailableModels(getFieldValue('aiProvider'))}
                          placeholder={getModelPlaceholder(getFieldValue('aiProvider'))}
                          filterOption={(inputValue, option) =>
                            option.value.toLowerCase().includes(inputValue.toLowerCase())
                          }
                        />
                      </Form.Item>
                      <Tooltip title="从AI提供商API获取最新模型列表">
                        <Button
                          icon={<ReloadOutlined />}
                          loading={refreshingModels}
                          onClick={handleRefreshModels}
                          disabled={!getFieldValue('aiProvider')}
                        >
                          刷新
                        </Button>
                      </Tooltip>
                    </Space.Compact>
                  </Form.Item>
                )}
              </Form.Item>

              {/* 余额卡片 - 根据选中的提供商配置决定是否显示 */}
              {selectedProvider?.supportBalance && (
                <Form.Item label="账户余额">
                  <Space direction="vertical" style={{ width: '100%' }}>
                    {/* 余额卡片 */}
                    <Card size="small" style={{ marginBottom: '16px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ fontWeight: 500 }}>💰 账户余额</span>
                          <Tooltip title={`查询 ${selectedProvider.displayName} 账户余额`}>
                            <QuestionCircleOutlined />
                          </Tooltip>
                        </div>
                        <Button
                          size="small"
                          onClick={fetchBalance}
                          loading={balanceLoading}
                          icon={<ReloadOutlined />}
                        >
                          刷新
                        </Button>
                      </div>

                      {balanceInfo?.error ? (
                        <Alert
                          type="error"
                          message={balanceInfo.error}
                          showIcon
                        />
                      ) : balanceInfo?.data ? (
                        <Row gutter={16}>
                          <Col span={8}>
                            <Statistic
                              title="总余额"
                              value={balanceInfo.data.total_balance}
                              prefix={balanceInfo.data.currency === 'CNY' ? '¥' : '$'}
                              precision={2}
                            />
                          </Col>
                          <Col span={8}>
                            <Statistic
                              title="赠金余额"
                              value={balanceInfo.data.granted_balance}
                              prefix={balanceInfo.data.currency === 'CNY' ? '¥' : '$'}
                              precision={2}
                            />
                          </Col>
                          <Col span={8}>
                            <Statistic
                              title="充值余额"
                              value={balanceInfo.data.topped_up_balance}
                              prefix={balanceInfo.data.currency === 'CNY' ? '¥' : '$'}
                              precision={2}
                            />
                          </Col>
                        </Row>
                      ) : (
                        <div style={{ color: '#999', textAlign: 'center' }}>
                          点击刷新按钮查询余额
                        </div>
                      )}
                    </Card>
                  </Space>
                </Form.Item>
              )}

              {/* 测试结果 */}
              {testResult && (
                <Alert
                  type={testResult.success ? 'success' : 'error'}
                  message={
                    <Space>
                      {testResult.success ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
                      <span>{testResult.message}</span>
                      {testResult.latency && <span>({testResult.latency}ms)</span>}
                    </Space>
                  }
                  description={testResult.error}
                  showIcon={false}
                  closable
                  onClose={() => setTestResult(null)}
                  style={{ marginBottom: '16px' }}
                />
              )}

              {/* 测试、记录开关和保存按钮 */}
              <div style={{
                marginTop: '24px',
                display: 'flex',
                flexDirection: isMobile ? 'column' : 'row',
                justifyContent: 'center',
                alignItems: 'center',
                gap: '16px'
              }}>
                <Button
                  icon={<ThunderboltOutlined />}
                  onClick={handleTestConnection}
                  loading={testing}
                  size="large"
                  style={{ minWidth: '150px', width: isMobile ? '100%' : 'auto' }}
                >
                  测试 AI 连接
                </Button>

                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: isMobile ? '0' : '0 16px',
                  width: isMobile ? '100%' : 'auto',
                  justifyContent: isMobile ? 'center' : 'flex-start'
                }}>
                  <span style={{ fontSize: '14px', whiteSpace: 'nowrap' }}>记录响应</span>
                  <Form.Item name="aiLogRawResponse" valuePropName="checked" noStyle>
                    <CustomSwitch
                      checkedChildren="记录"
                      unCheckedChildren="不记录"
                    />
                  </Form.Item>
                  <Tooltip title="启用后，AI的所有原始响应将被记录到 config/logs/ai_responses.log 文件中，用于调试。">
                    <QuestionCircleOutlined style={{ color: '#999' }} />
                  </Tooltip>
                </div>

                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  onClick={handleSaveConnectionConfig}
                  loading={saving}
                  size="large"
                  style={{ minWidth: '150px', width: isMobile ? '100%' : 'auto' }}
                >
                  保存 AI 连接配置
                </Button>
              </div>
            </TabPane>

            {/* 标签页2: AI自动匹配 */}
            <TabPane tab="AI自动匹配" key="match">
              <Row gutter={[16, 16]}>
                <Col xs={24} sm={8}>
                  <Card size="small" style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 500 }}>匹配模式</span>
                        <Tooltip title="AI智能匹配: 使用大语言模型理解上下文,综合考虑标题、类型、季度、年份、集数和精确标记等因素,选择最佳匹配结果。传统匹配: 基于标题相似度和类型匹配的算法,快速但可能不够精准。">
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </div>
                      <Form.Item name="aiMatchEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch
                          checkedChildren="AI智能匹配"
                          unCheckedChildren="传统匹配"
                          checked={matchMode === 'ai'}
                          onChange={checked => {
                            setMatchMode(checked ? 'ai' : 'traditional')
                            form.setFieldValue('aiMatchEnabled', checked)
                          }}
                        />
                      </Form.Item>
                    </div>
                  </Card>
                </Col>
                <Col xs={24} sm={8}>
                  <Card size="small" style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 500 }}>传统匹配兜底</span>
                        <Tooltip title={matchMode === 'traditional' ? '传统匹配模式下无需兜底' : '当AI匹配失败时,自动降级到传统匹配算法,确保功能可用性'}>
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </div>
                      <Form.Item name="aiFallbackEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch disabled={matchMode === 'traditional'} />
                      </Form.Item>
                    </div>
                  </Card>
                </Col>
                <Col xs={24} sm={8}>
                  <Card size="small" style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 500 }}>AI剧集组选择</span>
                        <Tooltip title="当作品有TMDB ID但缺少剧集组映射时,使用AI自动从TMDB剧集组列表中选择最佳匹配。启用后可实现等价集数映射（如S01E26↔S02E01），提高跨季弹幕匹配准确率。">
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </div>
                      <Form.Item name="aiEpisodeGroupEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch
                          disabled={matchMode !== 'ai'}
                          onChange={(checked) => setEpisodeGroupEnabled(checked)}
                        />
                      </Form.Item>
                    </div>
                  </Card>
                </Col>
              </Row>

              {/* 季度映射配置 */}
              <Card
                title="季度映射"
                size="small"
                style={{ marginBottom: '16px' }}
              >
                <Row gutter={[16, 16]}>
                  <Col xs={24} sm={12}>
                    <Row gutter={[16, 16]}>
                      <Col xs={24} sm={12}>
                        <Card size="small" style={{ marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontWeight: 500 }}>主页搜索</span>
                              <Tooltip title="启用后，主页搜索时会通过元数据源获取季度名称">
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="homeSearchEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren="启用" unCheckedChildren="关闭" />
                            </Form.Item>
                          </div>
                        </Card>
                        <Card size="small" style={{ marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontWeight: 500 }}>后备搜索</span>
                              <Tooltip title="启用后，后备搜索时会通过元数据源获取季度名称">
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="fallbackSearchEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren="启用" unCheckedChildren="关闭" />
                            </Form.Item>
                          </div>
                        </Card>
                        <Card size="small" style={{ marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontWeight: 500 }}>后备匹配</span>
                              <Tooltip title="启用后，匹配后备时会通过元数据源获取季度名称">
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="matchFallbackEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren="启用" unCheckedChildren="关闭" />
                            </Form.Item>
                          </div>
                        </Card>
                      </Col>
                      <Col xs={24} sm={12}>
                        <Card size="small" style={{ marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontWeight: 500 }}>Webhook</span>
                              <Tooltip title="启用后，Webhook导入时会通过元数据源获取季度名称">
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="webhookEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren="启用" unCheckedChildren="关闭" />
                            </Form.Item>
                          </div>
                        </Card>
                        <Card size="small" style={{ marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontWeight: 500 }}>外部控制-搜索媒体</span>
                              <Tooltip title="启用后，外部控制搜索媒体时会通过元数据源获取季度名称">
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="externalSearchEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren="启用" unCheckedChildren="关闭" />
                            </Form.Item>
                          </div>
                        </Card>
                        <Card size="small" style={{ marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontWeight: 500 }}>外部控制-全自动导入</span>
                              <Tooltip title="启用后，外部控制全自动导入时会通过元数据源获取季度名称">
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="autoImportEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren="启用" unCheckedChildren="关闭" />
                            </Form.Item>
                          </div>
                        </Card>
                      </Col>
                    </Row>
                  </Col>
                  <Col xs={24} sm={12}>
                    <Card size="small" style={{ marginBottom: '16px' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                          <span style={{ fontWeight: 500 }}>元数据源选择</span>
                          <Tooltip title="选择用于季度映射的元数据源。只能选择一个源。">
                            <QuestionCircleOutlined />
                          </Tooltip>
                        </div>
                        <Form.Item name="seasonMappingMetadataSource" noStyle>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
                            {[
                              { value: 'tmdb', label: 'TMDB' },
                              { value: 'tvdb', label: 'TVDB' },
                              { value: 'imdb', label: 'IMDB' },
                              { value: 'douban', label: '豆瓣' },
                              { value: 'bangumi', label: 'Bangumi' }
                            ].map(source => (
                              <div
                                key={source.value}
                                onClick={() => {
                                  setSelectedMetadataSource(source.value)
                                  form.setFieldValue('seasonMappingMetadataSource', source.value)
                                }}
                                style={{
                                  border: '1px solid #d9d9d9',
                                  borderRadius: '4px',
                                  padding: '12px',
                                  textAlign: 'center',
                                  cursor: 'pointer',
                                  backgroundColor: selectedMetadataSource === source.value ? '#1890ff' : 'transparent',
                                  color: selectedMetadataSource === source.value ? '#fff' : 'inherit',
                                  transition: 'all 0.3s'
                                }}
                              >
                                {source.label}
                              </div>
                            ))}
                          </div>
                        </Form.Item>
                      </div>
                    </Card>
                  </Col>
                </Row>
              </Card>

              {/* 提示词配置区域 - 下拉框切换 */}
              <Card size="small" style={{ marginTop: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <Space>
                    <span style={{ fontWeight: 500 }}>提示词配置</span>
                    <Select
                      value={selectedMatchPromptType}
                      onChange={setSelectedMatchPromptType}
                      style={{ width: 200 }}
                      disabled={matchMode !== 'ai'}
                    >
                      <Option value="aiPrompt">AI匹配提示词</Option>
                      <Option value="seasonMappingPrompt">AI季度映射提示词</Option>
                      <Option value="aiEpisodeGroupPrompt">AI剧集组选择提示词</Option>
                    </Select>
                    <Tooltip title={
                      selectedMatchPromptType === 'aiPrompt'
                        ? "用于指导AI如何选择最佳匹配结果的提示词。留空使用默认提示词。高级用户可自定义以优化匹配效果。"
                        : selectedMatchPromptType === 'seasonMappingPrompt'
                        ? "用于指导AI从元数据源搜索结果中选择最佳匹配的提示词。留空使用默认提示词。"
                        : "用于指导AI从TMDB剧集组列表中选择最佳匹配的提示词。留空使用默认提示词。"
                    }>
                      <QuestionCircleOutlined />
                    </Tooltip>
                  </Space>
                  <Button
                    size="small"
                    icon={<ReloadOutlined />}
                    onClick={() => handleFillDefaultPrompt(selectedMatchPromptType)}
                    disabled={matchMode !== 'ai' || (
                      selectedMatchPromptType === 'aiEpisodeGroupPrompt' && !episodeGroupEnabled
                    )}
                  >
                    填充默认提示词
                  </Button>
                </div>

                {/* AI匹配提示词 */}
                <Form.Item
                  name="aiPrompt"
                  noStyle
                >
                  <TextArea
                    rows={10}
                    placeholder="留空使用默认提示词..."
                    style={{
                      fontFamily: 'monospace',
                      fontSize: '12px',
                      display: selectedMatchPromptType === 'aiPrompt' ? 'block' : 'none'
                    }}
                    disabled={matchMode !== 'ai'}
                  />
                </Form.Item>

                {/* AI季度映射提示词 */}
                <Form.Item
                  name="seasonMappingPrompt"
                  noStyle
                >
                  <TextArea
                    rows={10}
                    placeholder="留空使用默认提示词..."
                    style={{
                      fontFamily: 'monospace',
                      fontSize: '12px',
                      display: selectedMatchPromptType === 'seasonMappingPrompt' ? 'block' : 'none'
                    }}
                    disabled={matchMode !== 'ai'}
                  />
                </Form.Item>

                {/* AI剧集组选择提示词 */}
                <Form.Item
                  name="aiEpisodeGroupPrompt"
                  noStyle
                >
                  <TextArea
                    rows={10}
                    placeholder="留空使用默认提示词..."
                    style={{
                      fontFamily: 'monospace',
                      fontSize: '12px',
                      display: selectedMatchPromptType === 'aiEpisodeGroupPrompt' ? 'block' : 'none'
                    }}
                    disabled={matchMode !== 'ai' || !episodeGroupEnabled}
                  />
                </Form.Item>
              </Card>

              {/* 保存按钮 */}
              <div style={{ marginTop: '24px', textAlign: 'center' }}>
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  onClick={handleSaveMatchConfig}
                  loading={saving}
                  size="large"
                  style={{ minWidth: '200px' }}
                >
                  保存 AI 自动匹配配置
                </Button>
              </div>
            </TabPane>

            {/* 标签页3: AI识别增强 */}
            <TabPane tab="AI识别增强" key="recognition">
              <Row gutter={[16, 16]}>
                <Col xs={24} sm={6}>
                  <Card size="small" style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 500 }}>AI辅助识别</span>
                        <Tooltip title="使用AI从标题中提取结构化信息(作品名称、季度、类型等),提高TMDB搜索准确率。应用于TMDB自动刮削定时任务。">
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </div>
                      <Form.Item name="aiRecognitionEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch
                          disabled={matchMode !== 'ai'}
                          onChange={(checked) => setRecognitionEnabled(checked)}
                        />
                      </Form.Item>
                    </div>
                  </Card>
                </Col>
                <Col xs={24} sm={6}>
                  <Card size="small" style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 500 }}>AI别名修正</span>
                        <Tooltip title="使用AI修正已有的错误别名(例如中文别名字段写入了非中文内容)。启用后,TMDB自动刮削任务会强制更新所有别名字段。注意:已锁定的别名不会被修正。">
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </div>
                      <Form.Item name="aiAliasCorrectionEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch disabled={matchMode !== 'ai' || !recognitionEnabled} />
                      </Form.Item>
                    </div>
                  </Card>
                </Col>
                <Col xs={24} sm={6}>
                  <Card size="small" style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 500 }}>AI别名扩展</span>
                        <Tooltip title="当元数据源返回非中文标题时，使用AI生成可能的别名（中文、罗马音、英文缩写等），然后在Bangumi/Douban中搜索以获取中文标题。应用于外部控制API全自动导入、Webhook自动导入等场景。">
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </div>
                      <Form.Item name="aiAliasExpansionEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch
                          disabled={matchMode !== 'ai'}
                          onChange={(checked) => setAliasExpansionEnabled(checked)}
                        />
                      </Form.Item>
                    </div>
                  </Card>
                </Col>
                <Col xs={24} sm={6}>
                  <Card size="small" style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 500 }}>AI名称转换</span>
                        <Tooltip title="在搜索前将非中文搜索词（英文、日文、罗马音等）转换为中文名称，提高在中文弹幕源中的搜索准确率。当元数据源查询失败时作为兜底方案。">
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </div>
                      <Form.Item name="aiNameConversionEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch
                          disabled={matchMode !== 'ai'}
                          onChange={(checked) => setNameConversionEnabled(checked)}
                        />
                      </Form.Item>
                    </div>
                  </Card>
                </Col>
              </Row>

              {/* 提示词配置区域 - 下拉框切换 */}
              <Card size="small" style={{ marginTop: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <Space>
                    <span style={{ fontWeight: 500 }}>提示词配置</span>
                    <Select
                      value={selectedPromptType}
                      onChange={setSelectedPromptType}
                      style={{ width: 200 }}
                      disabled={matchMode !== 'ai'}
                    >
                      <Option value="aiRecognitionPrompt">AI识别提示词</Option>
                      <Option value="aiAliasValidationPrompt">AI别名验证提示词</Option>
                      <Option value="aiAliasExpansionPrompt">AI别名扩展提示词</Option>
                      <Option value="aiNameConversionPrompt">AI名称转换提示词</Option>
                    </Select>
                    <Tooltip title={
                      selectedPromptType === 'aiRecognitionPrompt'
                        ? "用于指导AI如何从标题中提取结构化信息的提示词。留空使用默认提示词。"
                        : selectedPromptType === 'aiAliasValidationPrompt'
                        ? "用于指导AI如何验证和分类别名的提示词。AI会识别别名的语言类型并验证是否真正属于该作品。"
                        : selectedPromptType === 'aiAliasExpansionPrompt'
                        ? "用于指导AI如何生成可能的别名的提示词。AI会生成中文译名、罗马音、英文缩写等别名。"
                        : "用于指导AI如何将非中文名称转换为中文名称的提示词。"
                    }>
                      <QuestionCircleOutlined />
                    </Tooltip>
                  </Space>
                  <Button
                    size="small"
                    icon={<ReloadOutlined />}
                    onClick={() => handleFillDefaultPrompt(selectedPromptType)}
                    disabled={matchMode !== 'ai' || (
                      (selectedPromptType === 'aiRecognitionPrompt' && !recognitionEnabled) ||
                      (selectedPromptType === 'aiAliasValidationPrompt' && !recognitionEnabled) ||
                      (selectedPromptType === 'aiAliasExpansionPrompt' && !aliasExpansionEnabled) ||
                      (selectedPromptType === 'aiNameConversionPrompt' && !nameConversionEnabled)
                    )}
                  >
                    填充默认提示词
                  </Button>
                </div>

                {/* AI识别提示词 */}
                <Form.Item
                  name="aiRecognitionPrompt"
                  noStyle
                  style={{ display: selectedPromptType === 'aiRecognitionPrompt' ? 'block' : 'none' }}
                >
                  <TextArea
                    rows={10}
                    placeholder="留空使用默认提示词..."
                    style={{
                      fontFamily: 'monospace',
                      fontSize: '12px',
                      display: selectedPromptType === 'aiRecognitionPrompt' ? 'block' : 'none'
                    }}
                    disabled={matchMode !== 'ai' || !recognitionEnabled}
                  />
                </Form.Item>

                {/* AI别名验证提示词 */}
                <Form.Item
                  name="aiAliasValidationPrompt"
                  noStyle
                  style={{ display: selectedPromptType === 'aiAliasValidationPrompt' ? 'block' : 'none' }}
                >
                  <TextArea
                    rows={10}
                    placeholder="留空使用默认提示词..."
                    style={{
                      fontFamily: 'monospace',
                      fontSize: '12px',
                      display: selectedPromptType === 'aiAliasValidationPrompt' ? 'block' : 'none'
                    }}
                    disabled={matchMode !== 'ai' || !recognitionEnabled}
                  />
                </Form.Item>

                {/* AI别名扩展提示词 */}
                <Form.Item
                  name="aiAliasExpansionPrompt"
                  noStyle
                  style={{ display: selectedPromptType === 'aiAliasExpansionPrompt' ? 'block' : 'none' }}
                >
                  <TextArea
                    rows={10}
                    placeholder="留空使用默认提示词..."
                    style={{
                      fontFamily: 'monospace',
                      fontSize: '12px',
                      display: selectedPromptType === 'aiAliasExpansionPrompt' ? 'block' : 'none'
                    }}
                    disabled={matchMode !== 'ai' || !aliasExpansionEnabled}
                  />
                </Form.Item>

                {/* AI名称转换提示词 */}
                <Form.Item
                  name="aiNameConversionPrompt"
                  noStyle
                  style={{ display: selectedPromptType === 'aiNameConversionPrompt' ? 'block' : 'none' }}
                >
                  <TextArea
                    rows={10}
                    placeholder="留空使用默认提示词..."
                    style={{
                      fontFamily: 'monospace',
                      fontSize: '12px',
                      display: selectedPromptType === 'aiNameConversionPrompt' ? 'block' : 'none'
                    }}
                    disabled={matchMode !== 'ai' || !nameConversionEnabled}
                  />
                </Form.Item>
              </Card>

              {/* 保存按钮 */}
              <div style={{ marginTop: '24px', textAlign: 'center' }}>
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  onClick={handleSaveRecognitionConfig}
                  loading={saving}
                  size="large"
                  style={{ minWidth: '200px' }}
                >
                  保存 AI 识别增强配置
                </Button>
              </div>
            </TabPane>

            {/* 标签页4: AI使用统计 */}
            <TabPane tab="AI使用统计" key="metrics">
              <AIMetrics />
            </TabPane>
          </Tabs>
        </Form>

        {/* 说明文字 */}
        <div className="mt-6 p-4 rounded" style={{ backgroundColor: 'var(--color-card)' }}>
          <h4 className="mt-0" style={{ color: 'var(--color-text)' }}>功能说明</h4>
          <ul style={{ marginBottom: 0, paddingLeft: 20, color: 'var(--color-text)' }}>
            <li>
              <strong>传统匹配</strong>: 基于标题相似度和类型匹配的算法,快速但可能不够精准
            </li>
            <li>
              <strong>AI智能匹配</strong>: 使用大语言模型理解上下文,综合考虑标题、类型、季度、年份、集数和精确标记等因素,选择最佳匹配结果
            </li>
            <li>
              <strong>AI辅助识别</strong>: 使用AI从标题中提取结构化信息(作品名称、季度、类型等),提高TMDB搜索准确率。应用于TMDB自动刮削定时任务
            </li>
            <li>
              <strong>AI别名修正</strong>: 使用AI修正已有的错误别名(例如中文别名字段写入了非中文内容)。启用后会强制更新所有别名字段,但已锁定的别名不会被修正
            </li>
            <li>
              <strong>AI别名扩展</strong>: 当元数据源返回非中文标题时,使用AI生成可能的别名(中文译名、罗马音、英文缩写等),然后在Bangumi/Douban中搜索以获取中文标题
            </li>
            <li>
              <strong>AI名称转换</strong>: 在搜索前将非中文搜索词(英文、日文、罗马音等)转换为中文名称,提高在中文弹幕源中的搜索准确率。当元数据源查询失败时作为兜底方案
            </li>
            <li>
              <strong>传统匹配兜底</strong>: 当AI匹配失败时,自动降级到传统匹配算法,确保功能可用性(仅AI模式下可用)
            </li>
            <li>
              <strong>AI剧集组选择</strong>: 当作品有TMDB ID但缺少剧集组映射时,使用AI自动从TMDB剧集组列表中选择最佳匹配,实现等价集数映射(如S01E26↔S02E01),提高跨季弹幕匹配准确率
            </li>
            <li>
              <strong>应用场景</strong>:
              <ul>
                <li>AI智能匹配: 外部控制API全自动导入、Webhook自动导入、匹配后备机制</li>
                <li>AI辅助识别: TMDB自动刮削与剧集组映射定时任务</li>
                <li>AI别名扩展: 外部控制API全自动导入、Webhook自动导入等场景（当元数据源返回非中文标题时）</li>
                <li>AI名称转换: 弹幕搜索时将非中文搜索词转换为中文（元数据源查询失败时的兜底）</li>
                <li>AI剧集组选择: 弹幕匹配时自动选择TMDB剧集组,实现跨季等价集数映射</li>
              </ul>
            </li>
            <li>
              <strong>精确标记优先</strong>: AI会优先选择被用户标记为"精确"的数据源
            </li>
          </ul>
        </div>
      </Card>
    </Spin>
  )
}

export default AutoMatchSetting
