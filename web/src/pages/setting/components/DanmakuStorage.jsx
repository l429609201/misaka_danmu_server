import { useState, useEffect, useMemo, useRef } from 'react';
import { Form, Input, Switch, Button, Space, message, Card, Divider, Typography, Select, Row, Col, Tabs, Table, Modal, Tag, Checkbox, Tooltip, Collapse, Popover } from 'antd';
import { FolderOpenOutlined, CheckCircleOutlined, FileOutlined, SwapOutlined, EditOutlined, SyncOutlined, DeleteOutlined, SearchOutlined, ReloadOutlined, RocketOutlined } from '@ant-design/icons';
import { getConfig, setConfig, getAnimeLibrary, previewMigrateDanmaku, batchMigrateDanmaku, previewRenameDanmaku, batchRenameDanmaku, previewDanmakuTemplate, applyDanmakuTemplate, getTemplateVariables, getDanmakuLikesFetchEnabled, setDanmakuLikesFetchEnabled } from '@/apis';
import DirectoryBrowser from '../../media-fetch/components/DirectoryBrowser';
import { useTranslation } from 'react-i18next';

const { Text } = Typography;
const { Option } = Select;
const { TabPane } = Tabs;

// 模板定义（国际化版本）
const getTemplates = (t) => ({
  movie: [
    { label: t('danmakuStorage.tmplMovieByTitle'), value: '${title}/${episodeId}', desc: '${title}/${episodeId}' },
    { label: t('danmakuStorage.tmplMovieTitleYear'), value: '${title} (${year})/${episodeId}', desc: '${title} (${year})/${episodeId}' },
    { label: t('danmakuStorage.tmplFlat'), value: '${episodeId}', desc: '${episodeId}' },
  ],
  tv: [
    { label: t('danmakuStorage.tmplTvByAnimeId'), value: '${animeId}/${episodeId}', desc: '${animeId}/${episodeId}' },
    { label: t('danmakuStorage.tmplTvByTitleSeason'), value: '${title}/Season ${season}/${episodeId}', desc: '${title}/Season ${season}/${episodeId}' },
    { label: t('danmakuStorage.tmplPlexStyle'), value: '${title}/${title} - S${season:02d}E${episode:02d}', desc: '${title}/${title} - S${season:02d}E${episode:02d}' },
    { label: t('danmakuStorage.tmplFlat'), value: '${episodeId}', desc: '${episodeId}' },
  ]
});

