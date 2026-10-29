"""自定义Protobuf描述工具；平台字段定义由调用方持有。"""

from typing import Dict, Sequence, Type

from google.protobuf import message_factory
from google.protobuf.descriptor_pb2 import FileDescriptorProto
from google.protobuf.descriptor_pool import DescriptorPool
from google.protobuf.message import Message


def build_protobuf_message_classes(
    file_descriptors: Sequence[FileDescriptorProto],
    message_names: Sequence[str],
) -> Dict[str, Type[Message]]:
    """按依赖顺序注册自定义文件描述，并返回完整消息名对应的类。

    每次调用使用独立描述池，避免不同来源同名协议相互污染。
    非法描述和未知消息名保留Protobuf原始异常，不用版本回退掩盖错误。
    """
    if len(set(message_names)) != len(message_names):
        raise ValueError("Protobuf消息名不能重复")
    if any(not isinstance(name, str) or not name for name in message_names):
        raise ValueError("Protobuf消息名必须是非空字符串")

    pool = DescriptorPool()
    file_names = set()
    for descriptor in file_descriptors:
        if not isinstance(descriptor, FileDescriptorProto):
            raise TypeError("Protobuf文件描述必须是FileDescriptorProto")
        if not descriptor.name or descriptor.name in file_names:
            raise ValueError("Protobuf文件名不能为空或重复")
        file_names.add(descriptor.name)
        pool.Add(descriptor)

    get_message_class = getattr(message_factory, "GetMessageClass", None)
    factory_type = getattr(message_factory, "MessageFactory", None)
    factory = None
    if not callable(get_message_class):
        if not callable(factory_type):
            raise RuntimeError("当前Protobuf不提供消息类构建接口")
        factory = factory_type(pool)
        if not callable(getattr(factory, "GetPrototype", None)):
            raise RuntimeError("当前Protobuf不提供兼容消息工厂接口")

    classes: Dict[str, Type[Message]] = {}
    for name in message_names:
        descriptor = pool.FindMessageTypeByName(name)
        if callable(get_message_class):
            classes[name] = get_message_class(descriptor)
        else:
            classes[name] = factory.GetPrototype(descriptor)
    return classes


__all__ = ["build_protobuf_message_classes"]
