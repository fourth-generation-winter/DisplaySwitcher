# DisplaySwitcher · Endfield 风格 UI 重做 + 真实显示控制对接

## 一、原程序逆向结论
`DisplaySwitcher.exe` 是 **wxPython + ctypes 调 Win32 `ChangeDisplaySettings`** 的单文件程序
（Python 3.13，`pyinstaller --onefile --windowed`）。已提取并反编译 `display_switcher_app.pyc` 验证：

- 显示能力：`get_current_settings` / `EnumDisplaySettings` / `switch_mode`（分辨率+刷新率）
- 已有：切换时确认（`ConfirmDialog` + 自动回退计时）、`--switch` 命令行、桌面快捷方式
- **缺失（已在程序内新增）**：命名配置方案持久化、开机自启（注册表）

## 二、本次改造（前端 UI ↔ 真实程序 对接）
采用用户选定的 **原生内嵌窗口** 方案：沿用原 wxPython 程序，用 `wx.WebView` 渲染前端 HTML UI，
通过 **本地 REST API（127.0.0.1）** 做 JS↔Python 桥接，调用真实 Win32 显示控制。

### 文件结构
```
display-switcher/
├── index.html                 (旧版纯前端原型，仅供预览)
├── overview.md
└── app/                       ← 新程序工程
    ├── display_switcher_app.py  ← 程序本体（显示逻辑 + 配置 + 注册表 + REST + wx.WebView 窗口）
    ├── DisplaySwitcher.spec
    ├── webroot/
    │   ├── index.html           ← Endfield 风格 UI（数据层改为调用 API）
    │   ├── api.js               ← 前端桥接层（fetch REST）
    │   └── (css/脚本内联)
    └── dist/DisplaySwitcher.exe ← 打包产物（原生窗口 + 内嵌 HTML + 真实控制）
```

### 功能映射
| 前端模块 | 真实后端 | 状态 |
|---|---|---|
| 控制台：当前显示器 + 分辨率/刷新率芯片 + 应用 | `get_current_settings` / `get_available_modes` / `switch_mode` | ✅ 真实生效 |
| 配置方案：自定义名称/分辨率/刷新率，应用/删除 | `/api/profile*` + JSON 配置持久化 | ✅ 新增并持久化 |
| 设置·偏好：开机自启 | 注册表 `HKCU\...\Run` | ✅ 新增 |
| 设置·偏好：切换时确认 | 应用后 15s 自动回退 + 前端保持/回退模态 | ✅ 保留并强化 |
| 设置·外观：粒子/辉光/HUD配色/背景上传 | 纯前端视觉 + `/api/settings` 持久化 | ✅ |
| 命令行 | `DisplaySwitcher.exe --switch 1920 1080 60` | ✅ 保留 |

## 三、REST API（前端↔程序）
- `GET  /api/state`            当前分辨率/刷新率、全部可用模式、方案列表、设置
- `POST /api/apply`            应用分辨率/刷新率（支持确认回退）
- `POST /api/profile`          新建方案
- `POST /api/profile/apply`    应用方案
- `DELETE /api/profile/<id>`   删除方案
- `GET/POST /api/settings`     读取/更新设置（自启/确认/粒子/辉光/配色）
- `POST /api/autostart`        开关注册表自启
- `POST /api/confirm` `POST /api/revert`  确认保持 / 回退

## 四、验证
- 后端 REST 已无界面实测：返回真实显示信息（如本机 `1920×1080 @ 240Hz` 及全部模式），
  方案增删、设置持久化、静态资源均正常。
- 前端 JS / Python 语法校验通过。
- 已用 PyInstaller 打包为原生 exe（窗口内嵌 HTML，真实调用显示 API）。

## 五、使用 / 构建
直接运行 `dist/DisplaySwitcher.exe` 即为新程序（原生窗口 + Endfield UI + 真实切换）。
重新构建（**必须**带以下参数，否则会漏打包 wx 导致双击无反应）：
```
cd display-switcher/app
pyinstaller --onefile --windowed --name DisplaySwitcher ^
  --add-data "webroot;webroot" ^
  --add-binary "<venv>/Lib/site-packages/wx/WebView2Loader.dll;." ^
  --hidden-import wx --hidden-import wx.html2 ^
  display_switcher_app.py
```
注意：原生窗口使用系统 WebView2（Win10/11 一般自带）。原 `DisplaySwitcher.exe`（桌面）未被修改。

## 六、已知问题修复记录
- **症状**：双击打包后的 exe 无反应 / 一闪而过。
- **根因**：第一次打包时构建环境的 venv 未真正装好 wxPython，PyInstaller 因 `import wx` 是函数内懒加载而**静默跳过**了 wx 模块，生成的 exe 里根本没有 wx。windowed 模式吞掉异常，表现就是"点了没用"。server-only 模式不 import wx 故此前测试"正常"，掩盖了问题。
- **修复**：在装好 wxPython 4.3.1 的 venv 重新打包，并显式 `--add-binary` 打入 `WebView2Loader.dll`、加 `--hidden-import`。同时给程序加了：① 错误日志（`%APPDATA%/DisplaySwitcher/error.log`）；② WebView 充满窗口的 sizer；③ WebView2 缺失时弹窗提示并**自动回退到默认浏览器**打开控制台，保证可用性。

## 七、遗留说明
- 真实分辨率/刷新率切换需在**带显示器的真实 Windows** 上验证（沙箱无 GUI 仅验证了后端 API、WebView 初始化与打包）。
- 切换时确认回退以 15 秒为限，超时自动还原，避免黑屏风险。
- 若用户机器未安装 WebView2 运行时，程序会自动用默认浏览器打开 UI（仍可用），如需原生窗口请安装 Microsoft Edge WebView2 运行时。