const DanmakuStorage = () => {
  const { t } = useTranslation();
  const TEMPLATES = useMemo(() => getTemplates(t), [t]);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [customDanmakuPathEnabled, setCustomDanmakuPathEnabled] = useState(false);

  // 电影配置
  const [movieDanmakuDirectoryPath, setMovieDanmakuDirectoryPath] = useState('/app/config/danmaku/movies');
  const [movieDanmakuFilenameTemplate, setMovieDanmakuFilenameTemplate] = useState('${title}/${episodeId}');
  const [moviePreviewPath, setMoviePreviewPath] = useState('');

  // 电视配置
  const [tvDanmakuDirectoryPath, setTvDanmakuDirectoryPath] = useState('/app/config/danmaku/tv');
  const [tvDanmakuFilenameTemplate, setTvDanmakuFilenameTemplate] = useState('${animeId}/${episodeId}');
  const [tvPreviewPath, setTvPreviewPath] = useState('');

  // 模板选择器状态
  const [selectedType, setSelectedType] = useState('movie');
  const [selectedTemplate, setSelectedTemplate] = useState('${title}/${episodeId}');

  // 目录浏览器状态
  const [browserVisible, setBrowserVisible] = useState(false);
  const [browserTarget, setBrowserTarget] = useState(''); // 'movie' or 'tv'

  // Tab状态
  const [activeTab, setActiveTab] = useState('config');
  const [isMobile, setIsMobile] = useState(false);

  // 设置分页状态
  const [likesFetchEnabled, setLikesFetchEnabled] = useState(true);

  // 迁移与重命名状态
  const [libraryItems, setLibraryItems] = useState([]);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryTotal, setLibraryTotal] = useState(0);
  const [libraryPage, setLibraryPage] = useState(1);
  const [libraryPageSize, setLibraryPageSize] = useState(10);
  const [libraryKeyword, setLibraryKeyword] = useState('');
  const [libraryTypeFilter, setLibraryTypeFilter] = useState('all');
  const [selectedRowKeys, setSelectedRowKeys] = useState([]);
  const [selectedRows, setSelectedRows] = useState([]);
  // Modal状态
  const [migrateModalVisible, setMigrateModalVisible] = useState(false);
  const [renameModalVisible, setRenameModalVisible] = useState(false);
  const [templateModalVisible, setTemplateModalVisible] = useState(false);
  const [operationLoading, setOperationLoading] = useState(false);
  // 迁移配置
  const [migrateTargetPath, setMigrateTargetPath] = useState('/app/config/danmaku');
  const [migrateKeepStructure, setMigrateKeepStructure] = useState(true);
  const [migrateConflictAction, setMigrateConflictAction] = useState('skip');
  const [migratePreviewData, setMigratePreviewData] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  // 重命名配置 - 多规则系统
  const [renameRules, setRenameRules] = useState([]);
  const [selectedRuleType, setSelectedRuleType] = useState('replace');
  const [ruleParams, setRuleParams] = useState({});
  const [renamePreviewData, setRenamePreviewData] = useState(null);
  const [renamePreviewLoading, setRenamePreviewLoading] = useState(false);
  const [isRenamePreviewMode, setIsRenamePreviewMode] = useState(false);
  const [renameOriginalItems, setRenameOriginalItems] = useState([]); // 保存原始文件名列表
  // 模板转换配置
  const [templateTarget, setTemplateTarget] = useState('tv');
  const [customTemplate, setCustomTemplate] = useState('');  // 自定义模板
  const [templatePreviewData, setTemplatePreviewData] = useState(null);
  const [templatePreviewLoading, setTemplatePreviewLoading] = useState(false);

  // 从后端获取的模板变量（统一列表）
  const [templateVariables, setTemplateVariables] = useState([]);

  // 电影/电视配置Tab切换
  const [activeConfigTab, setActiveConfigTab] = useState('movie');
  // 快速模板弹窗
  const [quickTemplateModalVisible, setQuickTemplateModalVisible] = useState(false);
  const [quickTemplateType, setQuickTemplateType] = useState('movie'); // 'movie' or 'tv'

  // 输入框引用，用于插入变量到光标位置
  const movieTemplateInputRef = useRef(null);
  const tvTemplateInputRef = useRef(null);

  // 预设模板选项
  const presetTemplates = [
    { value: 'tv', label: t('danmakuStorage.tmplTvTemplate'), template: '${title}/Season ${season}/${title} - S${season}E${episode}' },
    { value: 'movie', label: t('danmakuStorage.tmplMovieTemplate'), template: '${title}/${title}' },
    { value: 'id', label: t('danmakuStorage.tmplIdTemplate'), template: '${animeId}/${episodeId}' },
    { value: 'plex', label: t('danmakuStorage.tmplPlexStyle'), template: '${title}/${title} - S${season:02d}E${episode:02d}' },
    { value: 'emby', label: t('danmakuStorage.tmplEmbyStyle'), template: '${title}/${title} S${season:02d}/${title} S${season:02d}E${episode:02d}' },
    { value: 'titleBase', label: t('danmakuStorage.tmplTitleBase'), template: '${titleBase}/Season ${season}/${titleBase} - S${season}E${episode}' },
    { value: 'custom_movie', label: t('danmakuStorage.tmplCustomMovie'), template: movieDanmakuFilenameTemplate || '${title}/${episodeId}' },
    { value: 'custom_tv', label: t('danmakuStorage.tmplCustomTv'), template: tvDanmakuFilenameTemplate || '${animeId}/${episodeId}' },
  ];

  // 多规则重命名 - 规则类型配置
  const ruleTypeOptions = [
    { value: 'replace', label: t('danmakuStorage.ruleReplace') },
    { value: 'regex', label: t('danmakuStorage.ruleRegex') },
    { value: 'insert', label: t('danmakuStorage.ruleInsert') },
    { value: 'delete', label: t('danmakuStorage.ruleDelete') },
    { value: 'serialize', label: t('danmakuStorage.ruleSerialize') },
    { value: 'case', label: t('danmakuStorage.ruleCase') },
    { value: 'strip', label: t('danmakuStorage.ruleStrip') },
  ];

  // 应用单条规则到文件名
  const applyRenameRule = (filename, rule, index) => {
    if (!rule.enabled) return filename;
    try {
      switch (rule.type) {
        case 'replace':
          return rule.params.caseSensitive
            ? filename.split(rule.params.search || '').join(rule.params.replace || '')
            : filename.replace(new RegExp((rule.params.search || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), rule.params.replace || '');
        case 'regex':
          return filename.replace(new RegExp(rule.params.pattern || '', 'g'), rule.params.replace || '');
        case 'insert':
          if (rule.params.position === 'start') return (rule.params.text || '') + filename;
          if (rule.params.position === 'end') return filename + (rule.params.text || '');
          const pos = parseInt(rule.params.index) || 0;
          return filename.slice(0, pos) + (rule.params.text || '') + filename.slice(pos);
        case 'delete':
          const deleteMode = rule.params.mode || 'text';

          switch (deleteMode) {
            case 'text':
              // 删除指定文本
              return rule.params.caseSensitive
                ? filename.split(rule.params.text || '').join('')
                : filename.replace(new RegExp((rule.params.text || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), '');

            case 'first':
              // 删除前N个字符
              const firstCount = parseInt(rule.params.count) || 0;
              return filename.slice(firstCount);

            case 'last':
              // 删除后N个字符
              const lastCount = parseInt(rule.params.count) || 0;
              return filename.slice(0, -lastCount || undefined);

            case 'toText':
              // 从开头删除到指定文本（包含该文本）
              const toText = rule.params.text || '';
              if (!toText) return filename;
              const toIndex = rule.params.caseSensitive
                ? filename.indexOf(toText)
                : filename.toLowerCase().indexOf(toText.toLowerCase());
              return toIndex >= 0 ? filename.slice(toIndex + toText.length) : filename;

            case 'fromText':
              // 从指定文本删除到结尾（包含该文本）
              const fromText = rule.params.text || '';
              if (!fromText) return filename;
              const fromIndex = rule.params.caseSensitive
                ? filename.indexOf(fromText)
                : filename.toLowerCase().indexOf(fromText.toLowerCase());
              return fromIndex >= 0 ? filename.slice(0, fromIndex) : filename;

            case 'range':
              // 删除指定范围（从位置X删除Y个字符）
              const from = parseInt(rule.params.from) || 0;
              const count = parseInt(rule.params.count) || 0;
              return filename.slice(0, from) + filename.slice(from + count);

            default:
              return filename;
          }
        case 'serialize':
          const start = parseInt(rule.params.start) || 1;
          const step = parseInt(rule.params.step) || 1;
          const digits = parseInt(rule.params.digits) || 2;
          const num = String(start + index * step).padStart(digits, '0');
          const serialized = (rule.params.prefix || '') + num + (rule.params.suffix || '');
          if (rule.params.position === 'start') return serialized + filename;
          if (rule.params.position === 'end') return filename + serialized;
          return serialized;
        case 'case':
          if (rule.params.mode === 'upper') return filename.toUpperCase();
          if (rule.params.mode === 'lower') return filename.toLowerCase();
          if (rule.params.mode === 'title') return filename.charAt(0).toUpperCase() + filename.slice(1).toLowerCase();
          return filename;
        case 'strip':
          let result = filename;
          if (rule.params.trimSpaces) result = result.trim();
          if (rule.params.trimDuplicateSpaces) result = result.replace(/\s+/g, ' ');
          if (rule.params.chars) result = result.split(rule.params.chars).join('');
          return result;
        default:
          return filename;
      }
    } catch (e) {
      message.error(t('danmakuStorage.ruleExecError', { label: ruleTypeOptions.find(r => r.value === rule.type)?.label, error: e.message }));
      return filename;
    }
  };

  // 应用所有规则到文件名
  const applyAllRenameRules = (filename, index) => {
    return renameRules.reduce((name, rule) => applyRenameRule(name, rule, index), filename);
  };

  // 添加规则
  const handleAddRenameRule = () => {
    // 验证必填参数
    if (selectedRuleType === 'replace' && !ruleParams.search) {
      message.warning(t('danmakuStorage.ruleSearchRequired'));
      return;
    }
    if (selectedRuleType === 'regex' && !ruleParams.pattern) {
      message.warning(t('danmakuStorage.ruleRegexRequired'));
      return;
    }
    if (selectedRuleType === 'insert') {
      if (!ruleParams.text) {
        message.warning(t('danmakuStorage.ruleInsertTextRequired'));
        return;
      }
      if (ruleParams.position === 'index' && ruleParams.index === undefined) {
        message.warning(t('danmakuStorage.ruleInsertPosRequired'));
        return;
      }
    }
    if (selectedRuleType === 'delete') {
      const mode = ruleParams.mode || 'text';
      if ((mode === 'text' || mode === 'toText' || mode === 'fromText') && !ruleParams.text) {
        message.warning(t('danmakuStorage.ruleDeleteTextRequired'));
        return;
      }
      if ((mode === 'first' || mode === 'last' || mode === 'range') && !ruleParams.count) {
        message.warning(t('danmakuStorage.ruleDeleteCountRequired'));
        return;
      }
      if (mode === 'range' && ruleParams.from === undefined) {
        message.warning(t('danmakuStorage.ruleDeleteStartRequired'));
        return;
      }
    }

    const newRule = {
      id: Date.now().toString(),
      type: selectedRuleType,
      enabled: true,
      params: { ...ruleParams }
    };
    setRenameRules(prev => [...prev, newRule]);
    setRuleParams({});
    message.success(t('danmakuStorage.ruleAdded'));
  };

  // 删除规则
  const handleDeleteRenameRule = (ruleId) => {
    setRenameRules(prev => prev.filter(r => r.id !== ruleId));
  };

  // 切换规则启用状态
  const handleToggleRenameRule = (ruleId) => {
    setRenameRules(prev => prev.map(r => r.id === ruleId ? { ...r, enabled: !r.enabled } : r));
  };

  // 监听规则变化，自动更新预览
  useEffect(() => {
    if (!isRenamePreviewMode || !renameModalVisible || renameOriginalItems.length === 0) return;

    // 使用从后端获取的原始文件名列表计算新名称
    const previewItems = renameOriginalItems.map((item, index) => {
      const oldName = item.oldName;
      const baseName = oldName.replace(/\.[^/.]+$/, '');
      const ext = oldName.includes('.') ? '.' + oldName.split('.').pop() : '';
      const newBaseName = applyAllRenameRules(baseName, index);
      return {
        oldName: oldName,
        newName: newBaseName + ext,
        episodeId: item.episodeId,
        oldPath: item.oldPath
      };
    });
    setRenamePreviewData({ totalCount: previewItems.length, previewItems: previewItems.slice(0, 20) });
  }, [renameRules, isRenamePreviewMode, renameModalVisible, renameOriginalItems]);

  // 检测是否为移动端
  useEffect(() => {
    const checkIsMobile = () => {
      setIsMobile(window.innerWidth <= 768);
    };
    checkIsMobile();
    window.addEventListener('resize', checkIsMobile);
    return () => window.removeEventListener('resize', checkIsMobile);
  }, []);

  // 加载配置
  useEffect(() => {
    loadConfig();
  }, []);

  // 更新路径预览
  useEffect(() => {
    updatePreview();
  }, [customDanmakuPathEnabled, movieDanmakuDirectoryPath, movieDanmakuFilenameTemplate, tvDanmakuDirectoryPath, tvDanmakuFilenameTemplate]);

  // 当选择类型改变时，更新默认模板
  useEffect(() => {
    const defaultTemplate = selectedType === 'movie' ? '${title}/${episodeId}' : '${animeId}/${episodeId}';
    setSelectedTemplate(defaultTemplate);
  }, [selectedType]);

  // 监听自定义模板变化，自动预览（防抖）
  const templatePreviewTimerRef = useRef(null);
  useEffect(() => {
    // 只在模板 Modal 打开且是自定义模式时才触发预览
    if (!templateModalVisible || templateTarget !== 'custom' || !customTemplate) {
      return;
    }

    // 清除之前的定时器
    if (templatePreviewTimerRef.current) {
      clearTimeout(templatePreviewTimerRef.current);
    }

    // 防抖：300ms 后调用预览 API
    templatePreviewTimerRef.current = setTimeout(async () => {
      setTemplatePreviewLoading(true);
      try {
        const response = await previewDanmakuTemplate({
          animeIds: selectedRowKeys,
          templateType: 'custom',
          customTemplate: customTemplate,
        });
        setTemplatePreviewData(response.data);
      } catch (error) {
        message.error(t('danmakuStorage.previewFailed', { error: error.message || t('common.unknown') }));
      } finally {
        setTemplatePreviewLoading(false);
      }
    }, 300);

    return () => {
      if (templatePreviewTimerRef.current) {
        clearTimeout(templatePreviewTimerRef.current);
      }
    };
  }, [customTemplate, templateTarget, templateModalVisible, selectedRowKeys]);

  // 监听迁移配置变化，自动预览（防抖）
  const migratePreviewTimerRef = useRef(null);
  useEffect(() => {
    // 只在迁移 Modal 打开且有目标路径时才触发预览
    if (!migrateModalVisible || !migrateTargetPath || selectedRowKeys.length === 0) {
      return;
    }

    // 清除之前的定时器
    if (migratePreviewTimerRef.current) {
      clearTimeout(migratePreviewTimerRef.current);
    }

    // 防抖：300ms 后调用预览 API
    migratePreviewTimerRef.current = setTimeout(async () => {
      setPreviewLoading(true);
      try {
        const response = await previewMigrateDanmaku({
          animeIds: selectedRowKeys,
          targetPath: migrateTargetPath,
          keepStructure: migrateKeepStructure,
        });
        setMigratePreviewData(response.data);
      } catch (error) {
        message.error(t('danmakuStorage.previewFailed', { error: error.message || t('common.unknown') }));
      } finally {
        setPreviewLoading(false);
      }
    }, 300);

    return () => {
      if (migratePreviewTimerRef.current) {
        clearTimeout(migratePreviewTimerRef.current);
      }
    };
  }, [migrateTargetPath, migrateKeepStructure, migrateModalVisible, selectedRowKeys]);

  const loadConfig = async () => {
    try {
      setLoading(true);

      // 加载配置
      const enabledRes = await getConfig('customDanmakuPathEnabled');
      const movieDirRes = await getConfig('movieDanmakuDirectoryPath');
      const movieTemplateRes = await getConfig('movieDanmakuFilenameTemplate');
      const tvDirRes = await getConfig('tvDanmakuDirectoryPath');
      const tvTemplateRes = await getConfig('tvDanmakuFilenameTemplate');

      const enabled = enabledRes?.data?.value === 'true';
      const movieDir = movieDirRes?.data?.value || '/app/config/danmaku/movies';
      const movieTemplate = movieTemplateRes?.data?.value || '${title}/${episodeId}';
      const tvDir = tvDirRes?.data?.value || '/app/config/danmaku/tv';
      const tvTemplate = tvTemplateRes?.data?.value || '${animeId}/${episodeId}';

      setCustomDanmakuPathEnabled(enabled);
      setMovieDanmakuDirectoryPath(movieDir);
      setMovieDanmakuFilenameTemplate(movieTemplate);
      setTvDanmakuDirectoryPath(tvDir);
      setTvDanmakuFilenameTemplate(tvTemplate);

      form.setFieldsValue({
        customDanmakuPathEnabled: enabled,
        movieDanmakuDirectoryPath: movieDir,
        movieDanmakuFilenameTemplate: movieTemplate,
        tvDanmakuDirectoryPath: tvDir,
        tvDanmakuFilenameTemplate: tvTemplate,
      });

      // 获取模板变量列表
      try {
        const varsRes = await getTemplateVariables();
        if (varsRes?.data) {
          setTemplateVariables(varsRes.data);
        }
      } catch (e) {
        console.warn('获取模板变量失败，使用默认值', e);
      }

      // 获取点赞开关
      try {
        const likesFetchRes = await getDanmakuLikesFetchEnabled();
        setLikesFetchEnabled(likesFetchRes?.data?.value !== 'false');
      } catch (e) {
        console.warn('获取点赞开关失败', e);
      }
    } catch (error) {
      message.error(t('danmakuStorage.loadConfigFailed'));
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  const updatePreview = () => {
    if (!customDanmakuPathEnabled) {
      setMoviePreviewPath('/app/config/danmaku/160/25000160010001.xml (默认路径)');
      setTvPreviewPath('/app/config/danmaku/160/25000160010001.xml (默认路径)');
      return;
    }

    // 电影示例数据
    const movieExampleContext = {
      animeId: '160',
      episodeId: '25000160010001',
      title: '铃芽之旅 第二季',
      titleBase: '铃芽之旅',  // 电影标题通常不含季度信息
      season: '1',
      episode: '1',
      year: '2022',
      provider: 'bilibili',
      sourceId: '192',
      tmdbId: '1022789',
    };

    // 电视示例数据
    const tvExampleContext = {
      animeId: '160',
      episodeId: '25000160010001',
      title: '葬送的芙莉莲 第二季',
      titleBase: '葬送的芙莉莲',  // 标准化标题，去除季度信息
      season: '1',
      episode: '1',
      year: '2023',
      provider: 'bilibili',
      sourceId: '192',
      tmdbId: '209867',
    };

    // 生成电影预览
    let moviePreview = movieDanmakuFilenameTemplate;
    moviePreview = moviePreview.replace(/\$\{(\w+):(\w+)\}/g, (match, varName, format) => {
      const value = movieExampleContext[varName];
      if (value && format.endsWith('d')) {
        const num = parseInt(value);
        const width = parseInt(format.match(/\d+/)?.[0] || '0');
        return num.toString().padStart(width, '0');
      }
      return value || match;
    });
    moviePreview = moviePreview.replace(/\$\{(\w+)\}/g, (match, varName) => {
      return movieExampleContext[varName] || match;
    });
    const movieDir = movieDanmakuDirectoryPath.replace(/[\/\\]+$/, '');
    const movieFilename = moviePreview.replace(/^[\/\\]+/, '');
    // 检测目录路径使用的分隔符，保持一致
    const sep = movieDir.includes('\\') ? '\\' : '/';
    const movieFullPath = `${movieDir}${sep}${movieFilename.replace(/[\/\\]/g, sep)}${movieFilename.endsWith('.xml') ? '' : '.xml'}`;
    setMoviePreviewPath(movieFullPath);

    // 生成电视预览
    let tvPreview = tvDanmakuFilenameTemplate;
    tvPreview = tvPreview.replace(/\$\{(\w+):(\w+)\}/g, (match, varName, format) => {
      const value = tvExampleContext[varName];
      if (value && format.endsWith('d')) {
        const num = parseInt(value);
        const width = parseInt(format.match(/\d+/)?.[0] || '0');
        return num.toString().padStart(width, '0');
      }
      return value || match;
    });
    tvPreview = tvPreview.replace(/\$\{(\w+)\}/g, (match, varName) => {
      return tvExampleContext[varName] || match;
    });
    const tvDir = tvDanmakuDirectoryPath.replace(/[\/\\]+$/, '');
    const tvFilename = tvPreview.replace(/^[\/\\]+/, '');
    const tvSep = tvDir.includes('\\') ? '\\' : '/';
    const tvFullPath = `${tvDir}${tvSep}${tvFilename.replace(/[\/\\]/g, tvSep)}${tvFilename.endsWith('.xml') ? '' : '.xml'}`;
    setTvPreviewPath(tvFullPath);
  };

  const handleSave = async () => {
    try {
      setLoading(true);

      // 保存配置
      await setConfig('customDanmakuPathEnabled', customDanmakuPathEnabled ? 'true' : 'false');
      await setConfig('movieDanmakuDirectoryPath', movieDanmakuDirectoryPath);
      await setConfig('movieDanmakuFilenameTemplate', movieDanmakuFilenameTemplate);
      await setConfig('tvDanmakuDirectoryPath', tvDanmakuDirectoryPath);
      await setConfig('tvDanmakuFilenameTemplate', tvDanmakuFilenameTemplate);

      message.success(t('danmakuStorage.saveSuccess'));
    } catch (error) {
      message.error(t('danmakuStorage.saveFailed'));
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  // ==================== 迁移与重命名功能 ====================

  // 加载弹幕库条目
  const loadLibraryItems = async (page = 1, keyword = '', typeFilter = 'all') => {
    setLibraryLoading(true);
    try {
      const params = {
        page,
        pageSize: libraryPageSize,
      };
      if (keyword) params.keyword = keyword;
      // 类型过滤：传递给后端处理，而不是前端过滤
      if (typeFilter !== 'all') params.type = typeFilter;

      const response = await getAnimeLibrary(params);
      const items = response.data?.list || [];

      setLibraryItems(items);
      setLibraryTotal(response.data?.total || 0);
      setLibraryPage(page);
    } catch (error) {
      console.error('加载弹幕库失败:', error);
      message.error(t('danmakuStorage.loadLibraryFailed'));
    } finally {
      setLibraryLoading(false);
    }
  };

  // 当切换到迁移与重命名tab时加载数据
  useEffect(() => {
    if (activeTab === 'migrate') {
      loadLibraryItems(1, libraryKeyword, libraryTypeFilter);
    }
  }, [activeTab]);

  // 搜索处理
  const handleLibrarySearch = () => {
    setSelectedRowKeys([]);
    setSelectedRows([]);
    loadLibraryItems(1, libraryKeyword, libraryTypeFilter);
  };

  // 刷新列表
  const handleLibraryRefresh = () => {
    setSelectedRowKeys([]);
    setSelectedRows([]);
    loadLibraryItems(libraryPage, libraryKeyword, libraryTypeFilter);
  };

  // 表格选择配置
  const rowSelection = {
    selectedRowKeys,
    onChange: (keys, rows) => {
      setSelectedRowKeys(keys);
      setSelectedRows(rows);
    },
  };

  // 计算选中条目的总弹幕文件数
  const selectedEpisodeCount = useMemo(() => {
    return selectedRows.reduce((sum, item) => sum + (item.episodeCount || 0), 0);
  }, [selectedRows]);

  // 表格列定义
  const libraryColumns = [
    {
      title: t('danmakuStorage.colTitle'),
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (text, record) => (
        <Space>
          <span>{text}</span>
          {record.season > 1 && <Tag color="blue">S{record.season}</Tag>}
        </Space>
      ),
    },
    {
      title: t('danmakuStorage.colType'),
      dataIndex: 'type',
      key: 'type',
      width: 80,
      render: (type) => {
        const typeMap = {
          'movie': { text: t('danmakuStorage.colTypeMovie'), color: 'orange' },
          'tv_series': { text: 'TV', color: 'blue' },
          'ova': { text: 'OVA', color: 'purple' },
          'other': { text: t('danmakuStorage.colTypeOther'), color: 'default' },
        };
        const config = typeMap[type] || typeMap['other'];
        return <Tag color={config.color}>{config.text}</Tag>;
      },
    },
    {
      title: t('danmakuStorage.colEpisodeCount'),
      dataIndex: 'episodeCount',
      key: 'episodeCount',
      width: 70,
      render: (count) => count ? t('danmakuStorage.colEpisodeCountSuffix', { count }) : '-',
    },
    {
      title: t('danmakuStorage.colDanmakuCount'),
      dataIndex: 'sourceCount',
      key: 'sourceCount',
      width: 90,
      render: (count) => count ? count.toLocaleString() : '-',
    },
    {
      title: t('danmakuStorage.colCollectedAt'),
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 100,
      render: (date) => date ? new Date(date).toLocaleDateString('zh-CN') : '-',
    },
  ];

  // 打开迁移Modal
  const handleOpenMigrateModal = async () => {
    if (selectedRows.length === 0) {
      message.warning(t('danmakuStorage.selectItemsFirst'));
      return;
    }
    setMigratePreviewData(null); // 清空预览数据
    setMigrateModalVisible(true);
    // 打开时自动预览
    if (migrateTargetPath) {
      setPreviewLoading(true);
      try {
        const response = await previewMigrateDanmaku({
          animeIds: selectedRowKeys,
          targetPath: migrateTargetPath,
          keepStructure: migrateKeepStructure,
        });
        setMigratePreviewData(response.data);
      } catch (error) {
        message.error(t('danmakuStorage.previewFailed', { error: error.message || t('common.unknown') }));
      } finally {
        setPreviewLoading(false);
      }
    }
  };

  // 预览迁移
  const handlePreviewMigrate = async () => {
    if (!migrateTargetPath) {
      message.warning(t('danmakuStorage.targetDirRequired'));
      return;
    }
    setPreviewLoading(true);
    try {
      const response = await previewMigrateDanmaku({
        animeIds: selectedRowKeys,
        targetPath: migrateTargetPath,
        keepStructure: migrateKeepStructure,
      });
      setMigratePreviewData(response.data);
    } catch (error) {
      message.error(t('danmakuStorage.previewFailed', { error: error.message || t('common.unknown') }));
    } finally {
      setPreviewLoading(false);
    }
  };

  // 打开重命名Modal
  const handleOpenRenameModal = async () => {
    if (selectedRows.length === 0) {
      message.warning(t('danmakuStorage.selectItemsFirstRename'));
      return;
    }
    // 重置多规则状态
    setRenameRules([]);
    setSelectedRuleType('replace');
    setRuleParams({});
    setRenamePreviewLoading(true);
    setRenameModalVisible(true);
    setIsRenamePreviewMode(true);

    // 调用后端API获取原始文件名列表
    try {
      const response = await previewRenameDanmaku({
        animeIds: selectedRowKeys,
        mode: 'prefix',
        prefix: '',
        suffix: '',
        regexPattern: '',
        regexReplace: '',
      });
      const items = response.data?.previewItems || [];
      // 保存原始文件名列表，用于后续规则计算
      setRenameOriginalItems(items);
      // 初始预览显示原始文件名
      const previewItems = items.map(item => ({
        oldName: item.oldName,
        newName: item.oldName, // 初始时新名称等于旧名称
        episodeId: item.episodeId,
        oldPath: item.oldPath
      }));
      setRenamePreviewData({ totalCount: items.length, previewItems: previewItems.slice(0, 20) });
    } catch (error) {
      message.error(t('danmakuStorage.previewFailed', { error: error.message || t('common.unknown') }));
      setRenamePreviewData(null);
      setRenameOriginalItems([]);
    } finally {
      setRenamePreviewLoading(false);
    }
  };

  // 打开模板转换Modal
  const handleOpenTemplateModal = async () => {
    if (selectedRows.length === 0) {
      message.warning(t('danmakuStorage.selectItemsFirstConvert'));
      return;
    }
    setTemplatePreviewData(null);
    setTemplateModalVisible(true);
    // 打开时自动预览
    setTemplatePreviewLoading(true);
    try {
      const response = await previewDanmakuTemplate({
        animeIds: selectedRowKeys,
        templateType: templateTarget,
        customTemplate: templateTarget === 'custom' ? customTemplate : undefined,
      });
      setTemplatePreviewData(response.data);
    } catch (error) {
      message.error(t('danmakuStorage.previewFailed', { error: error.message || t('common.unknown') }));
    } finally {
      setTemplatePreviewLoading(false);
    }
  };

  // 预览应用模板
  const handlePreviewTemplate = async () => {
    setTemplatePreviewLoading(true);
    try {
      const response = await previewDanmakuTemplate({
        animeIds: selectedRowKeys,
        templateType: templateTarget,
        customTemplate: templateTarget === 'custom' ? customTemplate : undefined,
      });
      setTemplatePreviewData(response.data);
    } catch (error) {
      message.error(t('danmakuStorage.previewFailed', { error: error.message || t('common.unknown') }));
    } finally {
      setTemplatePreviewLoading(false);
    }
  };

  // 执行迁移操作
  const handleExecuteMigrate = async () => {
    if (!migrateTargetPath) {
      message.warning(t('danmakuStorage.targetDirRequired'));
      return;
    }
    setOperationLoading(true);
    try {
      const response = await batchMigrateDanmaku({
        animeIds: selectedRowKeys,
        targetPath: migrateTargetPath,
        keepStructure: migrateKeepStructure,
        conflictAction: migrateConflictAction,
      });
      const result = response.data;
      if (result.success) {
        message.success(t('danmakuStorage.migrateSuccess', { success: result.successCount, skipped: result.skippedCount }));
      } else {
        message.warning(t('danmakuStorage.migratePartial', { success: result.successCount, failed: result.failedCount, skipped: result.skippedCount }));
      }
      setMigrateModalVisible(false);
      setMigratePreviewData(null);
      setSelectedRowKeys([]);
      setSelectedRows([]);
      loadLibraryItems(libraryPage, libraryKeyword, libraryTypeFilter);
    } catch (error) {
      message.error(t('danmakuStorage.migrateFailed', { error: error.message || t('common.unknown') }));
    } finally {
      setOperationLoading(false);
    }
  };

  // 执行重命名操作 - 使用多规则系统
  const handleExecuteRename = async () => {
    if (renameRules.length === 0) {
      message.warning(t('danmakuStorage.rulesRequired'));
      return;
    }

    if (renameOriginalItems.length === 0) {
      message.warning(t('danmakuStorage.noFilesToRename'));
      return;
    }

    // 使用从后端获取的原始文件名列表计算新名称
    const directRenames = renameOriginalItems.map((item, index) => {
      const oldName = item.oldName;
      const baseName = oldName.replace(/\.[^/.]+$/, '');
      const ext = oldName.includes('.') ? '.' + oldName.split('.').pop() : '';
      const newBaseName = applyAllRenameRules(baseName, index);
      return {
        episodeId: item.episodeId,
        newName: newBaseName + ext
      };
    });

    setOperationLoading(true);
    try {
      const response = await batchRenameDanmaku({
        animeIds: selectedRowKeys,
        mode: 'direct',
        directRenames: directRenames,
      });
      const result = response.data;
      if (result.success) {
        message.success(t('danmakuStorage.renameSuccess', { success: result.successCount, skipped: result.skippedCount }));
      } else {
        message.warning(t('danmakuStorage.renamePartial', { success: result.successCount, failed: result.failedCount, skipped: result.skippedCount }));
      }
      setRenameModalVisible(false);
      setRenameRules([]);
      setSelectedRowKeys([]);
      setSelectedRows([]);
      loadLibraryItems(libraryPage, libraryKeyword, libraryTypeFilter);
    } catch (error) {
      message.error(t('danmakuStorage.renameFailed', { error: error.message || t('common.unknown') }));
    } finally {
      setOperationLoading(false);
    }
  };

  // 执行模板转换操作
  const handleExecuteTemplate = async () => {
    setOperationLoading(true);
    try {
      const response = await applyDanmakuTemplate({
        animeIds: selectedRowKeys,
        templateType: templateTarget,
        customTemplate: templateTarget === 'custom' ? customTemplate : undefined,
      });
      const result = response.data;
      if (result.success) {
        message.success(t('danmakuStorage.templateSuccess', { success: result.successCount, skipped: result.skippedCount }));
      } else {
        message.warning(t('danmakuStorage.templatePartial', { success: result.successCount, failed: result.failedCount, skipped: result.skippedCount }));
      }
      setTemplateModalVisible(false);
      setTemplatePreviewData(null);
      setSelectedRowKeys([]);
      setSelectedRows([]);
      loadLibraryItems(libraryPage, libraryKeyword, libraryTypeFilter);
    } catch (error) {
      message.error(t('danmakuStorage.templateFailed', { error: error.message || t('common.unknown') }));
    } finally {
      setOperationLoading(false);
    }
  };

  // 应用模板
  const applyTemplate = () => {
    if (!selectedTemplate) {
      message.warning(t('danmakuStorage.selectTemplateFirst'));
      return;
    }

    if (selectedType === 'movie') {
      setMovieDanmakuFilenameTemplate(selectedTemplate);
      form.setFieldValue('movieDanmakuFilenameTemplate', selectedTemplate);
      message.success(t('danmakuStorage.movieTemplateApplied'));
    } else {
      setTvDanmakuFilenameTemplate(selectedTemplate);
      form.setFieldValue('tvDanmakuFilenameTemplate', selectedTemplate);
      message.success(t('danmakuStorage.tvTemplateApplied'));
    }
  };

  // 打开目录浏览器
  const handleBrowseDirectory = (target) => {
    setBrowserTarget(target);
    setBrowserVisible(true);
  };

  // 选择目录
  const handleSelectDirectory = async (path) => {
    if (browserTarget === 'movie') {
      setMovieDanmakuDirectoryPath(path);
      form.setFieldValue('movieDanmakuDirectoryPath', path);
      message.success(t('danmakuStorage.movieDirSelected', { path }));
    } else if (browserTarget === 'tv') {
      setTvDanmakuDirectoryPath(path);
      form.setFieldValue('tvDanmakuDirectoryPath', path);
      message.success(t('danmakuStorage.tvDirSelected', { path }));
    } else if (browserTarget === 'migrate') {
      // 迁移目录选择后自动预览
      setMigrateTargetPath(path);
      setBrowserVisible(false);
      // 自动执行预览
      setPreviewLoading(true);
      try {
        const response = await previewMigrateDanmaku({
          animeIds: selectedRowKeys,
          targetPath: path,
          keepStructure: migrateKeepStructure,
        });
        setMigratePreviewData(response.data);
      } catch (error) {
        message.error(t('danmakuStorage.previewFailed', { error: error.message || t('common.unknown') }));
      } finally {
        setPreviewLoading(false);
      }
      return; // 提前返回，不再执行下面的 setBrowserVisible
    }
    setBrowserVisible(false);
  };

  return (
    <Card>
      <Tabs activeKey={activeTab} onChange={setActiveTab}>
        <TabPane tab={t('danmakuStorage.tabConfig')} key="config">
          <Form
            form={form}
            layout="vertical"
            style={{ maxWidth: 1000 }}
          >
            {/* 启用自定义弹幕路径 */}
        <Form.Item
          label={t('danmakuStorage.labelCustomPath')}
          name="customDanmakuPathEnabled"
        >
          <div>
            <Switch
              checked={customDanmakuPathEnabled}
              onChange={async (checked) => {
                setCustomDanmakuPathEnabled(checked);
                form.setFieldValue('customDanmakuPathEnabled', checked);
                // 自动保存开关状态
                try {
                  await setConfig('customDanmakuPathEnabled', checked ? 'true' : 'false');
                  message.success(checked ? t('danmakuStorage.pathEnabledSaved') : t('danmakuStorage.pathDisabledSaved'));
                } catch (error) {
                  message.error(t('danmakuStorage.pathSaveFailed'));
                  console.error(error);
                  // 恢复原状态
                  setCustomDanmakuPathEnabled(!checked);
                  form.setFieldValue('customDanmakuPathEnabled', !checked);
                }
              }}
            />
            <div style={{ color: '#999', fontSize: '12px', marginTop: '4px' }}>
              {t('danmakuStorage.descCustomPath')}
            </div>
          </div>
        </Form.Item>

        {/* 可折叠变量区域 */}
        <Collapse
          defaultActiveKey={['variables']}
          style={{ marginBottom: '24px' }}
          items={[
            {
              key: 'variables',
              label: (
                <Space>
                  <span>{t('danmakuStorage.labelAvailableVars')}</span>
                  <span style={{ fontSize: '12px', color: 'var(--color-text-secondary)' }}>
                    {t('danmakuStorage.hintClickInsert')}
                  </span>
                </Space>
              ),
              children: (
                <div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '12px' }}>
                    {(templateVariables || []).map((v) => (
                      <Tooltip
                        key={v.name}
                        title={<div><div>{v.desc}</div><div style={{ color: '#aaa', marginTop: 4 }}>示例: {v.example}</div></div>}
                        placement="top"
                        trigger={isMobile ? 'click' : 'hover'}
                      >
                        <Button
                          size="small"
                          type="dashed"
                          disabled={!customDanmakuPathEnabled}
                          onClick={() => {
                            // 根据当前激活的Tab插入到对应的输入框光标处
                            const inputRef = activeConfigTab === 'movie' ? movieTemplateInputRef : tvTemplateInputRef;
                            const currentValue = activeConfigTab === 'movie' ? movieDanmakuFilenameTemplate : tvDanmakuFilenameTemplate;
                            const setValue = activeConfigTab === 'movie' ? setMovieDanmakuFilenameTemplate : setTvDanmakuFilenameTemplate;
                            const fieldName = activeConfigTab === 'movie' ? 'movieDanmakuFilenameTemplate' : 'tvDanmakuFilenameTemplate';

                            if (inputRef.current && inputRef.current.input) {
                              const input = inputRef.current.input;
                              const start = input.selectionStart || 0;
                              const end = input.selectionEnd || 0;
                              const newValue = currentValue.slice(0, start) + v.name + currentValue.slice(end);
                              setValue(newValue);
                              form.setFieldValue(fieldName, newValue);
                              // 设置光标位置
                              setTimeout(() => {
                                input.focus();
                                input.setSelectionRange(start + v.name.length, start + v.name.length);
                              }, 0);
                            } else {
                              // 如果无法获取光标，则追加到末尾
                              const newValue = currentValue + v.name;
                              setValue(newValue);
                              form.setFieldValue(fieldName, newValue);
                            }
                          }}
                          style={{ fontFamily: 'monospace', fontSize: '12px' }}
                        >
                          {v.name}
                        </Button>
                      </Tooltip>
                    ))}
                  </div>
                  <div style={{ color: 'var(--color-text-secondary)', fontSize: '12px' }}>
                    {t('danmakuStorage.hintMovieVarNote')}
                  </div>
                </div>
              )
            }
          ]}
        />

        {/* 电影/电视配置Tabs */}
        <Tabs
          activeKey={activeConfigTab}
          onChange={setActiveConfigTab}
          items={[
            {
              key: 'movie',
              label: <span>{t('danmakuStorage.tabMovie')}</span>,
              children: (
                <div>

        {/* 电影存储目录 */}
        <Form.Item
          label={t('danmakuStorage.labelMovieDir')}
          name="movieDanmakuDirectoryPath"
        >
          <div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <Input
                value={movieDanmakuDirectoryPath}
                onChange={(e) => {
                  setMovieDanmakuDirectoryPath(e.target.value);
                  form.setFieldValue('movieDanmakuDirectoryPath', e.target.value);
                }}
                placeholder="/app/config/danmaku/movies"
                disabled={!customDanmakuPathEnabled}
                style={{ flex: 1 }}
              />
              <Button
                icon={<FolderOpenOutlined />}
                onClick={() => handleBrowseDirectory('movie')}
                disabled={!customDanmakuPathEnabled}
              >
                {t('danmakuStorage.btnBrowse')}
              </Button>
            </div>
            <div style={{ color: '#999', fontSize: '12px', marginTop: '4px' }}>
              {t('danmakuStorage.descMovieDir')}
            </div>
          </div>
        </Form.Item>

        {/* 电影命名模板 */}
        <Form.Item
          label={t('danmakuStorage.labelNamingTemplate')}
          name="movieDanmakuFilenameTemplate"
        >
          <div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <Input
                ref={movieTemplateInputRef}
                value={movieDanmakuFilenameTemplate}
                onChange={(e) => {
                  setMovieDanmakuFilenameTemplate(e.target.value);
                  form.setFieldValue('movieDanmakuFilenameTemplate', e.target.value);
                }}
                placeholder="${title}/${episodeId}"
                disabled={!customDanmakuPathEnabled}
                style={{ flex: 1 }}
              />
              <Button
                icon={<FileOutlined />}
                onClick={() => {
                  setQuickTemplateType('movie');
                  setQuickTemplateModalVisible(true);
                }}
                disabled={!customDanmakuPathEnabled}
              >
                {t('danmakuStorage.btnQuickTemplate')}
              </Button>
            </div>
            <div style={{ color: 'var(--color-text-secondary)', fontSize: '12px', marginTop: '8px' }}>
              {t('danmakuStorage.hintSubdirSupport', { example: '${title}/${episodeId}' })}
            </div>
          </div>
        </Form.Item>

        {/* 电影路径预览 */}
        <Form.Item label={
          <Space>
            {t('danmakuStorage.labelPathPreview')}
          </Space>
        }>
          <div style={{
            padding: '16px',
            background: 'var(--color-hover)',
            borderRadius: '8px',
            border: '1px solid var(--color-border)',
            fontFamily: 'JetBrains Mono, Consolas, monospace',
            fontSize: '13px',
            wordBreak: 'break-all',
            color: 'var(--color-text)'
          }}>
            {moviePreviewPath || t('danmakuStorage.pathPreviewPlaceholder')}
          </div>
          <div style={{ color: 'var(--color-text-secondary)', fontSize: '12px', marginTop: '8px' }}>
            {t('danmakuStorage.moviePreviewExample')}
          </div>
        </Form.Item>
                </div>
              )
            },
            {
              key: 'tv',
              label: <span>{t('danmakuStorage.tabTv')}</span>,
              children: (
                <div>
        {/* 电视存储目录 */}
        <Form.Item
          label={t('danmakuStorage.labelTvDir')}
          name="tvDanmakuDirectoryPath"
        >
          <div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <Input
                value={tvDanmakuDirectoryPath}
                onChange={(e) => {
                  setTvDanmakuDirectoryPath(e.target.value);
                  form.setFieldValue('tvDanmakuDirectoryPath', e.target.value);
                }}
                placeholder="/app/config/danmaku/tv"
                disabled={!customDanmakuPathEnabled}
                style={{ flex: 1 }}
              />
              <Button
                icon={<FolderOpenOutlined />}
                onClick={() => handleBrowseDirectory('tv')}
                disabled={!customDanmakuPathEnabled}
              >
                {t('danmakuStorage.btnBrowse')}
              </Button>
            </div>
            <div style={{ color: '#999', fontSize: '12px', marginTop: '4px' }}>
              {t('danmakuStorage.descTvDir')}
            </div>
          </div>
        </Form.Item>

        {/* 电视命名模板 */}
        <Form.Item
          label={t('danmakuStorage.labelNamingTemplate')}
          name="tvDanmakuFilenameTemplate"
        >
          <div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <Input
                ref={tvTemplateInputRef}
                value={tvDanmakuFilenameTemplate}
                onChange={(e) => {
                  setTvDanmakuFilenameTemplate(e.target.value);
                  form.setFieldValue('tvDanmakuFilenameTemplate', e.target.value);
                }}
                placeholder="${animeId}/${episodeId}"
                disabled={!customDanmakuPathEnabled}
                style={{ flex: 1 }}
              />
              <Button
                icon={<FileOutlined />}
                onClick={() => {
                  setQuickTemplateType('tv');
                  setQuickTemplateModalVisible(true);
                }}
                disabled={!customDanmakuPathEnabled}
              >
                {t('danmakuStorage.btnQuickTemplate')}
              </Button>
            </div>
            <div style={{ color: 'var(--color-text-secondary)', fontSize: '12px', marginTop: '8px' }}>
              {t('danmakuStorage.hintSubdirSupport', { example: '${animeId}/${episodeId}' })}
            </div>
          </div>
        </Form.Item>

        {/* 电视路径预览 */}
        <Form.Item label={
          <Space>
            {t('danmakuStorage.labelPathPreview')}
          </Space>
        }>
          <div style={{
            padding: '16px',
            background: 'var(--color-hover)',
            borderRadius: '8px',
            border: '1px solid var(--color-border)',
            fontFamily: 'JetBrains Mono, Consolas, monospace',
            fontSize: '13px',
            wordBreak: 'break-all',
            color: 'var(--color-text)'
          }}>
            {tvPreviewPath || t('danmakuStorage.pathPreviewPlaceholder')}
          </div>
          <div style={{ color: 'var(--color-text-secondary)', fontSize: '12px', marginTop: '8px' }}>
            {t('danmakuStorage.tvPreviewExample')}
          </div>
        </Form.Item>
                </div>
              )
            }
          ]}
        />

            <Button
              type="primary"
              icon={<CheckCircleOutlined />}
              onClick={handleSave}
              loading={loading}
              size="large"
              block
              style={{
                marginTop: '24px',
                height: '48px',
                fontSize: '16px',
                fontWeight: 500
              }}
            >
              {t('danmakuStorage.btnSaveConfig')}
            </Button>
          </Form>
        </TabPane>

        {/* 迁移与重命名 Tab */}
        <TabPane tab={t('danmakuStorage.tabMigrate')} key="migrate">
          {/* 筛选条件 */}
          <Card size="small" style={{ marginBottom: 16 }}>
            <Space wrap>
              <span>{t('danmakuStorage.labelType')}</span>
              <Select
                value={libraryTypeFilter}
                onChange={(v) => { setLibraryTypeFilter(v); setSelectedRowKeys([]); setSelectedRows([]); }}
                style={{ width: 100 }}
              >
                <Option value="all">{t('danmakuStorage.optAll')}</Option>
                <Option value="movie">{t('danmakuStorage.optMovie')}</Option>
                <Option value="tv">TV/OVA</Option>
              </Select>
              <Popover
                trigger="click"
                placement="bottom"
                content={(
                  <div style={{ width: 250 }}>
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Input
                        placeholder={t('danmakuStorage.searchPlaceholder')}
                        value={libraryKeyword}
                        onChange={(e) => setLibraryKeyword(e.target.value)}
                        onPressEnter={handleLibrarySearch}
                        prefix={<SearchOutlined />}
                        allowClear
                      />
                      <div className="flex gap-2 justify-end">
                        <Button
                          size="small"
                          onClick={() => {
                            setLibraryKeyword('');
                            handleLibrarySearch();
                          }}
                        >
                          {t('danmakuStorage.btnClear')}
                        </Button>
                        <Button
                          type="primary"
                          size="small"
                          icon={<SearchOutlined />}
                          onClick={handleLibrarySearch}
                        >
                          {t('danmakuStorage.btnSearch')}
                        </Button>
                      </div>
                    </Space>
                  </div>
                )}
              >
                <Button icon={<SearchOutlined />}>
                  {libraryKeyword ? t('danmakuStorage.btnSearchWithKeyword', { keyword: libraryKeyword }) : t('danmakuStorage.btnSearch')}
                </Button>
              </Popover>
              <Button icon={<ReloadOutlined />} onClick={handleLibraryRefresh}>
                {t('danmakuStorage.btnRefresh')}
              </Button>
            </Space>
          </Card>

          {/* 条目列表 */}
          <Table
            rowKey="animeId"
            columns={libraryColumns}
            dataSource={libraryItems}
            rowSelection={rowSelection}
            loading={libraryLoading}
            pagination={{
              current: libraryPage,
              pageSize: libraryPageSize,
              total: libraryTotal,
              showSizeChanger: true,
              showTotal: (total) => t('danmakuStorage.totalItems', { total }),
              onChange: (page, pageSize) => {
                setLibraryPageSize(pageSize);
                loadLibraryItems(page, libraryKeyword, libraryTypeFilter);
              },
            }}
            size="small"
            scroll={{ y: 'calc(100vh - 500px)' }}
          />

          {/* 选择状态栏 */}
          <Card size="small" style={{ marginTop: 16, marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
              <Space>
                <Tag color={selectedRows.length > 0 ? 'blue' : 'default'}>
                  {t('danmakuStorage.selectedCount', { count: selectedRows.length })}
                </Tag>
                {selectedRows.length > 0 && (
                  <Tag color="cyan">{t('danmakuStorage.selectedEpisodes', { count: selectedEpisodeCount })}</Tag>
                )}
              </Space>
              <Space>
                <Button size="small" onClick={() => {
                  const allKeys = libraryItems.map(item => item.animeId);
                  setSelectedRowKeys(allKeys);
                  setSelectedRows(libraryItems);
                }}>
                  {t('danmakuStorage.btnSelectAll')}
                </Button>
                <Button size="small" onClick={() => { setSelectedRowKeys([]); setSelectedRows([]); }}>
                  {t('danmakuStorage.btnClearSelection')}
                </Button>
              </Space>
            </div>
          </Card>

          {/* 批量操作按钮 */}
          <Card size="small">
            <Space wrap>
              <Tooltip title={t('danmakuStorage.btnMigrateTo')}>
                <Button
                  icon={<SwapOutlined />}
                  onClick={handleOpenMigrateModal}
                  disabled={selectedRows.length === 0}
                >
                  {t('danmakuStorage.btnMigrateTo')}
                </Button>
              </Tooltip>
              <Tooltip title={t('danmakuStorage.btnBatchRename')}>
                <Button
                  icon={<EditOutlined />}
                  onClick={handleOpenRenameModal}
                  disabled={selectedRows.length === 0}
                >
                  {t('danmakuStorage.btnBatchRename')}
                </Button>
              </Tooltip>
              <Tooltip title={t('danmakuStorage.btnApplyTemplate')}>
                <Button
                  type="primary"
                  icon={<SyncOutlined />}
                  onClick={handleOpenTemplateModal}
                  disabled={selectedRows.length === 0}
                >
                  {t('danmakuStorage.btnApplyTemplate')}
                </Button>
              </Tooltip>
            </Space>
          </Card>

          {/* 迁移Modal */}
          <Modal
            title={t('danmakuStorage.titleMigrateModal')}
            open={migrateModalVisible}
            onCancel={() => { setMigrateModalVisible(false); setMigratePreviewData(null); }}
            onOk={handleExecuteMigrate}
            confirmLoading={operationLoading}
            okText={t('danmakuStorage.btnConfirmMigrate')}
            width={700}
          >
            <div style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 8 }}>{t('danmakuStorage.labelTargetDir')}</div>
              <div style={{ display: 'flex', gap: 8 }}>
                <Input
                  value={migrateTargetPath}
                  onChange={(e) => setMigrateTargetPath(e.target.value)}
                  placeholder="/app/config/danmaku/new"
                  style={{ flex: 1 }}
                />
                <Button
                  type="primary"
                  icon={<FolderOpenOutlined />}
                  onClick={() => handleBrowseDirectory('migrate')}
                >
                  {t('danmakuStorage.btnBrowse')}
                </Button>
              </div>
            </div>
            <div style={{ marginBottom: 16 }}>
              <Checkbox
                checked={migrateKeepStructure}
                onChange={(e) => setMigrateKeepStructure(e.target.checked)}
              >
                {t('danmakuStorage.labelKeepStructure')}
              </Checkbox>
            </div>
            <div style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 8 }}>{t('danmakuStorage.labelConflict')}</div>
              <Select
                value={migrateConflictAction}
                onChange={setMigrateConflictAction}
                style={{ width: 200 }}
              >
                <Option value="skip">{t('danmakuStorage.optSkip')}</Option>
                <Option value="overwrite">{t('danmakuStorage.optOverwrite')}</Option>
                <Option value="rename">{t('danmakuStorage.optRenameConflict')}</Option>
              </Select>
            </div>

            {/* 预览区域 */}
            {migratePreviewData && (
              <>
                <Divider orientation="left">{t('danmakuStorage.dividerMigratePreview')}</Divider>
                <div style={{ maxHeight: 300, overflowY: 'auto', border: '1px solid var(--color-border)', borderRadius: 4, padding: 8 }}>
                  {migratePreviewData.previewItems.map((item, index) => (
                    <div key={index} style={{ marginBottom: 12, padding: 8, background: 'var(--color-hover)', borderRadius: 4 }}>
                      <div style={{ fontWeight: 500, marginBottom: 4 }}>
                        {item.animeTitle} {item.episodeIndex ? t('danmakuStorage.templatePreviewEpisode', { ep: item.episodeIndex }) : ''}
                      </div>
                      <div style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
                        <div style={{ marginBottom: 4 }}>
                          <Text type="secondary">{t('danmakuStorage.labelOldPath')}</Text>
                          <Text code style={{ fontSize: 13 }}>{item.oldPath}</Text>
                        </div>
                        <div>
                          <Text type="secondary">{t('danmakuStorage.labelNewPath')}</Text>
                          <Text code style={{ fontSize: 13, color: '#52c41a' }}>{item.newPath}</Text>
                        </div>
                        {!item.exists && (
                          <Tag color="warning" style={{ marginTop: 4 }}>{t('danmakuStorage.tagFileNotExist')}</Tag>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                <div style={{ marginTop: 8, color: 'var(--color-text-secondary)' }}>
                  {t('danmakuStorage.migratePreviewTotal', { count: migratePreviewData.totalCount })}
                </div>
              </>
            )}

            {!migratePreviewData && (
              <>
                <Divider />
                <div style={{ color: '#666' }}>
                  {t('danmakuStorage.migrateWillMigrate', { items: selectedRows.length, episodes: selectedEpisodeCount })}
                  <div style={{ marginTop: 8, fontSize: 12 }}>
                    <Text type="secondary">{t('danmakuStorage.migrateClickPreview')}</Text>
                  </div>
                </div>
              </>
            )}
          </Modal>

          {/* 重命名Modal - 多规则系统 */}
          <Modal
            title={t('danmakuStorage.titleRenameModal')}
            open={renameModalVisible}
            onCancel={() => {
              setRenameModalVisible(false);
              setRenameRules([]);
              setRuleParams({});
              setIsRenamePreviewMode(false);
              setRenamePreviewData(null);
            }}
            onOk={handleExecuteRename}
            confirmLoading={operationLoading}
            okText={t('danmakuStorage.btnConfirmRename')}
            okButtonProps={{ disabled: renameRules.length === 0 }}
            width={800}
          >
            {/* 规则添加区域 */}
            <div style={{ marginBottom: 16, padding: 12, background: 'var(--color-hover)', borderRadius: 8 }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <span style={{ color: 'var(--color-text-secondary)', fontSize: 13 }}>{t('danmakuStorage.labelAddRule')}</span>
                <Select
                  value={selectedRuleType}
                  onChange={(v) => { setSelectedRuleType(v); setRuleParams({}); }}
                  style={{ width: 100 }}
                  options={ruleTypeOptions}
                  size="small"
                />
                {/* 替换规则参数 */}
                {selectedRuleType === 'replace' && (
                  <>
                    <Input size="small" value={ruleParams.search || ''} onChange={(e) => setRuleParams(p => ({ ...p, search: e.target.value }))} placeholder={t('danmakuStorage.placeholderSearch')} style={{ width: 120 }} />
                    <span style={{ color: 'var(--color-text-secondary)' }}>→</span>
                    <Input size="small" value={ruleParams.replace || ''} onChange={(e) => setRuleParams(p => ({ ...p, replace: e.target.value }))} placeholder={t('danmakuStorage.placeholderReplace')} style={{ width: 120 }} />
                    <Checkbox checked={ruleParams.caseSensitive || false} onChange={(e) => setRuleParams(p => ({ ...p, caseSensitive: e.target.checked }))}>{t('danmakuStorage.labelCaseSensitive')}</Checkbox>
                  </>
                )}
                {/* 正则规则参数 */}
                {selectedRuleType === 'regex' && (
                  <>
                    <Input size="small" value={ruleParams.pattern || ''} onChange={(e) => setRuleParams(p => ({ ...p, pattern: e.target.value }))} placeholder={t('danmakuStorage.placeholderRegex')} style={{ width: 150 }} />
                    <span style={{ color: 'var(--color-text-secondary)' }}>→</span>
                    <Input size="small" value={ruleParams.replace || ''} onChange={(e) => setRuleParams(p => ({ ...p, replace: e.target.value }))} placeholder={t('danmakuStorage.placeholderReplace')} style={{ width: 120 }} />
                  </>
                )}
                {/* 插入规则参数 */}
                {selectedRuleType === 'insert' && (
                  <>
                    <Input size="small" value={ruleParams.text || ''} onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))} placeholder={t('danmakuStorage.placeholderInsertText')} style={{ width: 120 }} />
                    <Select
                      size="small"
                      value={ruleParams.position || 'start'}
                      onChange={(v) => setRuleParams(p => ({ ...p, position: v }))}
                      style={{ width: 100 }}
                      options={[
                        { value: 'start', label: t('danmakuStorage.optInsertStart') },
                        { value: 'end', label: t('danmakuStorage.optInsertEnd') },
                        { value: 'index', label: t('danmakuStorage.optInsertIndex') }
                      ]}
                    />
                    {ruleParams.position === 'index' && (
                      <InputNumber
                        size="small"
                        value={ruleParams.index || 0}
                        onChange={(v) => setRuleParams(p => ({ ...p, index: v }))}
                        min={0}
                        placeholder={t('danmakuStorage.placeholderPosition')}
                        style={{ width: 80 }}
                        addonAfter={t('danmakuStorage.addonAfterChars')}
                      />
                    )}
                  </>
                )}
                {/* 删除规则参数 */}
                {selectedRuleType === 'delete' && (
                  <>
                    <Select
                      size="small"
                      value={ruleParams.mode || 'text'}
                      onChange={(v) => setRuleParams(p => ({ ...p, mode: v }))}
                      style={{ width: 140 }}
                      options={[
                        { value: 'text', label: t('danmakuStorage.optDelText') },
                        { value: 'first', label: t('danmakuStorage.optDelFirst') },
                        { value: 'last', label: t('danmakuStorage.optDelLast') },
                        { value: 'toText', label: t('danmakuStorage.optDelToText') },
                        { value: 'fromText', label: t('danmakuStorage.optDelFromText') },
                        { value: 'range', label: t('danmakuStorage.optDelRange') },
                      ]}
                    />
                    {(ruleParams.mode === 'text' || !ruleParams.mode) && (
                      <>
                        <Input size="small" value={ruleParams.text || ''} onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))} placeholder={t('danmakuStorage.placeholderDelText')} style={{ width: 120 }} />
                        <Checkbox checked={ruleParams.caseSensitive || false} onChange={(e) => setRuleParams(p => ({ ...p, caseSensitive: e.target.checked }))}>{t('danmakuStorage.labelCaseSensitive')}</Checkbox>
                      </>
                    )}
                    {ruleParams.mode === 'first' && (
                      <Input size="small" type="number" value={ruleParams.count || ''} onChange={(e) => setRuleParams(p => ({ ...p, count: e.target.value }))} placeholder={t('danmakuStorage.placeholderCount')} style={{ width: 100 }} />
                    )}
                    {ruleParams.mode === 'last' && (
                      <Input size="small" type="number" value={ruleParams.count || ''} onChange={(e) => setRuleParams(p => ({ ...p, count: e.target.value }))} placeholder={t('danmakuStorage.placeholderCount')} style={{ width: 100 }} />
                    )}
                    {ruleParams.mode === 'toText' && (
                      <>
                        <Input size="small" value={ruleParams.text || ''} onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))} placeholder={t('danmakuStorage.placeholderToText')} style={{ width: 120 }} />
                        <Checkbox checked={ruleParams.caseSensitive || false} onChange={(e) => setRuleParams(p => ({ ...p, caseSensitive: e.target.checked }))}>{t('danmakuStorage.labelCaseSensitive')}</Checkbox>
                      </>
                    )}
                    {ruleParams.mode === 'fromText' && (
                      <>
                        <Input size="small" value={ruleParams.text || ''} onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))} placeholder={t('danmakuStorage.placeholderFromText')} style={{ width: 120 }} />
                        <Checkbox checked={ruleParams.caseSensitive || false} onChange={(e) => setRuleParams(p => ({ ...p, caseSensitive: e.target.checked }))}>{t('danmakuStorage.labelCaseSensitive')}</Checkbox>
                      </>
                    )}
                    {ruleParams.mode === 'range' && (
                      <>
                        <span style={{ fontSize: 13 }}>{t('danmakuStorage.placeholderStart')}</span>
                        <Input size="small" type="number" value={ruleParams.from || ''} onChange={(e) => setRuleParams(p => ({ ...p, from: e.target.value }))} placeholder={t('danmakuStorage.placeholderStart')} style={{ width: 90 }} />
                        <Input size="small" type="number" value={ruleParams.count || ''} onChange={(e) => setRuleParams(p => ({ ...p, count: e.target.value }))} placeholder={t('danmakuStorage.placeholderCount')} style={{ width: 80 }} />
                      </>
                    )}
                  </>
                )}
                {/* 序列化规则参数 */}
                {selectedRuleType === 'serialize' && (
                  <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: '8px', padding: '8px', background: 'var(--color-hover)', borderRadius: '6px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 13, color: 'var(--color-text-tertiary)' }}>{t('danmakuStorage.labelSerializeFormat')}</span>
                      <Input size="small" value={ruleParams.prefix || ''} onChange={(e) => setRuleParams(p => ({ ...p, prefix: e.target.value }))} placeholder={t('danmakuStorage.serializePlaceholderPrefix')} style={{ width: 120 }} addonBefore={t('danmakuStorage.addonSerializePrefix')} />
                      <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>+</span>
                      <span style={{ padding: '2px 8px', background: '#e6f7ff', color: '#1890ff', borderRadius: '4px', fontSize: 12, fontFamily: 'monospace' }}>{t('danmakuStorage.serialNumber')}</span>
                      <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>+</span>
                      <Input size="small" value={ruleParams.suffix || ''} onChange={(e) => setRuleParams(p => ({ ...p, suffix: e.target.value }))} placeholder={t('danmakuStorage.serializePlaceholderSuffix')} style={{ width: 120 }} addonBefore={t('danmakuStorage.addonSerializeSuffix')} />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 13, color: 'var(--color-text-tertiary)' }}>{t('danmakuStorage.labelSerializeSettings')}</span>
                      <InputNumber size="small" value={ruleParams.start || 1} onChange={(v) => setRuleParams(p => ({ ...p, start: v }))} min={0} style={{ width: 130 }} addonBefore={t('danmakuStorage.addonSerializeStart')} />
                      <InputNumber size="small" value={ruleParams.digits || 2} onChange={(v) => setRuleParams(p => ({ ...p, digits: v }))} min={1} max={5} style={{ width: 130 }} addonBefore={t('danmakuStorage.addonSerializeDigits')} />
                      <Select size="small" value={ruleParams.position || 'replace'} onChange={(v) => setRuleParams(p => ({ ...p, position: v }))} style={{ width: 100 }} options={[
                        { value: 'start', label: t('danmakuStorage.optSerializeStart') },
                        { value: 'end', label: t('danmakuStorage.optSerializeEnd') },
                        { value: 'replace', label: t('danmakuStorage.optSerializeReplace') }
                      ]} />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>{t('danmakuStorage.labelSerializePreview')}</span>
                      <span style={{ fontSize: 13, fontFamily: 'monospace', color: '#1890ff', fontWeight: '600' }}>
                        {
                          ruleParams.position === 'start'
                            ? `${ruleParams.prefix || ''}${String(ruleParams.start || 1).padStart(ruleParams.digits || 2, '0')}${ruleParams.suffix || ''}${t('danmakuStorage.serializeOriginalName')}`
                            : ruleParams.position === 'end'
                            ? `${t('danmakuStorage.serializeOriginalName')}${ruleParams.prefix || ''}${String(ruleParams.start || 1).padStart(ruleParams.digits || 2, '0')}${ruleParams.suffix || ''}`
                            : `${ruleParams.prefix || ''}${String(ruleParams.start || 1).padStart(ruleParams.digits || 2, '0')}${ruleParams.suffix || ''}`
                        }
                      </span>
                    </div>
                  </div>
                )}
                {/* 大小写规则参数 */}
                {selectedRuleType === 'case' && (
                  <Select size="small" value={ruleParams.mode || 'upper'} onChange={(v) => setRuleParams(p => ({ ...p, mode: v }))} style={{ width: 120 }} options={[{ value: 'upper', label: t('danmakuStorage.optCaseUpper') }, { value: 'lower', label: t('danmakuStorage.optCaseLower') }, { value: 'title', label: t('danmakuStorage.optCaseTitle') }]} />
                )}
                {/* 清理规则参数 */}
                {selectedRuleType === 'strip' && (
                  <>
                    <Checkbox checked={ruleParams.trimSpaces || false} onChange={(e) => setRuleParams(p => ({ ...p, trimSpaces: e.target.checked }))}>{t('danmakuStorage.labelTrimSpaces')}</Checkbox>
                    <Checkbox checked={ruleParams.trimDuplicateSpaces || false} onChange={(e) => setRuleParams(p => ({ ...p, trimDuplicateSpaces: e.target.checked }))}>{t('danmakuStorage.labelTrimDuplicateSpaces')}</Checkbox>
                    <Input size="small" value={ruleParams.chars || ''} onChange={(e) => setRuleParams(p => ({ ...p, chars: e.target.value }))} placeholder={t('danmakuStorage.placeholderDeleteChars')} style={{ width: 80 }} />
                  </>
                )}
                <Button type="primary" size="small" onClick={handleAddRenameRule}>{t('danmakuStorage.btnAddRule')}</Button>
              </div>
            </div>

            {/* 已添加的规则列表 */}
            {renameRules.length > 0 && (
              <div style={{ border: '1px solid var(--color-border)', borderRadius: 8, padding: 8, marginBottom: 16, background: 'var(--color-card)', maxHeight: 120, overflowY: 'auto' }}>
                {renameRules.map((rule, idx) => (
                  <div key={rule.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', borderBottom: idx < renameRules.length - 1 ? '1px solid var(--color-border)' : 'none' }}>
                    <Checkbox checked={rule.enabled} onChange={() => handleToggleRenameRule(rule.id)} />
                    <span style={{ color: 'var(--color-text-secondary)', fontSize: 12 }}>{idx + 1}.</span>
                    <Tag color={rule.enabled ? 'blue' : 'default'}>{ruleTypeOptions.find(r => r.value === rule.type)?.label}</Tag>
                    <span style={{ fontSize: 13, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {rule.type === 'replace' && `"${rule.params.search}" → "${rule.params.replace || ''}"`}
                      {rule.type === 'regex' && `/${rule.params.pattern}/ → "${rule.params.replace || ''}"`}
                      {rule.type === 'insert' && `"${rule.params.text}" (${rule.params.position === 'start' ? t('danmakuStorage.ruleDescInsertStart') : t('danmakuStorage.ruleDescInsertEnd')})`}
                      {rule.type === 'delete' && (() => {
                        const mode = rule.params.mode || 'text';
                        switch (mode) {
                          case 'text': return t('danmakuStorage.ruleDescDelText', { text: rule.params.text });
                          case 'first': return t('danmakuStorage.ruleDescDelFirst', { count: rule.params.count || 0 });
                          case 'last': return t('danmakuStorage.ruleDescDelLast', { count: rule.params.count || 0 });
                          case 'toText': return t('danmakuStorage.ruleDescDelToText', { text: rule.params.text });
                          case 'fromText': return t('danmakuStorage.ruleDescDelFromText', { text: rule.params.text });
                          case 'range': return t('danmakuStorage.ruleDescDelRange', { from: rule.params.from || 0, count: rule.params.count || 0 });
                          default: return t('danmakuStorage.ruleDescDel');
                        }
                      })()}
                      {rule.type === 'serialize' && `${rule.params.prefix || ''}{${String(rule.params.start || 1).padStart(rule.params.digits || 2, '0')}}${rule.params.suffix || ''}`}
                      {rule.type === 'case' && (rule.params.mode === 'upper' ? t('danmakuStorage.optCaseUpper') : rule.params.mode === 'lower' ? t('danmakuStorage.optCaseLower') : t('danmakuStorage.optCaseTitle'))}
                      {rule.type === 'strip' && t('danmakuStorage.ruleDescCaseStrip')}
                    </span>
                    <Button type="text" danger size="small" onClick={() => handleDeleteRenameRule(rule.id)}>🗑</Button>
                  </div>
                ))}
              </div>
            )}

            {/* 预览开关和操作 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 13 }}>{t('danmakuStorage.labelPreviewToggle')}</span>
                <Switch
                  checked={isRenamePreviewMode}
                  onChange={(checked) => {
                    if (checked && renameOriginalItems.length > 0) {
                      // 使用从后端获取的原始文件名列表计算预览数据
                      const previewItems = renameOriginalItems.map((item, index) => {
                        const oldName = item.oldName;
                        const baseName = oldName.replace(/\.[^/.]+$/, '');
                        const ext = oldName.includes('.') ? '.' + oldName.split('.').pop() : '';
                        const newBaseName = applyAllRenameRules(baseName, index);
                        return {
                          oldName: oldName,
                          newName: newBaseName + ext,
                          episodeId: item.episodeId,
                          oldPath: item.oldPath
                        };
                      });
                      setRenamePreviewData({ totalCount: previewItems.length, previewItems: previewItems.slice(0, 20) });
                      setIsRenamePreviewMode(true);
                    } else {
                      setIsRenamePreviewMode(false);
                      setRenamePreviewData(null);
                    }
                  }}
                  disabled={renameOriginalItems.length === 0}
                  size="small"
                />
              </div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('danmakuStorage.renameWillRename', { items: selectedRows.length, files: renameOriginalItems.length })}
              </Text>
            </div>

            {/* 预览区域 */}
            {isRenamePreviewMode && renamePreviewData && (
              <>
                <Divider orientation="left" style={{ margin: '8px 0' }}>{t('danmakuStorage.dividerRenamePreview')}</Divider>
                <div style={{ maxHeight: 200, overflowY: 'auto', border: '1px solid var(--color-border)', borderRadius: 4, padding: 8 }}>
                  {renamePreviewData.previewItems.map((item, index) => (
                    <div key={index} style={{ marginBottom: 8, padding: 6, background: 'var(--color-hover)', borderRadius: 4 }}>
                      <div style={{ fontSize: 13 }}>
                        <Text code style={{ fontSize: 12 }}>{item.oldName}</Text>
                        <span style={{ margin: '0 8px', color: 'var(--color-text-secondary)' }}>→</span>
                        <Text code style={{ fontSize: 12, color: '#52c41a' }}>{item.newName}</Text>
                      </div>
                    </div>
                  ))}
                </div>
                <div style={{ marginTop: 8, color: 'var(--color-text-secondary)', fontSize: 12 }}>
                  {t('danmakuStorage.renamePreviewTotal', { count: renamePreviewData.totalCount })}
                </div>
              </>
            )}
          </Modal>

          {/* 模板转换Modal */}
          <Modal
            title={t('danmakuStorage.titleTemplateModal')}
            open={templateModalVisible}
            onCancel={() => setTemplateModalVisible(false)}
            onOk={handleExecuteTemplate}
            confirmLoading={operationLoading}
            okText={t('danmakuStorage.btnConfirmTemplate')}
            width={isMobile ? '95%' : 1350}
          >
            <div style={{ marginBottom: 16, padding: 12, background: '#f5f5f5', borderRadius: 4 }}>
              <Text type="secondary">{t('danmakuStorage.descTemplateModal')}</Text>
            </div>

            {/* 可用参数按钮组 */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 8, color: '#666' }}>{t('danmakuStorage.labelAvailableParams')}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {(templateVariables || []).map((v) => (
                  <Tooltip
                    key={v.name}
                    title={<div><div>{v.desc}</div><div style={{ color: '#aaa', marginTop: 4 }}>示例: {v.example}</div></div>}
                    placement="top"
                  >
                    <Button
                      size="small"
                      type="dashed"
                      onClick={() => {
                        const newTemplate = customTemplate + v.name;
                        setCustomTemplate(newTemplate);
                        setTemplateTarget('custom');
                      }}
                      style={{ fontFamily: 'monospace', fontSize: 12 }}
                    >
                      {v.name}
                    </Button>
                  </Tooltip>
                ))}
              </div>
            </div>

            <div style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 8 }}>{t('danmakuStorage.labelTargetTemplate')}</div>
              <Row gutter={12}>
                <Col span={isMobile ? 24 : 8}>
                  <Select
                    value={templateTarget}
                    onChange={async (v) => {
                      setTemplateTarget(v);
                      if (v !== 'custom') {
                        const preset = presetTemplates.find(p => p.value === v);
                        if (preset) {
                          setCustomTemplate(preset.template);
                        }
                        setTemplatePreviewLoading(true);
                        try {
                          const response = await previewDanmakuTemplate({
                            animeIds: selectedRowKeys,
                            templateType: v,
                            customTemplate: v === 'custom' ? customTemplate : undefined,
                          });
                          setTemplatePreviewData(response.data);
                        } catch (error) {
                          message.error(t('danmakuStorage.previewFailed', { error: error.message || t('common.unknown') }));
                        } finally {
                          setTemplatePreviewLoading(false);
                        }
                      }
                    }}
                    style={{ width: '100%', marginBottom: isMobile ? 8 : 0 }}
                  >
                    {presetTemplates.map(p => (
                      <Option key={p.value} value={p.value}>{p.label}</Option>
                    ))}
                    <Option value="custom">{t('danmakuStorage.optCustomTemplate')}</Option>
                  </Select>
                </Col>
                <Col span={isMobile ? 24 : 16}>
                  <Input
                    value={customTemplate}
                    onChange={(e) => {
                      setCustomTemplate(e.target.value);
                      setTemplateTarget('custom');
                    }}
                    placeholder={t('danmakuStorage.placeholderCustomTemplate')}
                    style={{ fontFamily: 'monospace' }}
                  />
                </Col>
              </Row>
              <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
                {t('danmakuStorage.currentTemplate')}<Text code style={{ fontSize: 12 }}>{customTemplate || presetTemplates.find(p => p.value === templateTarget)?.template || ''}.xml</Text>
              </div>
            </div>

            {/* 预览区域 */}
            {templatePreviewData && (
              <>
                <Divider orientation="left">{t('danmakuStorage.dividerTemplatePreview')}</Divider>
                <div style={{ maxHeight: 300, overflowY: 'auto', border: '1px solid var(--color-border)', borderRadius: 4, padding: 8 }}>
                  {templatePreviewData.previewItems.map((item, index) => (
                    <div key={index} style={{ marginBottom: 12, padding: 8, background: 'var(--color-hover)', borderRadius: 4 }}>
                      <div style={{ fontWeight: 500, marginBottom: 4 }}>
                        {item.animeTitle} {item.episodeIndex ? t('danmakuStorage.templatePreviewEpisode', { ep: item.episodeIndex }) : ''}
                      </div>
                      <div style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
                        <div style={{ marginBottom: 4 }}>
                          <Text type="secondary">{t('danmakuStorage.labelOldPath')}</Text>
                          <Text code style={{ fontSize: 13 }}>{item.oldPath}</Text>
                        </div>
                        <div>
                          <Text type="secondary">{t('danmakuStorage.labelNewPath')}</Text>
                          <Text code style={{ fontSize: 13, color: '#52c41a' }}>{item.newPath}</Text>
                        </div>
                        {!item.exists && (
                          <Tag color="warning" style={{ marginTop: 4 }}>{t('danmakuStorage.tagFileNotExist')}</Tag>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                <div style={{ marginTop: 8, color: 'var(--color-text-secondary)' }}>
                  {t('danmakuStorage.templatePreviewTotal', { count: templatePreviewData.totalCount })}
                </div>
              </>
            )}

            {!templatePreviewData && !templatePreviewLoading && (
              <>
                <Divider />
                <div style={{ color: 'var(--color-text-secondary)' }}>
                  {t('danmakuStorage.templateWillConvert', { items: selectedRows.length, episodes: selectedEpisodeCount })}
                  <div style={{ marginTop: 8, fontSize: 12 }}>
                    <Text type="secondary">{t('danmakuStorage.templateClickPreview')}</Text>
                  </div>
                </div>
              </>
            )}
            {templatePreviewLoading && (
              <div style={{ textAlign: 'center', padding: 20, color: 'var(--color-text-secondary)' }}>
                {t('danmakuStorage.templateLoadingPreview')}
              </div>
            )}
          </Modal>
        </TabPane>

        <TabPane tab={t('danmakuStorage.tabSettings')} key="settings">
          <div style={{ maxWidth: 600 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
              <span>{t('danmakuStorage.labelFetchLikes')}</span>
              <Switch
                checked={likesFetchEnabled}
                onChange={async (checked) => {
                  setLikesFetchEnabled(checked);
                  try {
                    await setDanmakuLikesFetchEnabled({ value: checked ? 'true' : 'false' });
                    message.success(checked ? t('danmakuStorage.likesEnabled') : t('danmakuStorage.likesDisabled'));
                  } catch (error) {
                    message.error(t('danmakuStorage.likesSaveFailed'));
                    setLikesFetchEnabled(!checked);
                  }
                }}
              />
            </div>
            <div style={{ color: '#999', fontSize: 12 }}>
              {t('danmakuStorage.descFetchLikes')}
            </div>
          </div>
        </TabPane>
      </Tabs>

      {/* 目录浏览器（用于存储配置中选择目录） */}
      <DirectoryBrowser
        visible={browserVisible}
        onClose={() => setBrowserVisible(false)}
        onSelect={handleSelectDirectory}
      />

      {/* 快速模板选择弹窗 */}
      <Modal
        title={t('danmakuStorage.titleQuickTemplate')}
        open={quickTemplateModalVisible}
        onCancel={() => setQuickTemplateModalVisible(false)}
        footer={null}
        width={500}
      >
        <div style={{ marginBottom: '16px', color: 'var(--color-text-secondary)', fontSize: '13px' }}>
          {quickTemplateType === 'movie' ? t('danmakuStorage.descQuickTemplateMovie') : t('danmakuStorage.descQuickTemplateTv')}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {presetTemplates.filter(t => !t.value.startsWith('custom_')).map((tpl) => (
            <Button
              key={tpl.value}
              block
              style={{
                textAlign: 'left',
                height: 'auto',
                padding: '12px 16px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'flex-start'
              }}
              onClick={() => {
                if (quickTemplateType === 'movie') {
                  setMovieDanmakuFilenameTemplate(tpl.template);
                  form.setFieldValue('movieDanmakuFilenameTemplate', tpl.template);
                } else {
                  setTvDanmakuFilenameTemplate(tpl.template);
                  form.setFieldValue('tvDanmakuFilenameTemplate', tpl.template);
                }
                setQuickTemplateModalVisible(false);
                message.success(t('danmakuStorage.templateApplied', { label: tpl.label }));
              }}
            >
              <div style={{ fontWeight: 500 }}>{tpl.label}</div>
              <div style={{
                fontSize: '12px',
                color: 'var(--color-text-secondary)',
                fontFamily: 'monospace',
                marginTop: '4px'
              }}>
                {tpl.template}
              </div>
            </Button>
          ))}
        </div>
      </Modal>
    </Card>
  );
};

export default DanmakuStorage;

