# Beacon专家 · 测试计划 v1

> 方案审计暂停(R1=64→R2=77→R3=83, 设计层面已完整)
> 进入: 测试计划 → 审计 → 修改 → 审计 loop → 全覆盖后开发

## 测试范围

基于 PRD v4 的 7 Phase + Phase 0，测试覆盖全部功能/边界/异常/安全/AI/性能。

---

## Phase 0: 引擎代码去硬化 (迁移正确性)

### T0.1 路径去硬化验证
- [ ] `grep -r "sys.path.insert" beacon-expert/src/engine/` → 0命中
- [ ] `grep -r "/Users/ahs" beacon-expert/src/engine/` → 0命中(除注释)
- [ ] `grep -r "/Applications/FreeCAD" beacon-expert/src/engine/` → 0命中
- [ ] config.FC_BIN = shutil.which('freecadcmd') or env or platform fallback → 正确探测
- [ ] 跨平台验证: macOS探测 / Linux探测(which freecadcmd) / 环境变量覆盖

### T0.2 冒烟测试(固定板端到端)
- [ ] `python3 beacon-expert/src/engine/pipeline.py /path/固定板.stp` → 生成DXF
- [ ] verify_report.json pass_rate = 100%(177/209, 32遮挡不计)
- [ ] eval_manufacturability 加工就绪度 ≥ 18/20
- [ ] 生成的DXF在CAD打开, 孔位/折弯/冲压位置正确
- [ ] adapter.py复制后import成功(beacon-expert/src/llm_adapter.py)

---

## Phase 1: 用户系统 + 认证 + 模型配置

### T1.1 用户注册/登录
- [ ] POST /api/auth/register {email,password} → 201 + 返回user_id
- [ ] 密码bcrypt哈希(非明文存储) → 验证DB中password_hash≠password
- [ ] 重复注册同email → 409 Conflict
- [ ] POST /api/auth/login {email,password} → 200 + JWT token
- [ ] 错误密码 → 401 Unauthorized
- [ ] JWT过期后请求 → 401(过期拒绝)
- [ ] JWT refresh → 新token(旧token失效)

### T1.2 RBAC权限
- [ ] admin → 可访问全部用户/知识库/转换历史
- [ ] engineer → 仅自己+同部门+企业级知识库
- [ ] viewer → 只读(GET可用, POST/DELETE拒绝)
- [ ] dept_manager → 管理本部门用户(增删改) + 不能跨部门
- [ ] 越权访问(普通用户访问admin端点) → 403 Forbidden
- [ ] scope_filter单元测试: personal跨用户隔离/dept跨部门隔离/enterprise全员可见/admin全量

### T1.3 知识库三级权限
- [ ] 创建personal知识库 → 仅自己可见
- [ ] 创建dept知识库 → 同部门可见, 跨部门不可见
- [ ] 创建enterprise知识库 → 全员可见
- [ ] 删除知识库 → 仅owner或admin可删
- [ ] 授权: admin给engineer加特定知识库访问权限 → engineer可见

### T1.4 模型配置
- [ ] GET /api/settings/llm → 返回当前provider/model/base_url(key隐藏)
- [ ] POST /api/settings/llm {provider,api_key,base_url,model} → 持久化config.json
- [ ] API key加密存储(fernet, 非明文)
- [ ] 测试连接按钮 → GLM/Claude/OpenAI/DeepSeek/Ollama各provider连通性
- [ ] provider切换 → 后续LLM调用使用新provider
- [ ] 无效API key → 测试连接返回错误信息

---

## Phase 2: CAD转换引擎 (M0-M8 + AI)

### T2.1 异步转换流程
- [ ] POST /api/convert/upload 固定板.stp → 201 + task_id
- [ ] GET /api/tasks/{id} → 状态轮询(queued→classifying→...→done)
- [ ] 并发提交3件 → semaphore限制, 2件并行1件排队
- [ ] work_dir隔离: 3个task的中间JSON不互相覆盖
- [ ] 进程崩溃恢复: kill进程 → 重启 → processing超时任务重投
- [ ] 断点续跑: work_dir有checkpoint → 从最后成功阶段重起
- [ ] 超时分级: M0分类60s超时/M3投影600s超时/M7审查120s超时

### T2.2 M0 AI工艺判断
- [ ] 固定板.stp → LLM判断 sheet_metal(置信度>0.7)
- [ ] 前壳.stp → LLM判断 injection_molding(置信度>0.7)
- [ ] LLM不可用 → 规则兜底(veritas.py分类)
- [ ] Pydantic校验失败 → 重试(max 3)
- [ ] 3次重试失败 → 降级到规则分类 + degraded=true标记
- [ ] 置信度<0.7 → 前端弹窗"请确认工艺类型"

### T2.3 M1-M2 AI理解+规划
- [ ] M1: veritas.json → LLM描述"钣金固定板,94孔,13折弯..."
- [ ] M2: LLM规划DrawingPlan → Pydantic校验字段匹配render_engine
- [ ] M2输出字段不匹配 → 重试(字段名严格)
- [ ] 记忆注入: 有历史记忆时 → M2规划包含用户偏好(φ vs Ø)
- [ ] M2 3次失败 → 降级到gen_ai_plan.py规则模板

