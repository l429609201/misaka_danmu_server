import React, { useState, useMemo } from 'react'
import { Modal, Table, Radio, Button, Space, InputNumber, Alert, Tag } from 'antd'
import { InfoCircleOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { useTranslation } from 'react-i18next'

/**
 * 番剧源关联冲突解决对话框
 */
const ReassociationConflictModal = ({ open, onCancel, onConfirm, conflictData, targetAnimeTitle }) => {
  const { t } = useTranslation()
  // 每个提供商的解决方案状态
  const [resolutions, setResolutions] = useState({})
  // 每个提供商的偏移量
  const [offsets, setOffsets] = useState({})

  // 初始化解决方案(默认全选目标)
  useMemo(() => {
    if (!conflictData || !conflictData.conflicts) return

    const initialResolutions = {}
    const initialOffsets = {}

    conflictData.conflicts.forEach(conflict => {
      const providerResolutions = {}
      conflict.conflictEpisodes.forEach(ep => {
        providerResolutions[ep.episodeIndex] = false // false = 保留目标
      })
      initialResolutions[conflict.providerName] = providerResolutions
      initialOffsets[conflict.providerName] = 0
    })

    setResolutions(initialResolutions)
    setOffsets(initialOffsets)
  }, [conflictData])

  // 处理单个分集的选择
  const handleEpisodeSelection = (providerName, episodeIndex, keepSource) => {
    setResolutions(prev => ({
      ...prev,
      [providerName]: {
        ...prev[providerName],
        [episodeIndex]: keepSource,
      },
    }))
  }

  // 全选源番剧
  const handleSelectAllSource = providerName => {
    const conflict = conflictData.conflicts.find(c => c.providerName === providerName)
    if (!conflict) return

    const newResolutions = {}
    conflict.conflictEpisodes.forEach(ep => {
      newResolutions[ep.episodeIndex] = true // true = 保留源
    })

    setResolutions(prev => ({
      ...prev,
      [providerName]: newResolutions,
    }))
  }

  // 全选目标番剧
  const handleSelectAllTarget = providerName => {
    const conflict = conflictData.conflicts.find(c => c.providerName === providerName)
    if (!conflict) return

    const newResolutions = {}
    conflict.conflictEpisodes.forEach(ep => {
      newResolutions[ep.episodeIndex] = false // false = 保留目标
    })

    setResolutions(prev => ({
      ...prev,
      [providerName]: newResolutions,
    }))
  }

  // 按弹幕数量选择
  const handleSelectByDanmakuCount = providerName => {
    const conflict = conflictData.conflicts.find(c => c.providerName === providerName)
    if (!conflict) return

    const newResolutions = {}
    conflict.conflictEpisodes.forEach(ep => {
      // 选择弹幕更多的
      newResolutions[ep.episodeIndex] = ep.sourceDanmakuCount > ep.targetDanmakuCount
    })

    setResolutions(prev => ({
      ...prev,
      [providerName]: newResolutions,
    }))
  }

  // 处理偏移量变化
  const handleOffsetChange = (providerName, value) => {
    setOffsets(prev => ({
      ...prev,
      [providerName]: value || 0,
    }))
  }

  // 确认关联
  const handleConfirm = () => {
    // 构建解决方案数据
    const resolutionData = conflictData.conflicts.map(conflict => ({
      providerName: conflict.providerName,
      sourceOffset: offsets[conflict.providerName] || 0,
      episodeResolutions: Object.entries(resolutions[conflict.providerName] || {}).map(
        ([episodeIndex, keepSource]) => ({
          episodeIndex: parseInt(episodeIndex),
          keepSource,
        })
      ),
    }))

    onConfirm(resolutionData)
  }

  // 表格列定义
  const getColumns = providerName => [
    {
      title: t('reassociation.colEpisode'),
      dataIndex: 'episodeIndex',
      key: 'episodeIndex',
      width: 80,
      align: 'center',
    },
    {
      title: t('reassociation.colSourceAnime'),
      key: 'source',
      width: 150,
      render: record => (
        <div>
          <div>{t('reassociation.danmakuCount', { count: record.sourceDanmakuCount })}</div>
          {record.sourceLastFetchTime && (
            <div style={{ fontSize: '12px', color: '#999' }}>
              📅 {dayjs(record.sourceLastFetchTime).format('YYYY-MM-DD')}
            </div>
          )}
        </div>
      ),
    },
    {
      title: t('reassociation.colTargetAnime'),
      key: 'target',
      width: 150,
      render: record => (
        <div>
          <div>{t('reassociation.danmakuCount', { count: record.targetDanmakuCount })}</div>
          {record.targetLastFetchTime && (
            <div style={{ fontSize: '12px', color: '#999' }}>
              📅 {dayjs(record.targetLastFetchTime).format('YYYY-MM-DD')}
            </div>
          )}
        </div>
      ),
    },
    {
      title: t('reassociation.colKeep'),
      key: 'keep',
      width: 150,
      align: 'center',
      render: record => (
        <Radio.Group
          value={resolutions[providerName]?.[record.episodeIndex] ?? false}
          onChange={e =>
            handleEpisodeSelection(providerName, record.episodeIndex, e.target.value)
          }
        >
          <Radio value={true}>{t('reassociation.radioSource')}</Radio>
          <Radio value={false}>{t('reassociation.radioTarget')}</Radio>
        </Radio.Group>
      ),
    },
  ]

  if (!conflictData || !conflictData.hasConflict) {
    return null
  }

  return (
    <Modal
      title={t('reassociation.title')}
      open={open}
      onCancel={onCancel}
      onOk={handleConfirm}
      width={900}
      okText={t('reassociation.okText')}
      cancelText={t('common.cancel')}
    >
      <Alert
        message={t('reassociation.alertMessage')}
        description={t('reassociation.alertDescription', { title: targetAnimeTitle })}
        type="warning"
        icon={<InfoCircleOutlined />}
        showIcon
        style={{ marginBottom: 16 }}
      />

      {conflictData.conflicts.map(conflict => (
        <div key={conflict.providerName} style={{ marginBottom: 24 }}>
          <div style={{ marginBottom: 12 }}>
            <Tag color="blue" style={{ fontSize: '14px', padding: '4px 12px' }}>
              📺 {conflict.providerName}
            </Tag>
            <span style={{ marginLeft: 8, color: '#999' }}>
              {t('reassociation.conflictEpisodes', { count: conflict.conflictEpisodes.length })}
            </span>
          </div>

          <Table
            dataSource={conflict.conflictEpisodes}
            columns={getColumns(conflict.providerName)}
            rowKey="episodeIndex"
            pagination={false}
            size="small"
            scroll={{ y: 300 }}
            style={{ marginBottom: 12 }}
          />

          <Space style={{ marginBottom: 12 }}>
            <Button size="small" onClick={() => handleSelectAllSource(conflict.providerName)}>
              {t('reassociation.selectAllSource')}
            </Button>
            <Button size="small" onClick={() => handleSelectAllTarget(conflict.providerName)}>
              {t('reassociation.selectAllTarget')}
            </Button>
            <Button
              size="small"
              type="primary"
              onClick={() => handleSelectByDanmakuCount(conflict.providerName)}
            >
              {t('reassociation.selectByDanmaku')}
            </Button>
          </Space>

          <div style={{ marginTop: 12 }}>
            <span style={{ marginRight: 8 }}>{t('reassociation.episodeOffset')}</span>
            <InputNumber
              size="small"
              value={offsets[conflict.providerName] || 0}
              onChange={value => handleOffsetChange(conflict.providerName, value)}
              style={{ width: 100 }}
              placeholder="0"
            />
            <span style={{ marginLeft: 8, color: '#999', fontSize: '12px' }}>
              {t('reassociation.offsetHint')}
            </span>
          </div>
        </div>
      ))}
    </Modal>
  )
}

export default ReassociationConflictModal

