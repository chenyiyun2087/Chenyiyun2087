# B2_SCALE40 R16.3: 不可变证券路径注入
## 路径身份断言
- Security paths identical: ❌ (SHA: e4f03b229c78bdf8)
- Exposure paths different: ✅
- C0/C1 use same security path: ✅
- C2/C3 use same security path: ✅

## Shapley 归因 (注入路径)
| 路径 | 收益 | Calmar |
|------|------|--------|
| C0 (S60+S60) | 10.93% | 0.09 |
| C1 (S60+B2) | 40.74% | 0.54 |
| C2 (B2+S60) | 10.93% | 0.09 |
| C3 (B2+B2) | 40.74% | 0.54 |

- 证券路径贡献: +0.00%
- 暴露路径贡献: +29.81%
- 总变化: +29.81%
- 残差: +0.000000