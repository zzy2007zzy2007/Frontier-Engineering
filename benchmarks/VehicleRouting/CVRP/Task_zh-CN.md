# Task：容量约束车辆路径问题（CVRP）

## 受众与假设

本任务假设你有一般 CS 背景，但对组合优化 / 车辆路径问题没有或只有很少了解。

## 问题背景

物流配送中，一辆辆货车从仓库出发为客户送货。**容量约束车辆路径问题（CVRP）** 是它的标准数学模型：

- **仓库（depot）**：编号 0，所有车辆从这里出发并返回。
- **客户**：编号 1..n，每个客户有需求量 `demand[c]`。
- **车辆**：同型，容量上限 `capacity`。
- **路线**：每条路线为 `仓库 → 若干客户 → 仓库`，且路线内总需求 ≤ 容量。

目标：用若干条路线服务**所有**客户（每个客户恰好被服务一次），使**总行驶距离最小**。

CVRP 是 NP-hard 问题，几十个客户的实例无法精确求解，必须使用启发式方法（最近邻、savings、2-opt、大邻域搜索、元启发式等）。

## 实例（data/instances/）

本任务提供 12 个确定性生成的聚簇分布实例（模拟城市客户分布，坐标 1..100，距离四舍五入取整）：

| 实例 | 客户数 | 容量 | 实例 | 客户数 | 容量 |
|------|--------|------|------|--------|------|
| VRP-19-2 | 19 | 270 | VRP-45-7 | 45 | 150 |
| VRP-21-3 | 21 | 165 | VRP-48-7 | 48 | 170 |
| VRP-22-4 | 22 | 130 | VRP-54-8 | 54 | 175 |
| VRP-32-5 | 32 | 160 | VRP-55-8 | 55 | 185 |
| VRP-37-6 | 37 | 145 | VRP-60-9 | 60 | 160 |
| VRP-45-6 | 45 | 190 | VRP-60-10 | 60 | 165 |

文件名即实例名（如 `VRP-19-2.vrp`），采用 TSPLIB 风格格式（`NODE_COORD_SECTION` / `DEMAND_SECTION` / `DEPOT_SECTION`，depot 为节点 1）。实例由 `verification/generate_instances.py` 确定性生成（seed 42，`seed_key` 固定为发布时的原始标识符，保证数据集跨版本逐字节稳定）。

## 输入 / 输出契约

### 候选程序 `baseline/solver.py`

```python
# EVOLVE-BLOCK-START
def solve(instance):
    """输入 instance dict，输出路线列表 list[list[int]]。
    每个子列表是一条路线的客户访问序列（客户编号 1..n，不含仓库 0）。"""
    ...
# EVOLVE-BLOCK-END
```

- `instance` 字段：
  - `n`：客户数（客户编号 1..n，仓库为 0）
  - `capacity`：车辆容量
  - `demand`：`demand[0..n]`，`demand[0] == 0`
  - `distance`：`(n+1)×(n+1)` 的欧氏四舍五入取整距离矩阵，`distance[0][c]` 为仓库到客户 c 的距离
- 输出：`list[list[int]]`。每条路线为**客户编号序列**（不含仓库 0），例如 `[[3,1,5],[2,4]]` 表示两辆车。
- 独立运行：`python baseline/solver.py <instance.vrp> <output.json>`（固定 I/O 部分不可修改）。

### 验证规则

对候选输出逐条检查：
1. 格式合法：`routes` 是列表的列表，元素为 1..n 的整数；
2. **全覆盖**：所有路线的客户并集恰好为 {1..n}（无重复、无遗漏）；
3. **容量**：每条路线 `sum(demand[c]) ≤ capacity`。

任一不满足 → 该实例判 invalid，得 0 分，且整个候选 `valid=0`。

## 评分

```
score_instance = min(100, 100 × reference_distance / candidate_distance)
combined_score = mean(score_instance)     # 跨 12 个实例平均
valid          = 全部实例合法？1 : 0
```

- `reference_distance` 来自 `data/reference.json`，由 `verification/ref_solver.py` 预计算：**确定性**的 GRASP 多起点 + 2-opt + relocate/swap + 2-opt* + LNS（贪心修复 + tabu 多样化），对固定种子列表 `(123, 2024, 7)` 逐实例取最优，任何机器上重新生成均字节一致。参考解为近最优（与本任务存档的 agent 最优解、OR-Tools GLS 交叉验证一致）。
- **100 分 = 达到参考求解器的解质量**；候选解比参考解更短时可超过 100 分（被截断在 100）。达到 100 意味着已经逼近该实例的实际最优。
- 可选评分旋钮 `CVRP_EVAL_SCORE_SCALE`（默认 1.0）：`score = min(100, scale × 100 × ref / cand)`。scale < 1 会收紧 100 分线（如 scale=2/3 要求候选距离 ≤ 参考的 2/3 才能得 100）。
- 非法 / 崩溃 / 超时的候选：对应实例 0 分，且 `valid=0`（整体判负）。

## 如何运行

```bash
# 在 CVRP 目录下评测一个候选求解器
python verification/evaluator.py baseline/solver.py

# 只评测部分实例
python verification/evaluator.py baseline/solver.py --instances VRP-19-2 VRP-32-5

# 框架适配验证（在仓库根目录）
python -m frontier_eval task=unified task.benchmark=VehicleRouting/CVRP algorithm.iterations=0
```

环境变量：`CVRP_EVAL_TIMEOUT_S`（每实例子进程超时，默认 60）、`CVRP_EVAL_INSTANCES`（实例子集）、`CVRP_EVAL_MAX_INSTANCES`（实例数上限）、`CVRP_EVAL_SCORE_SCALE`（评分旋钮，默认 1.0）。

## 参考分数（本机实测，对当前 reference.json）

agent 运行使用 `deepseek-v4-flash` 模型，每个框架列出其测得的最优分（思考强度设置因框架而异）。

| 求解器 | combined_score |
|--------|----------------|
| baseline（随机顺序最近插入） | **55.59** |
| reference（确定性 GRASP + LNS，评分基准） | 100（近最优） |
| agent（openevolve 5 轮，best） | 96.38 |
| agent（ShinkaEvolve 5 代，best） | 99.31 |
| agent（AB-MCTS 5 候选，best） | 98.70 |

## 优化提示（由弱到强）

1. **随机顺序最近插入**（baseline）：按随机顺序逐个把客户插入到所有路线中代价最小的可行位置 —— 弱但标准的随机化构造。
2. **Clarke-Wright savings**：按 `d(0,i)+d(0,j)-d(i,j)` 降序合并路线 —— 明显改进。
3. **路线内 2-opt**：翻转路线片段消除交叉 —— 进一步改进。
4. **跨路线局部搜索**：relocate / swap / 2-opt*。
5. **大邻域搜索（LNS）/ 模拟退火 / 遗传**：接近参考甚至超越参考。

每一步都是可验证的分数提升。**先保证合法（全覆盖 + 容量），再优化距离。**
