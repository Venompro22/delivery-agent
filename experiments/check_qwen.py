import torch
from transformers import AutoTokenizer, AutoModel

MODEL = "Qwen/Qwen2.5-0.5B"
device = "mps" if torch.backends.mps.is_available() else "cpu"

print("📥 Loading Qwen (first time downloads ~1 GB)...")
tokenizer = AutoTokenizer.from_pretrained(MODEL)
model = AutoModel.from_pretrained(MODEL).to(device).eval()

n_params = sum(p.numel() for p in model.parameters())
print(f"🧠 Qwen: {n_params:,} parameters · device: {device}")
print(f"   (your Mini-GPT had 35,022 → Qwen is {n_params // 35022:,}× bigger 😮)")

# 🔤 How does Qwen "read"? Tokens (pieces of words), not single characters
text = "我要两桶水 文三路100号"
ids = tokenizer(text)["input_ids"]
print(f"\n🔤 '{text}'")
print(f"   → {len(ids)} tokens: {[tokenizer.decode([i]) for i in ids]}")


# 🧭 Meaning vectors: similar meaning → similar vectors
@torch.no_grad()
def embed(sentence):
    batch = tokenizer(sentence, return_tensors="pt").to(device)
    hidden = model(**batch).last_hidden_state      # (1, tokens, 896)
    return hidden.mean(dim=1)[0]                    # average → one 896-number vector


a = embed("不要了 取消订单")
b = embed("算了 我不想要了")       # different words, SAME meaning
c = embed("我的水在哪里")          # different meaning
cos = torch.nn.functional.cosine_similarity
print(f"\n🧭 Vector size: {a.shape[0]} numbers per message")
print(f"   'cancel' vs 'never mind' (same meaning): {cos(a, b, dim=0):.2f}")
print(f"   'cancel' vs 'where is it' (different):   {cos(a, c, dim=0):.2f}")