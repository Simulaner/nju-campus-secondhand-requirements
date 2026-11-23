# 实验三第八步 · 远端复现与 UML 核对

## 源码与运行环境

验收分支为 `lab3-implementation`，实际构建和演示的提交为 `e6b16ef32eecd31360aa12d46c42494dc5ca0d45`。本机两次 Git HTTPS 克隆超时，随后从 GitHub 下载此提交的官方源码归档，在独立目录重建浅检出。归档含 215 个跟踪文件，重建的提交对象和 Git 根树均与 GitHub 一致，检出结果没有待提交的改动。[验收提交](https://github.com/Simulaner/nju-campus-secondhand-requirements/commit/e6b16ef32eecd31360aa12d46c42494dc5ca0d45) · [对应源码归档](https://codeload.github.com/Simulaner/nju-campus-secondhand-requirements/tar.gz/e6b16ef32eecd31360aa12d46c42494dc5ca0d45)

| 校验项 | 值 |
| --- | --- |
| 源码归档 SHA-256 | `6525dd732b67847fd5ce46964f744043475a078a32d37c44e51446ee0350ef70` |
| 验收提交的 Git 根树 | `4ff56eca3b4329d598ba96172a28afe55300f7fd` |
| 验收源码 `code/` 的 Git 子树 | `89727b247a2a9399858a0c40432500e9ca08a6e9` |
| 环境 | macOS arm64，Python 3.12.14，Django 5.2.19，SQLite |

从新目录复验时，可在检出后执行 `git rev-parse HEAD:code`，核对其输出与表中的源码子树一致。

```sh
git clone --branch lab3-implementation https://github.com/Simulaner/nju-campus-secondhand-requirements.git qiwu-lab3
cd qiwu-lab3/code
make setup PYTHON=python3.12
make build
make migrate
make test
.venv/bin/python -m pip check
.venv/bin/python qa/check_races.py
make run
```

独立检出初始没有虚拟环境和数据库。安装锁定依赖后，字节码编译、Django 系统检查、空库迁移、79 项自动测试及 8 组独立 SQLite 连接并发检查全部通过。[构建输出](fresh-build.txt) · [运行说明](../../code/README.md)

回到仓库根目录后执行 `cloc --quiet --hide-rate --list-file=validation/lab3-step7/runtime-python-files.txt`。用 cloc 2.10 重新统计得到 24 个运行时 Python 文件、**2,382 行代码**。统计范围排除迁移、测试、验收脚本、依赖和原型。[文件清单](../lab3-step7/runtime-python-files.txt) · [统计输出](runtime-python-lines.txt)

## 浏览器验收

使用互相独立的 Chrome 会话，验证学生域名注册卖家、教工域名注册买家和预置管理员登录。验证码通过本机文件收件箱获取。卖家提交三件商品，管理员审核通过，买家搜索并预约耳机。普通买家访问管理工作台返回 HTTP 403。卖家接受、买家单方确认、买卖双方确认三个时点，在 SQLite 中查询同一预约的成交记录数依次为 **0、0、1**。成交价为 68.96 元，售出商品的公开详情返回 HTTP 404。管理工作台显示商品 3 件、待审核 0 件、有效预约 0 笔、完成交易 1 笔。[浏览器检查输出](browser-proof.txt)

| 页面 | 截图 |
| --- | --- |
| 审核通过后的商品大厅 | [01-marketplace.jpg](screenshots/01-marketplace.jpg) |
| 管理员审核耳机商品 | [02-review.jpg](screenshots/02-review.jpg) |
| 买家看到双方确认后的成交详情 | [03-completed.jpg](screenshots/03-completed.jpg) |
| 管理员看到成交后的平台统计 | [04-management.jpg](screenshots/04-management.jpg) |

页面中的账号和商品均为验收样本，商品照片沿用原型素材。[素材与资料来源](../../design/素材与资料来源.md)

## UML 与源码对应

9 张图均存于仓库根目录的 `UML/`。使用 PlantUML 1.2025.10 重新导出后，逐张打开 PNG，并从 PNG 元数据提取图源，核对其与同名 `.puml` 一致。[图片尺寸与 SHA-256 清单](uml-manifest.json)

| 图 | 核对的实现 |
| --- | --- |
| U01 类图 | 账号、验证码、商品图片数据、预约回执、治理记录和数据库关联。[账号模型](../../code/qiwu/accounts/models.py) · [交易模型](../../code/qiwu/market/models.py) |
| U02 组件图 | Django 页面与服务、邮件后端、SQLite 图片数据、本机失败审计备份和到期扫描。[运行配置](../../code/qiwu/settings.py) · [审计](../../code/qiwu/market/events.py) · [服务入口](../../code/qiwu/market/management/commands/serve_market.py) |
| U03 用例图 | 访客浏览、普通用户买卖、当事人联系方式授权、平台治理和只读统计。[路由](../../code/qiwu/urls.py) · [管理入口](../../code/qiwu/market/management_views.py) |
| U04 交易活动图 | 送审和驳回重审、预约、面交确认和成交。[商品服务](../../code/qiwu/market/services.py) · [预约服务](../../code/qiwu/market/reservations.py) |
| U05 商品状态图 | 修改后暂停公开、预约占用、终止时按当前状态释放、违规下架后的重审限制。[商品服务](../../code/qiwu/market/services.py) · [治理服务](../../code/qiwu/market/governance.py) |
| U06 预约状态图 | 待确认预约的 24 小时边界、已接受预约的双方确认、终止时清理联系方式。[预约服务](../../code/qiwu/market/reservations.py) |
| U07 并发预约时序 | SQLite 写事务、按发起人查回执、请求摘要绑定商品版本、唯一有效预约约束。[预约服务](../../code/qiwu/market/reservations.py) · [预约模型](../../code/qiwu/market/models.py) |
| U08 双方确认时序 | 分别保存双方确认时间，最后一方确认时写入唯一成交、通知和审计。[预约服务](../../code/qiwu/market/reservations.py) |
| U09 下架与超时时序 | 卖家下架核对发布批次，治理操作检查版本，服务启动补扫并每 15 秒扫描。[预约服务](../../code/qiwu/market/reservations.py) · [到期命令](../../code/qiwu/market/management/commands/expire_reservations.py) · [服务入口](../../code/qiwu/market/management/commands/serve_market.py) |
