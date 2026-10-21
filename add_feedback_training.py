"""One-time script: intent_nn.py also learns from feedback.jsonl (the data flywheel).
Safe to run more than once. Run: python3 add_feedback_training.py"""
import sys

s = open("intent_nn.py", encoding="utf-8").read()
if "def load_feedback" in s:
    sys.exit("✅ Already added — nothing to do.")

main = 'if __name__ == "__main__":'
if main not in s:
    sys.exit("❌ Could not find the __main__ block. Nothing was changed.")
head, body = s.split(main, 1)

head += '''def _load_jsonl(filename, exam, known, seen):
    """Read one JSONL file of {"text", "intent"} rows → [(text, intent_index)]."""
    import json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            text, intent = row.get("text", ""), row.get("intent")
            if not text or intent not in INTENTS or text in exam or text in known or text in seen:
                continue
            seen.add(text)
            rows.append((text, INTENTS.index(intent)))
    return rows


def load_feedback():
    """🎡 Extra training examples: real feedback from the dashboard + synthetic starter data."""
    from eval_set import EVAL
    exam = {t for t, _ in EVAL}       # 🔒 never train on the final exam (no data leakage!)
    known = {t for t, _ in DATA}
    seen = set()
    real = _load_jsonl("feedback.jsonl", exam, known, seen)
    synthetic = _load_jsonl("synthetic_data.jsonl", exam, known, seen)
    return real + synthetic

'''
body = body.replace("DATA", "ALL")
body = '\n    ALL = DATA + load_feedback()\n    print(f"📚 {len(DATA)} built-in examples + {len(ALL) - len(DATA)} from feedback = {len(ALL)}")' + body
open("intent_nn.py", "w", encoding="utf-8").write(head + main + body)
print("✅ intent_nn.py now learns from feedback.jsonl too")