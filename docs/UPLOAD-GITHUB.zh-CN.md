# 把这个仓库发布到 GitHub

[English](UPLOAD-GITHUB.md) | [简体中文](UPLOAD-GITHUB.zh-CN.md)

下面的步骤全部在 Windows 的 **Git Bash** 里执行。这里不会碰到已安装的桌宠：
运行中的那份在 `%USERPROFILE%\.config\opencode\pet`，是本目录的**副本**，
所以发布仓库不会改变任何正在运行的桌宠。

---

## 0. 开始之前

你需要：

- **Git for Windows**（装完就有 Git Bash）—— <https://git-scm.com/download/win>
- 一个 **GitHub 账号**
- 别的都不需要。本项目没有构建步骤，也没有需要安装的依赖。

先确认一次：

```bash
git --version
```

---

## 1. 在 GitHub 上建一个空仓库

打开 <https://github.com/new>。

| 字段 | 填什么 |
| --- | --- |
| **Repository name** | `OpenCode-pet` |
| **Description** | A bilingual desktop pet that watches OpenCode's live event stream and tells you what it's doing. |
| **Visibility** | **Public** |
| **Initialize this repository with** | **None** —— 一个勾都不要勾 |

点 **Create repository**。

> 不要勾 "Add a README"、`.gitignore` 或 license。本仓库这三样都已经有了，
> 让 GitHub 自己生成会直接撞车。

---

## 2. 让 Git Bash 找到项目

Git Bash 默认停在用户主目录，先进去：

```bash
cd ~/Desktop/OpenCode-pet
```

确认没走错 —— 下面这条应该打印出 `OpenCode-pet`：

```bash
pwd
```

---

## 3. 做第一次提交

先确认身份。如果从没用过 Git，每台机器执行一次（带 `--global`）；已经配过就跳过。

```bash
git config --global user.name  "Hai-mian-33"
git config --global user.email "your-github-email@example.com"
```

> **请填和你在 GitHub 注册时相同的邮箱。** 不想公开个人邮箱的话，GitHub 可以给你
> 一个私密邮箱：<https://github.com/settings/emails> → *Keep my email addresses private*。

初始化仓库，然后提交：

```bash
git init
git add .
git status
```

**提交前一定要先看 `git status`。** 它应该列出源码、`docs/`、`assets/`、`art/`、
两份 README、两份 CONTRIBUTING 和 `LICENSE`。它**不应该**出现：

- `state/` —— 运行日志
- `pet_state.json` —— 你个人的窗口位置和语言设置
- `__pycache__/` —— Python 字节码
- `下载*.svg` / `AI建议.md` —— 以前散落在根目录的设计参考

如果出现了，先停下，回头检查 `.gitignore`。

然后提交：

```bash
git commit -m "Initial release: OpenCode Pet, a bilingual desktop pet for OpenCode"
```

---

## 4. 关联 GitHub 并推送

```bash
git branch -M main
git remote add origin https://github.com/Hai-mian-33/OpenCode-pet.git
git push -u origin main
```

Git 会问你的 **用户名**，然后问你的 **密码**。

> **这个密码不是你的账号密码。** GitHub 从 2021 年 8 月起就停用了密码认证，
> 这里要粘的是 **Personal Access Token（个人访问令牌）**。
> 如果你还没有，去 <https://github.com/settings/tokens> →
> *Generate new token (classic)* → 勾选 **`repo`** → *Generate token* → 复制保存，
> 它**只显示一次**。
>
> 或者干脆让 Git 替你记住 —— 会自动弹浏览器，点一下就行：
>
> ```bash
> git push -u origin main
> # Username: 你的 GitHub 用户名
> # Password: 你的 token
> ```

---

## 5. 验证

```bash
git remote -v
git status
```

`git status` 应该显示 *nothing to commit, working tree clean*。

打开 <https://github.com/Hai-mian-33/OpenCode-pet> 检查：

- **状态总览图**能正常显示（`docs/images/states-zh.png` 和 `states-en.png`）
- 每份 README 顶部的语言链接能在中英之间正常跳转
- 侧边栏里**没有** `state/` 目录

---

## 以后怎么更新

每次改动之后：

```bash
cd ~/Desktop/OpenCode-pet
git add .
git commit -m "描述这次改了什么"
git push
```

> **如果你改动了 `petcore/` 或 `petui/` 下的任何东西，请顺便重新生成截图并一起提交**，
> 否则文档会和代码对不上：
>
> ```bash
> python opencode_pet.py --selftest
> git add docs/images
> git commit -m "Regenerate documentation images"
> git push
> ```

---

## 出问题了怎么办

**`error: remote origin already exists`**

```bash
git remote set-url origin https://github.com/Hai-mian-33/OpenCode-pet.git
```

**`fatal: not a git repository`**

目录不对。`cd ~/Desktop/OpenCode-pet`，用 `pwd` 确认。

**`Permission denied (publickey)`** —— 你在用 SSH 地址却配的是 HTTPS 远端。
按上面的 `https://` 形式重新设置远端。

**`Updates were rejected`** —— 别人先推了。

```bash
git pull --rebase origin main
git push
```

**想彻底重来**

```bash
cd ~/Desktop/OpenCode-pet
rm -rf .git
git init && git add . && git status
```

你的文件一个都不会少，丢的只是 Git 的提交历史。

---

## 安全提醒

- **发布不影响正在运行的桌宠。** 桌宠跑在
  `%USERPROFILE%\.config\opencode\pet`，那是另一份独立副本。
- **绝对不要提交 `pet_state.json`。** 里面有你的窗口位置、缩放和语言设置。
  `.gitignore` 已经排除了它 —— 万一看到它被暂存，先 `git rm --cached pet_state.json` 再推。
- **`git add .` 会把当前目录里的一切都加上。** 上面第 3 步的 `git status` 复核是
  这份文档里最重要的一行。跳过它，就是个人信息上公网的方式。
