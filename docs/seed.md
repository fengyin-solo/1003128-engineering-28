# 初始化数据清单（Seed Manifest）

示例数据不再写死在代码里。所有业务模块（表）的初始数据由一份清单
**`backend/data/seed_manifest.json`** 按模块声明字段与记录，进程启动时统一装载。
本地开发、构建镜像、部署运行读的是同一份清单；环境之间只允许**数据量不同**，
**结构必须一致**。

## 1. 目录与依赖

```text
backend/
├── data/
│   ├── seed_manifest.json      # 初始化清单：20 张表的字段声明 + 初始记录（入库、随镜像分发）
│   └── runtime/
│       └── seed_last_good.json # 上一次成功装载的快照（自动生成，已 gitignore）
├── app/
│   ├── config.py               # 清单/快照路径与环境变量
│   ├── seed_loader.py          # 装载、校验、快照、CLI（只用标准库，无新增依赖）
│   ├── store.py                # 内存仓库：启动前为空，装载后整体换入
│   ├── routers/<模块>.py        # LIST_FIELDS / STATUSES：表结构注册表（字段、状态）
│   └── services/<模块>.py       # REQUIRED_FIELDS / STATUS_ORDER：必填字段、状态序列
└── tests/test_seed_loader.py   # 装载、校验、快照、概览同源的回归测试
```

- 清单格式是 JSON，读取与校验只用 Python 标准库，**requirements.txt 无新增依赖**。
- 表结构的唯一事实来源是代码：router 的 `LIST_FIELDS`/`STATUSES` 与 service 的
  `REQUIRED_FIELDS`/`STATUS_ORDER`。清单是这份结构的**数据投影**，两边必须逐一对齐。

## 2. 初始化顺序（启动链路）

```text
1. app.config.settings            解析环境变量、解析清单/快照路径
2. app.seed_loader.discover_registry()
                                  从 routers/services 汇总 20 张表的字段、必填、状态；
                                  router 与 service 自己对不上也直接失败
3. load_manifest_tables()         读取并全量校验 seed_manifest.json
   3a. 顶层结构（version、modules）
   3b. 表集合：缺表 / 多出未注册表 → 失败并点名
   3c. 每张表：字段集合与顺序、每条记录的键、id 唯一、status 合法、
       pending/abnormal 为布尔、必填字段非空
   3d. 若设置 SEED_RECORDS_DIR：合并覆盖记录，走同样的校验
4. 全部通过后 store.replace_tables()
                                  整体换入内存仓库（深拷贝，事务式）
5. save_snapshot()                原子写 data/runtime/seed_last_good.json
6. uvicorn 开始监听端口
```

- 任何一步失败都在**监听端口之前**抛出，进程**非零退出**；错误聚合成清单，
  每条都点名是哪一张表、哪一条记录、什么问题，**绝不静默跳过任何模块或字段**。
- 装载是事务式的：先在暂存区构建出全部 20 张表，全绿才换入。换入失败意味着
  根本没换，**现役数据（上一次成功装载的数据）原样保留**，修好清单重试即可：
  - 重启进程：`./run.sh`（或 `make backend`）；
  - 不重启：`POST /api/seed/reload`，失败时返回全部错误且现役数据不动。

## 3. 清单格式

```json
{
  "version": 1,
  "modules": [
    {
      "name": "gas",
      "label": "瓦斯监测",
      "fields": ["测点编号", "所在区域", "瓦斯浓度", "..."],
      "records": [
        {
          "id": 1,
          "status": "正常",
          "pending": true,
          "abnormal": false,
          "测点编号": "GAS-0001",
          "所在区域": "..."
        }
      ]
    }
  ]
}
```

| 部分 | 要求 |
| --- | --- |
| `version` | 固定为 `1`；不兼容升级时统一升版本，旧清单启动失败 |
| `modules[].name` | 必须是代码里已注册的模块名，20 张表一张不能少、一张不能多 |
| `modules[].label` | 可省略；写了就必须与 router 的 `tags[0]` 一致 |
| `modules[].fields` | 必须与 router `LIST_FIELDS` **集合与顺序完全一致**，沿用既有中文字段名 |
| 每条记录 | 必含 `id`/`status`/`pending`/`abnormal` + 全部 `fields`；不许有清单外字段 |
| `id` | 表内唯一的正整数 |
| `status` | 必须在 router `STATUSES`（= service `STATUS_ORDER`）里 |
| 必填字段 | service `REQUIRED_FIELDS` 对应的字段必须是非空字符串 |
| 其余字段 | 允许字符串/数字/null；`pending`/`abnormal` 必须是布尔 |

