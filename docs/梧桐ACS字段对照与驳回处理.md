# ACS 字段对照与本次驳回处理

核对对象：用户提供的 ROS2 智能体 ACS，与本项目五份 ACS。只比较文件声明，没有测试对方的服务、时延或认证。

| 字段或内容 | 对方文件 | 我方当前材料 |
|---|---|---|
| entityMeta | 填写流水线角色、上下文、输出结构、校验、时延和目标用户 | 未填写；这是可选自定义元数据 |
| provider.countryCode | CN | 未填写；国家代码为可选资料 |
| certificate.requestedValidity | 365 | 未指定；是期望有效期，不是已签发期限 |
| securitySchemes | 填写 mtls mutualTLS | 字段已存在，内容为空，待真实认证部署 |
| endPoints | 填写 HTTPS JSON-RPC 地址、协议和认证引用 | 字段已存在，数组为空，待真实 AIP 服务部署 |
| webAppUrl | 未填写 | 本次增加 http://129.204.33.138:5173/，已实际打开网页 |
| defaultInputModes / skills[].inputModes | 声明文本与 JSON 输入，部分技能仅文本 | 当前声明 text/plain；文本内的 JSON 参数不等于 JSON 数据项输入已兼容 |
| skills[].outputModes | 部分技能声明 JSON 和文本 | 当前只声明 application/json |
| skills[].examples | 两项技能均提供 | 库存总览、未关闭质量问题查询各一条无参示例，其余省略，未编造业务 ID |
| description | 包含输入、输出、上下文、失败分支、能力边界和性能等细节 | 已有正式业务范围和授权边界；公开机器调用契约仍较简略，值得按实际实现补充 |
| aic / active / lastModifiedTime | 未在该示例中列出 | 保留首次申请值，正式值由平台相应流程维护 |

严格按根级字段比较，修正前我方仅缺少对方的 entityMeta；认证和端点是已有字段但内容为空，MIME 数组则是能力声明不同。字段数量不能替代入口和功能验证。entityMeta、countryCode、requestedValidity 都不是此次截图明确指出的驳回原因。

本次实际驳回意见：至少一个有效 Endpoint 或 webAppUrl。已为五份材料补真实网页入口；用户截图对应 Leader，重新提交应使用新版 01-leader.acs.json。官网 Schema 的结构校验与平台自动准入审核是两个检查层次，前者通过不证明后者通过。

官方定义：[ACS v2.2.0](https://github.com/AIP-PUB/ACPs-community/blob/v2.2.0/acps-specs/03-ACPs-spec-ACS/ACPs-spec-ACS.md)。entityMeta 的内部键由提供方自定义；不能复制 ROS2 版本、无状态、性能数据、重试安全等声明到我方。
