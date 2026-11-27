# QuantStone Backtest

面向 A 股股票的事件驱动型量化回测系统，支持日级与分钟级模拟账户回测。

## 项目定位

`quantstone-backtest` 负责策略的历史模拟运行，与数据基础设施项目 `quantstone-data` 配合使用。

## 规划范围

- 模拟时钟与事件调度
- 策略生命周期与回调接口
- 历史行情访问与数据适配
- 订单管理与模拟撮合
- 资金、持仓与交易成本核算
- 回测绩效分析与运行日志
- Web 策略编写与回测操作界面

## 第一版技术方向

回测核心、后端与策略采用纯 Python，复用 `quantstone-data` 的 Python SDK。Python 版本须满足 SDK 的 >=3.11 要求，具体版本和依赖管理方式待确定。Web 前端技术栈另行确定。第一版不引入 Rust 核心或跨语言绑定。

## 当前状态

项目初始化阶段，具体架构与接口待设计。

## 项目上下文

开发者和 AI 开始工作前请阅读 [AGENTS.md](AGENTS.md)，其中集中记录项目要求、架构方向、当前状态及待决事项，并随开发持续更新。

## 代码仓库

[DXKevin/quantstone-backtest](https://github.com/DXKevin/quantstone-backtest)
