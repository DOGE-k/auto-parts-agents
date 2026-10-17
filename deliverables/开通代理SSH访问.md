# 让代理直接操作服务器：一次性开通步骤

## 现在的情况

我这边**没法直接接管你的服务器**，原因不是权限设置，而是认证方式：

```
ssh -o BatchMode=yes ubuntu@129.204.33.138
→ Permission denied (publickey,password)
```

服务器只接受**密码**或**已登记的密钥**。密码我拿不到，也不应该拿——
你输密码时我不能在旁边，而把密码写进命令或文件会让它以明文留在历史和磁盘里。
另外我这边的命令执行环境是非交互的，密码提示框根本弹不出来（`BatchMode` 探测 0.25 秒就返回失败）。

**但用密钥就能做到，而且只需要你做一次。** 之后我就能自己登录、自己执行、自己核对。

## 你只需要做这一次

我已经在本机生成好专用密钥对（没有覆盖你原有的任何密钥）：

```
私钥：C:\Users\DOGE\.ssh\dsh_wutong
公钥：C:\Users\DOGE\.ssh\dsh_wutong.pub
```

公钥内容（就是一整行）：

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKMwhGOqiXZk73VcXOrDMeZUdAfuTdayvIHZ2/RH9YeW dsh-agent-to-wutong-server
```

### 方式 A：你自己 ssh 上去加一行（推荐，30 秒）

在 Windows PowerShell 里执行（会提示输入服务器密码）：

```powershell
ssh ubuntu@129.204.33.138
```

进去后执行：

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKMwhGOqiXZk73VcXOrDMeZUdAfuTdayvIHZ2/RH9YeW dsh-agent-to-wutong-server' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
exit
```

### 方式 B：一条命令搞定（Windows 本机，会提示密码）

```powershell
Get-Content "$env:USERPROFILE\.ssh\dsh_wutong.pub" | ssh ubuntu@129.204.33.138 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo DONE"
```

看到 `DONE` 就成了。

## 开通后我会做什么

你回一句"可以了"，我就按下面顺序自己执行并逐条核对，你只需要看结果：

| 步骤 | 操作 | 我会核对 |
|---|---|---|
| 1 | 连通性与身份 | `hostname`、`git rev-parse HEAD` 应为 `524e56f` |
| 2 | scp 上传离线包 | 本地与远端 SHA256 一致 |
| 3 | `git stash push -u` | 工作区必须变空 |
| 4 | `git fetch` + `merge --ff-only` | HEAD 与 bundle 的 `refs/heads/main` 一致 |
| 5 | 校验新代码 | 三处 grep 均为 2 |
| 6 | 恢复本地独有改动 | 只剩 3 个文件为 M |
| 7 | `pip install -e './backend[wutong]'` | 输出 PIP_OK |
| 8 | stop → `unset` → start | 五行 `[通过]` + 一行 `[完成]` |
| 9 | 读启动日志 | 出现 `[密钥] 来源=dotenv` |

整套流程已封装在 `deliverables/deploy_to_server.ps1`，我也可以直接跑它，或逐步手工执行（便于中途停下来看）。

## 我会遵守的边界

- **只动代码和接入进程**：不碰 `.env`、不碰证书私钥、不碰 `backend/data/` 里的业务数据、不碰数据库；
- **不执行任何写入 ERP/MES 的操作**；
- **不读取、不打印任何密钥或密码**；
- 遇到与预期不符的输出（比如 merge 不是 fast-forward、stash 后工作区不干净）就**停下**报告，不强行继续；
- 全程留痕：每条命令和输出都记在回复里，你可以复核。

## 不想给密钥也可以

那还是你自己执行 `deliverables/服务器更新操作步骤.md` 里的 8 步，
把输出贴给我，我逐条判读。两条路的结果一样，只是前者省你手工操作。

## 撤销方式

不想继续授权时，在服务器上执行：

```bash
sed -i '/dsh-agent-to-wutong-server/d' ~/.ssh/authorized_keys
```

立刻失效；本机私钥可留可删（`Remove-Item "$env:USERPROFILE\.ssh\dsh_wutong*"`）。
