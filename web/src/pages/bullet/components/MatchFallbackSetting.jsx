import { Card, Form, Switch, Input, Button, Space, Tooltip, Checkbox, InputNumber } from 'antd'
import { useEffect, useState } from 'react'
import { getMatchFallback, setMatchFallback, getMatchFallbackBlacklist, setMatchFallbackBlacklist, getMatchFallbackTokens, setMatchFallbackTokens, getTokenList, getSearchFallback, setSearchFallback, getConfig, setConfig } from '../../../apis'
import { useMessage } from '../../../MessageContext'
import { QuestionCircleOutlined } from '@ant-design/icons'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store'
import { useTranslation } from 'react-i18next'

export const MatchFallbackSetting = () => {
  const { t } = useTranslation()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(true)
  const [blacklistSaving, setBlacklistSaving] = useState(false)
  const [tokensSaving, setTokensSaving] = useState(false)
  const [tokenList, setTokenList] = useState([])
  const messageApi = useMessage()
  const isMobile = useAtomValue(isMobileAtom)

  const fetchSettings = async () => {
    try {
      setLoading(true)
      const [fallbackRes, blacklistRes, tokensRes, tokenListRes, searchFallbackRes, externalApiFallbackRes, preDownloadRes, parallelSearchRes, autoRefreshRes] = await Promise.all([
        getMatchFallback(),
        getMatchFallbackBlacklist(),
        getMatchFallbackTokens(),
        getTokenList(),
        getSearchFallback(),
        getConfig('externalApiFallbackEnabled'),
        getConfig('preDownloadNextEpisodeEnabled'),
        getConfig('parallelSearchEnabled'),
        getConfig('danmakuAutoRefreshDays')
      ])
      setTokenList(tokenListRes.data || [])

      // 解析token配置
      let selectedTokens = []
      try {
        selectedTokens = JSON.parse(tokensRes.data.value || '[]')
      } catch (e) {
        console.warn('解析匹配后备Token配置失败:', e)
      }

      form.setFieldsValue({
        matchFallbackEnabled: fallbackRes.data.value === 'true',
        matchFallbackBlacklist: blacklistRes.data.value || '',
        matchFallbackTokens: selectedTokens,
        searchFallbackEnabled: searchFallbackRes.data.value === 'true',
        externalApiFallbackEnabled: externalApiFallbackRes.data?.value === 'true',
        preDownloadNextEpisodeEnabled: preDownloadRes.data?.value === 'true',
        parallelSearchEnabled: parallelSearchRes.data?.value === 'true',
        danmakuAutoRefreshDays: parseInt(autoRefreshRes.data?.value || '0', 10) || 0
      })
    } catch (error) {
      messageApi.error(t('bullet.fallbackGetFailed'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSettings()
  }, [])

  // 监听页面焦点，当页面重新获得焦点时刷新数据
  useEffect(() => {
    const handleFocus = () => {
      fetchSettings()
    }

    window.addEventListener('focus', handleFocus)
    return () => {
      window.removeEventListener('focus', handleFocus)
    }
  }, [])

  const handleValueChange = async changedValues => {
    try {
      if ('matchFallbackEnabled' in changedValues) {
        await setMatchFallback({ value: String(changedValues.matchFallbackEnabled) })
        messageApi.success(t('bullet.fallbackMatchSaved'))
      }
      if ('searchFallbackEnabled' in changedValues) {
        await setSearchFallback({ value: String(changedValues.searchFallbackEnabled) })
        messageApi.success(t('bullet.fallbackSearchSaved'))
      }
      if ('externalApiFallbackEnabled' in changedValues) {
        await setConfig('externalApiFallbackEnabled', String(changedValues.externalApiFallbackEnabled))
        messageApi.success(t('bullet.fallbackCascadeSaved'))
      }
      if ('preDownloadNextEpisodeEnabled' in changedValues) {
        await setConfig('preDownloadNextEpisodeEnabled', String(changedValues.preDownloadNextEpisodeEnabled))
        messageApi.success(t('bullet.fallbackPredownloadSaved'))
      }
      if ('parallelSearchEnabled' in changedValues) {
        await setConfig('parallelSearchEnabled', String(changedValues.parallelSearchEnabled))
        messageApi.success(t('bullet.fallbackParallelSaved'))
      }
      if ('danmakuAutoRefreshDays' in changedValues) {
        await setConfig('danmakuAutoRefreshDays', String(changedValues.danmakuAutoRefreshDays ?? 0))
        messageApi.success(t('bullet.fallbackAutoRefreshSaved'))
      }
      // 黑名单不自动保存，需要点击保存按钮
    } catch (error) {
      messageApi.error(t('bullet.fallbackSaveFailed'))
      fetchSettings()
    }
  }

  const handleBlacklistSave = async () => {
    try {
      setBlacklistSaving(true)
      const values = form.getFieldsValue()
      await setMatchFallbackBlacklist({ value: values.matchFallbackBlacklist || '' })
      messageApi.success(t('bullet.fallbackBlacklistSaved'))
    } catch (error) {
      messageApi.error(t('bullet.fallbackBlacklistSaveFailed'))
    } finally {
      setBlacklistSaving(false)
    }
  }

  const handleTokensSave = async () => {
    try {
      setTokensSaving(true)
      const values = form.getFieldsValue()
      const tokensValue = JSON.stringify(values.matchFallbackTokens || [])
      await setMatchFallbackTokens({ value: tokensValue })
      messageApi.success(t('bullet.fallbackTokenSaved'))
    } catch (error) {
      messageApi.error(t('bullet.fallbackTokenSaveFailed'))
    } finally {
      setTokensSaving(false)
    }
  }

  return (
    <Card title={t('bullet.fallbackTitle')} loading={loading}>
      <Form
        form={form}
        onValuesChange={handleValueChange}
        layout="vertical"
        initialValues={{
          matchFallbackEnabled: false,
          searchFallbackEnabled: false,
          externalApiFallbackEnabled: false,
          preDownloadNextEpisodeEnabled: false,
          parallelSearchEnabled: false,
          danmakuAutoRefreshDays: 0,
          matchFallbackBlacklist: '',
          matchFallbackTokens: []
        }}
      >
        <div className={isMobile ? "space-y-4" : ""} style={isMobile ? {} : { display: 'flex', gap: '16px', alignItems: 'flex-start' }}>
          {isMobile ? (
            <>
              <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start', marginBottom: '16px' }}>
                <Form.Item
                  name="matchFallbackEnabled"
                  label={t('bullet.fallbackEnableMatch')}
                  valuePropName="checked"
                  tooltip={t('bullet.fallbackEnableMatchTip')}
                  style={{ flex: 1 }}
                >
                  <Switch />
                </Form.Item>

                <Form.Item
                  name="searchFallbackEnabled"
                  label={t('bullet.fallbackEnableSearch')}
                  valuePropName="checked"
                  tooltip={t('bullet.fallbackEnableSearchTip')}
                  style={{ flex: 1 }}
                >
                  <Switch />
                </Form.Item>
              </div>

              <Form.Item
                noStyle
                shouldUpdate={(prevValues, currentValues) =>
                  prevValues.matchFallbackEnabled !== currentValues.matchFallbackEnabled ||
                  prevValues.searchFallbackEnabled !== currentValues.searchFallbackEnabled
                }
              >
                {({ getFieldValue }) => {
                  const matchFallbackEnabled = getFieldValue('matchFallbackEnabled')
                  const searchFallbackEnabled = getFieldValue('searchFallbackEnabled')
                  const isFallbackDisabled = !matchFallbackEnabled && !searchFallbackEnabled

                  return (
                    <>
                      <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start', marginBottom: '16px' }}>
                        <Form.Item
                          name="externalApiFallbackEnabled"
                          label={
                            <div className="flex items-center gap-2">
                              <span>{t('bullet.fallbackEnableCascade')}</span>
                              <Tooltip title={t('bullet.fallbackEnableCascadeTip')}>
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                          }
                          valuePropName="checked"
                          style={{ flex: 1 }}
                        >
                          <Switch disabled={isFallbackDisabled} />
                        </Form.Item>

                        <Form.Item
                          name="preDownloadNextEpisodeEnabled"
                          label={
                            <div className="flex items-center gap-2">
                              <span>{t('bullet.fallbackEnablePredownload')}</span>
                              <Tooltip title={t('bullet.fallbackEnablePredownloadTip')}>
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                          }
                          valuePropName="checked"
                          style={{ flex: 1 }}
                        >
                          <Switch disabled={isFallbackDisabled} />
                        </Form.Item>
                      </div>

                      <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start' }}>
                        <Form.Item
                          name="parallelSearchEnabled"
                          label={
                            <div className="flex items-center gap-2">
                              <span>{t('bullet.fallbackEnableParallel')}</span>
                              <Tooltip title={t('bullet.fallbackEnableParallelTip')}>
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                          }
                          valuePropName="checked"
                          style={{ flex: 1 }}
                        >
                          <Switch disabled={isFallbackDisabled} />
                        </Form.Item>
                      </div>

                      <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start' }}>
                        <Form.Item
                          name="danmakuAutoRefreshDays"
                          label={
                            <div className="flex items-center gap-2">
                              <span>{t('bullet.fallbackAutoRefresh')}</span>
                              <Tooltip title={t('bullet.fallbackAutoRefreshTip')}>
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                          }
                          style={{ flex: 1 }}
                        >
                          <InputNumber min={0} max={365} precision={0} style={{ width: '100%' }} placeholder={t('bullet.fallbackAutoRefreshPlaceholder')} />
                        </Form.Item>
                      </div>
                    </>
                  )
                }}
              </Form.Item>
            </>
          ) : (
            <>
              <Form.Item
                name="matchFallbackEnabled"
                label={t('bullet.fallbackEnableMatch')}
                valuePropName="checked"
                tooltip={t('bullet.fallbackEnableMatchTip')}
                style={isMobile ? {} : { flex: 1 }}
              >
                <Switch />
              </Form.Item>

              <Form.Item
                name="searchFallbackEnabled"
                label={t('bullet.fallbackEnableSearch')}
                valuePropName="checked"
                tooltip={t('bullet.fallbackEnableSearchTip')}
                style={isMobile ? {} : { flex: 1 }}
              >
                <Switch />
              </Form.Item>

              <Form.Item
                noStyle
                shouldUpdate={(prevValues, currentValues) =>
                  prevValues.matchFallbackEnabled !== currentValues.matchFallbackEnabled ||
                  prevValues.searchFallbackEnabled !== currentValues.searchFallbackEnabled
                }
              >
                {({ getFieldValue }) => {
                  const matchFallbackEnabled = getFieldValue('matchFallbackEnabled')
                  const searchFallbackEnabled = getFieldValue('searchFallbackEnabled')
                  const isFallbackDisabled = !matchFallbackEnabled && !searchFallbackEnabled

                  return (
                    <Form.Item
                      name="externalApiFallbackEnabled"
                      label={
                        <div className="flex items-center gap-2">
                          <span>{t('bullet.fallbackEnableCascade')}</span>
                          <Tooltip title={t('bullet.fallbackEnableCascadeTip')}>
                            <QuestionCircleOutlined />
                          </Tooltip>
                        </div>
                      }
                      valuePropName="checked"
                      style={isMobile ? {} : { flex: 1 }}
                    >
                      <Switch disabled={isFallbackDisabled} />
                    </Form.Item>
                  )
                }}
              </Form.Item>

              <Form.Item
                noStyle
                shouldUpdate={(prevValues, currentValues) =>
                  prevValues.matchFallbackEnabled !== currentValues.matchFallbackEnabled ||
                  prevValues.searchFallbackEnabled !== currentValues.searchFallbackEnabled
                }
              >
                {({ getFieldValue }) => {
                  const matchFallbackEnabled = getFieldValue('matchFallbackEnabled')
                  const searchFallbackEnabled = getFieldValue('searchFallbackEnabled')
                  const isFallbackDisabled = !matchFallbackEnabled && !searchFallbackEnabled

                  return (
                    <Form.Item
                      name="preDownloadNextEpisodeEnabled"
                      label={
                        <div className="flex items-center gap-2">
                          <span>{t('bullet.fallbackEnablePredownload')}</span>
                          <Tooltip title={t('bullet.fallbackEnablePredownloadTip')}>
                            <QuestionCircleOutlined />
                          </Tooltip>
                        </div>
                      }
                      valuePropName="checked"
                      style={isMobile ? {} : { flex: 1 }}
                    >
                      <Switch disabled={isFallbackDisabled} />
                    </Form.Item>
                  )
                }}
              </Form.Item>

              <Form.Item
                noStyle
                shouldUpdate={(prevValues, currentValues) =>
                  prevValues.matchFallbackEnabled !== currentValues.matchFallbackEnabled ||
                  prevValues.searchFallbackEnabled !== currentValues.searchFallbackEnabled
                }
              >
                {({ getFieldValue }) => {
                  const matchFallbackEnabled = getFieldValue('matchFallbackEnabled')
                  const searchFallbackEnabled = getFieldValue('searchFallbackEnabled')
                  const isFallbackDisabled = !matchFallbackEnabled && !searchFallbackEnabled

                  return (
                    <Form.Item
                      name="parallelSearchEnabled"
                      label={
                        <div className="flex items-center gap-2">
                          <span>{t('bullet.fallbackEnableParallel')}</span>
                          <Tooltip title={t('bullet.fallbackEnableParallelTip')}>
                            <QuestionCircleOutlined />
                          </Tooltip>
                        </div>
                      }
                      valuePropName="checked"
                      style={isMobile ? {} : { flex: 1 }}
                    >
                      <Switch disabled={isFallbackDisabled} />
                    </Form.Item>
                  )
                }}
              </Form.Item>

              <Form.Item
                name="danmakuAutoRefreshDays"
                label={
                  <div className="flex items-center gap-2">
                    <span>{t('bullet.fallbackAutoRefresh')}</span>
                    <Tooltip title={t('bullet.fallbackAutoRefreshTip')}>
                      <QuestionCircleOutlined />
                    </Tooltip>
                  </div>
                }
              >
                <InputNumber min={0} max={365} precision={0} style={{ width: '100%' }} placeholder={t('bullet.fallbackAutoRefreshPlaceholder')} />
              </Form.Item>

            </>
          )}
        </div>

        <Form.Item
          noStyle
          shouldUpdate={(prevValues, currentValues) =>
            prevValues.matchFallbackEnabled !== currentValues.matchFallbackEnabled ||
            prevValues.searchFallbackEnabled !== currentValues.searchFallbackEnabled
          }
        >
          {({ getFieldValue }) => {
            const isTokenSelectionDisabled = !getFieldValue('matchFallbackEnabled') && !getFieldValue('searchFallbackEnabled')

            return (
              <Form.Item
                label={
                  <Space>
                    {t('bullet.fallbackTokenAuth')}
                    <Tooltip title={t('bullet.fallbackTokenAuthTip')}>
                      <QuestionCircleOutlined />
                    </Tooltip>
                  </Space>
                }
              >
                <Card
                  size="small"
                  className={`transition-all duration-200 ${
                    isTokenSelectionDisabled
                      ? 'bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-700 opacity-60'
                      : 'bg-gradient-to-br from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 border-blue-200 dark:border-blue-800 shadow-sm hover:shadow-md'
                  }`}
                  bodyStyle={{ padding: '16px' }}
                >
                  {tokenList.length === 0 ? (
                    <div className="text-center py-8 text-gray-500">
                      <div className="text-lg mb-2">📝</div>
                      <div>{t('bullet.fallbackNoToken')}</div>
                      <div className="text-sm mt-1">{t('bullet.fallbackCreateToken')}</div>
                    </div>
                  ) : (
                    <>
                      <Form.Item
                        name="matchFallbackTokens"
                        style={{ marginBottom: 0 }}
                      >
                        <Checkbox.Group
                          style={{ width: '100%' }}
                          disabled={isTokenSelectionDisabled}
                        >
                          <div className={`grid gap-3 ${
                            isMobile ? 'grid-cols-1' : 'grid-cols-2 md:grid-cols-3'
                          }`}>
                            {tokenList.map(token => (
                              <div
                                key={token.id}
                                className={`
                                  relative p-3 rounded-lg border transition-all duration-200 cursor-pointer
                                  ${isTokenSelectionDisabled
                                    ? 'bg-gray-100 dark:bg-gray-800 border-gray-200 dark:border-gray-700 cursor-not-allowed'
                                    : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600 hover:shadow-sm'
                                  }
                                `}
                              >
                                <Checkbox
                                  value={token.id}
                                  disabled={isTokenSelectionDisabled}
                                  className="absolute top-2 right-2"
                                />
                                <div className="pr-6">
                                  <div className="font-medium text-gray-900 dark:text-gray-100 mb-1">
                                    {token.name}
                                  </div>
                                  <div className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${
                                    token.isEnabled
                                      ? 'bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300'
                                      : 'bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-300'
                                  }`}>
                                    <span className={`w-1.5 h-1.5 rounded-full mr-1.5 ${
                                      token.isEnabled ? 'bg-green-500' : 'bg-red-500'
                                    }`}></span>
                                    {token.isEnabled ? t('bullet.fallbackTokenEnabled') : t('bullet.fallbackTokenDisabled')}
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        </Checkbox.Group>
                      </Form.Item>
                      <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700 flex justify-end">
                        <Button
                          type="primary"
                          loading={tokensSaving}
                          onClick={handleTokensSave}
                          disabled={isTokenSelectionDisabled}
                          className="min-w-[100px]"
                        >
                          {t('bullet.fallbackSaveConfig')}
                        </Button>
                      </div>
                    </>
                  )}
                </Card>
              </Form.Item>
            )
          }}
        </Form.Item>

        <Form.Item
          label={
            <Space>
              {t('bullet.fallbackBlacklistTitle')}
              <Tooltip title={t('bullet.fallbackBlacklistTip')}>
                <QuestionCircleOutlined />
              </Tooltip>
            </Space>
          }
        >
          <div className={isMobile ? "space-y-3" : "flex gap-3"}>
            <Form.Item
              name="matchFallbackBlacklist"
              className={isMobile ? "mb-0" : "flex-1 mb-0"}
            >
              <Input.TextArea
                placeholder={t('bullet.fallbackBlacklistPlaceholder')}
                rows={isMobile ? 3 : 1}
                className="resize-none"
              />
            </Form.Item>
            <Button
              type="primary"
              loading={blacklistSaving}
              onClick={handleBlacklistSave}
              className={isMobile ? "w-full" : ""}
              style={isMobile ? {} : { height: '32px', minHeight: '32px', minWidth: '100px' }}
            >
              {t('bullet.fallbackSaveBlacklist')}
            </Button>
          </div>
        </Form.Item>
      </Form>
    </Card>
  )
}