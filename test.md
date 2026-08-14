| 文件路径 | 大概修改内容 | 预估变更行数 |
|---|---|---:|
| `src/pages/bond/bondTender/modules/screenWithExport/index.tsx` | 接入保存/更新方案及邮件订阅交互，方案上限设为 10；分别为招标页、中标页增加招标方和中标人企业组合筛选，并同步列表、导出及订阅条件。 | 约 180–280 行 |
| `src/pages/bond/bondTender/constants.tsx` | 增加组合筛选配置、招标/中标方案标识、订阅类型及方案配置常量。 | 约 30–60 行 |
| `src/pages/bond/bondTender/types.ts` | 补充招标方、中标人组合条件以及方案订阅相关的筛选参数类型。 | 约 20–40 行 |
| `src/pages/bond/bondTender/utils/index.ts` | 增加筛选结果到列表、导出和保存方案参数的转换逻辑，处理“不限”时不传企业代码。 | 约 40–80 行 |
| `package.json` | 升级高级筛选业务组件版本，以支持债融商机的仅邮箱订阅配置。 | 约 1–3 行 |
| `pnpm-lock.yaml` | 同步高级筛选业务组件升级后的依赖锁定信息。 | 约 10–25 行 |
| `src/pages/bond/bondTender/modules/screenWithExport/index.test.tsx` | 新文件；覆盖招标/中标组合参数映射、方案按钮状态、10 条上限及仅邮箱订阅配置。 | 约 120–200 行 |

**合计：约 401–688 行**