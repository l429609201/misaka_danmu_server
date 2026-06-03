import React, { useEffect, useState, useCallback } from 'react';
import { Modal, Form, Input, InputNumber, Select, Button, Space, Image, message, Tooltip } from 'antd';
import { SearchOutlined, LinkOutlined, EyeOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { updateMediaItem, updateLocalItem, getLocalImage, downloadPosterToLocal } from '../../../apis';
import PosterSearchModal from './PosterSearchModal';

const { Option } = Select;

const MediaItemEditor = ({ visible, item, onClose, onSaved, isLocal = false }) => {
  const { t } = useTranslation();
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
      message.success(t('mediaFetch.mediaItemEditor.updateSuccess'));
      onSaved();
    } catch (error) {
      if (error.errorFields) {
        message.warning(t('mediaFetch.mediaItemEditor.fillRequired'));
      } else {
        message.error(t('mediaFetch.mediaItemEditor.updateFailed') + (error.message || t('mediaFetch.mediaItemEditor.unknownError')));
      }
    } finally {
      setLoading(false);
    }
  };

  // 海报搜索选中回调
  const handlePosterSelect = (posterUrl) => {
    form.setFieldsValue({ posterUrl });
    message.success(t('mediaFetch.mediaItemEditor.posterFilled'));
  };

  // URL直搜：下载网络图片到本地
  const handleDownloadToLocal = async () => {
    const posterUrl = form.getFieldValue('posterUrl');
    if (!posterUrl) {
      message.warning(t('mediaFetch.mediaItemEditor.fillPosterUrlFirst'));
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
        message.success(t('mediaFetch.mediaItemEditor.posterDownloaded'));
      } else {
        message.error(t('mediaFetch.mediaItemEditor.downloadFailed'));
      }
    } catch (error) {
      message.error(t('mediaFetch.mediaItemEditor.downloadFailedWith') + (error?.response?.data?.detail || error.message || t('mediaFetch.mediaItemEditor.unknownError')));
    } finally {
      setDownloadingLocal(false);
    }
  };

  return (
    <>
    <Modal
      title={t('mediaFetch.mediaItemEditor.title')}
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
          label={t('mediaFetch.mediaItemEditor.labelTitle')}
          name="title"
          rules={[{ required: true, message: t('mediaFetch.mediaItemEditor.titleRequired') }]}
        >
          <Input />
        </Form.Item>

        <Form.Item
          label={t('mediaFetch.mediaItemEditor.labelType')}
          name="mediaType"
          rules={[{ required: true, message: t('mediaFetch.mediaItemEditor.typeRequired') }]}
        >
          <Select onChange={(value) => {
            setMediaType(value);
            // 切换到电影类型时清空季度和集数
            if (value === 'movie') {
              form.setFieldsValue({ season: null, episode: null });
            }
          }}>
            <Option value="movie">{t('mediaFetch.mediaItemEditor.movie')}</Option>
            <Option value="tv_series">{t('mediaFetch.mediaItemEditor.tvSeries')}</Option>
          </Select>
        </Form.Item>

        <Form.Item
          label={t('mediaFetch.mediaItemEditor.labelSeason')}
          name="season"
        >
          <InputNumber
            min={0}
            style={{ width: '100%' }}
            disabled={mediaType === 'movie'}
            placeholder={mediaType === 'movie' ? t('mediaFetch.mediaItemEditor.movieNoSeason') : ''}
          />
        </Form.Item>

        <Form.Item
          label={t('mediaFetch.mediaItemEditor.labelEpisode')}
          name="episode"
        >
          <InputNumber
            min={1}
            style={{ width: '100%' }}
            disabled={mediaType === 'movie'}
            placeholder={mediaType === 'movie' ? t('mediaFetch.mediaItemEditor.movieNoEpisode') : ''}
          />
        </Form.Item>

        <Form.Item
          label={t('mediaFetch.mediaItemEditor.labelYear')}
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

        <Form.Item label={t('mediaFetch.mediaItemEditor.posterUrl')}>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="posterUrl" noStyle>
              <Input placeholder="https://..." style={{ flex: 1 }} />
            </Form.Item>
            <Tooltip title={t('mediaFetch.mediaItemEditor.searchPoster')}>
              <Button
                icon={<SearchOutlined />}
                onClick={() => setPosterSearchVisible(true)}
              />
            </Tooltip>
            <Tooltip title={t('mediaFetch.mediaItemEditor.urlDirectSearch')}>
              <Button
                icon={<LinkOutlined />}
                loading={downloadingLocal}
                onClick={handleDownloadToLocal}
              />
            </Tooltip>
          </Space.Compact>
        </Form.Item>

        {/* 本地海报行 */}
        <Form.Item label={t('mediaFetch.mediaItemEditor.localPoster')}>
          <Space style={{ width: '100%' }}>
            <Input
              value={localImagePath || t('mediaFetch.mediaItemEditor.none')}
              readOnly
              style={{ flex: 1, minWidth: 300, color: localImagePath ? undefined : 'var(--text-tertiary, #999)' }}
            />
            <Tooltip title={t('mediaFetch.mediaItemEditor.previewPoster')}>
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
            label={t('mediaFetch.mediaItemEditor.danmakuFilePath')}
            name="filePath"
            tooltip={t('mediaFetch.mediaItemEditor.danmakuFilePathTip')}
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

