import { useState, useEffect, useMemo } from 'react';
import { Modal, Button, Space, Typography, message } from 'antd';
import { FolderOpenOutlined } from '@ant-design/icons';
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

// 定义中文文件操作
const ChineseActions = {
  EnableListView: defineFileAction({
    ...ChonkyActions.EnableListView,
    button: {
      name: '列表视图',
      toolbar: true,
      contextMenu: false,
      icon: ChonkyActions.EnableListView.button?.icon || 'list',
    },
  }),
  EnableGridView: defineFileAction({
    ...ChonkyActions.EnableGridView,
    button: {
      name: '网格视图',
      toolbar: true,
      contextMenu: false,
      icon: ChonkyActions.EnableGridView.button?.icon || 'th',
    },
  }),
  SortFilesByName: defineFileAction({
    ...ChonkyActions.SortFilesByName,
    button: {
      name: '按名称排序',
      toolbar: true,
      contextMenu: false,
    },
  }),
  SortFilesByDate: defineFileAction({
    ...ChonkyActions.SortFilesByDate,
    button: {
      name: '按日期排序',
      toolbar: true,
      contextMenu: false,
    },
  }),
  SortFilesBySize: defineFileAction({
    ...ChonkyActions.SortFilesBySize,
    button: {
      name: '按大小排序',
      toolbar: true,
      contextMenu: false,
    },
  }),
  ToggleShowFoldersFirst: defineFileAction({
    ...ChonkyActions.ToggleShowFoldersFirst,
    button: {
      name: '文件夹优先',
      toolbar: true,
      contextMenu: false,
    },
  }),
  CreateFolder: defineFileAction({
    ...ChonkyActions.CreateFolder,
    button: {
      name: '新建文件夹',
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
      name: '删除文件夹',
      toolbar: false,
      contextMenu: true,
      icon: 'trash',
    },
  }),
};

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

