# DisplaySwitcher

一个 Windows 平台的显示器分辨率 / 刷新率切换工具，采用 Endfield 风格的原生内嵌 UI。

> 真实调用 Win32 `ChangeDisplaySettings`，支持命名方案、切换确认回退、开机自启与外观自定义。

## ✨ 功能特性

- **真实显示控制**：读取当前分辨率 / 刷新率，一键切换（调用系统显示 API）
- **命名配置方案**：自定义名称 / 分辨率 / 刷新率，持久化保存，一键应用 / 删除
- **安全切换**：应用后 15 秒确认窗口，超时自动回退，避免黑屏风险
- **开机自启**：通过注册表 `HKCU\...\Run` 开关
- **外观自定义**：粒子 / 辉光 / HUD 配色 / 背景上传（设置持久化）
- **命令行模式**：`DisplaySwitcher.exe --switch 1920 1080 60`
- **单实例保护**：重复打开时自动聚焦已运行窗口，不会叠加多个进程
- **自动检查更新**：启动后静默检测 GitHub Release，发现新版本弹出居中 HUD 提示（可忽略，忽略后同版本不再打扰）
- **下载管理器**：「前往下载」提供两种方式——跳转 GitHub 自行下载，或由软件代为下载；代下时先直连 GitHub，速度过低自动切换镜像代理（`mirror.ghproxy.com`），带实时进度条 / 速度 / 已用源显示，完成后可一键打开所在文件夹
- **外链打开**：关于页仓库链接等外链点击后在系统默认浏览器打开
- **关于页**：作者、版本、许可证信息

## 🛠 技术栈

- Python 3.13 + wxPython 4.3.1
- 原生窗口内嵌 WebView2 渲染 HTML UI
- 本地 REST API（127.0.0.1）做 JS ↔ Python 桥接

## 📦 下载与安装

从 [Releases](https://github.com/fourth-generation-winter/DisplaySwitcher/releases) 下载最新 `DisplaySwitcher.exe`，双击运行即可（需 Windows 10 / 11 自带或已安装的 Microsoft Edge WebView2 运行时）。

> 若系统未安装 WebView2 运行时，程序会自动用默认浏览器打开控制台 UI，仍可正常使用。

## 🔧 从源码构建

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
cd app
pyinstaller DisplaySwitcher.spec --name DisplaySwitcher
# 产物：app/dist/DisplaySwitcher.exe
```

`DisplaySwitcher.spec` 已内置必要参数（`webroot` 数据、`WebView2Loader.dll`、`wx` 隐藏导入）。
**注意**：若手动打包，必须包含 `--add-data "webroot;webroot"`、`--add-binary "<venv>/Lib/site-packages/wx/WebView2Loader.dll;."` 与 `--hidden-import wx --hidden-import wx.html2`，否则打包后双击无反应。

## 🖥 使用

- 双击运行，在「控制台」选择分辨率 / 刷新率后点击应用。
- 命令行：`DisplaySwitcher.exe --switch 1920 1080 60`

## 🔌 REST API（程序内部）

| 端点 | 说明 |
|---|---|
| `GET  /api/state` | 当前分辨率/刷新率、可用模式、方案列表、设置 |
| `POST /api/apply` | 应用分辨率/刷新率（支持确认回退） |
| `POST /api/profile` | 新建方案 |
| `POST /api/profile/apply` | 应用方案 |
| `DELETE /api/profile/<id>` | 删除方案 |
| `GET/POST /api/settings` | 读取/更新设置（自启/确认/粒子/辉光/配色） |
| `POST /api/autostart` | 开关注册表自启 |
| `POST /api/confirm` `POST /api/revert` | 确认保持 / 回退 |
| `GET  /api/update/check` | 检测 GitHub Release 最新版本（当前版本 / 是否有更新 / 下载链接） |
| `POST /api/update/download` | 启动后台下载（body: `{url}`），文件保存到下载文件夹 |
| `GET  /api/update/progress` | 轮询下载进度（已下载 / 总大小 / 当前源 / 完成 / 错误） |
| `POST /api/update/cancel` | 取消进行中的下载 |
| `GET  /api/open_url` | 在系统默认浏览器打开外链（仓库 / Release 页） |
| `GET  /api/open_folder` | 打开文件所在文件夹（`explorer /select`） |

## 📝 说明

- 真实分辨率 / 刷新率切换需在带显示器的 Windows 上验证。
- 根目录 `index.html` 为早期纯前端原型（仅供预览），实际程序 UI 位于 `app/webroot/`。
- 「关于」页的「检查更新」与启动时的静默检测都会调用 GitHub Release API（`fourth-generation-winter/DisplaySwitcher`）：有更新时弹出居中 HUD 提示，「前往下载」可选择「跳转 GitHub 自行下载」或「由软件代为下载」；代下走直连 GitHub，若前 ~4 秒速度过低（<300KB）自动切换到镜像代理 `mirror.ghproxy.com`，进度弹窗实时显示百分比 / 速度 / 已用源（直连 / 镜像），完成后可一键打开下载文件夹。网络不可达时提示失败，不会反复打扰。

## 📄 许可证

MIT © 2026 [@Asepacehpe](https://github.com/fourth-generation-winter)
