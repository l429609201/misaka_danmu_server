import React, { useState, useEffect, useCallback } from 'react';
import { Modal, Input, Button, List, Tag, Image, Space, Spin, message, Empty, Tooltip } from 'antd';
import { SearchOutlined, CheckOutlined } from '@ant-design/icons';
import {
  getTmdbSearch, getDoubanSearch, getBgmSearch,
  getTvdbSearch, getImdbSearch, searchFanartPosters
} from '../../../apis';

/**
 * 海报搜索弹窗 - 多源并行搜索
 * 支持: TMDB, 豆瓣, Bangumi, TVDB, IMDB, Fanart.tv
 */
const PosterSearchModal = ({ visible, onClose, onSelect, defaultKeyword, tmdbId, tvdbId, mediaType }) => {
  const [keyword, setKeyword] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [sourceStatus, setSourceStatus] = useState({});

  useEffect(() => {
    if (visible && defaultKeyword) {
      setKeyword(defaultKeyword);
    }
    if (!visible) {
      setResults([]);
      setSourceStatus({});
    }
  }, [visible, defaultKeyword]);

  const handleSearch = useCallback(async () => {
    if (!keyword.trim()) {
      message.warning('请输入搜索关键词');
      return;
    }

    setLoading(true);
    setResults([]);
    const allResults = [];
    const status = {};

    // 定义搜索源
    const searchTasks = [
      {
        name: 'TMDB',
        fn: () => getTmdbSearch({ keyword: keyword.trim(), mediaType: 'multi' }),
        parse: (res) => (res?.data || []).map(item => ({
          url: item.imageUrl, title: item.title, year: item.year,
          type: item.type, source: 'TMDB', id: item.id
        })).filter(i => i.url)
      },
      {
        name: '豆瓣',
        fn: () => getDoubanSearch({ keyword: keyword.trim() }),
        parse: (res) => (res?.data || []).map(item => ({
          url: item.imageUrl, title: item.title, year: item.year,
          type: item.type, source: '豆瓣', id: item.id
        })).filter(i => i.url)
      },
      {
        name: 'Bangumi',
        fn: () => getBgmSearch({ keyword: keyword.trim() }),
        parse: (res) => (res?.data || []).map(item => ({
          url: item.imageUrl, title: item.title, year: item.year,
          type: item.type, source: 'Bangumi', id: item.id
        })).filter(i => i.url)
      },
      {
        name: 'TVDB',
        fn: () => getTvdbSearch({ keyword: keyword.trim(), mediaType: '' }),
        parse: (res) => (res?.data || []).map(item => ({
          url: item.imageUrl, title: item.title, year: item.year,
          type: item.type, source: 'TVDB', id: item.id
        })).filter(i => i.url)
      },
      {
        name: 'IMDB',
        fn: () => getImdbSearch({ keyword: keyword.trim(), mediaType: '' }),
        parse: (res) => (res?.data || []).map(item => ({
          url: item.imageUrl, title: item.title, year: item.year,
          type: item.type, source: 'IMDB', id: item.id
        })).filter(i => i.url)
      },
    ];

    // Fanart.tv 只在有 tmdbId 或 tvdbId 时参与
    if (tmdbId || tvdbId) {
      searchTasks.push({
        name: 'Fanart.tv',
        fn: () => searchFanartPosters({
          tmdbId: tmdbId || undefined,
          tvdbId: tvdbId || undefined,
          mediaType: mediaType === 'movie' ? 'movie' : 'tv'
        }),
        parse: (res) => (res?.data?.posters || []).map(item => ({
          url: item.url, title: `Fanart.tv (${item.lang || '?'})`,
          year: null, type: null, source: 'Fanart.tv',
          likes: item.likes
        }))
      });
    }

    // 并行搜索，逐个完成时更新状态
    const promises = searchTasks.map(async (task) => {
      status[task.name] = 'loading';
      setSourceStatus(prev => ({ ...prev, [task.name]: 'loading' }));
      try {
        const res = await task.fn();
        const parsed = task.parse(res);
        status[task.name] = 'done';
        setSourceStatus(prev => ({ ...prev, [task.name]: 'done' }));
        return parsed;
      } catch (e) {
        console.warn(`${task.name} 搜索失败:`, e);
        status[task.name] = 'error';
        setSourceStatus(prev => ({ ...prev, [task.name]: 'error' }));
        return [];
      }
    });

    try {
      const resultsArr = await Promise.all(promises);
      const merged = resultsArr.flat();
      setResults(merged);
    } finally {
      setLoading(false);
    }
  }, [keyword, tmdbId, tvdbId, mediaType]);

  const handleSelect = (item) => {
    onSelect(item.url);
    onClose();
  };

  const sourceColors = {
    'TMDB': 'blue', '豆瓣': 'green', 'Bangumi': 'magenta',
    'TVDB': 'orange', 'IMDB': 'gold', 'Fanart.tv': 'purple'
  };

  const statusIcons = {
    loading: '⏳', done: '✅', error: '❌'
  };

  return (
    <Modal
      title="搜索海报"
      open={visible}
      onCancel={onClose}
      footer={null}
      width={700}
      destroyOnClose
    >
      <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
        <Input
          placeholder="输入关键词搜索海报"
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          onPressEnter={handleSearch}
          allowClear
        />
        <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch} loading={loading}>
          搜索
        </Button>
      </Space.Compact>

      {/* 搜索源状态指示 */}
      {Object.keys(sourceStatus).length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <Space wrap size={[8, 4]}>
            {Object.entries(sourceStatus).map(([name, st]) => (
              <Tag key={name} color={st === 'done' ? 'success' : st === 'error' ? 'error' : 'processing'}>
                {statusIcons[st]} {name}
              </Tag>
            ))}
          </Space>
        </div>
      )}

      {/* 搜索结果列表 */}
      <div style={{ maxHeight: 500, overflowY: 'auto' }}>
        {loading && results.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin tip="搜索中..." /></div>
        ) : results.length === 0 && Object.keys(sourceStatus).length > 0 ? (
          <Empty description="未找到海报" />
        ) : (
          <List
            dataSource={results}
            renderItem={(item, index) => (
              <List.Item
                key={`${item.source}-${index}`}
                actions={[
                  <Button
                    type="primary"
                    size="small"
                    icon={<CheckOutlined />}
                    onClick={() => handleSelect(item)}
                  >
                    使用此海报
                  </Button>
                ]}
              >
                <List.Item.Meta
                  avatar={
                    <Image
                      src={item.url}
                      width={60}
                      height={85}
                      style={{ objectFit: 'cover', borderRadius: 4 }}
                      preview={true}
                      fallback="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjAiIGhlaWdodD0iODUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PHJlY3Qgd2lkdGg9IjYwIiBoZWlnaHQ9Ijg1IiBmaWxsPSIjZjBmMGYwIi8+PHRleHQgeD0iMzAiIHk9IjQ1IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmaWxsPSIjYmZiZmJmIiBmb250LXNpemU9IjEyIj7ml6Dlm748L3RleHQ+PC9zdmc+"
                    />
                  }
                  title={
                    <Space>
                      <span>{item.title}</span>
                      {item.year && <span style={{ color: 'var(--text-secondary, #999)' }}>({item.year})</span>}
                      <Tag color={sourceColors[item.source] || 'default'}>{item.source}</Tag>
                    </Space>
                  }
                  description={
                    <Tooltip title={item.url}>
                      <span style={{ fontSize: 12, color: 'var(--text-tertiary, #bbb)' }}>
                        {item.url?.length > 60 ? item.url.substring(0, 60) + '...' : item.url}
                      </span>
                    </Tooltip>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </div>
    </Modal>
  );
};

export default PosterSearchModal;

