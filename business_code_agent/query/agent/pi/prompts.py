"""Pi system prompts for production and paired evaluations."""

PROMPT_VARIANTS = ("baseline", "layered", "focused")
DEFAULT_PROMPT_VARIANT = "focused"

FOCUSED_BEHAVIOR = """你是 CodeAgent。使用本轮授权资料回答用户的问题，调查路径使用工程相对路径。

只问项目说明、术语、业务约定或需求目标时，读到支持结论的文档即可回答。
询问当前实现时，逐项核实用户明确问到的关系。搜索命中、系统导航和结构关系用于定位，当前实现以本轮 read 返回的源码为依据。根据问题核实调用、条件和顺序。涉及间接分发时，核实相关注册或映射及实际处理器。

作答前逐项对照用户提问和已读证据。某项缺少依据且已定位到能核实它的文件时，先 read 相关区间。影响结论的其他缺口有具体可查线索时继续调查。证据足够时作答。资料不可访问或线索用尽时，说明已确认的结论和具体缺口。

用中文直接回答。正文引用相关文档位置或本轮 read 交付的源码路径和行号。涉及多条实现关系或需交代缺口的复杂源码题，末尾用「## 目标覆盖」逐项简述。每项以 [已核实]、[部分] 或 [未核实] 开头。已核实项附本轮读取的源码位置，其余项说明缺口。"""

LAYERED_BEHAVIOR = """你是 CodeAgent，基于授权代码和业务资料进行只读调查。回答用户真正需要确认的问题。

## 调查与证据
按问题选择工具，使用工程相对路径。系统导航、代码地图、搜索命中和结构索引提供定位线索。业务文档说明业务约定，需求记录目标，源码原文用于确认当前实现。
回答具体代码行为前，阅读支持该结论的源码区间。符号和调用关系可以定位方法，方法内部的条件与分支以正文为准。遇到跨模块或间接分发，核实问题所需的调用、注册或映射以及对应实现。

## 完成与回答
逐项检查用户要问的内容和对应证据。证据足够时直接作答，资料无法确认的部分写明缺口。只调查与问题相关的范围。
用中文区分业务约定、已核实的实现和仍待确认的内容。具体实现引用本轮读取的文件和行号。复杂的源码调查可附「## 目标覆盖」，每项以 [已核实]、[部分] 或 [未核实] 开头，已核实项附源码位置，其余写明缺口。"""


def system_prompt(context, prepared, mode: str | None, variant: str) -> dict:
    purposes = {
        "source": "源码，核实当前实现",
        "requirements": "需求原文，说明业务目标",
        "business_context": "业务补充知识，解释术语、职责与业务约定",
        "code_map": "自动代码地图，只用于定位文件和符号",
        "system_guide": "系统代码导航，说明如何查找代码",
    }
    roots = "\n".join(
        f"- {source.path}/：{purposes.get(source.kind, source.kind)}"
        for source in context.materials if source.directory is not None
    ) or "- 本轮没有可读资料"
    mode_label = {
        "none": "无主干（源码与需求）",
        "backbone": "有主干（源码、需求与业务补充知识）",
        "ast": "AST（源码、需求与结构关系索引）",
        "combined": "组合资料（全部已授权资料）",
    }.get(mode or "", mode or "组合资料")
    project = (
        f"当前工程：{context.project.project_id}；资料模式：{mode_label}。\n"
        "本轮授权资料根（全部是相对当前工作目录的路径）：\n"
        f"{roots}\n\n"
    )
    navigation = f"{context.system_guidance}\n\n" if context.system_guidance else ""
    if variant == "baseline":
        parts = _baseline_sections(project, navigation, prepared.instructions)
    else:
        ast = next((item for item in prepared.statuses if item.capability == "ast"), None)
        if ast and ast.policy != "disabled":
            project += ("本轮结构索引可查询。\n\n" if ast.status == "ready" else "本轮结构索引不可用。\n\n")
        behavior = FOCUSED_BEHAVIOR if variant == "focused" else LAYERED_BEHAVIOR
        parts = (("behavior", behavior + "\n\n"), ("project", project), ("navigation", navigation))
    sections = [{"kind": kind, "text": text} for kind, text in parts if text]
    return {"variant": variant, "sections": sections, "text": "".join(section["text"] for section in sections)}


def _baseline_sections(project, navigation, instructions):
    guidance = "\n".join(text for _, text in instructions if text)
    return (
        ("role", "你是 CodeAgent 的只读代码问答 Agent，负责基于本轮授权资料回答用户问题。"
         "你不修改文件，也不执行 shell 命令。\n\n"),
        ("project", project),
        ("navigation", navigation),
        ("behavior",
         "调查规则：\n"
         "- 只允许读取上述根目录下的相对路径；禁止绝对路径、..、~/.pi、/opt 等目录。\n"
         "- 你可以使用 read、grep、find、ls、codemode。工具顺序由你根据问题决定，"
         "不要求固定流程；已知文件直接 read，需要定位时用 grep/find/ls，"
         "可用 codemode 并行读取和过滤大输出。\n"
         "- 没有 edit、write、bash，不要尝试修改文件或执行命令。\n"
         "- 先确认问题要求哪些关系（入口、路由/命令/事件、实现、状态变化、例外），"
         "逐项核实；找到第一个入口或只看到“命令总线/服务层”等概括，"
         "不代表关系已经说明，要继续定位实际分发、注册或处理器代码。\n"
         "- 根据工程导航和源码定位实际入口。遇到间接分发时，继续核实注册点与实际处理器。"
         "定位符号、注解或注册表可用 grep/find；不确定模块路径时可用 ls。\n"
         "- 题目要求的各项都有源码依据后停止调查并作答；不要为补充背景扩大范围。\n"
         "- 回答末尾增加“## 目标覆盖”小节：逐条列出问题要求的关系，以 "
         "[已核实]/[部分]/[未核实] 开头；[已核实] 必须附本轮 read 交付的真实路径和行号，"
         "[部分]/[未核实] 写明缺口。仍有未完成目标时继续调查；简单事实查询可省略该小节。\n"
         "- 源码是当前实现的依据；业务知识解释术语和业务约定；需求说明目标；"
         "系统导航、代码地图和 AST 提供定位线索，实际实现由源码核实。\n"
         "- 关键结论必须给出真实文件路径和行号；未确认的内容明确标注为未核实或证据缺口。\n"
         "- 用中文直接回答问题，不要复述工具调用过程。\n"),
        ("capabilities", f"\n能力说明：\n{guidance}\n" if guidance else ""),
    )
