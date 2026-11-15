# RedFoxHub API Skill

一个 Agent Skill：让 AI 能查到并调用 [RedFoxHub（红狐数据）](https://redfox.hk/apis) 的接口。覆盖抖音、小红书、公众号、B站、快手、视频号、今日头条、TikTok、YouTube、X、Instagram、微博、知乎、汽车/电商、全网热点榜、AI 搜索、图片视频生成等 **28 类共 201 个端点**。

仓库：https://github.com/liangdabiao/redfox_api_skill

**这个仓库的根目录就是 Skill 目录**，下载下来直接放进你的 skills 目录即可用，不需要额外安装 Python 依赖（只用标准库）。

## 安装

把整个仓库目录放进 skills 目录即可（目录名建议 `redfox-api-helper`）：

```bash
# Claude Code
git clone https://github.com/liangdabiao/redfox_api_skill.git ~/.claude/skills/redfox-api-helper

# WorkBuddy 项目级
git clone https://github.com/liangdabiao/redfox_api_skill.git .workbuddy/skills/redfox-api-helper
```

或者直接把本目录的文件拷进一个文件夹（`README.md` 可选）。

## 配置 Key

去 https://redfox.hk/settings/api-keys 生成 key，然后二选一：

```bash
export REDFOX_API_KEY="ak_xxxxxx"
```

或在**本目录外面**放一个 `.env`，写入 `REDFOX_API_KEY=ak_xxxxxx`。没配 key 时脚本会明确报错，不会静默发请求。别把 key 提交进仓库。

## 三步用法

```bash
# 1. 找端点
python api_searcher.py "抖音 账号 搜索"
python api_searcher.py platforms                    # 看有哪些平台
python api_searcher.py platform:xiaohongshu         # 看某平台全部端点

# 2. 查参数（给出真实字段名、必填项、可直接执行的命令）
python api_searcher.py detail:douyin.search_users

# 3. 调用
python api_client.py POST /story/api/dyData/searchUser "keyword=乐高"
```

`code: 2000` 表示成功，数据在 `data` 里。`3203/3801` 且文案是「未查询到数据 / 未收录 / 暂无作品」时属于**查无数据**，不是失败，退出码 0；其他 code 才是错误（退出码 1，原因打到 stderr）。

## 常用例子

| 想做什么 | 命令 |
|---|---|
| 查余额 | `python api_client.py GET /story/web/points/api-key/balance` |
| 搜抖音账号 | `python api_client.py POST /story/api/dyData/searchUser "keyword=乐高"` |
| 搜小红书笔记 | `python api_client.py POST /story/api/xhsUser/searchArticle "keyword=积木"` |
| 抖音热点榜 | `python api_client.py GET /story/api/hotSpot/getListByPlatform "platform=2" "startDate=2026-11-13" "endDate=2026-11-14"` |
| 全网热搜（数组参数） | `python api_client.py POST /story/api/hotSpot/getListByPlatformWithKeyword --body '{"keywords":["三星"],"startDate":"2026-11-01","endDate":"2026-11-14"}'` |
| 大响应存文件 | `python api_client.py POST /story/api/dyData/searchUser "keyword=乐高" --output result.json` |
| 只看结构 | `python api_client.py POST /story/api/dyData/searchUser "keyword=乐高" --summary` |

## 几点注意

- **POST 的 `k=v` 进 JSON body，GET 的进 query**。字段名用 `detail:` 里的 `api_name`（如 `sortType`），不是 SDK 文档的 snake_case。
- 有些参数 SDK 标成可选、服务端却要求必填（`page`、`offset`、`sourceType` 等）。`detail:` 给出的 `example_call` 已经带好默认值，**照抄就不会报 `1002`/`3203`**。
- 有些端点是「几个 ID 里至少传一个」：抖音作品列表 `accountId`、小红书 `redId`/`userid`、B站 `mid`。`example_call` 里带 `<...>` 占位符，换成真实 ID 再跑。抖音 / 小红书作品列表优质库覆盖有限，多个真实账号会返回「暂未收录」，不等于接口坏了。
- 鉴权只用请求头，别把 key 拼进 URL。
- 值写成 `["a","b"]`、`{"x":1}`、数字或 `true` 会自动按对应类型发送；中文和链接会正确编码。
- 按次计费（最低约 ¥0.02/次），默认不重试以免重复扣费；需要时用 `--retries 2`。
- AI 搜索、图片/视频生成是「提交任务 → 查结果」两步，提交就会计费；AI 搜索返回的是模型生成文本，事实类结论要交叉验证。
- 遇到 `400/1001` 用 `detail:` 核对参数；`401/3103/3105` 检查 key；`3201` 是积分不足；`4004` 是触发限流，稍等再试。
- `3203/3801` 看文案：「未查询到数据 / 暂未收录 / 资源在平台上未找到」属正常空结果（退出码 0），若写的是参数问题（如关键词超过 6 个字），仍按参数错误处理。**退出码 0 不代表免费**——`3203` 常写明「积分未扣除」，`3801` 会写明「已扣除 0.600 积分」。
- 账号搜索是模糊匹配，中文复合词容易串到无关行业（实测搜「MOC设计师」返回的多是室内 / 服装设计师）；命中后按简介和作品再筛。
- 批量做跨平台统计时，先确认字段路径再抽数：TikTok 结果用 `content` / `statsData` / `publishTime`，评论端点参数是 `aweme_id`；YouTube 给的是 `"19 hours ago"` 这类相对时间，要先换算。

官方文档：https://redfox.hk/apis ｜ 状态码：https://redfox.hk/api-codes  - 感谢社区 https://linux.do
