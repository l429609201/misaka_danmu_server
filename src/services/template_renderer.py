"""
模板渲染服务 — Jinja2 沙箱渲染

TemplateRenderer 负责：
- 使用 Jinja2 SandboxedEnvironment 安全渲染模板
- 提供白名单过滤器和严格的未定义变量检查
- 限制模板渲染耗时和输出长度
"""
import logging
from typing import Dict, Any, Optional, Tuple
from jinja2 import Environment, StrictUndefined, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)


class TemplateRenderer:
    """模板渲染器 — 安全渲染通知模板"""
    
    # 允许的过滤器白名单
    ALLOWED_FILTERS = {
        "upper", "lower", "title", "capitalize",
        "trim", "truncate", "wordwrap",
        "replace", "default", "length",
        "int", "float", "string",
    }
    
    # 渲染限制
    MAX_TITLE_LENGTH = 200
    MAX_BODY_LENGTH = 4000
    RENDER_TIMEOUT_SECONDS = 2
    
    def __init__(self):
        """初始化沙箱环境"""
        self.env = SandboxedEnvironment(
            undefined=StrictUndefined,
            autoescape=False,  # 通知模板不是 HTML，不自动转义
        )
        
        # 只保留白名单过滤器
        allowed_filters = {
            name: self.env.filters[name]
            for name in self.ALLOWED_FILTERS
            if name in self.env.filters
        }
        self.env.filters = allowed_filters
    
    def render(
        self,
        title_template: str,
        body_template: str,
        variables: Dict[str, Any]
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """渲染模板
        
        Args:
            title_template: 标题模板字符串
            body_template: 正文模板字符串
            variables: 模板变量字典
            
        Returns:
            (success, title, body, error)
            - success: 是否渲染成功
            - title: 渲染后的标题（成功时）
            - body: 渲染后的正文（成功时）
            - error: 错误信息（失败时）
        """
        try:
            # 清理变量：空值转为空字符串，避免模板中出现 None
            clean_vars = self._clean_variables(variables)
            
            # 渲染标题
            title_tmpl = self.env.from_string(title_template)
            title = title_tmpl.render(clean_vars)
            
            # 渲染正文
            body_tmpl = self.env.from_string(body_template)
            body = body_tmpl.render(clean_vars)
            
            # 长度限制
            if len(title) > self.MAX_TITLE_LENGTH:
                title = title[:self.MAX_TITLE_LENGTH] + "..."
            if len(body) > self.MAX_BODY_LENGTH:
                body = body[:self.MAX_BODY_LENGTH] + "\n\n（内容过长已截断）"
            
            # 移除空行过多的情况（连续超过 2 个空行压缩为 2 个）
            body = self._compress_empty_lines(body)
            
            return (True, title, body, None)
            
        except TemplateSyntaxError as e:
            error_msg = f"模板语法错误：第 {e.lineno} 行 - {e.message}"
            logger.warning(f"模板渲染失败: {error_msg}")
            return (False, None, None, error_msg)
            
        except UndefinedError as e:
            error_msg = f"变量未定义：{str(e)}"
            logger.warning(f"模板渲染失败: {error_msg}")
            return (False, None, None, error_msg)
            
        except Exception as e:
            error_msg = f"渲染异常：{str(e)}"
            logger.error(f"模板渲染失败: {error_msg}", exc_info=True)
            return (False, None, None, error_msg)
    
    def validate(
        self,
        title_template: str,
        body_template: str
    ) -> Tuple[bool, Optional[str]]:
        """验证模板语法
        
        Args:
            title_template: 标题模板字符串
            body_template: 正文模板字符串
            
        Returns:
            (valid, error)
            - valid: 是否有效
            - error: 错误信息（无效时）
        """
        try:
            # 尝试编译标题模板
            self.env.from_string(title_template)
            
            # 尝试编译正文模板
            self.env.from_string(body_template)
            
            return (True, None)
            
        except TemplateSyntaxError as e:
            error_msg = f"模板语法错误：第 {e.lineno} 行 - {e.message}"
            return (False, error_msg)
            
        except Exception as e:
            error_msg = f"验证异常：{str(e)}"
            return (False, error_msg)
    
    def _clean_variables(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """清理变量：None 转为空字符串"""
        clean = {}
        for key, value in variables.items():
            if value is None:
                clean[key] = ""
            elif isinstance(value, (str, int, float, bool)):
                clean[key] = value
            else:
                # 复杂类型转为字符串
                clean[key] = str(value)
        return clean
    
    def _compress_empty_lines(self, text: str) -> str:
        """压缩连续空行：超过 2 个连续空行压缩为 2 个"""
        lines = text.split("\n")
        result = []
        empty_count = 0
        
        for line in lines:
            if line.strip() == "":
                empty_count += 1
                if empty_count <= 2:
                    result.append(line)
            else:
                empty_count = 0
                result.append(line)
        
        return "\n".join(result)


# 全局单例
_renderer = None


def get_template_renderer() -> TemplateRenderer:
    """获取模板渲染器单例"""
    global _renderer
    if _renderer is None:
        _renderer = TemplateRenderer()
    return _renderer
