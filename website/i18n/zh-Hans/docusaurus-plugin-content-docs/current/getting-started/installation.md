---
sidebar_position: 2
title: "安装"
description: "在标准 Linux、macOS 或 WSL2 上安装此分支（x86_64 / ARM64）"
---

# 安装

使用一行安装命令，两分钟内即可启动并运行 Hermes Agent。

## 快速安装

### 一行安装命令（Linux / macOS / WSL2）

基于 Git 的安装方式，跟踪此分支的 `batumi/live`：

```bash
curl -fsSL https://raw.githubusercontent.com/batumilove/hermes-agent/batumi/live/scripts/install.sh | HERMES_REPO_URL=https://github.com/batumilove/hermes-agent.git bash -s -- --branch batumi/live --channel main
```

### 支持的平台与安装目录

此分支仅支持标准 Linux 和 macOS，架构为 x86_64 或 ARM64；WSL2 内的 Linux 也受支持。
不支持原生 Windows 或 Android/Termux。请勿使用上游的 PowerShell、MSIX、Microsoft Store 或 Termux 安装路线。
完整政策见[平台支持（英文）](https://github.com/batumilove/hermes-agent/blob/batumi/live/website/docs/getting-started/platform-support.md)。

macOS 使用此分支构建的 DMG/ZIP 软件包。`Hermes-Setup.dmg` 引导安装器仅支持 Apple Silicon；
Intel Mac 使用源码安装器或 `darwin-x64` 软件包。Linux 桌面从源码安装后运行 `hermes desktop`。

| 安装方式 | 代码 | 命令入口 | 默认数据目录 |
|---|---|---|---|
| POSIX 源码脚本 | `~/.hermes/hermes-agent/` | `~/.local/bin/hermes` 包装器 | `~/.hermes/` |
| macOS 桌面软件包 | 应用包内部 | 包内启动器 | 平台默认数据目录 |
| Linux Docker | `/opt/hermes/` | 镜像入口和 `hermes` | 挂载的 `/opt/data/` |

`HERMES_HOME` 选择数据目录；`--dir` 单独选择源码目录。请勿删除整个数据目录来修复应用。

### 安装后

重新加载 shell 并开始聊天：

```bash
source ~/.bashrc   # 或：source ~/.zshrc
hermes             # 开始聊天！
```

如需稍后重新配置单项设置，使用以下专用命令：

```bash
hermes model          # 选择 LLM 提供商和模型
hermes tools          # 配置启用的工具
hermes gateway setup  # 配置消息平台
hermes config set     # 设置单个配置项
hermes setup          # 或运行完整的设置向导一次性配置所有内容
```

:::tip 最快路径：Nous Portal
一个订阅涵盖 300+ 个模型以及 [Tool Gateway](../user-guide/features/tool-gateway.md)（网络搜索、图像生成、TTS、云端浏览器）。无需逐一管理各工具的密钥：

```bash
hermes setup --portal
```

该命令一次性完成登录、设置 Nous 为提供商并开启 Tool Gateway。
:::

---

## 前置条件

POSIX 源码脚本需要 Git、curl、tar 和 SHA-256 工具。源码构建还可能需要编译器和系统库。
Hermes 要求 Python 3.14（`>=3.14,<3.15`），工具版本由 `pm/lock.json` 决定。
自包含桌面软件包不要求用户自行编译基础依赖。

:::tip Nix 用户
如果你使用 Nix（在 NixOS、macOS 或 Linux 上），有专门的配置路径，包含 Nix flake、声明式 NixOS 模块和可选容器模式。请参阅 **[Nix & NixOS 配置](./nix-setup.md)** 指南。
:::

---

## 手动 / 开发者安装

如果你想克隆仓库并从源码安装——用于贡献代码、从特定分支运行或完全控制虚拟环境——请参阅贡献指南中的[开发环境配置](../developer-guide/contributing.md)章节。

---

## 非 Sudo / 系统服务用户安装

以目标服务用户运行源码安装脚本。由管理员预先安装构建所需工具和 Chromium 的系统库。
当前脚本不运行 Playwright 的 `--with-deps`，也不提供按发行版选择的 sudo 回退。

安装后，将 `$HOME/.local/bin` 加入服务用户的 PATH，并运行 `hermes doctor`。
请使用脚本生成的包装器，不要硬编码 `venv/bin/hermes`。
Linux 用户服务需要在注销后继续运行时，由管理员为该用户启用 lingering。
详见 [消息 Gateway](../user-guide/messaging/index.md)。

---

## 故障排查

| 问题                        | 解决方案                                                                           |
| --------------------------- | ---------------------------------------------------------------------------------- |
| `hermes: command not found` | 重新加载 shell（`source ~/.bashrc`）或检查 PATH                                    |
| `API key not set`           | 运行 `hermes model` 配置提供商，或 `hermes config set OPENROUTER_API_KEY your_key` |
| 更新后配置丢失              | 运行 `hermes config check`，然后运行 `hermes config migrate`                       |

如需更多诊断信息，运行 `hermes doctor`——它会告诉你确切缺少什么以及如何修复。

## 安装方式自动检测

Hermes 会自动检测安装方式（git 安装程序、Docker 或 NixOS），`hermes update` 会打印对应路径的更新命令。无需设置任何环境变量——检测基于安装目录结构（`~/.hermes/hermes-agent/` 检出、Docker 镜像标记或 Nix store 路径）。`hermes doctor` 也会在其环境摘要中显示检测到的安装方式。
