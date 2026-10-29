"""御坂助手宿主工具隔离契约；当前未提供已验证的容器执行后端。"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ContainerRuntimeContract:
    """未来适配器必须验证的条件，配置声明本身不等于安全验证。"""

    runtime: str
    image_digest: str
    runtime_healthy: bool
    user_isolated: bool
    minimal_mounts_verified: bool
    network_disabled_verified: bool

    @property
    def verified(self) -> bool:
        """判断容器隔离最低前提是否全部满足。"""
        return bool(
            self.runtime in ("docker", "podman")
            and self.image_digest.startswith("sha256:")
            and self.runtime_healthy
            and self.user_isolated
            and self.minimal_mounts_verified
            and self.network_disabled_verified
        )


async def execute_host_tool(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """拒绝命令、代码及任意文件请求，绝不回退为宿主 shell。"""
    return {"error": "宿主工具不可用：缺少已验证的隔离容器执行后端"}


def register_host_tools(contract: Optional[ContainerRuntimeContract] = None) -> bool:
    """尚无容器适配器时不注册宿主工具，即使传入声称健康的配置也拒绝。"""
    return False
