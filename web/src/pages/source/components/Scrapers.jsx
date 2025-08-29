import {
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  List,
  message,
  Modal,
  Switch,
  Tag,
  Tooltip,
} from 'antd'
import { useEffect, useState, useRef } from 'react'
import {
  biliLogout,
  getbiliLoginQrcode,
  getbiliUserinfo,
  getScrapers,
  getSingleScraper,
  pollBiliLogin,
  setScrapers,
  setSingleScraper,
} from '../../../apis'
import { MyIcon } from '@/components/MyIcon'
import {
  closestCorners,
  DndContext,
  DragOverlay,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

import { QRCodeCanvas } from 'qrcode.react'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'

const SortableItem = ({
  item,
  biliUserinfo,
  index,
  handleChangeStatus,
  handleConfig,
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: item.id || `item-${index}`, // 使用item.id或索引作为唯一标识
    data: {
      item,
      index,
    },
  })

  const isMobile = useAtomValue(isMobileAtom)

  // 拖拽样式
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    cursor: 'grab',
    touchAction: 'none', // 关键：阻止浏览器默认触摸行为
    userSelect: 'none', // 防止拖拽时选中文本
    ...(isDragging && { cursor: 'grabbing' }),
  }

  return (
    <List.Item ref={setNodeRef} style={style} {...attributes}>
      {/* 保留你原有的列表项渲染逻辑 */}
      <div className="w-full flex items-center justify-between">
        {/* 左侧添加拖拽手柄 */}
        <div className="flex items-center gap-2">
          <div {...listeners} style={{ cursor: 'grab' }}>
            <MyIcon icon="drag" size={24} />
          </div>
          <div>{item.providerName}</div>
        </div>
        <div className="flex items-center justify-around gap-4">
          {item.providerName === 'bilibili' && !isMobile && (
            <div>
              {biliUserinfo.isLogin ? (
                <div className="flex items-center justify-start gap-2">
                  <img
                    className="w-6 h-6 rounded-full"
                    src={biliUserinfo.face}
                  />
                  <span>{biliUserinfo.uname}</span>
                </div>
              ) : (
                <span className="opacity-50">未登录</span>
              )}
            </div>
          )}
          <div onClick={handleConfig} className="cursor-pointer">
            <MyIcon icon="setting" size={24} />
          </div>
          {item.isEnabled ? (
            <Tag color="green">已启用</Tag>
          ) : (
            <Tag color="red">未启用</Tag>
          )}
          <Tooltip title="切换启用状态">
            <div onClick={handleChangeStatus}>
              <MyIcon icon="exchange" size={24} />
            </div>
          </Tooltip>
        </div>
      </div>
    </List.Item>
  )
}

