"""An original illustrative manuscript; never presented as a published paper."""
from datetime import datetime, timezone
from .storage import refresh_progress


def make_demo():
    sections = [
        ("Abstract", "摘要", [
            ("Reading is a dialogue between what a paper says and what a reader already knows. When the language of a scientific article becomes a barrier, this dialogue can lose its rhythm. This illustrative manuscript explores a simple idea: keep the original paragraph visible and place its translation immediately below it.", "阅读是一场对话，连接着论文所述的内容与读者已有的知识。当科学论文的语言成为障碍时，这场对话的节奏便可能被打断。这篇示例文稿探讨了一个简单的想法：保留原文段落，并将译文紧接着放在下方。"),
            ("The following text is an original product demonstration, not a published research article. Its purpose is to show paragraph alignment, section navigation, and a calm reading experience. Import a PDF to begin working with your own research material.", "以下内容是原创的产品演示文稿，并非已发表的研究论文。它用于展示段落对照、章节导航与舒适的阅读体验。导入 PDF，即可开始阅读和翻译自己的研究材料。")]),
        ("1  Introduction", "1  引言", [
            ("A good reading environment gives ideas enough space to breathe. Academic articles contain dense arguments, unfamiliar terminology, and carefully qualified claims. A reader should be able to move between the source and the translation without losing the structure of the argument.", "良好的阅读环境应该为思想留出充足的空间。学术论文包含密集的论证、不熟悉的术语，以及经过谨慎限定的结论。读者应当能够在原文与译文之间自然切换，同时把握论证的结构。"),
            ("Paragraph-level alignment keeps each translation close to its evidence. It also makes comparison practical: a technical term, an assumption, or a limitation can be checked against the original sentence as soon as it appears. The original remains the reference for interpretation.", "段落级对照使每段译文都紧邻其依据，也让比对更加便捷：技术术语、研究假设或局限性一旦出现，读者就可以立即对照原句。原文始终是理解和解释的依据。")]),
        ("2  A paragraph-first workflow", "2  以段落为中心的工作流程", [
            ("The workflow begins with a local PDF. Text is extracted page by page and organized into paragraphs, while detected headings form an outline. For a two-column article, reading order should follow the left column before moving to the right column wherever the page structure permits.", "工作流程始于一份本地 PDF。程序逐页提取文本并整理为段落，同时根据识别到的标题生成目录。对于双栏论文，在页面结构允许时，阅读顺序应先沿左栏向下，再转入右栏。"),
            ("Each paragraph is sent to the configured language model for translation. Results are saved as they arrive, so a long document can be read before the final paragraph has been processed. Pausing the translation preserves the work already completed.", "每个段落都会发送至所配置的语言模型进行翻译。译文返回后即保存，因此无需等待整篇处理完毕就能开始阅读。暂停翻译时，已经完成的结果会被保留。")]),
        ("3  Reading with context", "3  结合上下文阅读", [
            ("Navigation connects the outline to the body of the article. Readers can jump to a section, search for a recurring term, or return to an earlier paragraph. A simple progress indicator describes the translation state without competing with the content for attention.", "导航将目录与文章正文连接起来。读者可以跳转至某个章节、检索反复出现的术语，或返回之前的段落。简洁的进度指示用于呈现翻译状态，避免分散读者对内容的注意力。"),
            ("Language models can make mistakes, especially when technical notation has been damaged during PDF extraction. Equations, tables, and figures may therefore require consultation of the original PDF. A useful reader makes that source easy to open.", "语言模型可能出错，尤其是在 PDF 提取过程中技术符号受损时。因此，公式、表格和图片可能仍需结合原始 PDF 查阅。实用的阅读工具应当让读者能够便捷地打开原始文档。"),
            (r"Mathematical notation stays close to the explanation. For example, a sequence can move from step $k$ to step $k+1$, while a weighted sum is written as \[y = \sum_{i=1}^{n} w_i x_i.\] This is an illustrative formula for checking the reading layout.", r"数学符号应紧邻解释。例如，一个序列可从第 $k$ 步推进到第 $k+1$ 步，而加权和可以表示为 \[y = \sum_{i=1}^{n} w_i x_i.\] 这是用于检查阅读排版的演示公式。")]),
        ("4  Conclusion", "4  结语", [
            ("The goal is a focused reading process: bring in a paper, translate at a comfortable pace, and keep the original close at hand. Clear typography and persistent progress support that process, leaving the reader free to concentrate on the ideas.", "我们的目标是让阅读更专注：导入论文，以合适的节奏翻译，并让原文始终触手可及。清晰的排版与持续保存的进度为这一过程提供支持，让读者能够将注意力集中在思想本身。")])
    ]
    paras, outline = [], []
    for index, (title, translation, pairs) in enumerate(sections):
        sid = f"s-{index + 1}"
        pid = f"p-{len(paras) + 1}"
        outline.append(dict(id=sid, title=title, level=1, page=index + 1, paragraph_id=pid))
        for text, zh, kind in [(title, translation, "heading")] + [(a, b, "paragraph") for a, b in pairs]:
            paras.append(dict(id=f"p-{len(paras) + 1}", text=text, translation=zh, kind=kind, page=index + 1, level=1 if kind == "heading" else 0, section_id=sid, error=None))
    return refresh_progress(dict(id="demo", title="A quieter way to read research", filename="原创示例文稿", page_count=5,
                                created_at=datetime.now(timezone.utc).isoformat(), status="completed", error=None,
                                paragraphs=paras, outline=outline, is_demo=True,
                                warnings=["这是原创示例文稿，用于展示阅读排版，并非已发表论文。导入 PDF 后可使用真实翻译。 "]))
