---
sidebar_position: 2.5
title: "Platform Support"
description: "This fork supports standard Linux and macOS on x86_64 and ARM64."
---

# Platform Support

This fork supports **standard Linux and macOS on x86_64 and ARM64**.
Linux running inside **WSL2** follows the Linux installation path.
Native Windows and Android/Termux are unsupported; installers and application
entry points refuse them before starting an installation or session.

## Supported platforms

| Host | Architecture | Installation |
| --- | --- | --- |
| Linux, including WSL2 | x86_64, ARM64 | [Source installer](./installation.md#linux--macos--wsl2) |
| macOS | Apple Silicon (ARM64), Intel (x86_64) | Source installer; native DMG/ZIP desktop bundles |
| Linux containers | x86_64, ARM64 | [Docker](../user-guide/docker.md) |

Linux CI uses Ubuntu. Distribution-specific system libraries are still required;
see [installation prerequisites](./installation.md#prerequisites). Containers are
updated by replacing the image. Nix on Linux and macOS remains a best effort
installation method with package-owned updates.

## Builds and tests

CI schedules Linux and macOS runners only. PM bundle checks cover both architectures
on each OS. Stable desktop releases select both Apple Silicon and Intel macOS
bundles by default. Windows, Microsoft Store and Termux release lanes are refused.

Linux desktop packaging remains disabled in the release workflow. Linux users
can install from source and run `hermes desktop`; no Linux desktop release artifact
is promised. The macOS `Hermes-Setup.dmg` bootstrap remains Apple Silicon only;
Intel Macs use the source installer or a `darwin-x64` desktop bundle.

## Unsupported

- Native Windows; use a standard Linux distribution inside WSL2 instead.
- Android/Termux on every architecture, including Android environments that report Linux.
- Architectures other than x86_64 and ARM64.
- Direct PyPI or Homebrew installation of the agent. Use the included installer.

Upstream Windows and Android implementation files remain in the tree to simplify
upstream merges. Their presence does not imply platform support in this fork.
The policy describes the host running Hermes, independently of remote SSH or
container execution targets and messaging platform integrations.