`records` 允许为空数组（该表不预置数据），但 `fields` 必须完整——**结构不允许缺**。

## 4. 常用命令

```bash
make seed-check           # 只校验清单（等价于下面一行）
cd backend && .venv/bin/python -m app.seed_loader --check
python -m app.seed_loader --snapshot-info   # 校验并查看上一次成功装载的快照
make test                 # 跑回归测试（含“概览统计 = 清单记录”同源校验）
```

`run.sh` 启动前先跑 `--check`；后端镜像在 **build 阶段**也跑 `--check`，
坏清单会让镜像构建失败，不会流出到部署环境。

## 5. 环境差异：只允许数据量不同

默认所有环境直接用清单里的 `records`。某环境需要不同数据量时，设置：

```bash
export SEED_RECORDS_DIR=/path/to/records
```

目录里放 `<模块名>.json`（如 `gas.json`），内容是**纯记录数组**，会整体替换该表
清单内的初始记录：

```json
[
  {"id": 1, "status": "正常", "pending": true, "abnormal": false, "测点编号": "...}
]
```

规则：

- 文件名必须是已注册的模块名；多出清单外文件 → 启动失败；
- 覆盖记录与清单记录走**同一套**结构校验：字段一个不能少/多、status 合法、id 唯一；
- 目录不存在 / 不是目录 / 有非 JSON 文件 → 启动失败。

因此“同一份代码不同环境数据对不上”的问题被限制为只剩行数差异，结构永远一致。

## 6. 运营概览与初始化数据同源

`GET /api/overview` 直接统计 `store` 里由清单装载的同一份表（`created/pending/abnormal`
都是现场 `sum`），没有第二份统计口径。测试
`OverviewConsistencyTests.test_overview_counts_equal_manifest_records` 锁定：
每张表概览数字 == 清单记录逐条统计。改字段或记录后跑 `make test` 即可发现偏差。

## 7. 故障与恢复

| 现象 | 行为 | 处理 |
| --- | --- | --- |
| 清单缺模块 / 字段对不上 / 记录坏 | 启动非零退出，日志列出全部问题表 | 按提示改 `seed_manifest.json`，`make seed-check` 复验后重启 |
| 运行中改坏清单并热重载 | 接口返回错误，**现役数据不动** | 改回后再次 `POST /api/seed/reload` |
| 进程上次成功启动过，这次清单坏 | 默认仍启动失败；快照不静默启用 | 需要应急时设 `SEED_FALLBACK_SNAPSHOT=1`，用 `data/runtime/seed_last_good.json` 起服务（快照也按当前代码校验，结构漂移照样失败） |
| 快照丢失/损坏 | `--snapshot-info` 会点名原因 | 修好清单正常启动会重新生成快照 |

## 8. 加模块 / 改字段 / 加初始记录（新同事照做即可）

**新增一个业务模块（新表）：**

1. 按现有约定新增 `app/routers/<模块>.py`（写 `LIST_FIELDS`、`STATUSES`）与
   `app/services/<模块>.py`（写 `MODULE`、`REQUIRED_FIELDS`、`STATUS_ORDER`）；
2. 在 `app/routers/__init__.py` 注册路由；
3. 在 `data/seed_manifest.json` 的 `modules` 里补一节（name/label/fields/records）；
4. `make seed-check && make test`，全绿后 `make backend`。

**改字段名 / 字段集合：**先改 router `LIST_FIELDS` 与 service `REQUIRED_FIELDS`，
再同步清单里每张相关记录的键；只改一边，启动会直接指出是哪张表对不上。

**只调整初始记录**（加行、改文案、改状态）：只改清单 `records`，保持字段集合不变，
`make seed-check` 即可。

## 9. 环境变量一览

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SEED_MANIFEST` | `backend/data/seed_manifest.json` | 清单文件路径 |
| `SEED_RECORDS_DIR` | 未设置 | 覆盖记录目录，仅替换各表记录，结构仍受清单约束 |
| `SEED_SNAPSHOT` | `backend/data/runtime/seed_last_good.json` | 上一次成功装载快照路径 |
| `SEED_FALLBACK_SNAPSHOT` | `0` | 清单坏时是否退回快照；默认关闭，坏清单必须失败 |
| `APP_ENV` / `APP_PORT` | `local` / `8000` | 运行环境与端口（原有配置） |
