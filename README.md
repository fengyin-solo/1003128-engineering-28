# 矿山安全监测管理平台

面向矿山井下环境监测、瓦斯治理、顶板管理、通风系统与人员定位的一体化矿山安全监测管理后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   ├── app/store.py          内存数据仓库（启动时按清单装载）
│   ├── app/seed_loader.py    示例数据清单的装载、校验与回退
│   ├── seed/manifest.yaml    示例数据清单：模块、字段与初始记录的唯一来源
│   ├── tests/                清单装载的回归测试（pytest）
│   └── var/                  运行期本地文件（上一次成功装载的快照，不入库）
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

`run.sh` 启动前会先校验示例数据清单，清单有问题在这里就能看到是哪张表。

健康检查：`curl http://127.0.0.1:8000/api/health`

回归测试：`cd backend && .venv/bin/python -m pytest tests/ -q`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 矿区台账 | `minearea` | 矿区 | 矿区编号、矿区名称、开采矿种 |
| 瓦斯监测 | `gas` | 瓦斯测点 | 测点编号、所在区域、瓦斯浓度 |
| 通风系统 | `ventilation` | 通风设备 | 设备编号、设备类型、额定风量 |
| 顶板管理 | `roof` | 顶板监测 | 监测编号、所在工作面、离层量 |
| 水害防治 | `waterhazard` | 水文监测 | 监测编号、所在区域、涌水量 |
| 冲击地压 | `rockburst` | 微震监测 | 监测编号、所在区域、微震能量 |
| 人员定位 | `personnel` | 定位终端 | 终端编号、携带人员、所在位置 |
| 粉尘防治 | `dust` | 粉尘测点 | 测点编号、所在区域、粉尘浓度 |
| 防灭火 | `fireprevent` | 防火监测 | 监测编号、所在区域、束管监测 |
| 皮带运输 | `belt` | 运输皮带 | 皮带编号、所属巷道、运输长度 |
| 提升系统 | `hoist` | 提升机 | 提升机编号、提升类型、提升高度 |
| 供电系统 | `power` | 供电设备 | 设备编号、设备类型、电压等级 |
| 应急救援 | `rescue` | 救援装备 | 装备编号、装备名称、装备类别 |
| 安全培训 | `training` | 培训记录 | 培训编号、培训主题、培训对象 |
| 入井管理 | `shift` | 入井记录 | 记录编号、入井人员、所属班组 |
| 爆破管理 | `explosive` | 爆破记录 | 爆破编号、爆破区域、炸药用量 |
| 巷道维修 | `roadway` | 维修任务 | 任务编号、维修巷道、维修内容 |
| 监测分站 | `monitorstation` | 监测分站 | 分站编号、分站名称、所在位置 |
| 持证管理 | `certificate` | 持证人员 | 人员编号、姓名、证书类别 |
| 应急演练 | `emergencydrill` | 演练记录 | 演练编号、演练主题、演练区域 |

## 初始化数据（示例数据清单）

示例数据不在代码里写死。`backend/seed/manifest.yaml` 是唯一来源：按模块声明
字段与初始记录，本地开发、构建镜像、部署读的都是这一份。运营概览
（`/api/overview`）直接统计装载出来的记录，两边是同一个数。

### 清单结构

```yaml
version: 1
modules:
  gas:                    # 模块名，与后端路由 /api/gas 对应
    label: 瓦斯监测        # 模块中文名
    fields:               # 字段声明，必须与 routers/gas.py 的 LIST_FIELDS 一致
      - 测点编号
      - 所在区域
      # ...
    records:              # 初始记录；没有就写 records: []
      - id: 1
        status: 正常
        pending: true
        abnormal: false
        values:           # 键必须正好是 fields 里声明的字段
          测点编号: GAS-0001
          所在区域: 瓦斯监测样例1
          # 日期等值请写成带引号的字符串，如 监测时刻: '2026-09-01'
```

### 启动时的装载顺序

1. 读取 `seed/manifest.yaml`（可用环境变量 `SEED_MANIFEST` 改路径）。
2. 逐模块校验：模块与代码里注册的路由一一对应、字段与 `LIST_FIELDS` 一致、
   每条记录的 `values` 正好是声明的字段、`id` 表内唯一、`pending/abnormal`
   是布尔值。任何问题都会汇成清单抛出，逐条指出是哪一张表、什么问题，
   不静默跳过。
3. 全部通过后一次性替换内存数据仓库；校验不过则已有数据原样保留。
4. 装载成功把结果写入快照 `backend/var/last-good.seed`（可用
   `SEED_SNAPSHOT` 改路径）。下次启动遇到清单损坏时回退到这份上一次成功
   的数据，进程继续运行并在 `/api/health` 的 `seed.degraded` 里标记；
   连快照都没有时启动直接失败。
5. 路由、服务、运营概览都从同一个内存仓库读数。

### 清单改坏了怎么办

- 启动失败或 `seed.degraded: true` 时，日志和 `/api/health` 会列出是哪张
  表、哪个字段对不上。
- 修好清单后二选一：重启进程，或不重启直接调
  `curl -X POST http://127.0.0.1:8000/api/admin/seed/reload`。
  重试失败返回 422 并保留当前数据，成功才替换。
- 改完清单建议先本地校验：`cd backend && .venv/bin/python -m app.seed_loader`。

### 环境之间只允许数据量不同

`SEED_SCALE`（正整数，默认 1）是唯一允许的环境差异：设为 N 时每条记录按
批次复制 N 份，`id` 按批次偏移、编号字段追加 `-S02`、`-S03` 等后缀，字段
结构完全不变。docker-compose 里已显式列出该变量，联调环境要更大的数据量
只改它，不许分叉清单。

### 新增模块或调整字段

1. 在 `app/routers/<模块>.py` 登记路由与 `LIST_FIELDS`（代码侧的唯一字段口径）。
2. 在 `seed/manifest.yaml` 里补同名模块：`label`、`fields`（与 LIST_FIELDS
   逐字一致）、`records`。
3. `python -m app.seed_loader` 校验通过后启动；少一步都会在启动阶段报错并
   指出是哪张表。

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 示例数据只认 `seed/manifest.yaml`，不在代码里写死；装载逻辑集中在
  `app/seed_loader.py`，校验失败必须报出具体表名，不允许静默跳过。