// 定义中文国际化配置
const createChineseI18n = (isMobile) => ({
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
    'chonky.toolbar.searchPlaceholder': '搜索',
    'chonky.toolbar.visibleFileCount': `{fileCount, plural,
      one {# 个文件}
      other {# 个文件}
    }`,
    'chonky.toolbar.selectedFileCount': `{fileCount, plural,
      =0 {未选}
      one {选#个}
      other {选#个}
    }`,
    'chonky.toolbar.hiddenFileCount': `{fileCount, plural,
      =0 {}
      one {# 已隐藏}
      other {# 已隐藏}
    }`,
    'chonky.fileList.nothingToShow': '这里空空如也！',
    'chonky.contextMenu.browserMenuShortcut': 'Alt+鼠标右键：显示浏览器菜单',
    'chonky.contextMenu.multipleSelection': '已选择 {count} 项',
    'chonky.contextMenu.emptySelection': '未选择任何项目',

    // 文件操作翻译字符串 - 电脑端隐藏actions和options按钮组
    [`chonky.actionGroups.Actions`]: isMobile ? '操作' : '',
    [`chonky.actionGroups.Options`]: isMobile ? '选项' : '',
    [`chonky.actions.${ChonkyActions.OpenParentFolder.id}.button.name`]: '打开上级文件夹',
    [`chonky.actions.${ChonkyActions.CreateFolder.id}.button.name`]: '新建文件夹',
    [`chonky.actions.${ChonkyActions.CreateFolder.id}.button.tooltip`]: '创建新文件夹',
    [`chonky.actions.delete_folder.button.name`]: '删除文件夹',
    [`chonky.actions.delete_folder.button.tooltip`]: '删除选中的文件夹',
    [`chonky.actions.${ChonkyActions.OpenSelection.id}.button.name`]: '打开选中项',
    [`chonky.actions.${ChonkyActions.SelectAllFiles.id}.button.name`]: '全选文件',
    [`chonky.actions.${ChonkyActions.ClearSelection.id}.button.name`]: '清除选择',
    [`chonky.actions.${ChonkyActions.EnableListView.id}.button.name`]: '列表视图',
    [`chonky.actions.${ChonkyActions.EnableGridView.id}.button.name`]: '网格视图',
    [`chonky.actions.${ChonkyActions.SortFilesByName.id}.button.name`]: '按名称排序',
    [`chonky.actions.${ChonkyActions.SortFilesByDate.id}.button.name`]: '按日期排序',
    [`chonky.actions.${ChonkyActions.SortFilesBySize.id}.button.name`]: '按大小排序',
    [`chonky.actions.${ChonkyActions.ToggleHiddenFiles.id}.button.name`]: '隐藏文件',
    [`chonky.actions.${ChonkyActions.ToggleShowFoldersFirst.id}.button.name`]: '文件夹优先',
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
const createFolderChain = (currentPath) => {
  if (!currentPath) {
    return [{ id: 'root', name: '根目录', isDir: true }];
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
  const chain = [{ id: '/', name: '根目录', isDir: true }];
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
        message.error('请先登录');
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
      const errorMessage = error.response?.data?.detail || error.message || '未知错误';
      message.error('加载目录失败：' + errorMessage);
    } finally {
      setLoading(false);
    }
  };

  // 处理创建文件夹
  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) {
      message.warning('请输入文件夹名称');
      return;
    }

    try {
      const normalizedCurrentPath = currentPath.replace(/^\/+/, '/');
      const separator = normalizedCurrentPath.includes('\\') ? '\\' : '/';
      const newFolderPath = normalizedCurrentPath ? `${normalizedCurrentPath}${separator}${newFolderName.trim()}` : newFolderName.trim();
      const res = await createFolder(normalizedCurrentPath, newFolderName.trim());
      message.success(res.data.message || '文件夹创建成功');
      setCreateFolderVisible(false);
      setNewFolderName('');
      // 定位到新创建的文件夹 - 使用正确的路径分隔符
      setCurrentPath(newFolderPath);
    } catch (error) {
      message.error('创建文件夹失败：' + (error.message || '未知错误'));
      console.error(error);
    }
  };

  // 处理删除文件夹
  const handleDeleteFolder = async (folderPath) => {
    const normalizedPath = folderPath.replace(/^\/+/, '/');
    const folderName = normalizedPath.split('/').pop() || normalizedPath.split('\\').pop();

    Modal.confirm({
      title: '确认删除文件夹',
      content: `确定要删除文件夹 "${folderName}" 吗？此操作不可逆。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await deleteFolder(normalizedPath);
          message.success(res.data.message || '文件夹删除成功');
          // 重新加载目录
          await loadDirectory(currentPath);
        } catch (error) {
          console.error('删除文件夹失败:', error);
          const errorMessage = error.response?.data?.detail || error.message || '未知错误';
          message.error('删除文件夹失败：' + errorMessage);
        }
      },
      onCancel: () => {
      },
    });
  };


  // 创建文件夹链
  const folderChain = useMemo(() => createFolderChain(currentPath), [currentPath]);

  // 选择当前目录 / 选择文件
  const handleSelectCurrent = () => {
    if (selectMode === 'file') {
      // 文件选择模式：必须选中一个非目录文件
      if (!selectedFile || selectedFile.isDir) {
        message.warning('请先选择一个文件');
        return;
      }
      if (fileFilter && !selectedFile.name?.toLowerCase().endsWith(fileFilter.toLowerCase())) {
        message.warning(`请选择 ${fileFilter} 格式的文件`);
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
          <span>目录浏览器</span>
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
            取消
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
              ? (selectedFile && !selectedFile.isDir ? '选择此文件' : '请选择文件')
              : (selectedFile && selectedFile.isDir ? '选择选中目录' : '选择当前目录')
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
              ChineseActions.CreateFolder,
              ChineseActions.DeleteFolder,
            ] : [
              // 电脑端保留自定义中文按钮
              ChineseActions.EnableListView,
              ChineseActions.EnableGridView,
              ChineseActions.SortFilesByName,
              ChineseActions.SortFilesByDate,
              ChineseActions.SortFilesBySize,
              ChineseActions.ToggleShowFoldersFirst,
              ChineseActions.CreateFolder,
              ChineseActions.DeleteFolder,
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
            else if (data.id === ChineseActions.CreateFolder.id) {
              setCreateFolderVisible(true);
            }
            // 处理删除文件夹
            else if (data.id === ChineseActions.DeleteFolder.id) {
              // 对于需要选择的action，使用 selectedFilesForAction
              const selectedFiles = data.state.selectedFilesForAction || [];
              const targetFile = selectedFiles.length > 0 ? selectedFiles[0] : null;

              if (targetFile && FileHelper.isDirectory(targetFile)) {
                handleDeleteFolder(targetFile.id);
              } else {
                message.warning('请先选择一个文件夹');
              }
            }
          }}
          i18n={createChineseI18n(isMobile)}
          defaultFileViewActionId={ChonkyActions.EnableListView.id}
          disableSelection={false}
          disableDragAndDrop={true}
        />

        {/* 创建文件夹对话框 */}
        <Modal
          title="新建文件夹"
          open={createFolderVisible}
          onOk={handleCreateFolder}
          onCancel={() => {
            setCreateFolderVisible(false);
            setNewFolderName('');
          }}
          okText="创建"
          cancelText="取消"
          width={400}
        >
          <div style={{ marginTop: '16px' }}>
            <Typography.Text>在当前目录创建新文件夹：</Typography.Text>
            <div style={{ marginTop: '12px' }}>
              <Typography.Text type="secondary" style={{ fontSize: '12px' }}>
                当前路径: {currentPath}
              </Typography.Text>
            </div>
            <div style={{ marginTop: '16px' }}>
              <input
                type="text"
                placeholder="请输入文件夹名称"
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

