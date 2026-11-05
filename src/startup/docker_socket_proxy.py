#!/usr/bin/env python3
"""
Docker Socket Proxy - 安全地将 Unix Socket 代理到 localhost TCP

参考 MoviePilot 的 nginx 代理方案，使用 Python 实现轻量级代理。
安全措施：
1. 只监听 127.0.0.1（不暴露到外部网络）
2. 以 root 身份运行（在 exec.sh 中 gosu 切换前启动）
3. 单进程处理，避免多线程复杂性
4. 超时保护，防止连接泄漏
5. 错误处理和日志记录

使用方式：
    在 exec.sh 中以 root 身份后台启动：
    python3 /app/docker_socket_proxy.py &
"""
import socket
import select
import logging
import sys
import os

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('docker-proxy')

# 配置参数
UNIX_SOCKET = '/var/run/docker.sock'
TCP_HOST = '127.0.0.1'  # 只监听本地，不暴露到外部
TCP_PORT = 38379       # 与 MoviePilot 保持一致
BUFFER_SIZE = 8192
TIMEOUT = 300          # 5分钟超时
MAX_CONNECTIONS = 10   # 最大连接数


def proxy_bidirectional(client_sock, unix_sock):
    """
    双向代理数据流
    
    Args:
        client_sock: TCP 客户端连接
        unix_sock: Unix Socket 连接
        
    Returns:
        bool: 代理是否正常结束
    """
    sockets = [client_sock, unix_sock]
    
    try:
        while True:
            # 使用 select 等待任一 socket 可读，带超时
            readable, _, exceptional = select.select(sockets, [], sockets, TIMEOUT)
            
            # 超时或异常，关闭连接
            if not readable or exceptional:
                if not readable:
                    logger.debug("连接超时，关闭")
                else:
                    logger.warning("Socket 异常，关闭")
                return False
            
            for sock in readable:
                try:
                    data = sock.recv(BUFFER_SIZE)
                    
                    # 连接关闭
                    if not data:
                        return True
                    
                    # 转发到对端
                    target = unix_sock if sock is client_sock else client_sock
                    target.sendall(data)
                    
                except socket.error as e:
                    logger.debug(f"数据传输错误: {e}")
                    return False
                    
    except Exception as e:
        logger.error(f"代理过程异常: {e}")
        return False


def handle_connection(client_sock, client_addr):
    """
    处理单个客户端连接
    
    Args:
        client_sock: 客户端 socket
        client_addr: 客户端地址
    """
    unix_sock = None
    
    try:
        # 验证客户端地址是否为本地
        if client_addr[0] != '127.0.0.1':
            logger.warning(f"拒绝非本地连接: {client_addr[0]}")
            return
        
        # 连接到 Docker Unix Socket
        unix_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        unix_sock.settimeout(10)  # 连接超时 10 秒
        unix_sock.connect(UNIX_SOCKET)
        unix_sock.settimeout(None)  # 取消超时，后续用 select
        
        logger.debug(f"新连接: {client_addr}")
        
        # 开始双向代理
        proxy_bidirectional(client_sock, unix_sock)
        
    except FileNotFoundError:
        logger.error(f"Docker socket 不存在: {UNIX_SOCKET}")
    except PermissionError:
        logger.error(f"无权访问 Docker socket: {UNIX_SOCKET}")
    except socket.timeout:
        logger.warning("连接 Docker socket 超时")
    except Exception as e:
        logger.error(f"处理连接失败: {e}")
    finally:
        # 确保清理资源
        if unix_sock:
            try:
                unix_sock.close()
            except:
                pass
        try:
            client_sock.close()
        except:
            pass


def check_requirements():
    """
    启动前检查
    
    Returns:
        bool: 检查是否通过
    """
    # 检查 Docker socket 是否存在
    if not os.path.exists(UNIX_SOCKET):
        logger.error(f"Docker socket 不存在: {UNIX_SOCKET}")
        return False
    
    # 检查是否为 socket 类型
    import stat
    mode = os.stat(UNIX_SOCKET).st_mode
    if not stat.S_ISSOCK(mode):
        logger.error(f"{UNIX_SOCKET} 不是 Unix Socket")
        return False
    
    # 检查是否可访问（需要 root 权限）
    if not os.access(UNIX_SOCKET, os.R_OK | os.W_OK):
        logger.error(f"无权访问 {UNIX_SOCKET}（需要 root 权限）")
        return False
    
    return True


def main():
    """主函数"""
    logger.info("=== Docker Socket Proxy 启动 ===")
    logger.info(f"代理: {UNIX_SOCKET} → tcp://{TCP_HOST}:{TCP_PORT}")
    
    # 启动前检查
    if not check_requirements():
        logger.error("启动前检查失败，退出")
        sys.exit(1)
    
    # 创建 TCP 监听 socket
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((TCP_HOST, TCP_PORT))
        server.listen(MAX_CONNECTIONS)
        logger.info(f"✓ 代理服务已启动，监听 {TCP_HOST}:{TCP_PORT}")
        
    except PermissionError:
        logger.error(f"无权绑定端口 {TCP_PORT}")
        sys.exit(1)
    except OSError as e:
        logger.error(f"无法启动服务: {e}")
        sys.exit(1)
    
    # 主循环：接受连接并处理
    connection_count = 0
    try:
        while True:
            try:
                client_sock, client_addr = server.accept()
                connection_count += 1
                
                # 单线程同步处理（Docker 操作通常是短连接，无需多线程）
                handle_connection(client_sock, client_addr)
                
                if connection_count % 100 == 0:
                    logger.info(f"已处理 {connection_count} 个连接")
                    
            except KeyboardInterrupt:
                logger.info("收到中断信号，正在关闭...")
                break
            except Exception as e:
                logger.error(f"接受连接失败: {e}")
                
    finally:
        server.close()
        logger.info(f"代理服务已停止（共处理 {connection_count} 个连接）")


if __name__ == '__main__':
    main()
