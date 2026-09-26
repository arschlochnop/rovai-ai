---
document_type: protocol-contract
contract: team-presets-v1
authority: named-camp-creation-team-presets
status: draft
version: 1
source_version: v1.70
last_updated: 2026-09-25
---

# Team Presets v1

队伍预设（Team Preset）是 Initial Camp Membership 与 Initial Default Lead 的具名创建输入，只在创建 Camp 时读取。
Camp 不记录来源预设，之后的 Dynamic Camp Membership 与预设无关。它不是 Project、Camp roster 或任何领域实体。

本合同是对 [Host Web v2 Shared creation preferences](host-web-v2.md#shared-creation-preferences) 的纯新增：
`new-conversation-preferences.json`、`preferences.newConversation.*` 的请求、一键创建、自动失效与确认 latch、
Desktop 私有 initialize 以及设置中的默认队员编辑器，语义全部不变。

状态：本地试用草案，未与上游对齐，不进入版本记录。

## 1. 存储

Core 在所选 data root 下保存 `team-presets.json`，使用平台私有文件发布原子写入，Core 数据库锁覆盖整个读、改、
发布过程，与 `new-conversation-preferences.json` 共用同一把锁。

```jsonc
{
  "schemaVersion": 1,
  "presets": [
    {
      "id": "tp_<uuid>",
      "name": "后端开发",
      "description": "负责服务端接口、数据访问与并发错误路径。",
      "memberAgentIds": ["agent_a", "agent_b"],
      "leadAgentId": "agent_a",
      "revision": 1
    }
  ]
}
```

- 结构封闭，拒绝未知字段。文件不存在等价于 `{schemaVersion:1, presets:[]}`。
- 文件不可读、超过 262,144 字节或校验失败时返回错误，不得当作空快照。
- `presets` 按创建顺序保存，最多 32 条。
- `description` 是可选介绍：旧记录缺该字段时读为空字符串，成功保存后写回完整字段。不需要用户可见的迁移流程。

## 2. 校验

| 字段 | 规则 |
| --- | --- |
| `id` | 由 Core 生成，格式为 `tp_` 加 UUID v4；客户端不能指定新 ID |
| `name` | 去除首尾空白并把连续空白压成一个空格后，长度为 1 到 40 个 Unicode 字符；按小写比较，全库唯一 |
| `description` | 可选，默认空字符串；去除首尾空白、保留正文换行；最多 200 个 Unicode 字符；不参与名称唯一性，也不用于生成 Camp 名称 |
| `memberAgentIds` | 与现有默认队伍相同：非空，最多 100 个，不重复，每个 ID 非空且不超过 200 字符，保存时每个 ID 都能查到 AgentProfile |
| `leadAgentId` | 必须明确指定，且必须在 `memberAgentIds` 中 |
| `revision` | 由 Core 维护，新建为 1，每次成功更新加 1 |

任何一条失败都拒绝整个请求，不写入任何文件。

## 3. 请求

三个请求的参数都封闭，Desktop 与 Web 均可调用，返回值都是第 4 节的快照。

| 请求 | 参数 | 行为 |
| --- | --- | --- |
| `preferences.teamPresets.list` | `{}` | 只读 |
| `preferences.teamPresets.save` | `{preset:{id,name,description,memberAgentIds,leadAgentId}, expectedRevision}` | `id` 为 `null` 时新建，`expectedRevision` 必须为 `null`；否则更新，`expectedRevision` 必须等于当前 revision。`description` 可省略，省略等价于空字符串 |
| `preferences.teamPresets.delete` | `{id, expectedRevision}` | 删除 |

- revision 不匹配时返回错误 `team_preset_revision_conflict`，快照不变；客户端应重读后再操作。
- 预设不存在返回 `team_preset_not_found`；名称重复返回 `team_preset_name_taken`；超出数量返回 `team_preset_limit_reached`。
- 所有 `team_preset_*` 错误必须作为 Core RPC `ErrorBody` 的稳定 `code` 暴露，`kind=domain_rejection`、
  `retryable=false`；Renderer 不得通过匹配 `message` 文本分支。

这些请求不得读取或写入 `new-conversation-preferences.json`，也不得发出
`preferences.new_conversation_changed`。设置中的默认队员、一键创建及其确认 latch 与队伍预设不存在关联关系。

## 4. 快照

```ts
interface TeamPresetsSnapshot {
  presets: TeamPreset[]
}
interface TeamPreset {
  id: string
  name: string
  description: string
  memberAgentIds: string[]
  leadAgentId: string
  revision: number
}
```

任何成功的写入都发出 `preferences.team_presets_changed`，客户端收到后重读快照。

## 5. 创建时的使用

- 新建对话 Dialog 提供互斥的两种创建模式：`自定义队伍` 与 `选择已有队伍`。默认进入现有的自定义队伍模式，
  完整保留原有成员与队长编辑行为；选择已有队伍模式时必须选定一个预设。
- 选择已有队伍后，Dialog 展示该队伍明确保存的成员与队长，但不允许在本次创建中增删成员或更换队长；需要不同
  组合时，用户应切回自定义队伍模式，或先到队伍页编辑/新建队伍。
- 选择的队伍含已移除或当前不可用队员时，禁止提交并明确提示用户到队伍页修正；不得静默过滤成员、回退队长，
  或把预设转换为可编辑的自定义输入。
- 自定义模式保持现有 `camps.create` 输入；已有队伍模式不得提交可编辑的成员和队长副本，而是提交
  `teamPresetSelection:{id,expectedRevision}`。两个输入分支必须且只能出现一个：

```ts
type CreateCampTeamInput =
  | { memberAgentIds: string[]; defaultLeadAgentId: string; teamPresetSelection?: never }
  | {
      memberAgentIds?: never
      defaultLeadAgentId?: never
      teamPresetSelection: { id: string; expectedRevision: number }
    }
```

- Core 在同一把数据库锁内、执行新的 `camp.create` command 前读取队伍：不存在返回
  `team_preset_not_found`，revision 不一致返回 `team_preset_revision_conflict`，任一成员已不存在或不可用返回
  `team_preset_members_unavailable`。只有校验通过后，Core 才把队伍的 `memberAgentIds` 与 `leadAgentId` 解析为
  command 的 Initial Camp Membership 与 Initial Default Lead。
- `commandId` 已有持久结果时必须先返回原结果，不得因为队伍后来修改或删除而把同一 command 的重试改成失败。
  新 command 才解析队伍。Camp 只保存解析后的成员与队长，不保存队伍 ID，也不与队伍建立后续关联。
- 上一条保证只覆盖队伍解析。`camps.create` 在到达 command replay 前既有的 workspace 选择与授权检查保持不变；
  workspace 已删除、移动或失去授权时，继续按现有行为失败，不在本合同中扩大重放边界。
- 自定义模式的 Core 准入不变；两种模式最终都继续执行现有 Camp membership 与 default lead 校验。
- 预设本身没有确认 latch。保存时每个队伍都必须有非空成员集合，且 `leadAgentId` 明确指向其中一位成员。
- 保存允许引用仍存在但当前不在场的 AgentProfile；创建时每位成员都必须为 `present`，否则返回稳定错误码
  `team_preset_members_unavailable`，由界面提示到队伍页修正。
- 一键创建只读取默认记录，与本合同无关。
- 在自定义模式和已有队伍模式之间切换时，Renderer 保留尚未提交的自定义草稿；提交 payload 只包含当前模式的
  输入，关闭 Dialog 后仍按现有规则清理草稿。

## 6. 界面与传输

- Renderer 在侧边栏“队员”之后新增一级入口“队伍”，Web 复用同一组件。队伍页不写入可恢复位置。
  移动端在“队员”页顶部提供“队员｜队伍”切换，不新增底部入口。
- Desktop Main 的 Renderer Core 方法白名单、`CoreMethod`、Web 客户端白名单与 rovai-web 操作集各增加这三个请求。
  Host Web 协议版本不变。
