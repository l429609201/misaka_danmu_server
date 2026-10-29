import { getSearchResult } from '../../../apis'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card, Form, Input, Progress } from 'antd'
import { useAtom } from 'jotai'
import {
  lastSearchResultAtom,
  searchHistoryAtom,
  searchLoadingAtom,
} from '../../../../store'

// 把主页搜索结果投递给御坂看板娘气泡（AssistantWidget 监听该事件）。
function notifyAssistant(text, kind, title, body) {
  window.dispatchEvent(new CustomEvent('assistant-notify', { detail: { text, kind, title, body } }))
}
import { useSearchParams } from 'react-router-dom'

export const SearchBar = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useAtom(searchLoadingAtom)
  const [form] = Form.useForm()
  const [percent, setPercent] = useState(0)
  const timer = useRef(0)

  const [, setSearchHistory] = useAtom(searchHistoryAtom)

  const [, setLastSearchResultData] = useAtom(lastSearchResultAtom)

  // 从 URL 参数读取 keyword 并自动填入；顶栏弹幕搜索会额外带 auto=1（立即搜索）和 provider（指定源）
  const [searchParams, setSearchParams] = useSearchParams()
  const pendingAutoSearchRef = useRef(null)
  useEffect(() => {
    const urlKeyword = searchParams.get('keyword')
    if (!urlKeyword) return
    form.setFieldValue('keyword', urlKeyword)
    if (searchParams.get('auto') === '1') {
      pendingAutoSearchRef.current = {
        keyword: urlKeyword,
        provider: searchParams.get('provider') || '',
      }
    }
    // 清除 URL 参数，避免刷新后重复触发；每次顶栏重新跳转都会带新参数，因此不再只处理首次
    setSearchParams({}, { replace: true })
  }, [searchParams, form, setSearchParams])

  const onSearch = async (values, page = 1, pageSize = 10, provider = '') => {
    try {
      if (loading) return
      setLoading(true)
      setSearchHistory(history => {
        if (history.includes(values.keyword)) return history
        return [values.keyword, ...history].slice(0, 10)
      })

      const startedAt = performance.now()
      timer.current = window.setInterval(() => {
        setPercent(p => (p <= 90 ? p + Math.ceil(Math.random() * 5) : 95))
      }, 200)

      const res = await getSearchResult(
        {
          keyword: values.keyword,
          page,
          pageSize,
          providerFilter: provider || undefined,
        },
        onProgress
      )

      setLastSearchResultData({
        ...(res?.data || {}),
        keyword: values.keyword,
        // 告诉结果区把来源筛选器同步为指定源（多选为逗号串，翻页时继续按这些源过滤）；'all' 表示全源
        requestedProvider: provider || 'all',
      })
      if (page === 1) {
        const data = res?.data || {}
        const total = data.total ?? data.results?.length ?? 0
        const sources = data.available_providers?.length || 0
        const supplemental = data.supplemental_results?.length || 0
        const seconds = ((performance.now() - startedAt) / 1000).toFixed(2)
        const params = { keyword: values.keyword, total, sources, supplemental, seconds }
        notifyAssistant(
          total ? t('home.searchNotifyDone', params) : t('home.searchNotifyEmpty', params),
          total ? 'done' : 'failed',
          t('home.searchNotifyTitle'),
          t('home.searchNotifyBody', params)
        )
      }
    } catch (error) {
      console.error(`搜索失败: ${error.message || error}`)
      notifyAssistant(
        t('home.searchNotifyFailed', { keyword: values.keyword, reason: String(error?.message || error).slice(0, 60) }),
        'failed',
        t('home.searchNotifyTitle')
      )
    } finally {
      setLoading(false)
      setPercent(0)
      clearInterval(timer.current)
    }
  }

  const onProgress = progressEvent => {
    clearInterval(timer.current)
    if (progressEvent.lengthComputable) {
      const percent = Math.round(
        (progressEvent.loaded / progressEvent.total) * 100
      )
      setPercent(percent)
    }
  }

  useEffect(() => {
    return () => {
      clearInterval(timer.current)
    }
  }, [])

  // 处理顶栏跳转带来的自动搜索（等 loading 空闲后执行，避免与进行中的搜索冲突）
  useEffect(() => {
    const pending = pendingAutoSearchRef.current
    if (!pending || loading) return
    pendingAutoSearchRef.current = null
    onSearch({ keyword: pending.keyword }, 1, 10, pending.provider)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, loading])

  // 本组件不再渲染条件栏：关键词、精确搜索、历史都在顶栏搜索框；
  // 这里只负责执行搜索（隐藏表单承接 URL 关键词）并在搜索中展示进度卡片
  return (
    <>
      <Form form={form} onFinish={onSearch} style={{ display: 'none' }}>
        <Form.Item name="keyword" noStyle>
          <Input />
        </Form.Item>
      </Form>
      {loading && (
        <Card>
          <div className="py-10 flex flex-col items-center gap-3">
            <div className="text-base font-medium">{t('home.searching')}</div>
            <div className="w-full" style={{ maxWidth: 520 }}>
              <Progress percent={percent} status="active" />
            </div>
            <div className="text-xs opacity-60">{form.getFieldValue('keyword')}</div>
          </div>
        </Card>
      )}
    </>
  )
}
