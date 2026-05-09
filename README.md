<div align="center">
<img src="web/public/images/logo.png" alt="御坂网络弹幕服务" width="128" style="border-radius: 16px;" />
<h1 align="center" style="margin-top: 0">御坂网络弹幕服务</h1>
<p align="center">
<strong>自托管弹幕聚合与管理服务，兼容弹弹play API</strong>
</p>

[快速开始](https://docs.misaka10876.top/快速开始)
|
[客户端配置](https://docs.misaka10876.top/客户端配置)
|
[元数据配置](https://docs.misaka10876.top/config/元数据源配置)
|
[常见问题](https://docs.misaka10876.top/常见问题)
|
[问题反馈](https://docs.misaka10876.top/问题反馈)

[![GitHub](https://img.shields.io/badge/-GitHub-181717?logo=github)](https://github.com/l429609201/misaka_danmu_server)
![GitHub License](https://img.shields.io/github/license/l429609201/misaka_danmu_server)
![Docker Pulls](https://img.shields.io/docker/pulls/l429609201/misaka_danmu_server)
[![GitHub release (latest SemVer)](https://img.shields.io/github/v/release/l429609201/misaka_danmu_server?color=blue&label=download&sort=semver)](https://github.com/l429609201/misaka_danmu_server/releases/latest)
[![Telegram](https://img.shields.io/badge/Telegram-misaka__danmu__server-blue?logo=telegram)](https://t.me/misaka_danmaku)
[![Docs](https://img.shields.io/badge/Docs-文档-blue?logo=readthedocs)](https://docs.misaka10876.top/)

</div>

---


本项目旨在通过刮削主流视频网站的弹幕，为您自己的媒体库提供一个统一、私有的弹幕API。它自带一个现代化的Web界面，方便您管理弹幕库、搜索源、API令牌和系统设置。



> [!IMPORTANT]
> **按需使用，请勿滥用**
> 本项目旨在作为个人媒体库的弹幕补充工具。所有弹幕数据均实时从第三方公开API或网站获取。请合理使用，避免对源站造成不必要的负担。过度频繁的请求可能会导致您的IP被目标网站屏蔽。

> [!NOTE]
> **网络与地区限制**
> 推荐使用大陆IP使用本项目，如果您在海外地区部署或使用此项目，可能会因网络或地区版权限制导致无法访问这些视频源。建议海外用户配置代理或确保网络环境可以访问国内网站。

## ✨ 核心功能

- **智能匹配**: 通过文件名或元数据（TMDB, TVDB等）智能匹配您的影视文件，提供准确的弹幕。
- **Web管理界面**: 提供一个直观的Web UI，用于：
  - 搜索和手动导入弹幕。
  - 管理已收录的媒体库、数据源和分集。
  - 创建和管理供第三方客户端（如 yamby, hills, 小幻影视, SenPlayer等）使用的API令牌。
  - 配置搜索源的优先级和启用状态。
  - 查看后台任务进度和系统日志。
- **弹幕源管理**: 支持从[资源仓库](docs/弹幕源管理.md)加载或上传离线包来安装弹幕源，支持备份和恢复功能。
- **元数据整合**: 支持与 TMDB, TVDB, Bangumi, Douban, IMDb 集成，丰富您的媒体信息。
- **自动化**: 支持通过 Webhook 接收来自 Sonarr, Radarr, Emby 等服务的通知，实现全自动化的弹幕导入。
- **灵活部署**: 提供 Docker 镜像和 Docker Compose 文件，方便快速部署。

## 其他

### 免责声明

在使用本项目前，请您务必仔细阅读并理解本声明。一旦您选择使用，即表示您已充分理解并同意以下所有条款。

#### 1. 项目性质

- **技术中立性**: 本项目是一个开源的、自托管的技术工具，旨在通过自动化程序从公开的第三方视频网站、公开的API中获取弹幕评论数据。
- **功能范围**: 本工具仅提供弹幕数据的聚合、存储和API访问功能，供用户在个人合法拥有的媒体上匹配和加载，以提升观影体验。
- **非内容提供方**: 本项目不生产、不修改、不存储、不分发任何视频内容本身，所有弹幕内容均来源于第三方平台的公开分享。

#### 2. 用户责任

- **遵守服务条款**: 您理解并同意，抓取第三方网站数据的行为可能违反其服务条款（ToS）。您承诺将自行承担因使用本工具而可能引发的任何风险，包括但不限于来自源网站的警告、账号限制或法律追究。
- **内容风险自负**: 所有弹幕均为第三方平台用户公开发布，其内容（可能包含不当言论、剧透、广告等）的合法性、真实性及安全性由发布者独立负责。您需自行判断并承担查看这些内容可能带来的所有风险。
- **合法合规使用**: 您承诺仅将本工具用于个人学习、研究或非商业用途，并遵守您所在国家/地区的相关法律法规，不得将本工具及获取的数据用于任何非法或侵权活动。

#### 3. 开发者免责

- **内容无关性**: 开发者仅提供技术实现，不参与任何弹幕内容的创作、审核、编辑或推荐，亦无法保证弹幕的准确性、完整性、实时性或质量。
- **服务不保证**: 由于本项目依赖第三方网站的接口和数据结构，开发者无法保证服务的永久可用性。任何因源网站API变更、反爬虫策略升级、网络环境变化或不可抗力导致的服务中断或功能失效，开发者不承担任何责任。
- **免责范围**: 在法律允许的最大范围内，开发者不对以下情况负责：
    - 您因违反第三方网站服务条款而导致的任何损失或法律后果。
    - 您因接触或使用弹幕内容而产生的任何心理或生理不适。
    - 因使用本工具导致的任何直接、间接、偶然或必然的设备损坏或数据丢失。
- **权利保留**: 开发者保留随时修改、更新或终止本项目的权利，恕不另行通知。

---

### 推广须知

- 请不要在 ***B站*** 或中国大陆社交平台发布视频或文章宣传本项目

### 相关文章

- [Docker 部署御坂网络弹幕服务 Misaka Danmu Server](https://catcat.blog/2025/12/misaka-danmu-server-deploy)

### 贡献者

<a href="https://github.com/l429609201/misaka_danmu_server/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=l429609201/misaka_danmu_server" alt="contributors" />
</a>

## Sponsors

### 🤖 Sponsored by [云AI](https://newnei.apifast.top/)

本项目的 AI 辅助匹配功能由 **云AI** 提供支持 —— 一站式 AI API 聚合平台，让你轻松接入主流大模型能力。

#### ✨ 服务优势

* 聚合多家主流大模型 API（OpenAI、Claude、Gemini、DeepSeek 等），一个 Key 即可调用
* 价格实惠，按量计费，适合个人开发者和小团队
* 兼容 OpenAI API 格式，无需修改代码即可无缝接入
* 稳定高可用，支持高并发请求

👉 [立即体验云AI](https://newnei.apifast.top/)

---

## 参考项目

 - 已接入[弹弹play](https://www.dandanplay.com)开放平台 - [弹弹play API文档](https://api.dandanplay.net/swagger/index.html)
 - [danmuku](https://github.com/lyz05/danmaku)
 - [emby-toolkit](https://github.com/hbq0405/emby-toolkit)
 - [swagger-ui](https://github.com/swagger-api/swagger-ui)
 - [Bangumi-syncer](https://github.com/SanaeMio/Bangumi-syncer)
 - [imdbsource](https://github.com/wumode/MoviePilot-Plugins/tree/main/plugins.v2/imdbsource)
 - [MoviePilot](https://github.com/jxxghp/MoviePilot)

---

## 📈 项目 Star 数增长趋势

### Star History

[![Star History Chart](https://api.star-history.com/svg?repos=l429609201/misaka_danmu_server&type=Date)](https://www.star-history.com/#l429609201/misaka_danmu_server&Date)
