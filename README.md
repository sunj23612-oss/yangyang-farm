# 洋洋农场 · 联网版

> **正式公网地址：<https://yangyang-farm.onrender.com/>**（注册账号即可开玩，手机电脑都能访问）

星露谷式可交互农场小游戏，支持账号系统、一号一档、好友互访与偷菜、角色与宠物自定义、屋内生活、昼夜季节循环。纯 Python 标准库后端 + 单文件 HTML 前端，**零第三方依赖**；云端存档使用 Turso 外置 SQLite，**实例重启 / 停用 / 重建均不丢数据**。

## 功能一览

- **账号系统**：注册 / 登录 / 会话 token（pbkdf2_hmac-sha256 12 万轮 + secrets token），一号一档，云端存档自动同步（每 20 秒 + 关键操作即时）
- **昼夜交替与季节系统**：一天 240 秒（游戏时间放缓），有昼夜明暗、星空月亮；春夏秋冬四季循环，季节改变色调与落叶特效；夜晚宠物睡觉、人物可选择睡觉或继续工作（继续工作有小概率抱怨台词）
- **可交互土地**：星露谷式瓦片地块（非背景图），人物须走到地块旁才能锄地 / 播种 / 浇水 / 收获；闲置 3 天未耕种的地块退回荒地
- **田地花园无缝格网**：田地 24 格、花园 12 格全铺满、格子间无缝隙，鱼塘水面直接划分在地面上（同好友农场视图）
- **人物受控移动**：点击地面 / 方向键 / WASD 移动，走位触发走路动画；农事、进出门、钓鱼均须走到目标附近再交互
- **角色自定义（1–2 人）**：数量 1–2 人、性别单选（形象与性别绑定：男=徐明涛 / 女=孙靖宇，无额外选项）、自定义姓名；可同步改名换肤
- **宠物自定义（0–2 只）**：猫 / 狗可选，5 款免费模型（哈士奇 / 灰猫 / 狸花猫 / 柴犬 / 德牧），默认名「猫一」「猫二」，可改名；宠物自主漫游、有台词气泡（按品种 / 名称适配 5 模型 × 白天 / 夜晚 / 守夜 / 投喂 / 偷吃 / 踩踏词表）
- **台词系统**：人物说话按对象区分（对他人 / 对宠物 / 自言自语），说话与动作系统解绑——台词不打断移动、不强制切动画
- **好感度（0–100）**：提升条件仅为投喂（喂鱼 +3/次，进度缓慢）；好感越高宠物偷吃菜 / 踩坏田地的几率越低（100 好感约降至 15%）；猫会搞破坏，**狗不会**
- **宠物守卫**：仅狗能守夜（猫不可选），守夜每夜略降 2 好感；夜晚狗不睡觉、看守菜园花园，好感奖惩平衡
- **背包与装备**：锄头 / 种子 / 水壶收进背包，装备到手上使用；未装备时点击即为移动
- **屋内页面（厨房+卧室）**：家具齐全（床=睡觉 / 灶台=烹饪 / 餐桌等，点击互动）；进房须走到小屋门口，出房须走到大门并点击交互；屋内移动已提速
- **告示牌**：菜园 / 花园 / 鱼塘改为插地告示牌（素材实拍，白字黑描边 22px 主场景 / 18px 好友页，清晰醒目）
- **好友系统**：添加好友、好友列表、好友农场只读视图
- **偷菜（进入对方农场）**：点好友「去偷菜」→ 我方人物进入对方农场页面 → 走到成熟作物格子旁自动摘下入包；每好友每日限偷 **3 次**，超限提示并拒绝
- **钓鱼**：不设额外按钮——点击鱼塘 / 告示牌，人物走到岸边自动打开钓鱼面板（游标粗细控制在鱼带内收竿）
- **厨房 / 商店 / 背包**等玩法保留
- 页面全屏自适应（桌面与手机视口均可）；顶部为 保存 / 同步 / 好友 / 自定义 / 退出 功能键，底部为工具栏与背包；底部固定素材署名

## 快速开始（本地运行）

```bash
# 需要 Python 3（任意 3.8+ 即可，无需安装任何第三方包）
# 默认端口 8000；可用环境变量 PORT 覆盖（服务端部署时也按此规则）
python -u server.py
```

浏览器访问：<http://127.0.0.1:8000/>

本地运行默认使用文件 `farm.db`（SQLite）存档；设置 `TURSO_URL` + `TURSO_TOKEN` 后自动改用云端 Turso 库（见下）。

