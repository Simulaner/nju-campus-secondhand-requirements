# 栖物校园二手交易平台

在本机运行 Django 和 SQLite，用桌面浏览器打开商品大厅。下列命令在本目录运行，适用于 macOS 或 Linux，需要 GNU Make 和 Python。锁定的依赖要求 Python 3.11 或更高版本，本项目使用 Python 3.12 验证。

## 首次运行

```sh
make setup
make build
make migrate
make test
make run
```

`make setup` 在本目录创建 `.venv`，安装 `requirements.txt` 中锁定的依赖，并生成本机 Django 密钥。若默认的 `python3` 版本较低，可在首次创建环境时用 `make setup PYTHON=python3.12`，也可把 `PYTHON` 指向解释器的绝对路径。后续命令自动使用项目虚拟环境。

`make build` 编译项目 Python 源码并运行 Django 配置检查。`make migrate` 可以初始化空库，也可以直接升级步骤一的 SQLite 数据库。`make test` 使用单独的测试库验证注册、登录、对象权限及数据库约束。

`make run` 会先应用尚未执行的迁移，默认首页为 <http://127.0.0.1:8000/>。端口占用时可用 `make run PORT=8001`。在启动服务的终端按 Ctrl+C 可停止服务。

## 在本机试用注册和登录

1. 在首页选择“注册校园账号”，输入教工邮箱（`@nju.edu.cn`）或学生邮箱（`@smail.nju.edu.cn`），点击“获取验证码”。本项目按完整域名匹配。[南大邮箱分类](https://oi.nju.edu.cn/_s302/17/b5/c21586a333749/page.psp)
2. 默认使用本机文件收件箱。打开本目录隐藏的 `.mailbox/`，在发给该地址的最新 `.log` 邮件中找到 6 位验证码。这个模式无需邮件服务器。
3. 在验证页输入验证码、昵称和密码。验证码 10 分钟内有效，60 秒后可重发，重发后使用最新一封。连续输错 5 次需要重新获取。
4. 注册完成后用邮箱和密码登录。“我的记录”显示本人发布的商品以及本人参与的预约。用另一个普通账号登录可检查各自的数据范围。

## 预置管理员

在本目录运行 `make admin`，按提示输入管理员校园邮箱、昵称和密码。在网页使用该账号登录，顶部导航会出现“商品审核”，打开后可查看待审商品。审核通过和驳回操作安排在步骤三。

## 让验证码投递到真实邮箱

使用已有邮箱服务商的 SMTP 服务即可。在启动 `make run` 的同一终端配置以下环境变量并重启应用。收件人使用自己的校园邮箱即可，发件服务可以来自其他服务商。

| 环境变量 | 用途 |
| --- | --- |
| `QIWU_SMTP_HOST` | 服务商给出的 SMTP 主机。设置后启用真实 SMTP 发送。 |
| `QIWU_SMTP_SECURITY`、`QIWU_SMTP_PORT` | 默认使用 `starttls` 和 587 端口。服务商使用隐式 TLS 时选 `ssl` 和 465 端口。与本机测试接收器联调时可选 `plain` 并指定其端口。 |
| `QIWU_SMTP_USER`、`QIWU_SMTP_PASSWORD` | 发件账号及其客户端专用密码或服务商授权码，在本机私下设置。 |
| `QIWU_FROM_EMAIL` | 该账号获准使用的发件地址。省略时采用 `QIWU_SMTP_USER`。 |

若用南大邮箱发信，可在网页版邮箱的客户端设置里启用 SMTP，并按学校说明获取客户端专用密码。[南大邮箱使用说明](https://oi.nju.edu.cn/96/2e/c21475a497198/page.htm)
