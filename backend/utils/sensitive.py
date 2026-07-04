from config.settings import SENSITIVE_WORDS
def filter_sensitive(text: str) -> tuple[bool, str]:
    """
    敏感词过滤
    :param text: 待检测文本
    :return: (是否命中敏感词, 过滤后的文本)
    """
    filtered_text = text
    hit = False
    # 遍历配置里的所有敏感词，全部替换
    for word in SENSITIVE_WORDS:
        if word in text:
            hit = True
            filtered_text = filtered_text.replace(word, "*"*len(word))
    return hit, filtered_text