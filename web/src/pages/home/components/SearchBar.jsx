import { getSearchResult, clearSearchCache } from '../../../apis'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Progress,
  Tag,
} from 'antd'
import { useAtom, useAtomValue } from 'jotai'
import {
  isMobileAtom,
  lastSearchResultAtom,
  searchHistoryAtom,
  searchLoadingAtom,
} from '../../../../store'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'
import { useSearchParams } from 'react-router-dom'

export const SearchBar = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useAtom(searchLoadingAtom)
  const [cacheLoading, setCacheLoading] = useState(false)
  const [form] = Form.useForm()
  const season = Form.useWatch('season', form)
  const episode = Form.useWatch('episode', form)
  const keyword = Form.useWatch('keyword', form)
  const [percent, setPercent] = useState(0)
  const timer = useRef(0)

  const isMobile = useAtomValue(isMobileAtom)
  const [searchHistory, setSearchHistory] = useAtom(searchHistoryAtom)

  //开启精确搜索
  const [exactSearch, setExactSearch] = useState(false)

  const [, setLastSearchResultData] = useAtom(lastSearchResultAtom)

  const modalApi = useModal()
  const messageApi = useMessage()

  // 从 URL 参数读取 keyword 并自动填入
  const [searchParams, setSearchParams] = useSearchParams()
  const initialKeywordRef = useRef(false)
  useEffect(() => {
    const urlKeyword = searchParams.get('keyword')
    if (urlKeyword && !initialKeywordRef.current) {
      initialKeywordRef.current = true
      form.setFieldValue('keyword', urlKeyword)
      // 清除 URL 参数，避免刷新后重复触发
      setSearchParams({}, { replace: true })
    }
  }, [searchParams, form, setSearchParams])

  const onInsert = () => {
    if (!season) {
      messageApi.destroy()
      messageApi.error(t('home.inputSeason'))
      return
    }
    let formatted = ` S${String(season).padStart(2, '0')}`
    if (episode) {
      formatted += `E${String(episode).padStart(2, '0')}`
    }
    form.setFieldValue('keyword', `${keyword}${formatted}`)
  }

  const onSearch = async (values, page = 1, pageSize = 10) => {
    try {
      if (loading) return
      setLoading(true)
      setSearchHistory(history => {
        if (history.includes(values.keyword)) return history
        return [values.keyword, ...history].slice(0, 10)
      })

      timer.current = window.setInterval(() => {
        setPercent(p => (p <= 90 ? p + Math.ceil(Math.random() * 5) : 95))
      }, 200)

      const res = await getSearchResult(
        {
          keyword: values.keyword,
          page,
          pageSize,
        },
        onProgress
      )

      setLastSearchResultData({
        ...(res?.data || {}),
        keyword: values.keyword,
      })
    } catch (error) {
      console.error(`搜索失败: ${error.message || error}`)
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

  const onClearCache = () => {
    modalApi.confirm({
      title: t('home.clearCacheTitle'),
      zIndex: 1002,
      content: (
        <div>
          {t('home.clearCacheContent')}
          <br />
          {t('home.clearCacheDesc')}
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          setCacheLoading(true)
          const res = await clearSearchCache()
          messageApi.destroy()
          messageApi.success(res.data.message || t('home.cacheCleared'))
        } catch (err) {
          messageApi.destroy()
          messageApi.error(`${t('home.clearCacheFailed')}: ${err.message || err}`)
        } finally {
          setCacheLoading(false)
        }
      },
    })
  }

  useEffect(() => {
    return () => {
      clearInterval(timer.current)
    }
  }, [])

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="text-lg font-semibold">{t('home.searchAnime')}</div>
        <Button type="primary" loading={cacheLoading} onClick={onClearCache}>
          {t('home.clearCache')}
        </Button>
      </div>

      <Form form={form} onFinish={onSearch}>
        <div className="flex items-center gap-3 mb-4">
          <Form.Item
            name="keyword"
            className="flex-1 mb-0"
            rules={[{ required: true, message: t('home.inputAnimeName') }]}
          >
            <Input.Search
              placeholder={t('home.inputAnimeName')}
              enterButton={t('home.search')}
              loading={loading}
              onSearch={value => {
                if (value) {
                  form.setFieldValue('keyword', value)
                  form.submit()
                }
              }}
            />
          </Form.Item>
        </div>

        {loading && (
          <div className="mb-4">
            <Progress percent={percent} />
          </div>
        )}

        <div className={`flex items-center ${isMobile ? 'gap-2' : 'gap-3 flex-wrap'}`}>
          <Checkbox
            checked={exactSearch}
            onChange={e => setExactSearch(e.target.checked)}
          >
            {isMobile ? <span>{t('home.exact')}<br />{t('home.search')}</span> : t('home.exactSearch')}
          </Checkbox>

          <div className={`flex items-center ${isMobile ? 'gap-2' : 'gap-3'}`}>
            <div className="flex items-center gap-1">
              <span className={`leading-8 ${exactSearch ? '' : 'text-gray-400'}`}>{t('home.season')}</span>
              <Form.Item name="season" noStyle>
                <InputNumber min={0} placeholder={t('home.season')} disabled={!exactSearch} style={{ width: 80 }} />
              </Form.Item>
            </div>
            <div className="flex items-center gap-1">
              <span className={`leading-8 ${exactSearch ? '' : 'text-gray-400'}`}>{t('home.episode')}</span>
              <Form.Item name="episode" noStyle>
                <InputNumber min={1} placeholder={t('home.episode')} disabled={!exactSearch || !season} style={{ width: 80 }} />
              </Form.Item>
            </div>
            <Button type="primary" onClick={onInsert} size="small" disabled={!exactSearch}>
              {t('home.insert')}
            </Button>
          </div>
          {!isMobile && (
            <span className={`text-xs ${exactSearch ? 'text-gray-500' : 'text-gray-300'}`}>
              {t('home.insertTip')}
            </span>
          )}
        </div>
      </Form>

      {!!searchHistory.length && (
        <div className="flex items-center flex-wrap gap-2 mt-4">
          {searchHistory.map((it, index) => (
            <Tag
              key={index}
              closable
              className="cursor-pointer"
              onClick={() => {
                form.setFieldsValue({ keyword: it })
                onSearch({ keyword: it })
              }}
              onClose={e => {
                e.preventDefault()
                setSearchHistory(history => history.filter(o => o !== it))
              }}
            >
              {it}
            </Tag>
          ))}
        </div>
      )}
    </div>
  )
}
