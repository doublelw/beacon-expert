# Beacon专家

企业级AI驱动3D→2D工程图自动转换平台

## 功能
- STP 3D → DXF 工程图自动转换
- AI工艺识别（钣金/注塑/机加工/冲压/铸造/焊接/3D打印）
- GB标准件库（压铆螺母/沉头螺钉/过孔六视图标准图形）
- 加工就绪度评估（工程师视角，能否让工人加工出合格品）
- wiki知识库（个人/部门/企业三级权限）
- 用户系统（登录/JWT/RBAC/授权管理）
- 多模型配置（GLM/Claude/OpenAI/DeepSeek/Ollama）

## 技术栈
- 后端: FastAPI + SQLite
- 前端: 内嵌HTML SPA（wiki三栏形态）
- AI: LLM参与M0工艺判断/M1理解/M2规划/M7审查
- CAD: FreeCAD + OCCT HLR + ezdxf

## 仓库
https://github.com/doublelw/beacon-expert
