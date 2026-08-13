# fate staynight 汉化

`Fate/stay night [Realta Nua]` PS Vita 版（`PCSG00122`）简体中文汉化项目，同时保留了本次汉化使用的自动化工具链。

> 本项目不包含密钥或绕过 DRM 的工具。使用者必须拥有正版游戏，并自行负责遵守所在地法律与软件许可。

## 汉化包

可直接安装的补丁和说明位于 [Fate/stay night [Realta Nua] PSV 简体中文汉化包](release/README.md)。

当前版本已在 PSV 实机启动验证：79,606 条剧情文本已写入，8 套中文字体可正常显示。已知问题是中文字形与原版日文字体的风格不完全一致。

## 它解决什么问题

典型的 Vita 汉化流程并不只是“把日文交给模型”：

1. 从自己的 PSV 只读提取资源；
2. 识别游戏引擎和资源容器；
3. 无损解包脚本并保留控制码；
4. 提取带稳定 ID 的文本；
5. 批量翻译、统一术语、断点续跑；
6. 校验控制码并合并译文；
7. 为中文字形建立编码槽位映射；
8. 重建脚本、字体与资源包；
9. 只部署到 `ux0:rePatch/`；
10. 在真机上由小到大验证。

本仓库提供通用安全层、翻译管线和 HuneX/MZX/MZP 格式参考工具。不同游戏通常需要实现自己的资源与字体适配器。

## 安全边界

`psvctl` 的远程路径策略写死在代码中：

- 可读取：`ux0:/app`、`ux0:/patch`、`ux0:/rePatch`
- 可写入：仅 `ux0:/rePatch`
- 拒绝 `..` 路径穿越、其他分区和直接覆盖游戏本体
- 下载默认拒绝覆盖本地文件
- 上传完成后校验远程文件大小

即使参数写错，工具也不会向 `ux0:/app` 或 `ux0:/patch` 写入。安装补丁前仍建议备份存档，并先用单句或单文件补丁验证。

## 环境要求

- Python 3.9 或更高版本
- 一台能运行 VitaShell FTP 的 PS Vita
- PSV 与电脑处于同一可信局域网
- 目标游戏所需的解包/重打包工具
- 可选：支持结构化 JSON 输出的翻译模型 CLI

核心工具只使用 Python 标准库。开发与测试不需要连接 PSV。

## 快速开始

### 1. 安装

可以直接克隆后运行，也可以安装命令：

```bash
python3 -m pip install -e .
psvctl --help
```

不安装时使用：

```bash
./psvctl --help
```

### 2. 连接 VitaShell

在 PSV 上打开 VitaShell，按 `SELECT` 启动 FTP，记下屏幕显示的 IP 和端口。然后在电脑运行：

```bash
./psvctl configure 192.168.1.123 1337
./psvctl status
./psvctl titles
./psvctl ls ux0:app/
```

连接信息只保存在 Git 忽略的 `config.json` 中。VitaShell FTP 是明文协议，只应在可信局域网中使用。

### 3. 只读提取

```bash
./psvctl pull \
  ux0:app/TITLEID/path/to/archive.bin \
  projects/my_game/original/archive.bin
```

某些游戏通过普通 FTP 读取到的是加密视图，需要在 VitaShell 中使用 **Open decrypted / 解密打开** 后再从对应 FTP 会话复制。具体行为取决于系统、插件和游戏版本；不要把解密资源提交到 Git。

### 4. 建立项目工作区

推荐目录：

```text
projects/my_game/
├── original/      # 原始只读备份
├── decrypted/     # 自行提取的可读资源
├── extracted/     # 解包脚本与待翻译文本
├── translation/   # 术语表、提示词和人工修订
├── build/         # 重建产物
└── repatch/       # 最终部署树
```

这些目录中的游戏数据默认不应进入公开仓库。

### 5. 文本提取与翻译

HuneX 参考实现可从解压后的 `.ini` 脚本提取 JSONL：

```bash
python3 tools/extract_dialogue.py \
  projects/my_game/extracted/scripts \
  projects/my_game/extracted/dialogue.jsonl
```

每条记录包含稳定 ID、脚本名、指令位置、原始模板和可读文本。翻译模型只需返回 `id` 与 `translation`；控制码应先在本地替换成短占位符，模型返回后再恢复并校验。

翻译检查点可合并回完整 JSONL：

```bash
python3 tools/merge_translations.py \
  --source projects/my_game/extracted/dialogue.jsonl \
  --checkpoint-dir projects/my_game/extracted/translation/batches \
  --output projects/my_game/extracted/dialogue.zh-Hans.jsonl
```

完整字段约定、批处理策略和适配步骤见 [自动化流水线](docs/PIPELINE.zh-CN.md)。

### 6. 构建与部署

先在本地完成解包—重建往返测试：没有修改时，重建文件应尽可能与原文件字节一致；至少必须能重新解包并得到同样内容。

将最终补丁树上传到 rePatch：

```bash
./psvctl push \
  projects/my_game/repatch/path/to/archive.bin \
  ux0:rePatch/TITLEID/path/to/archive.bin
```

推荐按以下顺序上机：单句 → 单场景 → 单章节 → 全量。每一步都保留可回滚的补丁副本。

## 仓库工具

| 工具 | 用途 |
|---|---|
| `psvctl.py` / `psvctl` | 受限 VitaShell FTP 读取与 rePatch 部署 |
| `tools/mzx_codec.py` | MZX0 解压/重压参考实现 |
| `tools/mzp.py` | MZP (`mrgd00`) 解包/重建参考实现 |
| `tools/extract_dialogue.py` | 从 HuneX 脚本提取稳定 ID 的 JSONL |
| `tools/translate_with_agy.py` | 检查点式结构化批量翻译参考实现 |
| `tools/merge_translations.py` | 合并并核对翻译检查点 |
| `tools/patch_font_slots.py` | 字体编码槽位替换参考实现 |
| `tools/patch_mzx.py` | 单条 MZX 文本替换验证工具 |
| `tools/make_manifest.py` | 为本地资源生成尺寸与哈希清单 |

## 设计原则

- 原始资源只读，修改内容只进入构建目录和 rePatch。
- 所有文本使用稳定 ID，不依赖模糊字符串匹配。
- 控制码由程序保护，不把结构正确性寄托在模型上。
- 每批结果原子落盘，随时可从检查点恢复。
- 模型只负责语言，程序负责结构、恢复和校验。
- 先验证格式往返，再扩大汉化范围。
- 不把游戏数据、账号凭据或模型登录缓存提交到仓库。

## 测试

```bash
python3 -m unittest discover -v
```

## 已知限制

- Vita 游戏使用多种引擎与专有资源格式，不存在真正通吃的解包器。
- 字符编码、字体图集、文本宽度和换行规则必须针对具体游戏验证。
- `translate_with_agy.py` 是可替换的模型适配示例，不是运行核心流程的必要条件。
- FTP 上传只能验证传输和文件大小，不能替代真机启动与剧情回归测试。

## 许可与贡献

本项目代码采用 [MIT License](LICENSE)。第三方工具遵循各自许可证，不应未经确认直接复制进本仓库。提交问题时请提供引擎/格式、错误日志和最小化的自制测试样本，不要另行上传未修改的原版或解密资源。
