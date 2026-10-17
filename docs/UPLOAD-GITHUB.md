# Publishing this repository to GitHub

[English](UPLOAD-GITHUB.md) | [简体中文](UPLOAD-GITHUB.zh-CN.md)

Everything below runs in **Git Bash** on Windows. Nothing here touches the
installed pet: the live copy at `%USERPROFILE%\.config\opencode\pet` is a
*copy* of this tree, so publishing the repository changes nothing about the pet
that is already running.

---

## 0. Before you start

You need:

- **Git for Windows** (this gives you Git Bash) — <https://git-scm.com/download/win>
- A **free GitHub account**
- Nothing else. The project has no build step and no dependencies to install in
  order to be published.

Check your Git installation once:

```bash
git --version
```

---

## 1. Create the empty repository on GitHub

Open <https://github.com/new>.

| Field | Value |
| --- | --- |
| **Repository name** | `OpenCode-pet` |
| **Description** | A bilingual desktop pet that watches OpenCode's live event stream and tells you what it's doing. |
| **Visibility** | **Public** |
| **Initialize this repository with** | **None** — leave every box unchecked |

> Leave "Add a README", `.gitignore` and a license unchecked. This repository
> already has all three, and letting GitHub add its own would collide with them.

Click **Create repository**.

---

## 2. Point Git Bash at the project

Git Bash starts in your home folder. Move to the project:

```bash
cd ~/Desktop/OpenCode-pet
```

Confirm you are in the right place — this should print `OpenCode-pet`:

```bash
pwd
```

---

## 3. Make the first commit

Check who you are. If you have never used Git, set this once per machine
(the `--global` flag). Skip it if you already have a username and email set.

```bash
git config --global user.name  "Hai-mian-33"
git config --global user.email "your-github-email@example.com"
```

> **Use the same email you registered on GitHub.** If you would rather not
> publish your personal address, GitHub can give you a private one:
> <https://github.com/settings/emails> → *Keep my email addresses private*.

Initialise the repository, then commit everything:

```bash
git init
git add .
git status
```

**Read `git status` before committing.** It should list the source files,
`docs/`, `assets/`, `art/`, both READMEs, both CONTRIBUTINGs and `LICENSE`.
It must **not** list:

- `state/` — runtime logs
- `pet_state.json` — your personal window position and language
- `__pycache__/` — Python bytecode
- `download*.svg` / `AI建议.md` — the old loose design references

If any of those appear, stop and re-check `.gitignore`.

Then make the commit:

```bash
git commit -m "Initial release: OpenCode Pet, a bilingual desktop pet for OpenCode"
```

---

## 4. Connect to GitHub and push

```bash
git branch -M main
git remote add origin https://github.com/Hai-mian-33/OpenCode-pet.git
git push -u origin main
```

Git asks for your **username** and then your **password**.

> **The password is not your account password.** GitHub retired password
> authentication in August 2021. Paste a **Personal Access Token** instead.
> If you do not have one, create it at
> <https://github.com/settings/tokens> → *Generate new token (classic)* →
> tick **`repo`** → *Generate token* → copy it once. GitHub shows it a single
> time.
>
> Or let Git remember it for you — a browser window opens and you click through:
>
> ```bash
> git push -u origin main
> # Username: your GitHub username
> # Password: your token
> ```

---

## 5. Confirm

```bash
git remote -v
git status
```

`git status` should say *nothing to commit, working tree clean*.

Open <https://github.com/Hai-mian-33/OpenCode-pet> and check:

- the **state sheet** renders (`docs/images/states-zh.png` and `states-en.png`)
- the language links at the top of each README switch between the two files
- the sidebar shows no `state/` folder

---

## Updating it later

After any change:

```bash
cd ~/Desktop/OpenCode-pet
git add .
git commit -m "Describe what changed"
git push
```

> **If you changed anything under `petcore/` or `petui/`, also regenerate the
> screenshots** and commit them, or the documentation will drift from the code:
>
> ```bash
> python opencode_pet.py --selftest
> git add docs/images
> git commit -m "Regenerate documentation images"
> git push
> ```

---

## If something goes wrong

**`error: remote origin already exists`**

```bash
git remote set-url origin https://github.com/Hai-mian-33/OpenCode-pet.git
```

**`fatal: not a git repository`**

You are in the wrong folder. `cd ~/Desktop/OpenCode-pet` and check with `pwd`.

**`Permission denied (publickey)`** — you are using SSH against an HTTPS URL.
Re-add the remote with the `https://` form shown above.

**`Updates were rejected`** — someone else pushed first.

```bash
git pull --rebase origin main
git push
```

**Started over, completely**

```bash
cd ~/Desktop/OpenCode-pet
rm -rf .git
git init && git add . && git status
```

Your files are untouched; only Git's history is gone.

---

## Safety notes

- **Publishing does not affect the running pet.** The pet runs from
  `%USERPROFILE%\.config\opencode\pet`, which is a separate copy.
- **Never commit `pet_state.json`.** It holds your personal window position,
  scale and language. `.gitignore` excludes it — if you ever see it staged,
  run `git rm --cached pet_state.json` before pushing.
- **`git add .` adds what is there.** The `git status` review in step 3 is the
  single most important line in this document. Skipping it is how personal
  files reach the public internet.
