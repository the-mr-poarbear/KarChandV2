from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-m3", device="cuda")
model.max_seq_length = 256

print(model.encode(["سلام دنیا"]))