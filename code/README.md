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

`make build` 将项目 Python 源码编译为字节码，并运行 Django 配置检查。`make migrate` 在空库中创建内容类型和会话表，可重复运行。`make test` 检查访客能否打开商品大厅。后续引入校园邮箱账号时，在账号应用的首个迁移中定义用户模型。

`make run` 会先应用尚未执行的迁移，默认首页为 <http://127.0.0.1:8000/>。端口占用时可用 `make run PORT=8001`。在启动服务的终端按 Ctrl+C 可停止服务。
