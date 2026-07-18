"""策略想法(ideas) 与 交易计划(plans) 的数据模型。

字段对应 ``docs/设计方案.md`` 4.1/4.2。SQLModel 下 ``table=True`` 的是真实
SQLite 表；不带的是 API 输入输出 schema。价格/手数/盈亏等用 str 存，
允许写区间或说明（如 ``"2手"`` / ``"4200-4250"``），将来要聚合再迁移。
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

# 想法状态机：新想法 → 已转回测 → 已采纳 / 已搁置
IDEA_STATUSES = ("新想法", "已转回测", "已采纳", "已搁置")
# 计划状态机：计划中 → 已执行 / 已放弃
PLAN_STATUSES = ("计划中", "已执行", "已放弃")

DEFAULT_AUTHOR = "DAREWIN"


# ============ 策略想法 ============

class IdeaBase(SQLModel):
    name: str = Field(index=True)                       # 策略名
    instrument: str = Field(default="", index=True)     # 品种 rb/ag/sn/MA...
    timeframe: str = Field(default="")                  # 周期 2H/4H/1D
    entry_condition: str = Field(default="")            # 开仓条件
    exit_condition: str = Field(default="")             # 平仓条件
    stop_loss: str = Field(default="")                  # 止损
    take_profit: str = Field(default="")                # 止盈
    volume: str = Field(default="")                     # 手数
    note: str = Field(default="")                       # 备注
    status: str = Field(default=IDEA_STATUSES[0], index=True)
    author: str = Field(default=DEFAULT_AUTHOR)


class Idea(IdeaBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class IdeaCreate(IdeaBase):
    """新建想法时的输入（不含 id / 时间戳）。"""


class IdeaUpdate(SQLModel):
    """部分更新（状态流转、改内容）。全部字段可选。"""
    name: Optional[str] = None
    instrument: Optional[str] = None
    timeframe: Optional[str] = None
    entry_condition: Optional[str] = None
    exit_condition: Optional[str] = None
    stop_loss: Optional[str] = None
    take_profit: Optional[str] = None
    volume: Optional[str] = None
    note: Optional[str] = None
    status: Optional[str] = None
    author: Optional[str] = None


# ============ 交易计划 ============

class PlanBase(SQLModel):
    instrument: str = Field(default="", index=True)
    direction: str = Field(default="")                  # 多 / 空
    planned_entry: str = Field(default="")              # 计划入场价/区间
    stop_loss: str = Field(default="")                  # 止损
    target: str = Field(default="")                     # 目标
    volume: str = Field(default="")                     # 手数
    planned_date: str = Field(default="", index=True)   # 计划执行日期 YYYY-MM-DD
    status: str = Field(default=PLAN_STATUSES[0], index=True)
    actual_entry: str = Field(default="")               # 执行后回填
    actual_pnl: str = Field(default="")                 # 执行后回填盈亏
    note: str = Field(default="")
    author: str = Field(default=DEFAULT_AUTHOR)


class Plan(PlanBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class PlanCreate(PlanBase):
    """新建计划时的输入。"""


class PlanUpdate(SQLModel):
    """部分更新（状态流转、执行回填 actual_*）。全部字段可选。"""
    instrument: Optional[str] = None
    direction: Optional[str] = None
    planned_entry: Optional[str] = None
    stop_loss: Optional[str] = None
    target: Optional[str] = None
    volume: Optional[str] = None
    planned_date: Optional[str] = None
    status: Optional[str] = None
    actual_entry: Optional[str] = None
    actual_pnl: Optional[str] = None
    note: Optional[str] = None
    author: Optional[str] = None
