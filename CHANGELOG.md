# 更新日志

## v2.7.9

### 新功能


### 修复

- **修复同步日历问题** — group_by(AnimeSource.id) 语法 mysql宽松模式不报错，PG要求所有非聚合列都在 GROUP BY 中。
- **内置代理范围修复** — 修复BGM的OAuth等部分操作未在内置代理范围内的问题。
- **修复ARM64全量替换导致容器无限重启** — asyncio.to_thread在ARM64+uvloop环境下触发native segfault，改为同步写入；全量替换前增加版本校验；失败自动降级增量更新并从备份恢复。
- **防御性弹幕源加载** — 加载.so前检查文件大小，跳过0字节损坏文件，避免ImportError崩溃。

### 优化

- **UI优化** — 部分UI优化。

### 维护
