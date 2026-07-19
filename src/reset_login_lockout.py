"""
清除全部登录锁定工具。

直接执行即可清除运行中服务内存里的全部 IP 登录屏蔽：
  python -m src.reset_login_lockout

注意：如果服务未运行，内存锁定记录本身已不存在，无需额外处理。
"""

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.core import settings


def main():
    # why：该脚本是本机紧急解锁工具，固定访问回环地址并一次性清除全部记录，避免误用复杂参数。
    url = f"http://127.0.0.1:{settings.server.port}/api/ui/auth/login-lockout"
    try:
        with urlopen(Request(url, method="DELETE"), timeout=5) as response:
            data = json.loads(response.read().decode())
            print("\n" + "=" * 50)
            print(f"✅ {data.get('message', '已清除所有登录锁定')}")
            print(f"   清除数量: {data.get('cleared', 0)}")
            print("=" * 50)
    except HTTPError as error:
        body = error.read().decode()
        print(f"❌ 清除失败 (HTTP {error.code}): {body}")
        sys.exit(1)
    except URLError as error:
        print(f"ℹ️ 服务未运行或无法连接：{error.reason}")
        print("   登录屏蔽只存在服务内存中，服务未运行时已自动清空。")


if __name__ == "__main__":
    main()
