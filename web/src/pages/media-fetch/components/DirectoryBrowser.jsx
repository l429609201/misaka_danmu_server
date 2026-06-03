import { useState, useEffect, useMemo } from 'react';
import { Modal, Button, Space, Typography, message } from 'antd';
import { FolderOpenOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import Cookies from 'js-cookie';
import {
  FullFileBrowser,
  setChonkyDefaults,
  ChonkyActions,
  FileHelper,
  defineFileAction
} from 'chonky';
import { ChonkyIconFA } from 'chonky-icon-fontawesome';
import { browseDirectory } from '../../../apis';
import { createFolder, deleteFolder } from '../../../apis';
import './DirectoryBrowser.css';

// 定义文件操作（接收 t 函数用于国际化）
const createFileActions = (t) => ({
  EnableListView: defineFileAction({
    ...ChonkyActions.EnableListView,
    button: {
      name: t('mediaFetch.directoryBrowser.listView'),
      toolbar: true,
      contextMenu: false,
      icon: ChonkyActions.EnableListView.button?.icon || 'list',
    },
  }),
  EnableGridView: defineFileAction({
    ...ChonkyActions.EnableGridView,
    button: {
      name: t('mediaFetch.directoryBrowser.gridView'),
      toolbar: true,
      contextMenu: false,
      icon: ChonkyActions.EnableGridView.button?.icon || 'th',
    },
  }),
  SortFilesByName: defineFileAction({
    ...ChonkyActions.SortFilesByName,
    button: {
      name: t('mediaFetch.directoryBrowser.sortByName'),
      toolbar: true,
      contextMenu: false,
    },
  }),
  SortFilesByDate: defineFileAction({
    ...ChonkyActions.SortFilesByDate,
    button: {
      name: t('mediaFetch.directoryBrowser.sortByDate'),
      toolbar: true,
      contextMenu: false,
    },
  }),
  SortFilesBySize: defineFileAction({
    ...ChonkyActions.SortFilesBySize,
    button: {
      name: t('mediaFetch.directoryBrowser.sortBySize'),
      toolbar: true,
      contextMenu: false,
    },
  }),
  ToggleShowFoldersFirst: defineFileAction({
    ...ChonkyActions.ToggleShowFoldersFirst,
    button: {
      name: t('mediaFetch.directoryBrowser.foldersFirst'),
      toolbar: true,
      contextMenu: false,
    },
  }),
  CreateFolder: defineFileAction({
    ...ChonkyActions.CreateFolder,
    button: {
      name: t('mediaFetch.directoryBrowser.newFolder'),
      toolbar: false,
      contextMenu: true,
      icon: 'folder', // 尝试简单的folder图标
    },
  }),
  DeleteFolder: defineFileAction({
    id: 'delete_folder',
    requiresSelection: true,
    fileFilter: (file) => FileHelper.isDirectory(file), // 只对文件夹显示
    button: {
      name: t('mediaFetch.directoryBrowser.deleteFolder'),
      toolbar: false,
      contextMenu: true,
      icon: 'trash',
    },
  }),
});

// 设置Chonky默认配置
setChonkyDefaults({
  iconComponent: ChonkyIconFA,
});

// 文件大小格式化函数
const formatFileSize = (bytes) => {
  if (bytes === 0) return '0 B';

  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

// 定义国际化配置（接收 t 函数）
const createChonkyI18n = (isMobile, t) => ({
  locale: 'zh',
  formatters: {
    formatFileModDate: (intl, file) => {
      const safeModDate = FileHelper.getModDate(file);
      if (safeModDate) {
        return `${intl.formatDate(safeModDate)}, ${intl.formatTime(safeModDate)}`;
      } else {
        return null;
      }
    },
    formatFileSize: (intl, file) => {
      if (!file || typeof file.size !== 'number') return null;
      return formatFileSize(file.size);
    },
  },
  messages: {
    // Chonky UI 翻译字符串
    'chonky.toolbar.searchPlaceholder': t('mediaFetch.directoryBrowser.search'),
    'chonky.toolbar.visibleFileCount': t('mediaFetch.directoryBrowser.visibleFileCount'),
    'chonky.toolbar.selectedFileCount': t('mediaFetch.directoryBrowser.selectedFileCount'),
    'chonky.toolbar.hiddenFileCount': t('mediaFetch.directoryBrowser.hiddenFileCount'),
    'chonky.fileList.nothingToShow': t('mediaFetch.directoryBrowser.nothingToShow'),
    'chonky.contextMenu.browserMenuShortcut': t('mediaFetch.directoryBrowser.browserMenuShortcut'),
    'chonky.contextMenu.multipleSelection': t('mediaFetch.directoryBrowser.multipleSelection'),
    'chonky.contextMenu.emptySelection': t('mediaFetch.directoryBrowser.noSelection'),

    // 文件操作翻译字符串 - 电脑端隐藏actions和options按钮组
    [`chonky.actionGroups.Actions`]: isMobile ? t('mediaFetch.directoryBrowser.actions') : '',
    [`chonky.actionGroups.Options`]: isMobile ? t('mediaFetch.directoryBrowser.options') : '',
    [`chonky.actions.${ChonkyActions.OpenParentFolder.id}.button.name`]: t('mediaFetch.directoryBrowser.openParentFolder'),
    [`chonky.actions.${ChonkyActions.CreateFolder.id}.button.name`]: t('mediaFetch.directoryBrowser.newFolder'),
    [`chonky.actions.${ChonkyActions.CreateFolder.id}.button.tooltip`]: t('mediaFetch.directoryBrowser.createFolder'),
    [`chonky.actions.delete_folder.button.name`]: t('mediaFetch.directoryBrowser.deleteFolder'),
    [`chonky.actions.delete_folder.button.tooltip`]: t('mediaFetch.directoryBrowser.deleteSelectedFolder'),
    [`chonky.actions.${ChonkyActions.OpenSelection.id}.button.name`]: t('mediaFetch.directoryBrowser.openSelection'),
    [`chonky.actions.${ChonkyActions.SelectAllFiles.id}.button.name`]: t('mediaFetch.directoryBrowser.selectAll'),
    [`chonky.actions.${ChonkyActions.ClearSelection.id}.button.name`]: t('mediaFetch.directoryBrowser.clearSelection'),
    [`chonky.actions.${ChonkyActions.EnableListView.id}.button.name`]: t('mediaFetch.directoryBrowser.listView'),
    [`chonky.actions.${ChonkyActions.EnableGridView.id}.button.name`]: t('mediaFetch.directoryBrowser.gridView'),
    [`chonky.actions.${ChonkyActions.SortFilesByName.id}.button.name`]: t('mediaFetch.directoryBrowser.sortByName'),
    [`chonky.actions.${ChonkyActions.SortFilesByDate.id}.button.name`]: t('mediaFetch.directoryBrowser.sortByDate'),
    [`chonky.actions.${ChonkyActions.SortFilesBySize.id}.button.name`]: t('mediaFetch.directoryBrowser.sortBySize'),
    [`chonky.actions.${ChonkyActions.ToggleHiddenFiles.id}.button.name`]: t('mediaFetch.directoryBrowser.hiddenFiles'),
    [`chonky.actions.${ChonkyActions.ToggleShowFoldersFirst.id}.button.name`]: t('mediaFetch.directoryBrowser.foldersFirst'),
  },
});

const { Text } = Typography;

// 将API返回的数据转换为Chonky格式
const convertToChonkyFiles = (apiFiles) => {
  return apiFiles.map(item => {
    const modDate = item.modify_time ? new Date(item.modify_time) : new Date();

    return {
      id: item.path,
      name: item.name,
      isDir: item.type === 'dir',
      modDate: modDate,
      ...(item.type !== 'dir' && { size: item.size || 0 }), // 只为文件设置大小，文件夹不设置大小
    };
  });
};

// 创建文件夹链
const createFolderChain = (currentPath, rootName) => {
  if (!currentPath) {
    return [{ id: 'root', name: rootName, isDir: true }];
  }

  // 检测路径分隔符
  const separator = currentPath.includes('\\') ? '\\' : '/';
  const parts = currentPath.split(separator).filter(p => p);

  // 对于Windows驱动器路径，如 C:\ 或 D:\
  if (separator === '\\' && parts.length > 0 && parts[0].match(/^[A-Za-z]:$/)) {
    const drive = parts[0];
    const chain = [{ id: drive + '\\', name: drive, isDir: true }];

    let currentId = drive + '\\';
    for (let i = 1; i < parts.length; i++) {
      const part = parts[i];
      currentId = currentId + part + '\\';
      chain.push({
        id: currentId,
        name: part,
        isDir: true,
      });
    }

    return chain;
  }

  // Unix/Linux路径
  const chain = [{ id: '/', name: rootName, isDir: true }];
  let currentId = '/';

  for (const part of parts) {
    currentId = currentId === '/' ? `/${part}` : `${currentId}/${part}`;
    chain.push({
      id: currentId,
      name: part,
      isDir: true,
    });
  }

  return chain;
};

const DirectoryBrowser = ({ visible, onClose, onSelect, selectMode = 'directory', fileFilter }) => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [currentPath, setCurrentPath] = useState('/');
  const [files, setFiles] = useState([]);
  const [isMobile, setIsMobile] = useState(false);
  const [createFolderVisible, setCreateFolderVisible] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);

  // 检测是否为移动端
  useEffect(() => {
    const checkIsMobile = () => {
      setIsMobile(window.innerWidth <= 768);
    };

    checkIsMobile();
    window.addEventListener('resize', checkIsMobile);

    return () => {
      window.removeEventListener('resize', checkIsMobile);
    };
  }, []);

  // 移动端简化日期显示
  useEffect(() => {
    if (isMobile && visible && files.length > 0) {
      const formatTimeElements = () => {
        const timeElements = document.querySelectorAll('.chonky-fileEntry > div:nth-child(2)');
        timeElements.forEach(el => {
          const text = el.textContent;
          if (text && text.includes(',')) {
            try {
              const date = new Date(text);
              if (!isNaN(date.getTime())) {
                const month = date.getMonth() + 1;
                const day = date.getDate();
                const hour = date.getHours();
                const minute = date.getMinutes();
                el.textContent = `${month}-${day} ${hour}:${minute.toString().padStart(2, '0')}`;
              }
            } catch (e) {
              // 忽略解析错误
            }
          }
        });
      };
      setTimeout(formatTimeElements, 100);
    }
  }, [isMobile, visible, files]);

  useEffect(() => {
    if (visible) {
      loadDirectory(currentPath);
      // 重置选择状态
      setSelectedFile(null);
    }
  }, [visible, currentPath]);

  const loadDirectory = async (path) => {
    setLoading(true);
    try {
      const token = Cookies.get('danmu_token');

      // 检查token是否存在
      if (!token) {
        message.error(t('mediaFetch.directoryBrowser.pleaseLogin'));
        return;
      }

      // 规范化路径，移除多余的前导斜杠
      const normalizedPath = path.replace(/^\/+/, '/');

      const requestData = {
        id: normalizedPath || 'root',  // 添加id字段，使用路径或root
        storage: 'local',
        type: 'dir',
        path: normalizedPath,
        name: ''
      };

      const response = await browseDirectory(requestData, 'name');

      // 显示所有文件和文件夹
      const allFiles = response.data;
      const chonkyFiles = convertToChonkyFiles(allFiles);
      setFiles(chonkyFiles);
    } catch (error) {
      console.error('加载目录失败:', error);
      console.error('错误详情:', error.response);
      const errorMessage = error.response?.data?.detail || error.message || t('mediaFetch.directoryBrowser.unknownError');
      message.error(t('mediaFetch.directoryBrowser.loadDirFailed') + errorMessage);
    } finally {
      setLoading(false);
    }
  };

  // 处理创建文件夹
  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) {
      message.warning(t('mediaFetch.directoryBrowser.folderNameRequired'));
      return;
    }

    try {
      const normalizedCurrentPath = currentPath.replace(/^\/+/, '/');
      const separator = normalizedCurrentPath.includes('\\') ? '\\' : '/';
      const newFolderPath = normalizedCurrentPath ? `${normalizedCurrentPath}${separator}${newFolderName.trim()}` : newFolderName.trim();
      const res = await createFolder(normalizedCurrentPath, newFolderName.trim());
      message.success(res.data.message || t('mediaFetch.directoryBrowser.folderCreated'));
      setCreateFolderVisible(false);
      setNewFolderName('');
      // 定位到新创建的文件夹 - 使用正确的路径分隔符
      setCurrentPath(newFolderPath);
    } catch (error) {
      message.error(t('mediaFetch.directoryBrowser.createFolderFailed') + (error.message || t('mediaFetch.directoryBrowser.unknownError')));
      console.error(error);
    }
  };

  // 处理删除文件夹
  const handleDeleteFolder = async (folderPath) => {
    const normalizedPath = folderPath.replace(/^\/+/, '/');
    const folderName = normalizedPath.split('/').pop() || normalizedPath.split('\\').pop();

    Modal.confirm({
      title: t('mediaFetch.directoryBrowser.confirmDeleteFolder'),
      content: t('mediaFetch.directoryBrowser.deleteFolderContent', { name: folderName }),
      okText: t('mediaFetch.directoryBrowser.delete'),
      okType: 'danger',
      cancelText: t('mediaFetch.directoryBrowser.cancel'),
      onOk: async () => {
        try {
          const res = await deleteFolder(normalizedPath);
          message.success(res.data.message || t('mediaFetch.directoryBrowser.folderDeleted'));
          // 重新加载目录
          await loadDirectory(currentPath);
        } catch (error) {
          console.error('删除文件夹失败:', error);
          const errorMessage = error.response?.data?.detail || error.message || t('mediaFetch.directoryBrowser.unknownError');
          message.error(t('mediaFetch.directoryBrowser.deleteFolderFailed') + errorMessage);
        }
      },
      onCancel: () => {
      },
    });
  };


  // 创建文件夹链
  const folderChain = useMemo(() => createFolderChain(currentPath, t('mediaFetch.directoryBrowser.rootDir')), [currentPath, t]);

  // 创建文件操作（依赖 t）
  const fileActionsMap = useMemo(() => createFileActions(t), [t]);

  // 选择当前目录 / 选择文件
  const handleSelectCurrent = () => {
    if (selectMode === 'file') {
      // 文件选择模式：必须选中一个非目录文件
      if (!selectedFile || selectedFile.isDir) {
        message.warning(t('mediaFetch.directoryBrowser.selectFileFirst'));
        return;
      }
      if (fileFilter && !selectedFile.name?.toLowerCase().endsWith(fileFilter.toLowerCase())) {
        message.warning(t('mediaFetch.directoryBrowser.selectFileFormat', { format: fileFilter }));
        return;
      }
      const pathToSelect = selectedFile.id.replace(/^\/+/, '/');
      onSelect(pathToSelect);
      onClose();
    } else {
      // 目录选择模式（原有逻辑）
      const rawPath = selectedFile ? selectedFile.id : currentPath;
      const pathToSelect = rawPath.replace(/^\/+/, '/');
      onSelect(pathToSelect);
      onClose();
    }
  };

  return (
    <Modal
      className="DirectoryBrowser-modal"
      title={
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          fontSize: '16px',
          fontWeight: 600,
          color: 'var(--color-text)'
        }}>
          <div style={{
            width: '32px',
            height: '32px',
            borderRadius: '8px',
            background: 'var(--color-primary)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'white'
          }}>
            <FolderOpenOutlined style={{ fontSize: '16px' }} />
          </div>
          <span>{t('mediaFetch.directoryBrowser.title')}</span>
        </div>
      }
      open={visible}
      onCancel={onClose}
      width={isMobile ? "95vw" : "60vw"}
      style={{
        margin: isMobile ? '1vh 2.5vw 2vh' : '2vh 20vw 4vh',
        top: isMobile ? '1vh' : '2vh',
        height: isMobile ? '96vh' : '94vh',
        maxWidth: 'none',
        paddingBottom: 0,
        borderRadius: '12px',
        overflow: 'hidden'
      }}
      styles={{
        body: {
          padding: 0,
          height: isMobile ? 'calc(96vh - 100px)' : 'calc(94vh - 120px)',
          overflow: 'hidden',
          background: 'var(--color-bg)'
        }
      }}
      footer={
        <div style={{
          display: 'flex',
          justifyContent: 'flex-end',
          alignItems: 'center',
          gap: '12px',
          padding: '12px 24px',
          background: 'var(--color-card)',
          borderTop: '1px solid var(--color-border)',
          borderRadius: '0 0 12px 12px'
        }}>
          <Button
            onClick={onClose}
            style={{
              borderRadius: '6px',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text-secondary)',
              padding: '6px 16px',
              height: '32px',
              fontSize: '14px'
            }}
          >
            {t('mediaFetch.directoryBrowser.cancel')}
          </Button>
          <Button
            type="primary"
            onClick={handleSelectCurrent}
            disabled={selectMode === 'file' && (!selectedFile || selectedFile.isDir)}
            style={{
              borderRadius: '6px',
              background: 'var(--color-primary)',
              border: 'none',
              fontWeight: 500,
              padding: '6px 16px',
              height: '32px',
              fontSize: '14px'
            }}
          >
            {selectMode === 'file'
              ? (selectedFile && !selectedFile.isDir ? t('mediaFetch.directoryBrowser.selectThisFile') : t('mediaFetch.directoryBrowser.pleaseSelectFile'))
              : (selectedFile && selectedFile.isDir ? t('mediaFetch.directoryBrowser.selectSelectedDir') : t('mediaFetch.directoryBrowser.selectCurrentDir'))
            }
          </Button>
        </div>
      }
      destroyOnClose
      maskClosable={false}
      centered={false}
    >
      <div style={{
        height: '100%',
        position: 'relative',
        overflow: 'hidden'
      }}>
        <FullFileBrowser
          files={files}
          folderChain={folderChain}
          fileActions={[
            // 两端都保留现有按钮，同时都添加创建文件夹功能
            ...(isMobile ? [
              // 手机端保留默认的下拉菜单，并添加创建文件夹
              ChonkyActions.OpenFiles,
              fileActionsMap.CreateFolder,
              fileActionsMap.DeleteFolder,
            ] : [
              // 电脑端保留自定义中文按钮
              fileActionsMap.EnableListView,
              fileActionsMap.EnableGridView,
              fileActionsMap.SortFilesByName,
              fileActionsMap.SortFilesByDate,
              fileActionsMap.SortFilesBySize,
              fileActionsMap.ToggleShowFoldersFirst,
              fileActionsMap.CreateFolder,
              fileActionsMap.DeleteFolder,
            ]),
          ]}
          // 电脑端完全禁用默认action，手机端显示默认action
          disableDefaultFileActions={!isMobile}
          onFileAction={(data) => {

            // 处理鼠标点击选择文件
            if (data.id === 'mouse_click_file' && data.payload.clickType === 'single') {
              const clickedFile = data.payload.file;

              // 如果点击的是已选择的文件，则取消选择；否则选择该文件
              if (selectedFile && selectedFile.id === clickedFile.id) {
                setSelectedFile(null);
              } else {
                setSelectedFile(clickedFile);
              }
            }

            // 处理空白区域点击
            if (data.id === 'change_selection') {
              setSelectedFile(null); // 取消文件选择
            }

            // 处理双击进入文件夹
            if (data.id === ChonkyActions.OpenFiles.id) {
              const { targetFile } = data.payload;
              if (targetFile && FileHelper.isDirectory(targetFile)) {
                const normalizedPath = targetFile.id.replace(/^\/+/, '/');
                setCurrentPath(normalizedPath);
                // 清空选择状态，因为进入了新目录
                setSelectedFile(null);
              }
            }
            // 处理点击面包屑导航
            else if (data.id === ChonkyActions.OpenParentFolder.id) {
              const { targetFile } = data.payload;
              if (targetFile) {
                const normalizedPath = targetFile.id.replace(/^\/+/, '/');
                setCurrentPath(normalizedPath);
                // 清空选择状态，因为进入了新目录
                setSelectedFile(null);
              }
            }
            // 处理创建文件夹
            else if (data.id === fileActionsMap.CreateFolder.id) {
              setCreateFolderVisible(true);
            }
            // 处理删除文件夹
            else if (data.id === fileActionsMap.DeleteFolder.id) {
              // 对于需要选择的action，使用 selectedFilesForAction
              const selectedFiles = data.state.selectedFilesForAction || [];
              const targetFile = selectedFiles.length > 0 ? selectedFiles[0] : null;

              if (targetFile && FileHelper.isDirectory(targetFile)) {
                handleDeleteFolder(targetFile.id);
              } else {
                message.warning(t('mediaFetch.directoryBrowser.selectFolderFirst'));
              }
            }
          }}
          i18n={createChonkyI18n(isMobile, t)}
          defaultFileViewActionId={ChonkyActions.EnableListView.id}
          disableSelection={false}
          disableDragAndDrop={true}
        />

        {/* 创建文件夹对话框 */}
        <Modal
          title={t('mediaFetch.directoryBrowser.newFolderTitle')}
          open={createFolderVisible}
          onOk={handleCreateFolder}
          onCancel={() => {
            setCreateFolderVisible(false);
            setNewFolderName('');
          }}
          okText={t('mediaFetch.directoryBrowser.create')}
          cancelText={t('mediaFetch.directoryBrowser.cancel')}
          width={400}
        >
          <div style={{ marginTop: '16px' }}>
            <Typography.Text>{t('mediaFetch.directoryBrowser.createInCurrentDir')}</Typography.Text>
            <div style={{ marginTop: '12px' }}>
              <Typography.Text type="secondary" style={{ fontSize: '12px' }}>
                {t('mediaFetch.directoryBrowser.currentPath')}{currentPath}
              </Typography.Text>
            </div>
            <div style={{ marginTop: '16px' }}>
              <input
                type="text"
                placeholder={t('mediaFetch.directoryBrowser.folderNamePlaceholder')}
                value={newFolderName}
                onChange={(e) => setNewFolderName(e.target.value)}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  border: '1px solid #d9d9d9',
                  borderRadius: '6px',
                  fontSize: '14px',
                  outline: 'none',
                  boxSizing: 'border-box',
                  backgroundColor: 'white'
                }}
                onKeyPress={(e) => {
                  if (e.key === 'Enter') {
                    handleCreateFolder();
                  }
                }}
              />
            </div>
          </div>
        </Modal>
      </div>
    </Modal>
  );
};

export default DirectoryBrowser;

