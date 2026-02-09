import React, { useEffect, useState, useCallback } from 'react';
import { Modal, Form, Input, InputNumber, Select, Button, Space, Image, message, Tooltip } from 'antd';
import { SearchOutlined, LinkOutlined, EyeOutlined } from '@ant-design/icons';
import { updateMediaItem, updateLocalItem, getLocalImage, downloadPosterToLocal } from '../../../apis';
import PosterSearchModal from './PosterSearchModal';

const { Option } = Select;

const MediaItemEditor = ({ visible, item, onClose, onSaved, isLocal = false }) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [mediaType, setMediaType] = useState('tv_series');
  const [posterSearchVisible, setPosterSearchVisible] = useState(false);
  const [localImagePath, setLocalImagePath] = useState(null);
  const [localImageAnimeId, setLocalImageAnimeId] = useState(null);
  const [downloadingLocal, setDownloadingLocal] = useState(false);
  const [previewVisible, setPreviewVisible] = useState(false);

  // 加载本地海报信息
  const loadLocalImage = useCallback(async (title, season, year) => {
    if (!title) return;
    try {
      const res = await getLocalImage({ title, season: season || 1, year: year || undefined });
      const data = res?.data;
      setLocalImagePath(data?.localImagePath || null);
      setLocalImageAnimeId(data?.animeId || null);
    } catch {
      setLocalImagePath(null);
      setLocalImageAnimeId(null);
    }
  }, []);

  useEffect(() => {
    if (visible && item) {
      form.setFieldsValue({
        title: item.title,
        mediaType: item.mediaType,
        season: item.season,
        episode: item.episode,
        year: item.year,
        tmdbId: item.tmdbId,
        tvdbId: item.tvdbId,
        imdbId: item.imdbId,
        posterUrl: item.posterUrl,
        filePath: item.filePath,
      });
      setMediaType(item.mediaType);
      loadLocalImage(item.title, item.season, item.year);
    }
    if (!visible) {
      setLocalImagePath(null);
      setLocalImageAnimeId(null);
    }
  }, [visible, item, form, loadLocalImage]);

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      setLoading(true);

      if (isLocal) {
        await updateLocalItem(item.id, values);
      } else {
        await updateMediaItem(item.id, values);
      }
      message.success('更新成功');
      onSaved();
    } catch (error) {
      if (error.errorFields) {
        message.warning('请填写所有必填字段');
      } else {
        message.error('更新失败: ' + (error.message || '未知错误'));
      }
    } finally {
      setLoading(false);
    }
  };

  // 海报搜索选中回调
  const handlePosterSelect = (posterUrl) => {
    form.setFieldsValue({ posterUrl });
    message.success('已填入海报URL');
  };

  // URL直搜：下载网络图片到本地
  const handleDownloadToLocal = async () => {
    const posterUrl = form.getFieldValue('posterUrl');
    if (!posterUrl) {
      message.warning('请先填写海报URL');
      return;
    }
    const title = form.getFieldValue('title');
    const season = form.getFieldValue('season');
    const year = form.getFieldValue('year');

    setDownloadingLocal(true);
    try {
      const res = await downloadPosterToLocal({
        imageUrl: posterUrl,
        title: title || '',
        season: season || 1,
        year: year || undefined,
      });
      const data = res?.data;
      if (data?.localImagePath) {
        setLocalImagePath(data.localImagePath);
        setLocalImageAnimeId(data.animeId);
        message.success('海报已下载到本地');
      } else {
        message.error('下载失败');
      }
    } catch (error) {
      message.error('下载失败: ' + (error?.response?.data?.detail || error.message || '未知错误'));
    } finally {
      setDownloadingLocal(false);
    }
  };

  return (
    <>
    <Modal
      title="编辑媒体项"
      open={visible}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={loading}
      width={600}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          label="标题"
          name="title"
          rules={[{ required: true, message: '请输入标题' }]}
        >
          <Input />
        </Form.Item>

        <Form.Item
          label="类型"
          name="mediaType"
          rules={[{ required: true, message: '请选择类型' }]}
        >
          <Select onChange={(value) => {
            setMediaType(value);
            // 切换到电影类型时清空季度和集数
            if (value === 'movie') {
              form.setFieldsValue({ season: null, episode: null });
            }
          }}>
            <Option value="movie">电影</Option>
            <Option value="tv_series">电视节目</Option>
          </Select>
        </Form.Item>

        <Form.Item
          label="季度"
          name="season"
        >
          <InputNumber
            min={0}
            style={{ width: '100%' }}
            disabled={mediaType === 'movie'}
            placeholder={mediaType === 'movie' ? '电影无需填写季度' : ''}
          />
        </Form.Item>

        <Form.Item
          label="集数"
          name="episode"
        >
          <InputNumber
            min={1}
            style={{ width: '100%' }}
            disabled={mediaType === 'movie'}
            placeholder={mediaType === 'movie' ? '电影无需填写集数' : ''}
          />
        </Form.Item>

        <Form.Item
          label="年份"
          name="year"
        >
          <InputNumber min={1900} max={2100} style={{ width: '100%' }} />
        </Form.Item>

        <Form.Item
          label="TMDB ID"
          name="tmdbId"
        >
          <Input placeholder="例如: 12345" />
        </Form.Item>

        <Form.Item
          label="TVDB ID"
          name="tvdbId"
        >
          <Input placeholder="例如: 67890" />
        </Form.Item>

        <Form.Item
          label="IMDB ID"
          name="imdbId"
        >
          <Input placeholder="例如: tt1234567" />
        </Form.Item>

        <Form.Item label="海报URL">
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="posterUrl" noStyle>
              <Input placeholder="https://..." style={{ flex: 1 }} />
            </Form.Item>
            <Tooltip title="搜索海报">
              <Button
                icon={<SearchOutlined />}
                onClick={() => setPosterSearchVisible(true)}
              />
            </Tooltip>
            <Tooltip title="URL直搜（下载到本地）">
              <Button
                icon={<LinkOutlined />}
                loading={downloadingLocal}
                onClick={handleDownloadToLocal}
              />
            </Tooltip>
          </Space.Compact>
        </Form.Item>

        {/* 本地海报行 */}
        <Form.Item label="本地海报">
          <Space style={{ width: '100%' }}>
            <Input
              value={localImagePath || '暂无'}
              readOnly
              style={{ flex: 1, minWidth: 300, color: localImagePath ? undefined : 'var(--text-tertiary, #999)' }}
            />
            <Tooltip title="预览海报">
              <Button
                icon={<EyeOutlined />}
                disabled={!localImagePath}
                onClick={() => setPreviewVisible(true)}
              />
            </Tooltip>
          </Space>
        </Form.Item>

        {isLocal && (
          <Form.Item
            label="弹幕文件存储路径"
            name="filePath"
            tooltip="弹幕XML文件的存储路径，修改后会更新数据库记录（不会移动实际文件）"
          >
            <Input placeholder="例如: D:\Danmaku\xxx.xml" />
          </Form.Item>
        )}
      </Form>
    </Modal>

    {/* 海报搜索弹窗 */}
    <PosterSearchModal
      visible={posterSearchVisible}
      onClose={() => setPosterSearchVisible(false)}
      onSelect={handlePosterSelect}
      defaultKeyword={form.getFieldValue('title') || item?.title || ''}
      tmdbId={form.getFieldValue('tmdbId') || item?.tmdbId}
      tvdbId={form.getFieldValue('tvdbId') || item?.tvdbId}
      mediaType={form.getFieldValue('mediaType') || item?.mediaType}
    />

    {/* 本地海报预览 */}
    {previewVisible && localImagePath && (
      <Image
        style={{ display: 'none' }}
        preview={{
          visible: previewVisible,
          src: localImagePath,
          onVisibleChange: (vis) => setPreviewVisible(vis),
        }}
      />
    )}
    </>
  );
};

export default MediaItemEditor;

