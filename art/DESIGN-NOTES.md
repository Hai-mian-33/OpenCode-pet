# OpenCode Pet 主视觉设计方案

我的首选方案是：以 OpenCode 官方的黑白几何「O」Logo 为原型，设计一只拟人化的像素机器人桌宠。 建议命名为 OpenCode Pet（昵称 OpenO / 小 O）。

不建议另外选择鲸鱼、猫咪、章鱼等动物作为默认形象。你的前三款桌宠已经形成了一条很清晰的设计路线：将对应 AI 编程工具的品牌标志人格化，使它既保留官方品牌辨识度，又能够通过动画传达任务状态。

## 一、OpenCode 应当使用哪个 Logo？

官方浅色版 Logo

官方深色版 Logo

OpenCode 官方品牌标志的核心是一个粗线条、方形几何结构的字母 O，中央留有矩形空间，并通过黑、白、灰三个色调建立层次。整体呈现像素化、极简的开发者工具风格。

官方已经发布独立的品牌资源页面，提供 PNG、SVG，以及浅色和深色版本。

[image](https://www.google.com/s2/favicons?domain=https://opencode.ai\&sz=32)

Brand

+1



可以直接访问：[OpenCode 官方品牌资源与 Logo 下载](https://opencode.ai/brand?utm_source=chatgpt.com)。

对于你的桌宠，我建议采用：

官方 O 形轮廓 + 中央表情区域 + 可动画化的四肢 + 黑白灰主体 + 少量状态强调色。

这样能够最大限度延续原有品牌，又让静态 Logo 变成有生命力的桌面角色。

## 二、从三个已有项目中总结出的设计原则

我查看了三个仓库的 README、目录结构，以及主要的绘制和素材生成代码。它们的技术实现不同，但视觉策略高度一致。

| 项目                                                                                               | 当前主视觉     | 实现方式                                        | OpenCode 可借鉴的部分                  |
| -------------------------------------------------------------------------------------------------- | -------------- | ----------------------------------------------- | -------------------------------------- |
| [DeepSeek Harness Pet](https://github.com/Hai-mian-33/DeepSeek-Harness-Pet?utm_source=chatgpt.com) | 蓝色鲸鱼       | 提取官方 SVG 路径，生成 8 列 × 9 行的动画精灵图 | 保持品牌图形原貌，使用姿态变化表现状态 |
| [ZCode Pet](https://github.com/Hai-mian-33/ZCode-pet?utm_source=chatgpt.com)                       | 黄色拟人 Z     | PyQt5 / QPainter 实时绘制 Z 字形、眼睛与嘴巴    | 最适合 OpenCode 的拟人化方案           |
| [MiniMax Pet](https://github.com/Hai-mian-33/MiniMax-pet?utm_source=chatgpt.com)                   | 官方蓝色吉祥物 | 从 `icon.icns` 提取主体，叠加表情、阴影及光晕   | 素材分层与丰富的表情动画               |

尤其值得借鉴的是 ZCode 项目中的 `ZLogoFace` 和 MiniMax 项目中的 `MascotFace`。这两种方案都不需要为了改变表情而更换整个角色形象，而是让一个稳定的主体根据任务状态实时变化。

## 三、推荐主视觉：OpenO 几何机器人

概念草图 · OpenO

原创桌宠拟人化示意，非官方 Logo

保留 O 形粗边框与中央上下分色结构，添加极简五官、短手短脚和状态指示灯。正式版本应以官方 SVG 为几何基础重新绘制。

我建议主视觉遵循以下规格：

| 设计元素   | 建议                                                        |
| ---------- | ----------------------------------------------------------- |
| 整体形态   | 竖直方形机身，短手短脚，类似一个活过来的 O 字母             |
| 主体颜色   | `#202020` 深炭黑                                            |
| 面部颜色   | `#F8F8F7` 暖白                                              |
| 下半部分   | `#C9C9C9` 浅灰                                              |
| 眼睛与嘴巴 | 深色，简洁、几何化                                          |
| 设计风格   | 极简几何 + 轻像素风 + 少量拟人化                            |
| 默认体型   | 约 110 × 140 逻辑像素，支持缩放                             |
| 动画原则   | 保持 O 形主体不变，主要改变眼睛、嘴巴、倾斜角度和状态指示灯 |

外轮廓最好坚持方角，而不是做成常见的圆角电视机器人。这样能够突出 OpenCode 原本的 Logo 特征。

## 四、不同任务状态下的主视觉

沿用前三款桌宠的状态系统，我建议设计八种基本状态：

| 状态             | 表情及动作                 | 状态提示     |
| ---------------- | -------------------------- | ------------ |
| Idle 待机        | 眨眼、轻微呼吸、左右观察   | 灰色         |
| Thinking 思考    | 目光上移，主体微微倾斜     | 黄色         |
| Executing 执行   | 眼睛专注，机身有节奏地弹动 | 蓝色         |
| Waiting 等待授权 | 睁大眼睛，橙色指示灯闪烁   | 橙色         |
| Done 完成        | 弯眼微笑、轻轻跳跃         | 绿色         |
| Error 错误       | 沮丧表情，轻微左右晃动     | 红色         |
| Drag 拖拽        | 双脚收起，朝拖拽方向倾斜   | 无需额外颜色 |
| Long Task 长任务 | 专注表情，缓慢重复工作动画 | 蓝色呼吸灯   |

这里有一个重要设计选择：尽量不要让颜色覆盖整个 O 形机身。 黑白灰应当成为角色的永久特征，彩色仅承担状态表达功能。

例如，正在执行任务时不需要让整个机器人变蓝，只需要蓝色状态灯和动作变化即可。这一点也能够保证桌宠在小尺寸下仍然易于识别。

## 五、Logo 应当分成两套资源

官方品牌 Logo

用于 README 中的 OpenCode 标识、介绍文档、产品来源说明。直接从官方品牌资源页面获取 SVG，不自行改变官方标志的含义。

OpenCode Pet 自有 Logo

用于桌宠托盘图标、安装包、GitHub 头像以及动画资源。采用拟人化 OpenO 形象，与原始品牌标志明显区分。

OpenCode 的代码采用开源许可证，但代码许可证不应被直接视为对品牌 Logo 的无限制授权。建议在项目 README 中明确标注这是独立的非官方桌宠项目，与 OpenCode 官方团队无隶属关系。OpenCode 上游 README 也专门要求相关衍生项目作出此类说明。

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub

+1



## 六、实际开发建议

结合你已有项目，我认为最值得采用的技术组合是：

ZCode 的 PyQt5 实时绘制 + MiniMax 的图片分层与状态表情 + DeepSeek 的动画资源规范。

素材目录可以按下列结构组织：

```
OpenCode-pet/
├── assets/
│   ├── opencode-official.svg
│   ├── openo-base.svg
│   ├── openo-tray.png
│   ├── openo-shadow.png
│   └── sprites/
│       ├── idle/
│       ├── thinking/
│       ├── executing/
│       ├── waiting/
│       ├── done/
│       ├── error/
│       └── drag/
├── docs/
│   └── screenshots/
└── opencode_pet.py
```

在任务状态接入方面，OpenCode 比你之前适配过的一些客户端更方便：它有官方插件事件系统，提供 `session.status`、`session.idle`、`permission.asked`、`permission.replied` 和 `tool.execute.before/after` 等事件；服务器还提供 SSE 事件流。可以优先走这条路线，而不是依赖桌面窗口截图判断任务状态。

[image](https://www.google.com/s2/favicons?domain=https://opencode.ai\&sz=32)

OpenCode

+1



## 七、最终选择

| 主视觉路线           | 品牌辨识度 | 动画扩展性 | 我的评价           |
| -------------------- | ---------- | ---------- | ------------------ |
| OpenO 几何机器人     | 极高       | 高         | 最适合你的桌宠系列 |
| 官方 O Logo 原样跳动 | 极高       | 中         | 适合追求极简的版本 |
| 全新动物吉祥物       | 较低       | 高         | 不建议作为默认形象 |

最终建议：使用官方黑白几何 O 作为造型基础，以拟人化 OpenO 机器人作为项目 Logo 和桌宠主体。

这样 DeepSeek 是鲸鱼、ZCode 是黄色 Z、MiniMax 是蓝色吉祥物、OpenCode 是黑白像素 O。四个项目放在一起时，既有鲜明的品牌差异，又具备统一的系列感。