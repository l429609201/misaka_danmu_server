import { getSearchResult, clearSearchCache } from '../../../apis'
import { useEffect, useRef, useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  Col,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Progress,
  Row,
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

export const SearchBar = () => {
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

  const onInsert = () => {
    if (!season) {
      messageApi.destroy()
      messageApi.error('请输入季数')
      return
    }
    let formatted = ` S${String(season).padStart(2, '0')}`
    if (episode) {
      formatted += `E${String(episode).padStart(2, '0')}`
    }
    form.setFieldValue('keyword', `${keyword}${formatted}`)
  }

  const onSearch = async values => {
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
      title: '清除缓存',
      zIndex: 1002,
      content: (
        <div>
          您确定要清除所有缓存吗？
          <br />
          这将清除所有搜索结果和分集列表的临时缓存，下次访问时需要重新从网络获取。
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          setCacheLoading(true)
          const res = await clearSearchCache()
          messageApi.destroy()
          messageApi.success(res.data.message || '缓存已成功清除！')
        } catch (err) {
          messageApi.destroy()
          messageApi.error(`清除缓存失败: ${error.message || error}`)
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
    <div className="my-4">
      <Card
        title="搜索"
        extra={
          <Button type="primary" loading={cacheLoading} onClick={onClearCache}>
            清除缓存
          </Button>
        }
      >
        <Form
          form={form}
          layout="horizontal"
          onFinish={onSearch}
          className="px-6 pb-6"
        >
          <Row gutter={12}>
            <Col md={18} xs={24}>
              {/* 名称输入 */}
              <Form.Item
                name="keyword"
                label="输入番剧名称"
                rules={[{ required: true, message: '请输入番剧名称' }]}
              >
                <Input placeholder="请输入番剧名称" />
              </Form.Item>
            </Col>
            <Col md={6} xs={24}>
              <Form.Item>
                <Button
                  block
                  type="primary"
                  htmlType="submit"
                  loading={loading}
                >
                  搜索
                </Button>
              </Form.Item>
            </Col>
          </Row>
          {loading && (
            <div className="-mt-4 mb-4">
              <Progress percent={percent} />
            </div>
          )}
          <Row gutter={12} className="mb-5 md:mb-0">
            <Col md={6} xs={24}>
              <Form.Item
                style={{
                  marginBottom: 0,
                }}
              >
                <Checkbox
                  checked={exactSearch}
                  onChange={e => setExactSearch(e.target.checked)}
                >
                  电视节目精确搜索
                </Checkbox>
              </Form.Item>
            </Col>
            {exactSearch && (
              <Col md={18} xs={24}>
                <div className="flex items-center justify-start gap-3">
                  <Form.Item name="season" label="季度：">
                    <InputNumber min={0} placeholder="季数" />
                  </Form.Item>
                  <Form.Item name="episode" label="集数：">
                    <InputNumber
                      min={1}
                      placeholder="集数"
                      disabled={!season}
                    />
                  </Form.Item>

                  {!isMobile && (
                    <>
                      <Form.Item>
                        <Button type="primary" onClick={onInsert}>
                          插入
                        </Button>
                      </Form.Item>
                      <Form.Item>
                        <div className="text-xs">
                          需要填写季、集后可插入，其中季可单独插入
                        </div>
                      </Form.Item>
                    </>
                  )}
                </div>
                {isMobile && (
                  <>
                    <div>
                      <Button block type="primary" onClick={onInsert}>
                        插入
                      </Button>
                    </div>
                    <div className="text-xs mt-3">
                      需要填写季、集后可插入，其中季可单独插入
                    </div>
                  </>
                )}
              </Col>
            )}
          </Row>
        </Form>
        {!!searchHistory.length && (
          <div className="flex items-center justify-start flex-wrap gap-2 mt-4">
            {searchHistory.map((it, index) => {
              return (
                <span
                  key={index}
                  className="cursor-pointer"
                  onClick={() => {
                    form.setFieldsValue({
                      keyword: it,
                    })
                    onSearch({
                      keyword: it,
                    })
                  }}
                >
                  <Tag
                    closable
                    onClose={e => {
                      e.preventDefault()
                      setSearchHistory(history => history.filter(o => o !== it))
                    }}
                  >
                    {it}
                  </Tag>
                </span>
              )
            })}
          </div>
        )}
      </Card>
    </div>
  )
}
