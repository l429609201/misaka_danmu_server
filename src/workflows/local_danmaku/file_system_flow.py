"""
本地弹幕 - 文件系统操作 Workflow
处理文件浏览、文件夹创建和删除
"""
import logging
from pathlib import Path
from typing import List, Dict, Any
import os
import stat

from src.schemas.ui_models import FileItem

logger = logging.getLogger(__name__)


async def browse_directory_flow(path: str, sort: str = "name") -> List[FileItem]:
    """
    浏览本地文件系统目录

    Args:
        path: 要浏览的目录路径
        sort: 排序方式 (name/time)

    Returns:
        目录下的子目录和文件列表

    Raises:
        ValueError: 路径非法或不存在
        PermissionError: 无权限访问
    """
    # 安全检查:防止路径遍历攻击
    try:
        path_obj = Path(path).resolve()
    except Exception as e:
        logger.error(f"路径解析失败: {path}, 错误: {e}")
        raise ValueError(f"无效的路径: {path}")

    # 检查路径是否存在
    if not path_obj.exists():
        raise ValueError(f"路径不存在: {path}")

    # 检查是否是目录
    if not path_obj.is_dir():
        raise ValueError(f"路径不是目录: {path}")

    # 获取目录内容
    try:
        items: List[FileItem] = []
        for entry in path_obj.iterdir():
            try:
                # 获取文件信息
                file_stat = entry.stat()
                is_dir = entry.is_dir()

                # 格式化修改时间
                mtime = file_stat.st_mtime

                # 获取文件大小(目录显示为0)
                size = 0 if is_dir else file_stat.st_size

                # 检查是否可读
                readable = os.access(str(entry), os.R_OK)

                items.append(FileItem(
                    name=entry.name,
                    path=str(entry),
                    isDirectory=is_dir,
                    size=size,
                    modifiedTime=mtime,
                    readable=readable
                ))
            except (PermissionError, OSError) as e:
                # 如果某个文件无法访问,记录日志但继续处理其他文件
                logger.warning(f"无法访问文件 {entry}: {e}")
                continue

        # 排序
        if sort == "time":
            items.sort(key=lambda x: x.modifiedTime or 0, reverse=True)
        else:  # 默认按名称排序
            # 目录在前,文件在后,同类按名称排序
            items.sort(key=lambda x: (not x.isDirectory, x.name.lower()))

        return items

    except PermissionError:
        raise PermissionError(f"没有权限访问目录: {path}")
    except Exception as e:
        logger.error(f"浏览目录失败: {path}, 错误: {e}", exc_info=True)
        raise ValueError(f"浏览目录失败: {str(e)}")


async def create_folder_flow(parent_path: str, folder_name: str) -> Dict[str, str]:
    """
    创建新文件夹

    Args:
        parent_path: 父目录路径
        folder_name: 新文件夹名称

    Returns:
        创建结果信息

    Raises:
        ValueError: 路径非法或文件夹已存在
        PermissionError: 无权限创建
    """
    # 防止路径遍历攻击
    if '..' in parent_path or '..' in folder_name:
        raise ValueError("无效的路径")

    # 解析父路径
    try:
        parent_path_obj = Path(parent_path).resolve()
    except Exception as e:
        logger.error(f"父路径解析失败: {parent_path}, 错误: {e}")
        raise ValueError("无效的父路径")

    # 检查父路径是否存在且是目录
    if not parent_path_obj.exists():
        raise ValueError("父路径不存在")

    if not parent_path_obj.is_dir():
        raise ValueError("父路径不是目录")

    # 构造新文件夹路径
    new_folder_path = parent_path_obj / folder_name

    # 检查文件夹是否已存在
    if new_folder_path.exists():
        raise ValueError("文件夹已存在")

    # 创建文件夹
    try:
        new_folder_path.mkdir(parents=True, exist_ok=False)
        logger.info(f"创建了文件夹: {new_folder_path}")
        return {
            "message": "文件夹创建成功",
            "path": str(new_folder_path),
            "name": folder_name
        }
    except PermissionError:
        raise PermissionError("没有权限创建文件夹")
    except Exception as e:
        logger.error(f"创建文件夹失败: {new_folder_path}, 错误: {e}")
        raise ValueError(f"创建文件夹失败: {str(e)}")




async def delete_folder_flow(folder_path: str) -> Dict[str, str]:
    """
    删除指定的文件夹（仅限空文件夹）

    Args:
        folder_path: 要删除的文件夹路径

    Returns:
        删除结果信息

    Raises:
        ValueError: 路径非法、不存在或文件夹非空
        PermissionError: 无权限删除
    """
    # 防止路径遍历攻击
    if '..' in folder_path:
        raise ValueError("无效的文件夹路径")

    # 检查是否是绝对路径（Windows或Unix风格）
    if os.name == 'nt':  # Windows
        # Windows: 允许驱动器字母路径，如 C:\ 或 E:\test
        if not (len(folder_path) >= 3 and folder_path[1:3] == ':\\' and folder_path[0].isalpha()):
            # 如果不是Windows绝对路径，则不允许以 / 或 \ 开头（防止Unix风格路径遍历）
            if folder_path.startswith('/') or folder_path.startswith('\\'):
                raise ValueError("无效的文件夹路径")
    else:  # Unix/Linux
        # Unix: 不允许以 / 开头的绝对路径（防止路径遍历）
        if folder_path.startswith('/'):
            raise ValueError("无效的文件夹路径")

    # 解析文件夹路径
    try:
        folder_path_obj = Path(folder_path).resolve()
    except Exception as e:
        logger.error(f"文件夹路径解析失败: {folder_path}, 错误: {e}")
        raise ValueError("无效的文件夹路径")

    # 检查文件夹是否存在
    if not folder_path_obj.exists():
        raise ValueError("文件夹不存在")

    # 检查是否为目录
    if not folder_path_obj.is_dir():
        raise ValueError("路径不是文件夹")

    # 检查文件夹是否为空（为了安全起见）
    try:
        folder_contents = list(folder_path_obj.iterdir())
        if folder_contents:
            raise ValueError("只能删除空文件夹")
    except PermissionError:
        raise PermissionError("没有权限访问文件夹内容")

    # 删除文件夹
    try:
        folder_path_obj.rmdir()  # 只删除空文件夹
        logger.info(f"删除了文件夹: {folder_path_obj}")
        return {
            "message": "文件夹删除成功",
            "path": str(folder_path_obj)
        }
    except PermissionError:
        raise PermissionError("没有权限删除文件夹")
    except OSError as e:
        if "directory not empty" in str(e).lower():
            raise ValueError("文件夹不为空，无法删除")
        else:
            raise ValueError(f"删除文件夹失败: {str(e)}")
    except Exception as e:
        logger.error(f"删除文件夹失败: {folder_path_obj}, 错误: {e}")
        raise ValueError(f"删除文件夹失败: {str(e)}")