### T2.4 M3-M6 投影+渲染
- [ ] M3 HLR三路 → proj_v3.json(六视图几何)
- [ ] 特征ID关联 → circles/arcs带hole_type(镜像视图匹配)
- [ ] M4-M6 render → DXF(标准件GB图形+标注+图框+技术要求)
- [ ] 固定板DXF → verify pass_rate=100% + eval加工就绪度≥18/20
- [ ] 标准件图形: 压铆3圈花形/沉头3圈/过孔单圆(各视图区分)

### T2.5 M7 双层审查
- [ ] 第一层 auditor.py确定性检查 → 孔数/尺寸/视图数零误差
- [ ] 第二层 audit_ai.py LLM审查 → "能加工吗?缺什么?"
- [ ] 两层冲突: auditor pass + LLM fail → done+warnings
- [ ] 两层冲突: auditor fail → failed(确定性错误)
- [ ] readiness_score = 0.6*eval + 0.4*llm → 输出综合分

### T2.6 freecadcmd进程隔离
- [ ] 并发2件 → 各自独立HOME=tmp/fc_home_{uuid}
- [ ] freecadcmd一次性执行退出(无常驻全局状态)
- [ ] PYTHONPATH隔离(无sys.path.insert污染)

---

## Phase 3: 前端SPA

### T3.1 登录/导航
- [ ] 未登录 → 跳转登录页
- [ ] 登录成功 → 三栏wiki布局
- [ ] 左栏知识库树(按权限过滤)
- [ ] 右栏Beacon面板(上传/状态/预览)

### T3.2 CAD转换面板
- [ ] 拖拽STP上传 → 显示进度
- [ ] AI工艺判断结果 → 显示(置信度+可修改)
- [ ] 会话补充加工要求 → 对话框
- [ ] 转换完成 → DXF预览 + 加工就绪度评分 + 下载按钮

---

## Phase 4: AI会话

### T4.1 多轮会话
- [ ] 用户上传 → AI"这是钣金件,确认?" → 用户"是" → 继续
- [ ] 用户"压铆用BSO-M6" → AI记忆存储 → 下次自动应用
- [ ] 会话上下文保持(M0→M1→M2连贯)
- [ ] 用户中途修正 → 重新规划

---

## Phase 5: 图纸/零部件效率系统

### T5.1 图纸库
- [ ] 转换完成 → 自动存入图纸库
- [ ] 搜索图纸(按名称/工艺/标签)
- [ ] 图纸详情(STEP/DXF/特征/标注)

### T5.2 变体派生
- [ ] 选图纸→"创建变体"→调参数(放大1.2倍)
- [ ] AI判断: 直接缩放DXF(2秒) vs 重跑pipeline(2分钟)
- [ ] 变体DXF → 存库(parent_id标记)
- [ ] 增减孔/换材料 → 重跑pipeline

---

## Phase 6: 智能记忆系统

### T6.1 记忆存储/检索
- [ ] 用户纠正"固定爪是2个孔" → 存MemoryEntry
- [ ] 下次转换 → build_context注入"固定爪2个孔" → 正确识别
- [ ] 用户偏好"用φ不用Ø" → M2规划应用
- [ ] 项目知识"客户A都是SPCC" → M0工艺判断应用

---

## Phase 7: 部署运维

### T7.1 Docker部署
- [ ] docker-compose up → 服务启动(:8767)
- [ ] SQLite数据卷挂载(持久化)
- [ ] Dockerfile FROM freecad/freecad → freecadcmd可用

### T7.2 监控
- [ ] /api/health → freecadcmd可用+LLM连通+磁盘水位
- [ ] LLM成本统计(每次调用记录)
- [ ] SQLite日备份(backup/)
- [ ] 磁盘告警(work_dir超阈值)

---

## 回归测试集 (fixtures)

### T8.1 钣金(已有)
- [ ] fixtures/sheet_metal/固定板.stp + expected_dxf + eval_score
- [ ] fixtures/sheet_metal/test_block.stp + expected_dxf
- [ ] fixtures/sheet_metal/(再找2件不同结构)

### T8.2 注塑(待收集)
- [ ] fixtures/injection/前壳.stp + expected_dxf + eval_score

### T8.3 机加工(待收集)
- [ ] fixtures/machining/(找1件)

### 回归CI
- [ ] 每次push → 自动跑全部fixtures → eval/verify/audit三口径分别报告

---

## 安全测试

### T9.1 上传安全
- [ ] 非STP文件上传 → 拒绝(magic number校验)
- [ ] 超大文件(>50MB) → 拒绝
- [ ] 路径遍历(../../etc/passwd) → 拒绝(realpath边界)
- [ ] 恶意STEP(含脚本注入) → freecadcmd沙箱隔离

### T9.2 认证安全
- [ ] 无JWT访问API → 401
- [ ] 伪造JWT → 拒绝(签名校验)
- [ ] JWT密钥轮换 → 双密钥并行期+旧密钥过期
- [ ] CORS白名单(非*)

### T9.3 数据安全
- [ ] API key非明文存储(fernet加密)
- [ ] STP文件存储隔离(/storage/tasks/{uuid}/沙箱)
- [ ] 转换完成后work_dir清理策略(定时清理+容量告警)
