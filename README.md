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
│   ├── app/routers/          每个业务模块一组接口（LIST_FIELDS/STATUSES 即表结构）
│   ├── app/services/         业务规则与状态流转（REQUIRED_FIELDS/STATUS_ORDER）
│   ├── app/seed_loader.py    初始化清单的装载、校验与快照
│   ├── app/store.py          内存数据仓库（启动时按清单整体装载）
│   ├── data/seed_manifest.json  初始化数据清单（按模块声明字段与初始记录）
│   └── tests/                清单装载与概览同源的回归测试
├── docs/seed.md              初始化数据工程：清单格式、初始化顺序、加模块/改字段流程
├── .gitignore
└── docker-compose.yml
```

## 初始化数据

启动时不再依赖写死在代码里的示例数据，而是按 `backend/data/seed_manifest.json`
这份清单装载：每张表声明字段与初始记录，缺模块或字段对不上会**在启动阶段直接失败
并指出是哪一张表**，绝不静默跳过；本地开发、构建镜像和部署读同一份清单，环境之间
只允许数据量不同（`SEED_RECORDS_DIR`），结构必须一致。完整说明（清单格式、初始化
顺序、依赖、故障恢复、加模块/改字段步骤）见 **[docs/seed.md](docs/seed.md)**。

```bash
make seed-check   # 改完清单先校验；run.sh 与 Docker 构建时也会自动执行
make test         # 跑装载/概览同源回归测试
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

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

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 初始数据统一在 `backend/data/seed_manifest.json` 按模块声明，改字段或加模块后
  先跑 `make seed-check`；流程见 [docs/seed.md](docs/seed.md)。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
