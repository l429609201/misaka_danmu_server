"""
Swagger UI 汉化模块
通过 MutationObserver 注入脚本，动态替换 Swagger UI 中的英文按钮/文本为中文。
"""

from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

# Swagger UI 汉化翻译映射
_CN_TRANSLATIONS = {
    "Authorize": "认证",
    "Try it out": "试一下",
    "Execute": "执行",
    "Clear": "清空",
    "Cancel": "取消",
    "Close": "关闭",
    "Logout": "登出",
    "Available authorizations": "可用的认证方式",
    "Parameters": "参数",
    "Responses": "响应",
    "Response body": "响应内容",
    "Response headers": "响应头",
    "Request body": "请求体",
    "Description": "描述",
    "No description": "暂无描述",
    "Example Value": "示例值",
    "Model": "模型",
    "Loading...": "加载中...",
    "Filter by tag": "按标签筛选",
    "No parameters": "无参数",
    "Required": "必填",
}


def _build_cn_script() -> str:
    """构建汉化注入脚本"""
    entries = ",\n      ".join(
        f"'{k}': '{v}'" for k, v in _CN_TRANSLATIONS.items()
    )
    return f"""
<script>
document.addEventListener('DOMContentLoaded', function() {{
  var map = {{
      {entries}
  }};
  new MutationObserver(function() {{
    document.querySelectorAll('button, .btn, span, label, h4').forEach(function(el) {{
      var t = el.textContent.trim();
      if (map[t] && !el.dataset.cn) {{
        el.textContent = map[t];
        el.dataset.cn = '1';
      }}
    }});
  }}).observe(document.body, {{ childList: true, subtree: true }});
}});
</script>
"""


def get_swagger_ui_html_cn(
    openapi_url: str,
    title: str = "API 文档",
    swagger_js_url: str = "/static/swagger-ui/swagger-ui-bundle.js",
    swagger_css_url: str = "/static/swagger-ui/swagger-ui.css",
    swagger_favicon_url: str = "/static/swagger-ui/favicon-32x32.png",
) -> HTMLResponse:
    """返回汉化后的 Swagger UI HTML 页面"""
    html = get_swagger_ui_html(
        openapi_url=openapi_url,
        title=title,
        swagger_js_url=swagger_js_url,
        swagger_css_url=swagger_css_url,
        swagger_favicon_url=swagger_favicon_url,
        swagger_ui_parameters={
            "docExpansion": "list",
            "filter": True,
            "tryItOutEnabled": True,
        },
    )
    content = html.body.decode()
    content = content.replace("</body>", _build_cn_script() + "</body>")
    return HTMLResponse(content)
