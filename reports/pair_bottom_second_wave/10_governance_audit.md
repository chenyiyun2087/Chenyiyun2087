# 10_GOVERNANCE_AUDIT_REPORT

- **研究性质**: `RESEARCH_ONLY`
- **代码分支**: `review/luwei-second-wave-pair-bottom-v1`
- **生产隔离**: 零生产数据库写入，零实盘下单接口调用。
- **Point-in-Time 审查**: 所有用于回测与因子计算的变量均满足 `available_at <= signal_date`。
- **数据准入**: 通过 ASDC 检查，未绕过任何权限门禁。
