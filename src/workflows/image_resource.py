"""工作流图片资源接口；实现统一位于图片资源服务。"""

from src.services.image_resource_service import IMAGE_DIR, load_image_bytes, save_public_thumbnail

__all__ = ["IMAGE_DIR", "load_image_bytes", "save_public_thumbnail"]