## 部署到公网（Render · 已上线）

**当前正式地址：<https://yangyang-farm.onrender.com/>**（Render Free 实例，已与 GitHub 仓库 `sunj23612-oss/yangyang-farm` 自动部署关联）

- 一键部署配置见仓库根目录 `render.yaml`；也可在 Render 手动建 Web Service，推荐参数：
  - **Environment**：`Python 3`；**Region**：`Oregon (US West)`
  - **Branch**：`main`；**Root Directory**：留空
  - **Build Command**：`echo 'no build needed - pure stdlib'`
  - **Start Command**：`python server.py`（服务内部读取 `PORT` 环境变量，Render 会自动注入）
  - **Instance Type**：Free（$0/月，512 MB）
- 前端由后端同源静态托管（`web/洋洋农场.html`），**无跨域问题，无需单独部署前端**
- 其他任意有 Python 3 的服务器 / 云主机 / NAS 也可直接运行本服务；如需 HTTPS / 域名，用 nginx / caddy 反向代理到本服务端口即可

### 云端存档（Turso 外置 SQLite）

- 数据不在实例盘上，存在 Turso 云端 SQLite：`https://yangyangnongchang-wushii114514.aws-ap-northeast-1.turso.io`
- 在 Render 服务 **Environment** 中配置两个变量（**密钥不要写进仓库代码**）：
  - `TURSO_URL`：Turso 数据库 URL（`libsql://…` 或 `https://…` 均可，代码内自动归一）
  - `TURSO_TOKEN`：Turso 数据库鉴权 Token
- 双模式设计：未配置时自动回落本地 `farm.db`（文件 SQLite，标准库实现），配置后走 Turso HTTP 协议（`libsql_db.py`，纯标准库，无第三方依赖）
- 数据库 schema 初始化与迁移由代码启动时自动执行（7 条 DDL + 索引），无需手工建表
- **Free 实例注意**：Render Free 实例闲置约 15 分钟会 spin down，下次访问冷启动约需 50 秒，随后恢复常速；数据已持久化在 Turso，不受实例重启 / 停用影响

### 本地隧道（historical，已弃用）

早期曾用 localtunnel 临时隧道（`grumpy-dancers-nail.loca.lt`）过渡，该方式依赖本机进程存活、域名随机、无法被搜索引擎稳定收录，**已废弃**，正式访问一律走 Render 域名。

## 目录结构

```
洋洋农场-联网版/
├── server.py          # 后端：API + 数据库 + 静态托管（零第三方依赖）
├── libsql_db.py       # Turso HTTP 适配层（URL 归一 + SQL 拼接 + 错误处理）
├── test_turso_mock.py # 后端 mock 契约测试（跑一遍即可验证云端适配逻辑）
├── render.yaml        # Render 一键部署配置
├── Dockerfile         # 容器化部署方案（可选）
├── requirements.txt   # 空依赖声明（纯标准库）
├── web/
│   └── 洋洋农场.html  # 前端单文件（素材已内联为 base64）
└── farm.db            # 本地 SQLite 数据库（仅本地模式生成；云端模式不使用）
```

## 本地测试

```bash
# 云端适配层契约测试（不依赖真实网络，全部本地 mock）
python -u test_turso_mock.py
```

## 素材来源（均在页面底部固定署名）

| 素材 | 作者 | 许可 |
| --- | --- | --- |
| Tiny16 角色（徐明涛 / 孙靖宇 等） | Sharm · withthelove | CC-BY 3.0 |
| Cozy Farm Lite 瓦片 / 道具 / 树木 | Cozy Pixel · roberto mellado | CC-BY 4.0 |
| 宠物模型（德牧 / 犬 / 猫大图） | Shepardskin | CC0 |
| 宠物模型（哈士奇） | Hellkipz | CC0 |
| 宠物模型（狸花猫 / 柴犬） | pzUH Cat & Dog | CC0 |
| 猫屋木屋 | Cabin In The Woods · Lexaei | CC-BY 4.0 |
| 屋内家具（厨房 + 卧室） | Kenney Roguelike Indoor | CC0 |
| 插地告示牌 | Istarnion | CC0 |
| 鱼塘水面 | Demetrius | CC0 |
| 灰猫（Gray cat asset pack） | skristi / Krystsina Staselovich | CC-BY 4.0 |
| 白狗（Dog Spritesheets） | Jason of GDN | CC0 |

素材均来自 OpenGameArt 免费素材库，网页底部固定展示完整署名。