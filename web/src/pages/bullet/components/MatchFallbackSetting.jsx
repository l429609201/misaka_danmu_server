import { Card, Form, Switch, Input, Button, Space, Tooltip, Select, Tag, InputNumber } from 'antd'
import { useEffect, useState } from 'react'
import { getMatchFallback, setMatchFallback, getMatchFallbackBlacklist, setMatchFallbackBlacklist, getMatchFallbackTokens, setMatchFallbackTokens, getPosterProxyTokens, setPosterProxyTokens, getTokenList, getSearchFallback, setSearchFallback, getConfig, setConfig } from '../../../apis'
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
  const [posterProxyTokensSaving, setPosterProxyTokensSaving] = useState(false)
  const [tokenList, setTokenList] = useState([])
  const messageApi = useMessage()
  const isMobile = useAtomValue(isMobileAtom)

  const fetchSettings = async () => {
    try {
      setLoading(true)
      const [fallbackRes, blacklistRes, tokensRes, tokenListRes, searchFallbackRes, externalApiFallbackRes, preDownloadRes, parallelSearchRes, autoRefreshRes, refreshThresholdRes, posterProxyTokensRes] = await Promise.all([
        getMatchFallback(),
        getMatchFallbackBlacklist(),
        getMatchFallbackTokens(),
        getTokenList(),
        getSearchFallback(),
        getConfig('externalApiFallbackEnabled'),
        getConfig('preDownloadNextEpisodeEnabled'),
        getConfig('parallelSearchEnabled'),
        getConfig('danmakuAutoRefreshDays'),
        getConfig('danmakuRefreshThreshold'),
        getPosterProxyTokens(),
      ])
      setTokenList(tokenListRes.data || [])

      // 解析 matchFallbackTokens 配置
      let selectedTokens = []
      try {
        selectedTokens = JSON.parse(tokensRes.data.value || '[]')
      } catch (e) {
        console.warn('解析匹配后备Token配置失败:', e)
      }

      // 解析 posterProxyTokens 配置
      let selectedPosterProxyTokens = []
      try {
        selectedPosterProxyTokens = JSON.parse(posterProxyTokensRes.data?.value || '[]')
      } catch (e) {
        console.warn('解析外联海报Token配置失败:', e)
      }

      form.setFieldsValue({
        matchFallbackEnabled: fallbackRes.data.value === 'true',
        matchFallbackBlacklist: blacklistRes.data.value || '',
        matchFallbackTokens: selectedTokens,
        searchFallbackEnabled: searchFallbackRes.data.value === 'true',
        externalApiFallbackEnabled: externalApiFallbackRes.data?.value === 'true',
        preDownloadNextEpisodeEnabled: preDownloadRes.data?.value === 'true',
        parallelSearchEnabled: parallelSearchRes.data?.value === 'true',
        danmakuAutoRefreshDays: parseInt(autoRefreshRes.data?.value || '0', 10) || 0,
        danmakuRefreshThreshold: parseInt(refreshThresholdRes.data?.value || '5000', 10) || 0,
        posterProxyTokens: selectedPosterProxyTokens,
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
      if ('danmakuRefreshThreshold' in changedValues) {
        await setConfig('danmakuRefreshThreshold', String(changedValues.danmakuRefreshThreshold ?? 5000))
        messageApi.success(t('bullet.fallbackRefreshThresholdSaved'))
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

  const handlePosterProxyTokensSave = async () => {
    try {
      setPosterProxyTokensSaving(true)
      const values = form.getFieldsValue()
      const tokensValue = JSON.stringify(values.posterProxyTokens || [])
      await setPosterProxyTokens({ value: tokensValue })
      messageApi.success(t('bullet.posterProxyTokenSaved'))
    } catch (error) {
      messageApi.error(t('bullet.posterProxyTokenSaveFailed'))
    } finally {
      setPosterProxyTokensSaving(false)
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
          danmakuRefreshThreshold: 5000,
          matchFallbackBlacklist: '',
          matchFallbackTokens: [],
          posterProxyTokens: [],
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

                      <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start' }}>
                        <Form.Item
                          name="danmakuRefreshThreshold"
                          label={
                            <div className="flex items-center gap-2">
                              <span>{t('bullet.fallbackRefreshThreshold')}</span>
                              <Tooltip title={t('bullet.fallbackRefreshThresholdTip')}>
                                <QuestionCircleOutlined />
                              </Tooltip>
                            </div>
                          }
                          style={{ flex: 1 }}
                        >
                          <InputNumber min={0} max={1000000} precision={0} style={{ width: '100%' }} placeholder={t('bullet.fallbackRefreshThresholdPlaceholder')} />
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

              {/* 两个数值输入：删除标题后整列自然上移；addonBefore 宽 80px；controls=false 隐藏右侧上下按钮
                  数字输入区长度 = InputNumber 总宽 - 80（addonBefore），改下面 width 数字即可单独调节 */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                <Form.Item name="danmakuAutoRefreshDays" style={{ marginBottom: 8 }}>
                  <InputNumber
                    controls={false}
                    min={0}
                    max={365}
                    precision={0}
                    style={{ width: 240 }}
                    placeholder={t('bullet.fallbackAutoRefreshPlaceholder')}
                    addonBefore={
                      <Tooltip title={t('bullet.fallbackAutoRefreshTip')}>
                        <span style={{ display: 'inline-block', width: 80, textAlign: 'center' }}>
                          {t('bullet.fallbackAutoRefreshShort')}
                        </span>
                      </Tooltip>
                    }
                  />
                </Form.Item>
                <Form.Item name="danmakuRefreshThreshold" style={{ marginBottom: 0 }}>
                  <InputNumber
                    controls={false}
                    min={0}
                    max={1000000}
                    precision={0}
                    style={{ width: 240 }}
                    placeholder={t('bullet.fallbackRefreshThresholdPlaceholder')}
                    addonBefore={
                      <Tooltip title={t('bullet.fallbackRefreshThresholdTip')}>
                        <span style={{ display: 'inline-block', width: 80, textAlign: 'center' }}>
                          {t('bullet.fallbackRefreshThresholdShort')}
                        </span>
                      </Tooltip>
                    }
                  />
                </Form.Item>
              </div>

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
                {/* why：方案G —— 用 antd Select 多选替代原复选框卡片墙：
                    与页面其他配置项形态统一、占用高度最小；
                    已授权 Token 显示为胶囊，点 × 单个移除，「取消全部」一键清空 */}
                <div className={isMobile ? 'space-y-3' : 'flex gap-3'}>
                  <Form.Item
                    name="matchFallbackTokens"
                    className={isMobile ? 'mb-0' : 'flex-1 mb-0'}
                  >
                    <Select
                      mode="multiple"
                      allowClear
                      disabled={isTokenSelectionDisabled}
                      placeholder={t("bullet.fallbackTokenSelectPlaceholder")}
                      notFoundContent={t("bullet.fallbackCreateToken")}
                      optionFilterProp="name"
                      options={tokenList.map(token => ({
                        value: token.id,
                        name: token.name,
                        label: (
                          <Space size={6}>
                            <span>{token.name}</span>
                            <Tag color={token.isEnabled ? "success" : "default"} style={{ marginInlineEnd: 0 }}>
                              {token.isEnabled ? t("bullet.fallbackTokenEnabled") : t("bullet.fallbackTokenDisabled")}
                            </Tag>
                          </Space>
                        ),
                      }))}
                      tagRender={({ label, value, closable, onClose }) => {
                        const token = tokenList.find(t => t.id === value)
                        return (
                          <span
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 4,
                              padding: "2px 8px",
                              marginInlineEnd: 4,
                              borderRadius: 999,
                              fontSize: 12,
                              lineHeight: "20px",
                              background: token?.isEnabled ? "rgba(0,128,0,0.12)" : "rgba(0,0,0,0.06)",
                              border: token?.isEnabled ? "1px solid rgba(0,128,0,0.3)" : "1px solid rgba(0,0,0,0.15)",
                              color: "inherit",
                            }}
                          >
                            {token?.name || value}
                            {closable && (
                              <span
                                style={{ cursor: "pointer", opacity: 0.5, fontSize: 11, lineHeight: 1 }}
                                onClick={e => { e.stopPropagation(); onClose() }}
                              >
                                ×
                              </span>
                            )}
                          </span>
                        )
                      }}
                    />
                  </Form.Item>
                  <div className="flex gap-2 flex-shrink-0">
                    {/* why：一键清空所有已授权 Token，清空后需点「保存配置」才落库 */}
                    <Button
                      onClick={() => form.setFieldsValue({ matchFallbackTokens: [] })}
                      disabled={isTokenSelectionDisabled}
                      className={isMobile ? 'flex-1' : ''}
                    >
                      {t('bullet.fallbackTokenClearAll')}
                    </Button>
                    <Button
                      type="primary"
                      loading={tokensSaving}
                      onClick={handleTokensSave}
                      disabled={isTokenSelectionDisabled}
                      className={isMobile ? 'flex-1' : ''}
                    >
                      {t('bullet.fallbackSaveConfig')}
                    </Button>
                  </div>
                </div>
              </Form.Item>
            )
          }}
        </Form.Item>

        {/* 外联海报模式 Token 授权 —— 与后备功能 Token 授权完全相同的 Select 多选设计 */}
        <Form.Item
          label={
            <Space>
              {t('bullet.posterProxyTokenAuth')}
              <Tooltip title={t('bullet.posterProxyTokenAuthTip')}>
                <QuestionCircleOutlined />
              </Tooltip>
            </Space>
          }
        >
          <div className={isMobile ? 'space-y-3' : 'flex gap-3'}>
            <Form.Item
              name="posterProxyTokens"
              className={isMobile ? 'mb-0' : 'flex-1 mb-0'}
            >
              <Select
                mode="multiple"
                allowClear
                placeholder={t("bullet.posterProxyTokenSelectPlaceholder")}
                notFoundContent={t("bullet.fallbackCreateToken")}
                optionFilterProp="name"
                options={tokenList.map(token => ({
                  value: token.id,
                  name: token.name,
                  label: (
                    <Space size={6}>
                      <span>{token.name}</span>
                      <Tag color={token.isEnabled ? "success" : "default"} style={{ marginInlineEnd: 0 }}>
                        {token.isEnabled ? t("bullet.fallbackTokenEnabled") : t("bullet.fallbackTokenDisabled")}
                      </Tag>
                    </Space>
                  ),
                }))}
                tagRender={({ label, value, closable, onClose }) => {
                  const token = tokenList.find(t => t.id === value)
                  return (
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                        padding: "2px 8px",
                        marginInlineEnd: 4,
                        borderRadius: 999,
                        fontSize: 12,
                        lineHeight: "20px",
                        background: token?.isEnabled ? "rgba(0,128,0,0.12)" : "rgba(0,0,0,0.06)",
                        border: token?.isEnabled ? "1px solid rgba(0,128,0,0.3)" : "1px solid rgba(0,0,0,0.15)",
                        color: "inherit",
                      }}
                    >
                      {token?.name || value}
                      {closable && (
                        <span
                          style={{ cursor: "pointer", opacity: 0.5, fontSize: 11, lineHeight: 1 }}
                          onClick={e => { e.stopPropagation(); onClose() }}
                        >
                          ×
                        </span>
                      )}
                    </span>
                  )
                }}
              />
            </Form.Item>
            <div className="flex gap-2 flex-shrink-0">
              <Button
                onClick={() => form.setFieldsValue({ posterProxyTokens: [] })}
                className={isMobile ? 'flex-1' : ''}
              >
                {t('bullet.fallbackTokenClearAll')}
              </Button>
              <Button
                type="primary"
                loading={posterProxyTokensSaving}
                onClick={handlePosterProxyTokensSave}
                className={isMobile ? 'flex-1' : ''}
              >
                {t('bullet.fallbackSaveConfig')}
              </Button>
            </div>
          </div>
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
