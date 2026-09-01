import React, { useState, useEffect } from 'react'
import { Form, Input, Select, Switch, Button, message, Spin, Card, Tabs, Space, Tooltip, Row, Col, Alert, Statistic, AutoComplete, InputNumber, Collapse, Slider } from 'antd'
const { TextArea } = Input
const { TabPane } = Tabs
const { Option } = Select
import { getConfig, setConfig, getDefaultAIPrompts, getAIBalance, getAIModels } from '@/apis'
import api from '@/apis/fetch'
import { QuestionCircleOutlined, SaveOutlined, ThunderboltOutlined, CheckCircleOutlined, CloseCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import AIMetrics from './AIMetrics'
import { SkillManager } from './SkillManager'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store/index.js'
import { useTranslation } from 'react-i18next'
import { useHashTab } from '@/hooks/useHashTab'

// 功能搜索锚点 -> 内层 Tab key。定义在模块级保证引用稳定。
const AUTOMATCH_ANCHOR_TABS = {
  'feat-ai-connection': 'connection',
  'feat-ai-match': 'match',
  'feat-ai-season-mapping': 'match',
  'feat-ai-recognition': 'recognition',
}

const CustomSwitch = (props) => {
  return <Switch {...props} />
}

const AutoMatchSetting = () => {
  const { t } = useTranslation()
  // 功能搜索深链：锚点哈希映射到内层 Tab key
  const [activeTab, setActiveTab] = useHashTab(AUTOMATCH_ANCHOR_TABS, 'connection')
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
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false) // 刷新后强制打开模型下拉框
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
        thinkingEnabledRes,
        callTimeoutRes,
        homeSearchSeasonMappingRes,
        fallbackSearchSeasonMappingRes,
        webhookSeasonMappingRes,
        matchFallbackSeasonMappingRes,
        externalSearchSeasonMappingRes,
        autoImportSeasonMappingRes,
        seasonMappingSourceRes,
        seasonMappingPromptRes,
        episodeGroupEnabledRes,
        episodeGroupPromptRes,
        asstChannelChatRes,
        asstNotifyRes,
        asstNotifyCompleteRes,
        asstNotifyFailedRes,
        asstNotifyStartRes,
        asstNotifyIntervalRes,
        asstTemperatureRes,
        asstMaxTokensRes,
        asstTopPRes,
        asstPresencePenaltyRes,
        asstFrequencyPenaltyRes,
        asstTimeoutRes,
        asstProxyEnabledRes
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
        getConfig('aiThinkingEnabled'),
        getConfig('aiCallTimeout'),
        getConfig('homeSearchEnableTmdbSeasonMapping'),
        getConfig('fallbackSearchEnableTmdbSeasonMapping'),
        getConfig('webhookEnableTmdbSeasonMapping'),
        getConfig('matchFallbackEnableTmdbSeasonMapping'),
        getConfig('externalSearchEnableTmdbSeasonMapping'),
        getConfig('autoImportEnableTmdbSeasonMapping'),
        getConfig('seasonMappingMetadataSource'),
        getConfig('seasonMappingPrompt'),
        getConfig('aiEpisodeGroupEnabled'),
        getConfig('aiEpisodeGroupPrompt'),
        // 御坂助手配置（容错：不存在时用默认值）
        getConfig('assistantChannelChatEnabled').catch(() => ({ data: { value: 'true' } })),
        getConfig('assistantNotifyEnabled').catch(() => ({ data: { value: 'true' } })),
        getConfig('assistantNotifyOnComplete').catch(() => ({ data: { value: 'true' } })),
        getConfig('assistantNotifyOnFailed').catch(() => ({ data: { value: 'true' } })),
        getConfig('assistantNotifyOnStart').catch(() => ({ data: { value: 'false' } })),
        getConfig('assistantNotifyInterval').catch(() => ({ data: { value: '15' } })),
        // 御坂助手高级 LLM 参数（容错：不存在时用默认值）
        getConfig('assistantTemperature').catch(() => ({ data: { value: '0.7' } })),
        getConfig('assistantMaxTokens').catch(() => ({ data: { value: '2000' } })),
        getConfig('assistantTopP').catch(() => ({ data: { value: '0.9' } })),
        getConfig('assistantPresencePenalty').catch(() => ({ data: { value: '0.0' } })),
        getConfig('assistantFrequencyPenalty').catch(() => ({ data: { value: '0.0' } })),
        getConfig('assistantTimeout').catch(() => ({ data: { value: '120' } })),
        getConfig('assistantProxyEnabled').catch(() => ({ data: { value: 'false' } }))
      ])

      const enabled = enabledRes.data.value === 'true'
      const fallback = fallbackRes.data.value === 'true'
      const recognition = recognitionEnabledRes.data.value === 'true'
      const aliasCorrection = aliasCorrectionEnabledRes.data.value === 'true'
      const aliasExpansion = aliasExpansionEnabledRes.data.value === 'true'
      const nameConversion = nameConversionEnabledRes.data.value === 'true'
      const logRawResponse = logRawResponseRes.data.value === 'true'
      const thinkingEnabled = thinkingEnabledRes.data.value === 'true'
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
        aiModel: modelRes.data.value || '',
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
        aiThinkingEnabled: thinkingEnabled,
        aiCallTimeout: parseInt(callTimeoutRes.data.value || '60', 10),
        homeSearchEnableTmdbSeasonMapping: homeSearchSeasonMappingRes.data.value === 'true',
        fallbackSearchEnableTmdbSeasonMapping: fallbackSearchSeasonMappingRes.data.value === 'true',
        webhookEnableTmdbSeasonMapping: webhookSeasonMappingRes.data.value === 'true',
        matchFallbackEnableTmdbSeasonMapping: matchFallbackSeasonMappingRes.data.value === 'true',
        externalSearchEnableTmdbSeasonMapping: externalSearchSeasonMappingRes.data.value === 'true',
        autoImportEnableTmdbSeasonMapping: autoImportSeasonMappingRes.data.value === 'true',
        seasonMappingMetadataSource: seasonMappingSourceRes.data.value || 'tmdb',
        seasonMappingPrompt: seasonMappingPromptRes.data.value || '',
        aiEpisodeGroupEnabled: episodeGroup,
        aiEpisodeGroupPrompt: episodeGroupPromptRes.data.value || '',
        // 御坂助手
        assistantChannelChatEnabled: asstChannelChatRes.data.value !== 'false',
        assistantNotifyEnabled: asstNotifyRes.data.value !== 'false',
        assistantNotifyOnComplete: asstNotifyCompleteRes.data.value !== 'false',
        assistantNotifyOnFailed: asstNotifyFailedRes.data.value !== 'false',
        assistantNotifyOnStart: asstNotifyStartRes.data.value === 'true',
        assistantNotifyInterval: parseInt(asstNotifyIntervalRes.data.value || '15', 10),
        // 御坂助手高级 LLM 参数
        assistantTemperature: parseFloat(asstTemperatureRes.data.value || '0.7'),
        assistantMaxTokens: parseInt(asstMaxTokensRes.data.value || '2000', 10),
        assistantTopP: parseFloat(asstTopPRes.data.value || '0.9'),
        assistantPresencePenalty: parseFloat(asstPresencePenaltyRes.data.value || '0.0'),
        assistantFrequencyPenalty: parseFloat(asstFrequencyPenaltyRes.data.value || '0.0'),
        assistantTimeout: parseInt(asstTimeoutRes.data.value || '120', 10),
        assistantProxyEnabled: asstProxyEnabledRes.data.value === 'true',
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
      console.error(t('autoMatch.logLoadConfigFailed'), error)
      message.error(t('autoMatch.loadFailed', { error: error?.response?.data?.message || error?.message || error?.detail || String(error) || t('common.unknown') }))
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
      console.error(t('autoMatch.logLoadProvidersFailed'), error)
      // 使用默认配置
      const defaultProviders = [
        {
          id: 'deepseek',
          displayName: 'DeepSeek',
          modelPlaceholder: t('autoMatch.modelPlaceholder'),
          baseUrlPlaceholder: `https://api.deepseek.com ${t('autoMatch.baseUrlDefaultHint')}`
        },
        {
          id: 'siliconflow',
          displayName: 'SiliconFlow 硅基流动',
          modelPlaceholder: t('autoMatch.modelPlaceholder'),
          baseUrlPlaceholder: `https://api.siliconflow.cn/v1 ${t('autoMatch.baseUrlDefaultHint')}`
        },
        {
          id: 'openai',
          displayName: 'OpenAI (兼容接口)',
          modelPlaceholder: t('autoMatch.modelPlaceholder'),
          baseUrlPlaceholder: `https://api.openai.com/v1 ${t('autoMatch.baseUrlDefaultHint')} ${t('autoMatch.baseUrlOpenaiHint')}`
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
      console.error(t('autoMatch.logFetchBalanceFailed'), error)
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
        setConfig('aiLogRawResponse', values.aiLogRawResponse ? 'true' : 'false'),
        setConfig('aiThinkingEnabled', values.aiThinkingEnabled ? 'true' : 'false'),
        setConfig('aiCallTimeout', String(values.aiCallTimeout || 60))
      ])

      message.success(t('autoMatch.saveConnectionSuccess'))

      // 保存成功后重新加载余额
      if (selectedProvider?.supportBalance) {
        fetchBalance()
      }
    } catch (error) {
      console.error(t('autoMatch.logSaveConfigFailed'), error)
      message.error(t('autoMatch.saveFailed', { error: error?.response?.data?.message || error?.message || t('common.unknown') }))
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

      message.success(t('autoMatch.saveMatchSuccess'))
    } catch (error) {
      console.error(t('autoMatch.logSaveConfigFailed'), error)
      message.error(t('autoMatch.saveFailed', { error: error?.response?.data?.message || error?.message || t('common.unknown') }))
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

      message.success(t('autoMatch.saveRecognitionSuccess'))
    } catch (error) {
      console.error(t('autoMatch.logSaveConfigFailed'), error)
      message.error(t('autoMatch.saveFailed', { error: error?.response?.data?.message || error?.message || t('common.unknown') }))
    } finally {
      setSaving(false)
    }
  }

  // 保存 Tab: 御坂助手
  const handleSaveAssistantConfig = async () => {
    try {
      setSaving(true)
      const values = form.getFieldsValue()
      const interval = Math.min(60, Math.max(10, parseInt(values.assistantNotifyInterval || 15, 10) || 15))
      const timeout = Math.min(300, Math.max(10, parseInt(values.assistantTimeout || 120, 10) || 120))
      const temperature = Math.min(2, Math.max(0, parseFloat(values.assistantTemperature || 0.7) || 0.7))
      const maxTokens = Math.min(8000, Math.max(100, parseInt(values.assistantMaxTokens || 2000, 10) || 2000))
      const topP = Math.min(1, Math.max(0, parseFloat(values.assistantTopP || 0.9) || 0.9))
      const presencePenalty = Math.min(2, Math.max(-2, parseFloat(values.assistantPresencePenalty || 0) || 0))
      const frequencyPenalty = Math.min(2, Math.max(-2, parseFloat(values.assistantFrequencyPenalty || 0) || 0))

      await Promise.all([
        setConfig('assistantChannelChatEnabled', values.assistantChannelChatEnabled ? 'true' : 'false'),
        setConfig('assistantNotifyEnabled', values.assistantNotifyEnabled ? 'true' : 'false'),
        setConfig('assistantNotifyOnComplete', values.assistantNotifyOnComplete ? 'true' : 'false'),
        setConfig('assistantNotifyOnFailed', values.assistantNotifyOnFailed ? 'true' : 'false'),
        setConfig('assistantNotifyOnStart', values.assistantNotifyOnStart ? 'true' : 'false'),
        setConfig('assistantNotifyInterval', String(interval)),
        // 高级 LLM 参数
        setConfig('assistantTemperature', String(temperature)),
        setConfig('assistantMaxTokens', String(maxTokens)),
        setConfig('assistantTopP', String(topP)),
        setConfig('assistantPresencePenalty', String(presencePenalty)),
        setConfig('assistantFrequencyPenalty', String(frequencyPenalty)),
        setConfig('assistantTimeout', String(timeout)),
        setConfig('assistantProxyEnabled', values.assistantProxyEnabled ? 'true' : 'false'),
      ])
      message.success(t('autoMatch.assistantSaveSuccess'))
    } catch (error) {
      console.error(t('autoMatch.logSaveConfigFailed'), error)
      message.error(t('autoMatch.saveFailed', { error: error?.response?.data?.message || error?.message || t('common.unknown') }))
    } finally {
      setSaving(false)
    }
  }

  // 获取模型名称占位符
  const getModelPlaceholder = (provider) => {
    const providerConfig = aiProviders.find(p => p.id === provider)
    return providerConfig?.modelPlaceholder || t('autoMatch.modelPlaceholder')
  }

  // 刷新模型列表
  const handleRefreshModels = async () => {
    const currentProvider = form.getFieldValue('aiProvider')
    if (!currentProvider) {
      message.warning(t('autoMatch.selectProviderFirst'))
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

        // 刷新成功后强制打开下拉框，让用户从列表中选择
        setModelDropdownOpen(true)

        const newCount = response.data.newCount || 0
        if (newCount > 0) {
          message.success(t('autoMatch.refreshModelsNewCount', { count: newCount }))
        } else {
          message.success(t('autoMatch.refreshModelsLatest'))
        }
      }
    } catch (error) {
      console.error(t('autoMatch.logRefreshModelsFailed'), error)
      message.error(t('autoMatch.refreshModelsFailed', { error: error.response?.data?.detail || error.message }))
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
            {model.isNew && <span style={{ marginLeft: '8px', color: '#52c41a', fontSize: '12px' }}>{t('autoMatch.modelNew')}</span>}
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
    return providerConfig?.baseUrlPlaceholder || t('autoMatch.baseUrlPlaceholder')
  }

  // 测试AI连接
  const handleTestConnection = async () => {
    try {
      setTesting(true)
      setTestResult(null)

      const values = form.getFieldsValue(['aiProvider', 'aiApiKey', 'aiBaseUrl', 'aiModel'])

      if (!values.aiProvider || !values.aiApiKey || !values.aiModel) {
        message.warning(t('autoMatch.testRequiredFields'))
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
        message.success(t('autoMatch.testSuccess', { latency: response.data.latency }))
      } else {
        message.error(t('autoMatch.testFailed'))
      }
    } catch (error) {
      setTestResult({
        success: false,
        message: t('autoMatch.testRequestFailed'),
        error: error?.response?.data?.message || error?.message || error?.detail || String(error) || t('common.unknown')
      })
      message.error(t('autoMatch.loadFailed', { error: error?.response?.data?.message || error?.message || error?.detail || String(error) || t('common.unknown') }))
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
        message.success(t('autoMatch.fillDefaultSuccess'))
      } else {
        message.error(t('autoMatch.fillDefaultNotFound'))
      }
    } catch (error) {
      console.error(t('autoMatch.logFetchDefaultPromptFailed'), error)
      message.error(t('autoMatch.fillDefaultFailed', { error: error?.response?.data?.message || error?.message || t('common.unknown') }))
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
          <Tabs activeKey={activeTab} onChange={setActiveTab}>
            {/* 标签页1: 连接配置 */}
            <TabPane tab={t('autoMatch.tabConnection')} key="connection">
              <div id="feat-ai-connection" />
              <Form.Item
                name="aiProvider"
                label={
                  <Space>
                    <span>{t('autoMatch.labelProvider')}</span>
                    <Tooltip title={t('autoMatch.tooltipProvider')}>
                      <QuestionCircleOutlined />
                    </Tooltip>
                  </Space>
                }
                rules={[{ required: matchMode === 'ai', message: t('autoMatch.ruleProvider') }]}
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
                    <span>{t('autoMatch.labelApiKey')}</span>
                    <Tooltip title={t('autoMatch.tooltipApiKey')}>
                      <QuestionCircleOutlined />
                    </Tooltip>
                  </Space>
                }
                rules={[{ required: matchMode === 'ai', message: t('autoMatch.ruleApiKey') }]}
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
                        <span>{t('autoMatch.labelBaseUrl')}</span>
                        <Tooltip title={t('autoMatch.tooltipBaseUrl')}>
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
                        <span>{t('autoMatch.labelModel')}</span>
                        <Tooltip title={t('autoMatch.tooltipModel')}>
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </Space>
                    }
                  >
                    <Space.Compact style={{ width: '100%' }}>
                      <Form.Item
                        name="aiModel"
                        noStyle
                        rules={[{ required: matchMode === 'ai', message: t('autoMatch.ruleModel') }]}
                      >
                        <AutoComplete
                          style={{ flex: 1 }}
                          options={getAvailableModels(getFieldValue('aiProvider'))}
                          placeholder={getModelPlaceholder(getFieldValue('aiProvider'))}
                          open={modelDropdownOpen || undefined}
                          filterOption={modelDropdownOpen
                            ? false
                            : (inputValue, option) =>
                                option.value.toLowerCase().includes(inputValue.toLowerCase())
                          }
                          onSelect={() => setModelDropdownOpen(false)}
                          onBlur={() => setModelDropdownOpen(false)}
                        />
                      </Form.Item>
                      <Tooltip title={t('autoMatch.tooltipRefreshModels')}>
                        <Button
                          icon={<ReloadOutlined />}
                          loading={refreshingModels}
                          onClick={handleRefreshModels}
                          disabled={!getFieldValue('aiProvider')}
                        >
                          {t('autoMatch.btnRefreshModels')}
                        </Button>
                      </Tooltip>
                    </Space.Compact>
                  </Form.Item>
                )}
              </Form.Item>

              {/* 余额卡片 - 根据选中的提供商配置决定是否显示 */}
              {selectedProvider?.supportBalance && (
                <Form.Item label={t('autoMatch.labelBalance')}>
                  <Space direction="vertical" style={{ width: '100%' }}>
                    {/* 余额卡片 */}
                    <Card size="small" style={{ marginBottom: '16px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ fontWeight: 500 }}>{t('autoMatch.balanceTitle')}</span>
                          <Tooltip title={t('autoMatch.tooltipBalance', { name: selectedProvider.displayName })}>
                            <QuestionCircleOutlined />
                          </Tooltip>
                        </div>
                        <Button
                          size="small"
                          onClick={fetchBalance}
                          loading={balanceLoading}
                          icon={<ReloadOutlined />}
                        >
                          {t('autoMatch.btnRefreshBalance')}
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
                              title={t('autoMatch.balanceTotal')}
                              value={balanceInfo.data.total_balance}
                              prefix={balanceInfo.data.currency === 'CNY' ? '¥' : '$'}
                              precision={2}
                            />
                          </Col>
                          <Col span={8}>
                            <Statistic
                              title={t('autoMatch.balanceGranted')}
                              value={balanceInfo.data.granted_balance}
                              prefix={balanceInfo.data.currency === 'CNY' ? '¥' : '$'}
                              precision={2}
                            />
                          </Col>
                          <Col span={8}>
                            <Statistic
                              title={t('autoMatch.balanceToppedUp')}
                              value={balanceInfo.data.topped_up_balance}
                              prefix={balanceInfo.data.currency === 'CNY' ? '¥' : '$'}
                              precision={2}
                            />
                          </Col>
                        </Row>
                      ) : (
                        <div style={{ color: '#999', textAlign: 'center' }}>
                          {t('autoMatch.balancePlaceholder')}
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
                  {t('autoMatch.btnTestConnection')}
                </Button>

                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: isMobile ? '0' : '0 16px',
                  width: isMobile ? '100%' : 'auto',
                  justifyContent: isMobile ? 'center' : 'flex-start'
                }}>
                  <span style={{ fontSize: '14px', whiteSpace: 'nowrap' }}>{t('autoMatch.labelLogResponse')}</span>
                  <Form.Item name="aiLogRawResponse" valuePropName="checked" noStyle>
                    <CustomSwitch
                      checkedChildren={t('autoMatch.switchLogOn')}
                      unCheckedChildren={t('autoMatch.switchLogOff')}
                    />
                  </Form.Item>
                  <Tooltip title={t('autoMatch.tooltipLogResponse')}>
                    <QuestionCircleOutlined style={{ color: '#999' }} />
                  </Tooltip>
                </div>

                <Form.Item noStyle shouldUpdate={(prev, cur) => prev.aiProvider !== cur.aiProvider}>
                  {({ getFieldValue }) => getFieldValue('aiProvider') === 'deepseek' && (
                    <div style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      padding: isMobile ? '0' : '0 16px',
                      width: isMobile ? '100%' : 'auto',
                      justifyContent: isMobile ? 'center' : 'flex-start'
                    }}>
                      <span style={{ fontSize: '14px', whiteSpace: 'nowrap' }}>{t('autoMatch.labelThinking')}</span>
                      <Form.Item name="aiThinkingEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch
                          checkedChildren={t('autoMatch.switchThinkingOn')}
                          unCheckedChildren={t('autoMatch.switchThinkingOff')}
                        />
                      </Form.Item>
                      <Tooltip title={t('autoMatch.tooltipThinking')}>
                        <QuestionCircleOutlined style={{ color: '#999' }} />
                      </Tooltip>
                    </div>
                  )}
                </Form.Item>

                {/* API 超时时间输入 */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: isMobile ? '0' : '0 16px',
                  width: isMobile ? '100%' : 'auto',
                  justifyContent: isMobile ? 'center' : 'flex-start'
                }}>
                  <span style={{ fontSize: '14px', whiteSpace: 'nowrap' }}>{t('autoMatch.labelCallTimeout')}</span>
                  <Form.Item name="aiCallTimeout" noStyle>
                    <InputNumber
                      min={10}
                      max={600}
                      step={10}
                      style={{ width: 90 }}
                      addonAfter={t('common.seconds', '秒')}
                    />
                  </Form.Item>
                  <Tooltip title={t('autoMatch.tooltipCallTimeout')}>
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
                  {t('autoMatch.btnSaveConnection')}
                </Button>
              </div>
            </TabPane>

            {/* 标签页2: 自动匹配 */}
            <TabPane tab={t('autoMatch.tabMatch')} key="match">
              <div id="feat-ai-match" />
              <Row gutter={[16, 16]}>
                <Col xs={24} sm={8}>
                  <Card size="small" style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 500 }}>{t('autoMatch.labelMatchMode')}</span>
                        <Tooltip title={t('autoMatch.tooltipMatchMode')}>
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </div>
                      <Form.Item name="aiMatchEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch
                          checkedChildren={t('autoMatch.switchAiMatch')}
                          unCheckedChildren={t('autoMatch.switchTraditional')}
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
                        <span style={{ fontWeight: 500 }}>{t('autoMatch.labelFallback')}</span>
                        <Tooltip title={matchMode === 'traditional' ? t('autoMatch.tooltipFallbackTraditional') : t('autoMatch.tooltipFallbackAi')}>
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
                        <span style={{ fontWeight: 500 }}>{t('autoMatch.labelEpisodeGroup')}</span>
                        <Tooltip title={t('autoMatch.tooltipEpisodeGroup')}>
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
                id="feat-ai-season-mapping"
                title={t('autoMatch.cardSeasonMapping')}
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
                              <span style={{ fontWeight: 500 }}>{t('autoMatch.labelHomeSearch')}</span>
                              <Tooltip title={t('autoMatch.tooltipHomeSearch')}>
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="homeSearchEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren={t('autoMatch.switchEnable')} unCheckedChildren={t('autoMatch.switchDisable')} />
                            </Form.Item>
                          </div>
                        </Card>
                        <Card size="small" style={{ marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontWeight: 500 }}>{t('autoMatch.labelFallbackSearch')}</span>
                              <Tooltip title={t('autoMatch.tooltipFallbackSearch')}>
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="fallbackSearchEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren={t('autoMatch.switchEnable')} unCheckedChildren={t('autoMatch.switchDisable')} />
                            </Form.Item>
                          </div>
                        </Card>
                        <Card size="small" style={{ marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontWeight: 500 }}>{t('autoMatch.labelMatchFallback')}</span>
                              <Tooltip title={t('autoMatch.tooltipMatchFallback')}>
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="matchFallbackEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren={t('autoMatch.switchEnable')} unCheckedChildren={t('autoMatch.switchDisable')} />
                            </Form.Item>
                          </div>
                        </Card>
                      </Col>
                      <Col xs={24} sm={12}>
                        <Card size="small" style={{ marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontWeight: 500 }}>{t('autoMatch.labelWebhook')}</span>
                              <Tooltip title={t('autoMatch.tooltipWebhook')}>
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="webhookEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren={t('autoMatch.switchEnable')} unCheckedChildren={t('autoMatch.switchDisable')} />
                            </Form.Item>
                          </div>
                        </Card>
                        <Card size="small" style={{ marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontWeight: 500 }}>{t('autoMatch.labelExternalSearch')}</span>
                              <Tooltip title={t('autoMatch.tooltipExternalSearch')}>
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="externalSearchEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren={t('autoMatch.switchEnable')} unCheckedChildren={t('autoMatch.switchDisable')} />
                            </Form.Item>
                          </div>
                        </Card>
                        <Card size="small" style={{ marginBottom: '16px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontWeight: 500 }}>{t('autoMatch.labelAutoImport')}</span>
                              <Tooltip title={t('autoMatch.tooltipAutoImport')}>
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                            <Form.Item name="autoImportEnableTmdbSeasonMapping" valuePropName="checked" noStyle>
                              <CustomSwitch checkedChildren={t('autoMatch.switchEnable')} unCheckedChildren={t('autoMatch.switchDisable')} />
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
                          <span style={{ fontWeight: 500 }}>{t('autoMatch.labelMetadataSource')}</span>
                          <Tooltip title={t('autoMatch.tooltipMetadataSource')}>
                            <QuestionCircleOutlined />
                          </Tooltip>
                        </div>
                        <Form.Item name="seasonMappingMetadataSource" noStyle>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
                            {[
                              { value: 'tmdb', label: 'TMDB' },
                              { value: 'tvdb', label: 'TVDB' },
                              { value: 'imdb', label: 'IMDB' },
                              { value: 'douban', label: t('autoMatch.labelDouban') },
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

              {/* 提示词配置区域 */}
              <Card size="small" style={{ marginTop: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <Space>
                    <span style={{ fontWeight: 500 }}>{t('autoMatch.labelPromptConfig')}</span>
                    <Select
                      value={selectedMatchPromptType}
                      onChange={setSelectedMatchPromptType}
                      style={{ width: 200 }}
                      disabled={matchMode !== 'ai'}
                    >
                      <Option value="aiPrompt">{t('autoMatch.optionAiPrompt')}</Option>
                      <Option value="seasonMappingPrompt">{t('autoMatch.optionSeasonMappingPrompt')}</Option>
                      <Option value="aiEpisodeGroupPrompt">{t('autoMatch.optionEpisodeGroupPrompt')}</Option>
                    </Select>
                    <Tooltip title={
                      selectedMatchPromptType === 'aiPrompt'
                        ? t('autoMatch.tooltipAiPrompt')
                        : selectedMatchPromptType === 'seasonMappingPrompt'
                        ? t('autoMatch.tooltipSeasonMappingPrompt')
                        : t('autoMatch.tooltipEpisodeGroupPrompt')
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
                    {t('autoMatch.btnFillDefault')}
                  </Button>
                </div>

                {/* AI匹配提示词 */}
                <Form.Item name="aiPrompt" noStyle>
                  <TextArea rows={10} placeholder={t('autoMatch.promptPlaceholder')}
                    style={{ fontFamily: 'monospace', fontSize: '12px', display: selectedMatchPromptType === 'aiPrompt' ? 'block' : 'none' }}
                    disabled={matchMode !== 'ai'} />
                </Form.Item>

                {/* AI季度映射提示词 */}
                <Form.Item name="seasonMappingPrompt" noStyle>
                  <TextArea rows={10} placeholder={t('autoMatch.promptPlaceholder')}
                    style={{ fontFamily: 'monospace', fontSize: '12px', display: selectedMatchPromptType === 'seasonMappingPrompt' ? 'block' : 'none' }}
                    disabled={matchMode !== 'ai'} />
                </Form.Item>

                {/* AI剧集组选择提示词 */}
                <Form.Item name="aiEpisodeGroupPrompt" noStyle>
                  <TextArea rows={10} placeholder={t('autoMatch.promptPlaceholder')}
                    style={{ fontFamily: 'monospace', fontSize: '12px', display: selectedMatchPromptType === 'aiEpisodeGroupPrompt' ? 'block' : 'none' }}
                    disabled={matchMode !== 'ai' || !episodeGroupEnabled} />
                </Form.Item>
              </Card>

              {/* 保存按钮 */}
              <div style={{ marginTop: '24px', textAlign: 'center' }}>
                <Button type="primary" icon={<SaveOutlined />} onClick={handleSaveMatchConfig}
                  loading={saving} size="large" style={{ minWidth: '200px' }}>
                  {t('autoMatch.btnSaveMatch')}
                </Button>
              </div>
            </TabPane>

            {/* 标签页3: 识别增强 */}
            <TabPane tab={t('autoMatch.tabRecognition')} key="recognition">
              <div id="feat-ai-recognition" />
              <Row gutter={[16, 16]}>
                <Col xs={24} sm={6}>
                  <Card size="small" style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 500 }}>{t('autoMatch.labelAiAssist')}</span>
                        <Tooltip title={t('autoMatch.tooltipAiAssist')}>
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </div>
                      <Form.Item name="aiRecognitionEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch disabled={matchMode !== 'ai'} onChange={(checked) => setRecognitionEnabled(checked)} />
                      </Form.Item>
                    </div>
                  </Card>
                </Col>
                <Col xs={24} sm={6}>
                  <Card size="small" style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 500 }}>{t('autoMatch.labelAliasCorrection')}</span>
                        <Tooltip title={t('autoMatch.tooltipAliasCorrection')}>
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
                        <span style={{ fontWeight: 500 }}>{t('autoMatch.labelAliasExpansion')}</span>
                        <Tooltip title={t('autoMatch.tooltipAliasExpansion')}>
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </div>
                      <Form.Item name="aiAliasExpansionEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch disabled={matchMode !== 'ai'} onChange={(checked) => setAliasExpansionEnabled(checked)} />
                      </Form.Item>
                    </div>
                  </Card>
                </Col>
                <Col xs={24} sm={6}>
                  <Card size="small" style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontWeight: 500 }}>{t('autoMatch.labelNameConversion')}</span>
                        <Tooltip title={t('autoMatch.tooltipNameConversion')}>
                          <QuestionCircleOutlined />
                        </Tooltip>
                      </div>
                      <Form.Item name="aiNameConversionEnabled" valuePropName="checked" noStyle>
                        <CustomSwitch disabled={matchMode !== 'ai'} onChange={(checked) => setNameConversionEnabled(checked)} />
                      </Form.Item>
                    </div>
                  </Card>
                </Col>
              </Row>

              {/* 提示词配置区域 */}
              <Card size="small" style={{ marginTop: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <Space>
                    <span style={{ fontWeight: 500 }}>{t('autoMatch.labelPromptConfig')}</span>
                    <Select value={selectedPromptType} onChange={setSelectedPromptType} style={{ width: 200 }} disabled={matchMode !== 'ai'}>
                      <Option value="aiRecognitionPrompt">{t('autoMatch.optionAiRecognitionPrompt')}</Option>
                      <Option value="aiAliasValidationPrompt">{t('autoMatch.optionAiAliasValidationPrompt')}</Option>
                      <Option value="aiAliasExpansionPrompt">{t('autoMatch.optionAiAliasExpansionPrompt')}</Option>
                      <Option value="aiNameConversionPrompt">{t('autoMatch.optionAiNameConversionPrompt')}</Option>
                    </Select>
                    <Tooltip title={
                      selectedPromptType === 'aiRecognitionPrompt' ? t('autoMatch.tooltipAiRecognitionPrompt')
                        : selectedPromptType === 'aiAliasValidationPrompt' ? t('autoMatch.tooltipAiAliasValidationPrompt')
                        : selectedPromptType === 'aiAliasExpansionPrompt' ? t('autoMatch.tooltipAiAliasExpansionPrompt')
                        : t('autoMatch.tooltipAiNameConversionPrompt')
                    }>
                      <QuestionCircleOutlined />
                    </Tooltip>
                  </Space>
                  <Button size="small" icon={<ReloadOutlined />}
                    onClick={() => handleFillDefaultPrompt(selectedPromptType)}
                    disabled={matchMode !== 'ai' || (
                      (selectedPromptType === 'aiRecognitionPrompt' && !recognitionEnabled) ||
                      (selectedPromptType === 'aiAliasValidationPrompt' && !recognitionEnabled) ||
                      (selectedPromptType === 'aiAliasExpansionPrompt' && !aliasExpansionEnabled) ||
                      (selectedPromptType === 'aiNameConversionPrompt' && !nameConversionEnabled)
                    )}
                  >
                    {t('autoMatch.btnFillDefault')}
                  </Button>
                </div>

                <Form.Item name="aiRecognitionPrompt" noStyle style={{ display: selectedPromptType === 'aiRecognitionPrompt' ? 'block' : 'none' }}>
                  <TextArea rows={10} placeholder={t('autoMatch.promptPlaceholder')}
                    style={{ fontFamily: 'monospace', fontSize: '12px', display: selectedPromptType === 'aiRecognitionPrompt' ? 'block' : 'none' }}
                    disabled={matchMode !== 'ai' || !recognitionEnabled} />
                </Form.Item>
                <Form.Item name="aiAliasValidationPrompt" noStyle style={{ display: selectedPromptType === 'aiAliasValidationPrompt' ? 'block' : 'none' }}>
                  <TextArea rows={10} placeholder={t('autoMatch.promptPlaceholder')}
                    style={{ fontFamily: 'monospace', fontSize: '12px', display: selectedPromptType === 'aiAliasValidationPrompt' ? 'block' : 'none' }}
                    disabled={matchMode !== 'ai' || !recognitionEnabled} />
                </Form.Item>
                <Form.Item name="aiAliasExpansionPrompt" noStyle style={{ display: selectedPromptType === 'aiAliasExpansionPrompt' ? 'block' : 'none' }}>
                  <TextArea rows={10} placeholder={t('autoMatch.promptPlaceholder')}
                    style={{ fontFamily: 'monospace', fontSize: '12px', display: selectedPromptType === 'aiAliasExpansionPrompt' ? 'block' : 'none' }}
                    disabled={matchMode !== 'ai' || !aliasExpansionEnabled} />
                </Form.Item>
                <Form.Item name="aiNameConversionPrompt" noStyle style={{ display: selectedPromptType === 'aiNameConversionPrompt' ? 'block' : 'none' }}>
                  <TextArea rows={10} placeholder={t('autoMatch.promptPlaceholder')}
                    style={{ fontFamily: 'monospace', fontSize: '12px', display: selectedPromptType === 'aiNameConversionPrompt' ? 'block' : 'none' }}
                    disabled={matchMode !== 'ai' || !nameConversionEnabled} />
                </Form.Item>
              </Card>

              {/* 保存按钮 */}
              <div style={{ marginTop: '24px', textAlign: 'center' }}>
                <Button type="primary" icon={<SaveOutlined />} onClick={handleSaveRecognitionConfig}
                  loading={saving} size="large" style={{ minWidth: '200px' }}>
                  {t('autoMatch.btnSaveRecognition')}
                </Button>
              </div>
            </TabPane>

            {/* 标签页: 御坂助手（看板娘对话 + 任务播报） */}
            <TabPane tab={t('autoMatch.assistantTab')} key="assistant">
              <Alert
                type="info"
                message={t('autoMatch.assistantAlertTitle')}
                description={t('autoMatch.assistantAlertDesc')}
                style={{ marginBottom: 16 }}
              />
              <Row gutter={[16, 16]}>
                <Col xs={24} sm={12}>
                  <Form.Item name="assistantChannelChatEnabled" valuePropName="checked"
                    label={t('autoMatch.assistantChannelChatLabel')} tooltip={t('autoMatch.assistantChannelChatTooltip')}>
                    <CustomSwitch />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="assistantNotifyEnabled" valuePropName="checked"
                    label={t('autoMatch.assistantNotifyEnabledLabel')} tooltip={t('autoMatch.assistantNotifyEnabledTooltip')}>
                    <CustomSwitch />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="assistantNotifyOnComplete" valuePropName="checked" label={t('autoMatch.assistantNotifyOnCompleteLabel')}>
                    <CustomSwitch />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="assistantNotifyOnFailed" valuePropName="checked" label={t('autoMatch.assistantNotifyOnFailedLabel')}>
                    <CustomSwitch />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="assistantNotifyOnStart" valuePropName="checked"
                    label={t('autoMatch.assistantNotifyOnStartLabel')} tooltip={t('autoMatch.assistantNotifyOnStartTooltip')}>
                    <CustomSwitch />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="assistantNotifyInterval"
                    label={t('autoMatch.assistantNotifyIntervalLabel')} tooltip={t('autoMatch.assistantNotifyIntervalTooltip')}>
                    <InputNumber min={10} max={60} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              {/* 高级 LLM 参数（参考 MoviePilot AI Agent，默认展开可折叠） */}
              <Collapse
                defaultActiveKey={['advanced']}
                style={{ marginTop: 16 }}
                items={[{
                  key: 'advanced',
                  label: t('autoMatch.assistantAdvancedTitle'),
                  children: (
                    <>
                      <Alert
                        type="info"
                        message={t('autoMatch.assistantAdvancedDesc')}
                        style={{ marginBottom: 16 }}
                      />
                      <Row gutter={[16, 8]}>
                        <Col xs={24} sm={12}>
                          <Form.Item name="assistantTemperature"
                            label={t('autoMatch.assistantTemperatureLabel')}
                            tooltip={t('autoMatch.assistantTemperatureTooltip')}>
                            <Slider min={0} max={2} step={0.1} marks={{ 0: '0', 0.7: '0.7', 2: '2' }} />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="assistantTopP"
                            label={t('autoMatch.assistantTopPLabel')}
                            tooltip={t('autoMatch.assistantTopPTooltip')}>
                            <Slider min={0} max={1} step={0.05} marks={{ 0: '0', 0.9: '0.9', 1: '1' }} />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="assistantPresencePenalty"
                            label={t('autoMatch.assistantPresencePenaltyLabel')}
                            tooltip={t('autoMatch.assistantPresencePenaltyTooltip')}>
                            <Slider min={-2} max={2} step={0.1} marks={{ '-2': '-2', 0: '0', 2: '2' }} />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="assistantFrequencyPenalty"
                            label={t('autoMatch.assistantFrequencyPenaltyLabel')}
                            tooltip={t('autoMatch.assistantFrequencyPenaltyTooltip')}>
                            <Slider min={-2} max={2} step={0.1} marks={{ '-2': '-2', 0: '0', 2: '2' }} />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="assistantMaxTokens"
                            label={t('autoMatch.assistantMaxTokensLabel')}
                            tooltip={t('autoMatch.assistantMaxTokensTooltip')}>
                            <InputNumber min={100} max={8000} step={100} style={{ width: '100%' }} />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="assistantTimeout"
                            label={t('autoMatch.assistantTimeoutLabel')}
                            tooltip={t('autoMatch.assistantTimeoutTooltip')}>
                            <InputNumber min={10} max={300} step={10} style={{ width: '100%' }} />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="assistantProxyEnabled" valuePropName="checked"
                            label={t('autoMatch.assistantProxyEnabledLabel')}
                            tooltip={t('autoMatch.assistantProxyEnabledTooltip')}>
                            <CustomSwitch />
                          </Form.Item>
                        </Col>
                      </Row>
                    </>
                  ),
                }]}
              />

              <div style={{ marginTop: '24px', textAlign: 'center' }}>
                <Button type="primary" icon={<SaveOutlined />} onClick={handleSaveAssistantConfig}
                  loading={saving} size="large" style={{ minWidth: '200px' }}>
                  {t('autoMatch.assistantSaveBtn')}
                </Button>
              </div>
            </TabPane>

            {/* 标签页: 御坂技能管理（用户可自制 skill 到 config/skills/） */}
            <TabPane tab={t('autoMatch.skillsTab')} key="skills">
              <SkillManager />
            </TabPane>

            {/* 标签页4: 使用统计 */}
            <TabPane tab={t('autoMatch.tabMetrics')} key="metrics">
              <AIMetrics />
            </TabPane>
          </Tabs>
        </Form>

        {/* 说明文字 */}
        <div className="mt-6 p-4 rounded" style={{ backgroundColor: 'var(--color-card)' }}>
          <h4 className="mt-0" style={{ color: 'var(--color-text)' }}>{t('autoMatch.descTitle')}</h4>
          <ul style={{ marginBottom: 0, paddingLeft: 20, color: 'var(--color-text)' }}>
            <li dangerouslySetInnerHTML={{ __html: t('autoMatch.descTraditional') }} />
            <li dangerouslySetInnerHTML={{ __html: t('autoMatch.descAiMatch') }} />
            <li dangerouslySetInnerHTML={{ __html: t('autoMatch.descAiRecognition') }} />
            <li dangerouslySetInnerHTML={{ __html: t('autoMatch.descAliasCorrection') }} />
            <li dangerouslySetInnerHTML={{ __html: t('autoMatch.descAliasExpansion') }} />
            <li dangerouslySetInnerHTML={{ __html: t('autoMatch.descNameConversion') }} />
            <li dangerouslySetInnerHTML={{ __html: t('autoMatch.descFallback') }} />
            <li dangerouslySetInnerHTML={{ __html: t('autoMatch.descEpisodeGroup') }} />
            <li>
              <span dangerouslySetInnerHTML={{ __html: t('autoMatch.descScenes') }} />
              <ul>
                <li>{t('autoMatch.descSceneAiMatch')}</li>
                <li>{t('autoMatch.descSceneAiRecognition')}</li>
                <li>{t('autoMatch.descSceneAliasExpansion')}</li>
                <li>{t('autoMatch.descSceneNameConversion')}</li>
                <li>{t('autoMatch.descSceneEpisodeGroup')}</li>
              </ul>
            </li>
            <li dangerouslySetInnerHTML={{ __html: t('autoMatch.descPrecision') }} />
          </ul>
        </div>
      </Card>
    </Spin>
  )
}

export default AutoMatchSetting
