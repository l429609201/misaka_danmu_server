"""
服务器实例 ID（serverInstanceId）。
"""

import hashlib
import secrets
from typing import Optional

# 归属标记：反解后应能识别出该前缀，确认 ID 由本弹幕库生成
BRAND_MARK = "misaka10876"

# 混淆密钥（占位，可按需修改；随源码公开，仅作归属识别用途）
OBF_KEY = "misaka_danmu_server"


def _keystream(length: int) -> bytes:
    """由 sha256(OBF_KEY) 循环扩展出指定长度的密钥流。"""
    seed = hashlib.sha256(OBF_KEY.encode("utf-8")).digest()
    if length <= 0:
        return b""
    # 循环拼接直到达到所需长度
    buf = bytearray()
    while len(buf) < length:
        buf.extend(seed)
    return bytes(buf[:length])


def _xor(data: bytes) -> bytes:
    ks = _keystream(len(data))
    return bytes(b ^ k for b, k in zip(data, ks))


def generate_server_instance_id(token: Optional[str] = None) -> str:
    """生成带归属标记、可反解的 serverInstanceId（小写 hex 字符串）。

    :param token: 可选，指定内部随机串（便于测试）；默认用 secrets.token_hex(32)。
    """
    token = token or secrets.token_hex(32)
    payload = f"{BRAND_MARK}:{token}".encode("utf-8")
    return _xor(payload).hex()


def parse_server_instance_id(server_instance_id: str) -> Optional[str]:
    """反解 serverInstanceId。

    :return: 校验通过（含 BRAND_MARK 前缀）时返回内部 token；否则返回 None。
    """
    if not server_instance_id:
        return None
    try:
        raw = bytes.fromhex(server_instance_id.strip())
    except (ValueError, TypeError):
        return None
    try:
        payload = _xor(raw).decode("utf-8")
    except UnicodeDecodeError:
        return None
    prefix = f"{BRAND_MARK}:"
    if not payload.startswith(prefix):
        return None
    return payload[len(prefix):]


def is_valid_server_instance_id(server_instance_id: str) -> bool:
    """判断 serverInstanceId 是否为本弹幕库生成的合法带标记 ID。"""
    return parse_server_instance_id(server_instance_id) is not None