export const Scrapers = () => {
  const [loading, setLoading] = useState(true)
  const [list, setList] = useState([])
  const [activeItem, setActiveItem] = useState(null)
  const dragOverlayRef = useRef(null)
  // 设置窗口
  const [open, setOpen] = useState(false)
  // 设置类型
  const [setname, setSetname] = useState('')
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [form] = Form.useForm()

  // bili 相关
  const [biliQrcode, setBiliQrcode] = useState({})
  const [biliQrcodeStatus, setBiliQrcodeStatus] = useState('')
  const [biliQrcodeLoading, setBiliQrcodeLoading] = useState(false)
  const [biliUserinfo, setBiliUserinfo] = useState({})
  console.log(biliUserinfo, 'biliUserinfo')
  const [biliLoginOpen, setBiliLoginOpen] = useState(false)
  const [biliQrcodeChecked, setBiliQrcodeChecked] = useState(false)
  /** 扫码登录轮训 */
  const timer = useRef(0)

  const modalApi = useModal()
  const messageApi = useMessage()

  const sensors = useSensors(
    useSensor(MouseSensor, {
      activationConstraint: {
        distance: 5,
      },
    }),
    useSensor(TouchSensor, {
      activationConstraint: {
        distance: 8,
        delay: 100,
      },
    })
  )

  useEffect(() => {
    getInfo()
  }, [])

  const getInfo = async () => {
    try {
      setLoading(true)
      const [res1, res2] = await Promise.all([getScrapers(), getbiliUserinfo()])
      setList(res1.data ?? [])
      setBiliUserinfo(res2.data)
    } catch (error) {
    } finally {
      setLoading(false)
    }
  }

  const handleDragEnd = event => {
    const { active, over } = event

    // 拖拽无效或未改变位置
    if (!over || active.id === over.id) {
      setActiveItem(null)
      return
    }

    // 找到原位置和新位置
    const activeIndex = list.findIndex(
      item => item.providerName === active.data.current.item.providerName
    )
    const overIndex = list.findIndex(
      item => item.providerName === over.data.current.item.providerName
    )

    if (activeIndex !== -1 && overIndex !== -1) {
      // 1. 重新排列数组
      const newList = [...list]
      const [movedItem] = newList.splice(activeIndex, 1)
      newList.splice(overIndex, 0, movedItem)

      // 2. 重新计算所有项的display_order（从1开始连续编号）
      const updatedList = newList.map((item, index) => ({
        ...item,
        displayOrder: index + 1, // 排序值从1开始
      }))

      // 3. 更新状态
      console.log(updatedList, 'updatedList')
      setList(updatedList)
      setScrapers(updatedList)
      messageApi.success(
        `已更新排序，${movedItem.providerName} 移动到位置 ${overIndex + 1}`
      )
    }

    setActiveItem(null)
  }

  // 处理拖拽开始
  const handleDragStart = event => {
    const { active } = event
    // 找到当前拖拽的项
    const item = list.find(
      item => (item.id || `item-${list.indexOf(item)}`) === active.id
    )
    setActiveItem(item)
  }

  const handleChangeStatus = item => {
    const newList = list.map(it => {
      if (it.providerName === item.providerName) {
        return {
          ...it,
          isEnabled: Number(!it.isEnabled),
        }
      } else {
        return it
      }
    })
    setList(newList)
    setScrapers(newList)
  }

  const handleConfig = async item => {
    const res = await getSingleScraper({
      name: item.providerName,
    })
    setOpen(true)
    setSetname(item.providerName)
    const setNameCapitalize = `${item.providerName.charAt(0).toUpperCase()}${item.providerName.slice(1)}`
    form.setFieldsValue({
      [`scraper${setNameCapitalize}LogResponses`]:
        res.data?.[`scraper${setNameCapitalize}LogResponses`] ?? false,
      [`${item.providerName}EpisodeBlacklistRegex`]:
        res.data?.[`${item.providerName}EpisodeBlacklistRegex`] || '',
      [`${item.providerName}Cookie`]:
        res.data?.[`${item.providerName}Cookie`] ?? undefined,
      [`${item.providerName}UserAgent`]:
        res.data?.[`${item.providerName}UserAgent`] ?? undefined,
      useProxy: res.data?.useProxy ?? false,
    })
  }

  const handleSaveSingleScraper = async () => {
    try {
      setConfirmLoading(true)
      const values = await form.validateFields()
      const setNameCapitalize = `${setname.charAt(0).toUpperCase()}${setname.slice(1)}`

      await setSingleScraper({
        ...values,
        [`scraper${setNameCapitalize}LogResponses`]:
          values[`scraper${setNameCapitalize}LogResponses`],
        name: setname,
      })
      messageApi.success('保存成功')
    } catch (error) {
      console.error(error)
      messageApi.error('保存失败')
    } finally {
      setConfirmLoading(false)
      setOpen(false)
      form.resetFields()
    }
  }

  const startBiliLoginPoll = data => {
    timer.current = window.setInterval(() => {
      pollBiliLogin({
        qrcodeKey: data.qrcodeKey,
      })
        .then(res => {
          if (res.data.code === 86038) {
            clearInterval(timer.current)
            setBiliQrcodeStatus('expire')
          } else if (res.data.code === 86090) {
            setBiliQrcodeStatus('mobileConfirm')
          } else if (res.data.code === 0) {
            // 登录成功
            clearInterval(timer.current)
            setBiliLoginOpen(false)
            setOpen(false)
            getInfo()
          }
        })
        .catch(error => {
          setBiliQrcodeStatus('error')
          clearInterval(timer.current)
        })
    }, 1000)
  }

  useEffect(() => {
    return () => {
      clearInterval(timer.current)
    }
  }, [])

  const handleBiliQrcode = async () => {
    try {
      const res = await getbiliLoginQrcode()
      setBiliQrcode(res.data)
      setBiliQrcodeLoading(true)
      setBiliLoginOpen(true)
      startBiliLoginPoll(res.data)
      setBiliQrcodeStatus('')
    } catch (error) {
      messageApi.error('获取二维码失败')
    } finally {
      setBiliQrcodeLoading(false)
    }
  }

  const cancelBiliLogin = () => {
    setBiliLoginOpen(false)
    clearInterval(timer.current)
    setBiliQrcodeStatus('')
  }

  const handleBiliLogout = () => {
    modalApi.confirm({
      title: '清除缓存',
      zIndex: 1002,
      content: <div>确定要注销当前的Bilibili登录吗？</div>,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          await biliLogout()
          getInfo()
          setBiliQrcodeStatus('')
        } catch (err) {}
      },
    })
  }

  const renderDragOverlay = () => {
    if (!activeItem) return null

    return (
      <div ref={dragOverlayRef} style={{ width: '100%', maxWidth: '100%' }}>
        <List.Item
          style={{
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
            opacity: 0.9,
          }}
        >
          <div className="w-full flex items-center justify-between">
            <div className="flex items-center gap-2">
              <MyIcon icon="drag" size={24} />
              <div>{activeItem.providerName}</div>
            </div>
            <div className="flex items-center justify-around gap-4">
              <MyIcon icon="setting" size={24} />
              {activeItem.isEnabled ? (
                <Tag color="green">已启用</Tag>
              ) : (
                <Tag color="red">未启用</Tag>
              )}
            </div>
          </div>
        </List.Item>
      </div>
    )
  }

  return (
    <div className="my-6">
      <Card loading={loading} title="弹幕搜索源">
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            strategy={verticalListSortingStrategy}
            items={list.map((item, index) => item.id || `item-${index}`)}
          >
            <List
              itemLayout="vertical"
              size="large"
              dataSource={list}
              renderItem={(item, index) => (
                <SortableItem
                  key={item.id || index}
                  item={item}
                  index={index}
                  biliUserinfo={biliUserinfo}
                  handleChangeStatus={() => handleChangeStatus(item)}
                  handleConfig={() => handleConfig(item)}
                />
              )}
            />
          </SortableContext>

          {/* 拖拽覆盖层 */}
          <DragOverlay>{renderDragOverlay()}</DragOverlay>
        </DndContext>
      </Card>
      <Modal
        title={`配置: ${setname}`}
        open={open}
        onOk={handleSaveSingleScraper}
        confirmLoading={confirmLoading}
        cancelText="取消"
        okText="确认"
        onCancel={() => setOpen(false)}
      >
        <Form form={form} layout="vertical">
          <div className="mb-4">请为 {setname} 源填写以下配置信息。</div>
          <Form.Item
            name="useProxy"
            label="使用代理"
            valuePropName="checked"
            className="mb-4"
          >
            <Switch />
          </Form.Item>
          {/* gamer ua cookie */}
          {setname === 'gamer' && (
            <>
              <Form.Item
                name={`${setname}Cookie`}
                label="Cookie"
                className="mb-4"
              >
                <Input.TextArea />
              </Form.Item>
              <Form.Item
                name={`${setname}UserAgent`}
                label="User-Agent"
                className="mb-4"
              >
                <Input />
              </Form.Item>
            </>
          )}
          {/* 通用部分 分集标题黑名单 记录原始响应 */}
          <Form.Item
            name={`${setname}EpisodeBlacklistRegex`}
            label="分集标题黑名单 (正则)"
            className="mb-4"
          >
            <Input />
          </Form.Item>
          <div className="flex items-center justify-start flex-wrap md:flex-nowrap gap-2 mb-4">
            <Form.Item
              name={`scraper${setname.charAt(0).toUpperCase()}${setname.slice(1)}LogResponses`}
              label="记录原始响应"
              valuePropName="checked"
              className="min-w-[100px] shrink-0 !mb-0"
            >
              <Switch />
            </Form.Item>
            <div className="w-full">
              启用后，此源的所有API请求的原始响应将被记录到
              config/logs/scraper_responses.log 文件中，用于调试。
            </div>
          </div>
          {/* bilibili登录信息 */}
          {setname === 'bilibili' && (
            <div>
              {biliUserinfo.isLogin ? (
                <div className="text-center">
                  <div className="flex items-center justify-center gap-2 mb-4">
                    <img
                      className="w-10 h-10 rounded-full"
                      src={biliUserinfo.face}
                    />
                    <span>{biliUserinfo.uname}</span>
                    {biliUserinfo.vipStatus === 1 && (
                      <Tag
                        color={biliUserinfo.vipType === 2 ? '#f50' : '#2db7f5'}
                      >
                        {biliUserinfo.vipType === 2 ? '年度大会员' : '大会员'}
                      </Tag>
                    )}
                  </div>
                  <Button type="primary" danger onClick={handleBiliLogout}>
                    注销登录
                  </Button>
                </div>
              ) : (
                <div className="text-center">
                  <div className="mb-4">当前未登录。</div>
                  <div className="flex items-center justify-center">
                    <Checkbox
                      checked={biliQrcodeChecked}
                      onChange={() => setBiliQrcodeChecked(v => !v)}
                    />
                    <span
                      className="ml-2 cursor-pointer"
                      onClick={() => setBiliQrcodeChecked(v => !v)}
                    >
                      我已阅读并同意以下免责声明
                    </span>
                  </div>
                  <div className="my-3">
                    登录接口由{' '}
                    <a
                      href="https://github.com/SocialSisterYi/bilibili-API-collect"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      bilibili-API-collect
                    </a>{' '}
                    提供，为Blibili官方非公开接口。
                    您的登录凭据将加密存储在您自己的数据库中。登录行为属用户个人行为，通过该登录获取数据同等于使用您的账号获取，由登录用户自行承担相关责任，与本工具无关。使用本接口登录等同于认同该声明。
                  </div>
                  <Button
                    disabled={!biliQrcodeChecked}
                    type="primary"
                    loading={biliQrcodeLoading}
                    onClick={handleBiliQrcode}
                  >
                    扫码登录
                  </Button>
                </div>
              )}
            </div>
          )}
        </Form>
      </Modal>
      <Modal
        title="bilibili扫码登录"
        open={biliLoginOpen}
        footer={null}
        onCancel={() => setBiliLoginOpen(false)}
      >
        <div className="text-center">
          <div className="relative w-[200px] h-[200px] mx-auto mb-3">
            <QRCodeCanvas
              value={biliQrcode.url}
              size={200}
              fgColor="#000"
              level="M"
            />

            {biliQrcodeStatus === 'expire' && (
              <div
                className="absolute left-0 top-0 w-full h-full p-3 flex items-center justify-center bg-black/80 cursor-pointer text-neutral-100"
                onClick={handleBiliQrcode}
              >
                二维码已失效
                <br />
                点击重新获取
              </div>
            )}
            {biliQrcodeStatus === 'mobileConfirm' && (
              <div className="absolute left-0 top-0 w-full h-full p-3 flex items-center justify-center bg-black/80 text-neutral-100">
                已扫描，请在
                <br />
                手机上确认登录
              </div>
            )}
            {biliQrcodeStatus === 'error' && (
              <div
                className="absolute left-0 top-0 w-full h-full p-3 flex items-center justify-center bg-black/80 cursor-pointer text-neutral-100"
                onClick={handleBiliQrcode}
              >
                轮询失败
                <br />
                点击重新获取
              </div>
            )}
          </div>
          <div className="mb-3">请使用Bilibili手机客户端扫描二维码</div>
          <Button type="primary" danger onClick={cancelBiliLogin}>
            取消登录
          </Button>
        </div>
      </Modal>
    </div>
  )
}
