import re


def find_phone(text):
    match = re.search(r"1[3-9]\d{9}", text)     # ← كمّل الـ regex
    if match:
        return match.group()
    return None

def find_deadline(text):
    match = re.search(r"(上午|下午|晚上|中午)?(\d{1,2})点(半)?", text)
    if not match:
        return None

    period = match.group(1)      # "下午" ولا None
    hour = int(match.group(2))   # 3
    half = match.group(3)        # "半" ولا None

    # ✍️ 1) إلا period هو "下午" ولا "晚上"، وhour أقل من 12 → زيد 12
    # ✍️ 2) minute = 30 إلا كاين half، وإلا 0

    return f"{hour:02d}:{minute:02d}"

def find_deadline(text):
    match = re.search(r"(上午|下午|晚上|中午)?(\d{1,2})点(半)?", text)
    if not match:
        return None

    period = match.group(1)
    hour = int(match.group(2))
    half = match.group(3)

    if period in ("下午", "晚上") and hour < 12:
        hour += 12

    if half:
        minute = 30
    else:
        minute = 0

    return f"{hour:02d}:{minute:02d}"
ADDRESS_MARKERS = ("路", "街", "号", "楼", "栋", "室", "单元", "小区")
NOT_NAMES = ("送到", "送", "到", "前", "尽快", "谢谢")
def find_address(text):
    parts = []
    for word in text.split():
        if any(m in word for m in ADDRESS_MARKERS):
            parts.append(word)

    if parts:
        return " ".join(parts)
    return None
def find_name(text):
    for word in text.split():
        if any(m in word for m in ADDRESS_MARKERS):
            continue
        if "点" in word:
            continue
        if word in NOT_NAMES:
            continue
        if re.search(r"1[3-9]\d{9}", word):
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", word):
            return word
    return None
def parse_message(text):
    return {
        "name": find_name(text),
        "phone": find_phone(text),
        "address": find_address(text),
        "deadline": find_deadline(text),
    }
if __name__ == "__main__":
    tests = [
        "李明 13912345678 西湖区文三路100号 5号楼3楼 下午3点前",
        "王芳 15800002222 阳光小区 3单元 502室",
        "上午10点 送到",
    ]
    for t in tests:
        print(parse_message(t))