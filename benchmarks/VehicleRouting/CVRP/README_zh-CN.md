# CVRP（容量约束车辆路径问题）Benchmark

标准 CVRP：一支同型车队从单一仓库出发服务所有客户，每条路线不超过车辆容量，目标是最小化总行驶距离。

## 目录结构

```
CVRP/
├── Task.md                  # 任务说明（规则、输入输出契约、评分）
├── baseline/
│   └── solver.py            # 候选求解器（随机顺序最近插入）+ EVOLVE-BLOCK
├── verification/
│   ├── evaluator.py         # 运行器 + 验证器 + 评分器（仅标准库）
│   ├── ref_solver.py        # 参考求解器：确定性 GRASP 多起点 + 2-opt + relocate/swap + 2-opt* + LNS
│   ├── generate_instances.py# 确定性实例生成器（seed 42，逐字节稳定）
│   ├── requirements.txt
│   └── docker/
│       └── Dockerfile       # 容器化评测环境（Docker 优先）
├── data/
│   ├── instances/           # 12 个生成的 TSPLIB 风格 .vrp 实例
│   └── reference.json       # 每个实例的参考距离（评分基准）
└── frontier_eval/           # Unified-task 元数据
```

## 依赖

仅需 Python 标准库，无第三方依赖。

## 如何运行

```bash
# 评测一个候选求解器（在 CVRP 目录下）
python verification/evaluator.py baseline/solver.py

# 框架适配验证（仓库根目录）
python -m frontier_eval task=unified task.benchmark=VehicleRouting/CVRP algorithm.iterations=0
```

### Docker

```bash
# 构建镜像（在 CVRP 目录下）
docker build -t cvrp-benchmark -f verification/docker/Dockerfile .

# 评测 baseline
docker run --rm -it cvrp-benchmark

# 评测候选（候选需挂载进容器，镜像内只有构建时的文件）
docker run --rm -it -v "$(pwd)/candidate.py:/app/candidate.py" cvrp-benchmark candidate.py
```

## 评分

`score = min(100, 100 × reference_distance / candidate_distance)`，跨 12 个实例取平均。
参考距离由确定性的 `verification/ref_solver.py` 预计算，为近最优（与存档最优 agent 解、OR-Tools GLS 交叉验证一致）。
非法解（客户缺失/重复、超容量、崩溃、超时）得 0 分，并使整个运行判无效（valid=0）。
可选评分旋钮 `CVRP_EVAL_SCORE_SCALE`（默认 1.0）可收紧 100 分线：`score = min(100, scale × 100 × ref / cand)`。

## 参考分数（本机实测）

agent 运行使用 `deepseek-v4-flash` 模型，每个框架列出其测得的最优分（思考强度设置因框架而异）。

| 求解器 | combined_score |
|--------|----------------|
| baseline（随机顺序最近插入） | 55.59 |
| reference（确定性 GRASP + LNS，评分基准） | 100（近最优） |
| agent（openevolve，5 轮，best） | 96.38 |
| agent（ShinkaEvolve，5 代，best） | 99.31 |
| agent（AB-MCTS，5 候选，best） | 98.70 |

## Unified-task 集成

- **Benchmark id**：`VehicleRouting/CVRP`
- 评测器仅使用 Python 标准库，因此**无需任何 runtime 覆盖项**（不需要 `python_path`、conda 环境或 Docker 镜像指定）；`eval_command.txt` 直接使用 `{python}`。
- 仅 Windows：本地验证需将 `task.runtime.shell` 指向 Git Bash（如 `task.runtime.shell=C:/Program Files/Git/bin/bash.exe`），因为默认 `bash` 会解析到没有 `python` 的 WSL 垫片（returncode 127）。Linux 无需覆盖。
- 框架适配验证命令（仓库根目录）：
  `python -m frontier_eval task=unified task.benchmark=VehicleRouting/CVRP algorithm.iterations=0`
