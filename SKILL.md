---
name: redfox-api-helper
description: 检索并调用 RedFoxHub（红狐数据）API，覆盖抖音、小红书、公众号、B站、快手、视频号、TikTok、YouTube、X、Instagram、微博、知乎、汽车与电商平台、全网热点榜、AI 搜索、图片/视频生成、素材上传、账户余额等 28 类共 201 个端点。当用户需要查社媒账号/作品/评论/榜单数据、热点、达人、商品数据，或要调用红狐接口时使用。Use when the user asks for RedFox / 红狐 data, or social media data that can be fetched through these endpoints.
---

# RedFoxHub API Helper

本仓库根目录就是 Skill 目录。`SKILL.md`、`apis.json`（201 个端点的本地目录）、`api_searcher.py`（检索）、`api_client.py`（调用）都在这一层，`README.md` 是给人看的说明。命令都在该目录执行。

## 数据源

`apis.json` 是唯一数据源，含每个端点的 HTTP 方法、路径、中英文摘要、真实请求字段名、必填项、默认值和可直接执行的 `example_call`。检索脚本只读本地文件，不联网、不消耗积分。

## 配置 API Key

调用前必须有 key，从 https://redfox.hk/settings/api-keys 获取。读取顺序：`--token` → 环境变量 `REDFOX_API_KEY` → 本目录及上级目录的 `.env`。没配会直接报错，不会静默发请求。**不要把 key 写进本仓库。**

```bash
export REDFOX_API_KEY="ak_xxxxxx"
```

## 工作流

1. 用关键词或平台列端点：`python api_searcher.py "小红书 搜索 笔记"`、`python api_searcher.py platform:xiaohongshu`、`python api_searcher.py platforms`
2. 用 `detail:` 查真实字段名和必填项：`python api_searcher.py detail:douyin.search_users`
3. 按 `example_call` 调用：`python api_client.py POST /story/api/dyData/searchUser "keyword=乐高"`
4. 看响应：`code: 2000` 为成功，数据在 `data`；`3203/3801` 且文案为查无数据时是**空结果**（不是调用失败，退出码 0）；其他 code 才是业务错误（见下方）。

## 命令速查

```bash
python api_searcher.py "抖音 账号 搜索" --limit 5
python api_searcher.py "hot rank" --json
python api_searcher.py platforms                       # 28 个平台及端点数
python api_searcher.py platform:xiaohongshu            # 该平台全部端点
python api_searcher.py platform:xiaohongshu --json
python api_searcher.py detail:douyin.search_users      # 参数 / 必填 / 示例命令
python api_searcher.py detail:/story/api/dyData/searchUser --full

python api_client.py GET /story/web/points/api-key/balance
python api_client.py POST /story/api/dyData/searchUser "keyword=乐高"
python api_client.py GET /story/api/hotSpot/getListByPlatform "platform=2" "startDate=2026-11-13" "endDate=2026-11-14"
python api_client.py POST /story/api/xhsUser/searchArticle "keyword=积木" --summary
python api_client.py POST /story/api/hotSpot/getListByPlatformWithKeyword --body '{"keywords":["三星"],"startDate":"2026-11-01","endDate":"2026-11-14"}'
python api_client.py POST /story/api/dyData/searchUser "keyword=乐高" --output result.json
```

## 调用约定

- **请求头**由脚本自动加：`REDFOX_API_KEY`、`Content-Type: application/json`、浏览器 User-Agent。
- **POST**：`k=v` 进 JSON body；**GET**：`k=v` 进 query。两种都支持 `--body '{"a":1}'` 传复杂结构；`k=v` 的值写成 `["a","b"]` / `{"x":1}` / 数字 / `true` 会自动解析成对应类型，其余按字符串传。中文、URL、特殊字符会正确编码。
- **字段名用 `detail:` 给出的 `api_name`**（camelCase，如 `sortType`、`startDate`），不要照抄 SDK 的 snake_case 参数名。
- 鉴权只靠请求头，脚本自动带上；不要把 key 拼进 URL。
- **部分标为可选的参数服务端实际必填**（如 `page` / `offset` / `sourceType`），`example_call` 已给出默认值，照抄即可；只传必填项会返回 `1002`/`3203`。
- **上传类接口**（2 个）用 `--file <本地文件> --field file`，其余参数照常 `k=v`。
- 输出：`--summary` 只看结构、`--compact` 压缩、`--output file.json` 落盘大响应、`--retries N` 才会在 429/5xx 重试（默认 0，避免重复计费）。
- 退出码：成功、查无数据为 `0`，其余错误为 `1`。stderr 打印 `HTTP xxx` / `RedFox code xxxx: msg`；查无数据会额外标 `no matching data, not a failure`。完整响应始终输出/落盘，便于区分「没这个号」和「接口坏了」。

## 状态码

`code: 2000` 成功。常见失败：`400` 参数错误、`401` 鉴权失败、`403` 无权限、`1001` 缺必要参数、`1002/1003` 参数格式/类型错、`3103` key 不存在、`3105` key 被禁用、`3201` 积分不足、`4004` 操作过于频繁（限流）、5xx 服务端异常。完整列表：https://redfox.hk/api-codes

`3203` / `3801` 必须看文案区分：

- 「未查询到相关数据 / 暂未收录 / 库内不存在 / 未找到账号 / 数据不存在 / 暂无作品」是**查无数据**，不是调用失败，退出码 0，stderr 标 `no matching data`。`3203` 文案通常写明「积分未扣除」；`3801` 文案会写明已扣积分（实测 0.600）。
- 其它文案（例如「关键词长度请不要超过6个字」）是真正的参数错误，退出码 1。

## 注意

- 按次计费（最低约 ¥0.02/次），部分接口按累计调用量阶梯计价；不要无意义地重复调用，重试默认关闭。
- AI 搜索、图片/视频生成是「提交任务 → 查询结果」两步，提交即计费；先用 `detail:` 看清参数。
- **AI 搜索返回的是模型生成文本，不是结构化事实**：实测把 GitHub 关注者数字写错、引用来源混入无关页面；做调研时要用具体数据端点或官方来源交叉验证。
- 快手作品搜索关键词不要超过 6 个字（超出返回 `3203`）；抖音优质库只收录部分账号，查不到时换关键词或换端点再试，别当作接口故障。
- 文档站：https://redfox.hk/apis ；单个端点页可查响应字段结构。
- 目录里的 `apis.json` 由官方 Python SDK 端点定义生成；若官方 SDK 更新，端点路径与字段可能变化，调用前以 `detail:` 为准。
