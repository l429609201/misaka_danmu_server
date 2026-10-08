"""
Swagger UI 汉化模块
通过注入脚本实现多语言切换（简体中文/繁体中文/English），持续翻译防止回退。
"""

from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

# 多语言翻译映射
_TRANSLATIONS = {
    "zh-CN": {
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
        "Schema": "数据结构",
        "Model": "模型",
        "Loading...": "加载中...",
        "Filter by tag": "按标签筛选",
        "No parameters": "无参数",
        "Required": "必填",
        "Try it out ": "试一下",
        "Server response": "服务器响应",
        "Code": "状态码",
        "Details": "详情",
        "Response": "响应",
        "Curl": "Curl命令",
        "Request URL": "请求地址",
        "Undocumented": "未记录",
        "Media type": "媒体类型",
        "Controls Accept header.": "控制Accept请求头",
        "Example": "示例",
        "Value": "值",
        "Name": "名称",
        "Type": "类型",
        "In": "位置",
        "Send empty value": "发送空值",
        "Array of": "数组",
        "Default value": "默认值",
        "Possible values": "可选值",
        "Successful operation": "操作成功",
        "Validation Error": "验证错误",
    },
    "zh-TW": {
        "Authorize": "認證",
        "Try it out": "試一下",
        "Execute": "執行",
        "Clear": "清空",
        "Cancel": "取消",
        "Close": "關閉",
        "Logout": "登出",
        "Available authorizations": "可用的認證方式",
        "Parameters": "參數",
        "Responses": "回應",
        "Response body": "回應內容",
        "Response headers": "回應標頭",
        "Request body": "請求主體",
        "Description": "描述",
        "No description": "暫無描述",
        "Example Value": "範例值",
        "Schema": "資料結構",
        "Model": "模型",
        "Loading...": "載入中...",
        "Filter by tag": "按標籤篩選",
        "No parameters": "無參數",
        "Required": "必填",
        "Try it out ": "試一下",
        "Server response": "伺服器回應",
        "Code": "狀態碼",
        "Details": "詳情",
        "Response": "回應",
        "Curl": "Curl指令",
        "Request URL": "請求網址",
        "Undocumented": "未記錄",
        "Media type": "媒體類型",
        "Controls Accept header.": "控制Accept請求標頭",
        "Example": "範例",
        "Value": "值",
        "Name": "名稱",
        "Type": "類型",
        "In": "位置",
        "Send empty value": "發送空值",
        "Array of": "陣列",
        "Default value": "預設值",
        "Possible values": "可選值",
        "Successful operation": "操作成功",
        "Validation Error": "驗證錯誤",
    },
}


def _build_i18n_script() -> str:
    """构建多语言切换注入脚本"""
    import json
    translations_json = json.dumps(_TRANSLATIONS, ensure_ascii=False)
    return """
<style>
#lang-switcher {
  display: inline-flex; gap: 4px; background: #fff; border: 1px solid #ddd;
  border-radius: 4px; padding: 2px; box-shadow: 0 1px 4px rgba(0,0,0,.08);
  margin-right: 12px; vertical-align: middle;
}
#lang-switcher button {
  border: none; background: transparent; padding: 4px 8px;
  cursor: pointer; font-size: 12px; border-radius: 3px; color: #555;
}
#lang-switcher button.active {
  background: #49cc90; color: #fff;
}
</style>
<script>
(function() {
  var TRANSLATIONS = """ + translations_json + """;
  var currentLang = localStorage.getItem('swagger_lang') || 'zh-CN';

  function getMap(lang) { return TRANSLATIONS[lang] || {}; }

  function getReverseMap() {
    var rev = {};
    Object.keys(TRANSLATIONS).forEach(function(lang) {
      var m = TRANSLATIONS[lang];
      Object.keys(m).forEach(function(en) { rev[m[en]] = en; });
    });
    return rev;
  }
  var reverseMap = getReverseMap();

  function translate() {
    if (currentLang === 'en') {
      document.querySelectorAll('[data-i18n-original]').forEach(function(el) {
        el.textContent = el.dataset.i18nOriginal;
      });
      return;
    }
    var map = getMap(currentLang);
    var sel = 'button, .btn, span, label, h4, h5, .opblock-summary-description, td, small';
    document.querySelectorAll(sel).forEach(function(el) {
      if (el.children.length > 0 && el.querySelector('button, span, svg')) return;
      if (el.id === 'lang-switcher' || el.closest('#lang-switcher')) return;
      var text = el.textContent.trim();
      if (map[text]) {
        el.dataset.i18nOriginal = text;
        el.textContent = map[text];
      } else if (reverseMap[text] && map[reverseMap[text]]) {
        el.dataset.i18nOriginal = reverseMap[text];
        el.textContent = map[reverseMap[text]];
      }
    });
  }

  // 注入语言切换按钮到 Authorize 按钮左边
  function injectSwitcher() {
    if (document.getElementById('lang-switcher')) return true;
    var authWrapper = document.querySelector('.auth-wrapper');
    if (!authWrapper) return false;
    var switcher = document.createElement('div');
    switcher.id = 'lang-switcher';
    switcher.innerHTML = '<button data-lang="zh-CN">简体</button><button data-lang="zh-TW">繁體</button><button data-lang="en">EN</button>';
    authWrapper.insertBefore(switcher, authWrapper.firstChild);

    switcher.addEventListener('click', function(e) {
      if (e.target.tagName === 'BUTTON' && e.target.dataset.lang) {
        currentLang = e.target.dataset.lang;
        localStorage.setItem('swagger_lang', currentLang);
        document.querySelectorAll('[data-i18n-original]').forEach(function(el) {
          el.textContent = el.dataset.i18nOriginal;
          el.removeAttribute('data-i18n-original');
        });
        updateButtons();
        translate();
      }
    });
    updateButtons();
    return true;
  }

  function updateButtons() {
    document.querySelectorAll('#lang-switcher button').forEach(function(btn) {
      btn.classList.toggle('active', btn.dataset.lang === currentLang);
    });
  }

  // 持续尝试注入 + 翻译
  var timer = setInterval(function() {
    injectSwitcher();
    translate();
  }, 800);
})();
</script>
"""


def get_swagger_ui_html_cn(
    openapi_url: str,
    title: str = "API 文档",
    swagger_js_url: str = "/static/swagger-ui/swagger-ui-bundle.js",
    swagger_css_url: str = "/static/swagger-ui/swagger-ui.css",
    swagger_favicon_url: str = "/static/swagger-ui/favicon-32x32.png",
) -> HTMLResponse:
    """返回带多语言切换的 Swagger UI HTML 页面"""
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
    content = content.replace("</body>", _build_i18n_script() + "</body>")
    return HTMLResponse(content)
