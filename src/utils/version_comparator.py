"""
版本比较工具

提供统一的版本比较逻辑，用于判断是否需要更新/加载弹幕源。
"""
import logging
from pathlib import Path
from typing import Optional, Tuple

from src.utils.scraper_version_manager import ScraperVersionManager

logger = logging.getLogger(__name__)


class VersionComparator:
    """版本比较器"""
    
    @staticmethod
    def _collect_hashes(manifest: dict) -> dict:
        """从 manifest 的 sources 中收集 {源名: 哈希} 映射。

        manifest 的 hashes 是按平台分桶的（{platform: hash}），此处只取当前平台，
        跨平台哈希不参与比对（本机只可能部署本平台的二进制）。
        """
        platform_key = ScraperVersionManager.get_platform_key()
        result = {}
        for name, entry in (manifest.get("sources") or {}).items():
            if not isinstance(entry, dict):
                continue
            hashes = entry.get("hashes")
            if isinstance(hashes, dict):
                value = hashes.get(platform_key)
            else:
                value = hashes if isinstance(hashes, str) else None
            if value:
                result[name] = value
        return result

    @staticmethod
    def _diff_by_hash(local_manifest: dict, remote_manifest: dict) -> Tuple[bool, str]:
        """按逐源哈希比对本地与远程 manifest，判断内容是否真的有差异。

        why：测试通道的包版本号长期不变（如一直是 2.3.0），仅比版本号会把已经
        变更的 .so 误判为"已是最新"，导致刚下载好的文件被直接丢弃。
        """
        local_hashes = VersionComparator._collect_hashes(local_manifest)
        remote_hashes = VersionComparator._collect_hashes(remote_manifest)
        if not remote_hashes:
            # 远程无哈希信息，无法据此判断，交由调用方按版本号结论处理
            return (False, "")

        added = sorted(set(remote_hashes) - set(local_hashes))
        changed = sorted(
            name for name in set(remote_hashes) & set(local_hashes)
            if remote_hashes[name] != local_hashes[name]
        )
        if not added and not changed:
            return (False, "")

        parts = []
        if changed:
            parts.append(f"{len(changed)} 个源内容变更({', '.join(changed[:5])}"
                         f"{'...' if len(changed) > 5 else ''})")
        if added:
            parts.append(f"{len(added)} 个新增源({', '.join(added[:5])}"
                         f"{'...' if len(added) > 5 else ''})")
        return (True, "哈希差异: " + "；".join(parts))

    @staticmethod
    def should_update(
        local_dir: Path,
        remote_version: str,
        remote_branch: Optional[str] = None,
        remote_manifest: Optional[dict] = None,
    ) -> Tuple[bool, str]:
        """判断是否需要更新

        Args:
            local_dir: 本地目录（scrapers 或 backup）
            remote_version: 远程版本号
            remote_branch: 远程分支名（可选）
            remote_manifest: 远程/待部署包的 manifest（可选）。
                提供时会在版本号相同的情况下继续按逐源哈希比对，
                以支持版本号固定不变的测试通道。

        Returns:
            (是否需要更新, 原因说明)
        """
        # 加载本地 manifest
        local_manifest = ScraperVersionManager.load_manifest(local_dir)

        # 本地没有 manifest，需要下载
        if not local_manifest:
            return (True, "本地无版本信息")

        # 本地版本号
        local_version = local_manifest.get("version", "unknown")

        # 版本号不同，需要更新
        if local_version != remote_version:
            return (True, f"版本不同 (本地: {local_version}, 远程: {remote_version})")

        # 检查分支（如果提供）
        if remote_branch:
            local_branch = local_manifest.get("branch", "main")
            if local_branch != remote_branch:
                return (True, f"分支不同 (本地: {local_branch}, 远程: {remote_branch})")

        # 版本与分支相同时，进一步按文件哈希比对内容
        # why：测试通道版本号恒定，只比版本号会漏掉真实的文件更新
        if remote_manifest:
            has_diff, reason = VersionComparator._diff_by_hash(local_manifest, remote_manifest)
            if has_diff:
                return (True, f"版本号相同({local_version})但{reason}")

        # 版本、分支、内容都相同，不需要更新
        return (False, f"已是最新版本 ({local_version})")

    @staticmethod
    def compare_versions(version_a: str, version_b: str) -> int:
        """比较两个版本号
        
        Args:
            version_a: 版本号 A
            version_b: 版本号 B
            
        Returns:
            -1: A < B
             0: A == B
             1: A > B
        """
        # 移除 'v' 前缀
        version_a = version_a.lstrip('v')
        version_b = version_b.lstrip('v')
        
        # 相同版本号
        if version_a == version_b:
            return 0
        
        # 尝试按语义化版本比较
        try:
            parts_a = [int(x) for x in version_a.split('.')]
            parts_b = [int(x) for x in version_b.split('.')]
            
            # 补齐长度
            max_len = max(len(parts_a), len(parts_b))
            parts_a.extend([0] * (max_len - len(parts_a)))
            parts_b.extend([0] * (max_len - len(parts_b)))
            
            # 逐段比较
            for a, b in zip(parts_a, parts_b):
                if a < b:
                    return -1
                elif a > b:
                    return 1
            
            return 0
        except (ValueError, AttributeError):
            # 无法解析为语义化版本，按字符串比较
            if version_a < version_b:
                return -1
            elif version_a > version_b:
                return 1
            else:
                return 0
    
    @staticmethod
    def is_newer(version_a: str, version_b: str) -> bool:
        """判断版本 A 是否比版本 B 新
        
        Args:
            version_a: 版本号 A
            version_b: 版本号 B
            
        Returns:
            A > B 时返回 True
        """
        return VersionComparator.compare_versions(version_a, version_b) > 0
    
    @staticmethod
    def format_version_info(manifest: dict) -> str:
        """格式化版本信息为易读字符串
        
        Args:
            manifest: manifest 字典
            
        Returns:
            格式化的版本信息字符串
        """
        if not manifest:
            return "未知版本"
        
        version = manifest.get("version", "unknown")
        branch = manifest.get("branch", "main")
        updated_at = manifest.get("updated_at", "N/A")
        
        info = f"版本 {version}"
        if branch and branch != "main":
            info += f" (分支: {branch})"
        if updated_at and updated_at != "N/A":
            info += f" - 更新于 {updated_at[:19]}"  # 只显示日期和时间部分
        
        return info
