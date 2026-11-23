# Lab3 步骤 7 验收记录

## 搜索性能

在 Apple M6、12 核、24 GiB 内存的 Mac 上，使用 macOS 27.2.1、Python 3.12.14、Django 5.2.19 和 SQLite 3.53.1。测试客户端和 `serve_market` 通过 `127.0.0.1` 通信。每次使用新建的 SQLite 数据库。样本含 2,400 件商品、4 个分类、2,400 张封面和 400 笔有效预约，其中 1,600 件已发布、400 件已预约、400 件处于非公开状态。

四种查询是名称加发布时间排序、多条件组合加价格排序、已预约商品的第二页，以及按最早发布时间浏览全部公开商品。独立计算每种查询应返回的总数、商品标识和顺序。每轮 100 个线程同时发起请求，从实际发起连接到读完 HTML 计时。HTTP 200、内容匹配且耗时不超过 2 秒才计入分子。每轮全部 100 个请求都计入分母，另有四次热身请求。

| 运行 | 第 1 轮 | 第 2 轮 | 第 3 轮 | 合计 |
| --- | ---: | ---: | ---: | ---: |
| 连接队列 10 | 31/100 | 23/100 | 29/100 | 83/300 |
| 连接队列 128 | 100/100 | 100/100 | 100/100 | 300/300 |

初测有 217 次连接错误，其中 208 次为连接重置。日志记录了 83 次计入测量的搜索请求。把本地服务器的连接等待队列从 10 调整为 128 后，用同一套数据和查询复测。三轮 P95 分别为 601.792、657.674 和 615.645 ms，合并 P95 为 641.296 ms，最大耗时为 665.440 ms。每轮两秒内成功比例均超过 REQ-026 要求的 95%。[初测明细](search-baseline/requests.csv) · [初测汇总](search-baseline/summary.json) · [复测明细](search/requests.csv) · [复测汇总与环境](search/summary.json)

## 功能和风格

- `make build test` 完成 Python 字节码编译、Django 系统检查及 79 项测试。分项输出记录了权限、重复提交、截止边界、通知写入失败及事务回滚等测试。[构建与测试输出](build-test.log) · [分项输出](test-cases.log)
- 用两个独立 SQLite 连接运行 8 组竞争场景，覆盖同时预约、重复请求、改价与预约、接受与超时、下架与超时、双方确认、第二方确认与取消及停用与新预约。[并发用例](../../code/qa/check_races.py) · [检查输出](concurrency.log)
- 用隔离数据库重跑 Chrome 注册和登录、面交成交、管理工作台。两种校园邮箱完成本地验证码流程，回环 SMTP 收件器收到邮件。成交记录数、启动补扫、违规处理以及停用恢复均通过。日志中的 HTTP 403 是普通账号尝试进入审核队列时的预期结果。浏览器输出用 `<workspace>` 代替本机工作区绝对路径。[账号输出](browser-accounts.log) · [面交输出](browser-handover.log) · [管理输出](browser-management.log)
- Ruff 0.17.6 的导入、语法、兼容性和常见错误检查通过。格式检查覆盖 42 个文件。自动生成的两份初始迁移按原格式保留。[风格检查输出](style.log)

## 代码规模

用 cloc 2.10 按所列文件计算非空白、非注释的物理代码行。运行时代码 **2,382 行 Python**，范围含项目入口和两个业务应用，排除迁移、测试、压测脚本、第三方依赖和实验二原型。手写模板 835 行 HTML，样式 531 行 CSS，测试和验收工具 2,520 行 Python。后面三项单列。[原始统计](lines.txt) · [运行时清单](runtime-python-files.txt) · [模板和样式清单](html-css-files.txt) · [测试清单](tests-and-qa-files.txt) · [cloc 2.10 发布页](https://github.com/AlDanial/cloc/releases/tag/v2.10)

## 复现命令

在项目仓库根目录执行。压测输出路径可换成任意新目录。

```sh
make -C code build test
(cd code && .venv/bin/python manage.py test --verbosity 2)
(cd code && .venv/bin/python qa/check_races.py)
(cd code && .venv/bin/python qa/benchmark_search.py --output /tmp/qiwu-search-check)

(cd code && ruff check --no-cache --select E4,E7,E9,F,I,UP,B,SIM --target-version py311 --exclude 0001_initial.py manage.py qiwu qa)
(cd code && ruff format --check --no-cache --line-length=100 --exclude 0001_initial.py manage.py qiwu qa)
cloc --quiet --hide-rate --list-file=validation/lab3-step7/runtime-python-files.txt
cloc --quiet --hide-rate --list-file=validation/lab3-step7/html-css-files.txt
cloc --quiet --hide-rate --list-file=validation/lab3-step7/tests-and-qa-files.txt
```
