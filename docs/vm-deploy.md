# 在虚拟机里部署微信转发 bot

bot 需要独占鼠标、键盘和屏幕，所以把小号微信和 bot 一起放进虚拟机。宿主机完全不受影响。

## 1. 虚拟机软件

宿主机是 Windows 11 家庭版，没有 Hyper-V，用 **VMware Workstation Pro**（个人免费）：

- 从 Broadcom 官方支持门户下载（需要注册 Broadcom 账号）。不要用来路不明的网盘破解包。
- 备选：VirtualBox（免费、开源），步骤基本一样。

## 2. 虚拟机配置

宿主机：Ryzen 7 5800H，16 GB 内存。C 盘只剩 13 GB，**虚拟机文件放 E 盘**。

| 项目 | 推荐值 | 说明 |
|---|---|---|
| 系统 | Windows 11（或 Windows 10 22H2） | Win11 需在 VMware 里开启 vTPM（加密选“仅加密所需文件”） |
| CPU | 2 核（4 线程更流畅） | 识别主要在云端，本地压力不大 |
| 内存 | 4 GB（Win11 最低要求） | 宿主机还剩 12 GB |
| 硬盘 | 64 GB，放 `E:\VMs\WeChatRelay` | 微信缓存会慢慢变大 |
| 网络 | NAT | 需要能访问 soclaas API |
| 分辨率 | 固定 **1920×1080，缩放 100%** | 和 `relay.json` 的 `window_rect` 对应 |

VMware 里关掉这些（虚拟机设置 → 选项）：

- **客户机隔离 → 取消“启用复制粘贴”和“启用拖放”**。bot 大量使用剪贴板，开着会和宿主机的剪贴板互相覆盖。
- 显示器 → “自动调整客户机大小”关掉，否则窗口缩放会改变布局。

Windows 未激活也能正常运行，只是有水印、不能改个性化设置。

## 3. 虚拟机里的系统设置

1. 装 VMware Tools，然后重启。
2. **不锁屏、不休眠**：锁屏后截图和模拟输入都会失效。
   - 设置 → 账户 → 登录选项：“离开后何时要求重新登录” → 从不。
   - 屏幕保护程序关闭（`setup_vm.ps1` 会关掉休眠和息屏）。
3. **开机自动登录**：运行 `netplwiz`，取消“要使用本计算机，用户必须输入用户名和密码”；或者用微软 Sysinternals 的 Autologon 工具。
4. 安装 Python 3.12+（勾选 “Add to PATH”）和 Git。

## 4. 微信

1. 安装 WeChat 4.x，用手机扫码登录**小号**。
   - 一个号同一时间只能在一台电脑上登录，宿主机上的小号微信会被挤下线，这是正常的。
2. 微信设置：
   - 浅色主题（bot 按浅色主题的颜色识别）。
   - 开机自动启动微信。
   - 主窗口保持打开，不要最小化；bot 启动时会自动把它摆到固定位置和大小。
3. 手机端尽量不要先查看小号消息：手机上读过的消息，电脑上就没有红点，bot 不会转发。

## 5. 部署代码

在虚拟机里：

```powershell
cd C:\
git clone https://github.com/ZitongShi/WeChatBot
cd WeChatBot
```

再从宿主机拷两个不进 git 的文件到虚拟机同样的位置：

- `data\relay\soclaas.env`（API key）
- `relay.json`（可选；没有的话脚本会用示例配置生成）

用**管理员** PowerShell 运行一次：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_vm.ps1
```

它会安装中文 OCR 语言包、关闭休眠/息屏、创建 `.venv`、安装依赖、生成 `relay.json`，最后跑单元测试。

## 6. 校准与试运行

1. 打开微信主窗口，随便点开一个会话。
2. 运行检测（只截图，不点不发）：

   ```powershell
   .\start_relay.ps1 -Check
   ```

   打开 `data\relay\debug\layout.png`，确认：蓝框是会话标题，绿框是消息区，蓝点在输入框里，有红点的会话被紫框标出。
3. 正式运行：

   ```powershell
   .\start_relay.ps1
   ```

   日志在 `logs\relay_console.log` 和 `logs\relay.jsonl`。

## 7. 开机自动运行

虚拟机里（普通 PowerShell 即可）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_autostart.ps1
```

登录 90 秒后自动启动 bot，崩溃会在 15 秒后自动重启。

宿主机开机自动启动虚拟机（可选）：在任务计划程序里新建“登录时”任务，运行

```text
"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe" -T ws start "E:\VMs\WeChatRelay\WeChatRelay.vmx" nogui
```

`nogui` 表示后台运行，不弹出 VMware 窗口。

## 8. 大号上的用法

- 转发格式：`#12【群】群名` 或 `#13【私】联系人`，下面每行是“发送者：内容”。
- 回复：**引用**某条转发消息后直接打字，或者发 `#12 内容`。
- `/list` 查看最近会话，`/help` 查看用法。
- 回复成功后会收到 `✓ 已回复 #12 群名`；打开的会话不对时不会发送，会回 `✗ 回复失败`。
