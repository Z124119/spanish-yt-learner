"""西语 YouTube 学习辅助工具 —— 核心逻辑包。

模块划分：
    url_parser       YouTube 链接 → (video_id, start_seconds)
    transcript       通过 youtube-transcript-api 获取字幕，并把异常翻译成用户可读提示
    subtitle_parser  SRT / VTT 字幕文件解析
    segmenter        字幕 cue → 句子 → 段落（保留时间戳）
    lemmatizer       轻量西语词形还原 + 停用词（无 spaCy 依赖）
    level            CEFR 等级判定（ELELex 优先 → wordfreq 兜底）
    glossary         西汉词表加载与查询
    providers/       词典与翻译提供方（默认离线，可选 LLM）
    pipeline         把以上部件编排成最终学习材料
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
