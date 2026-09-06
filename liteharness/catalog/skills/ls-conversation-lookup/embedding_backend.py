"""Use LiteHarness's CPU MiniLM backend without importing torch/transformers."""
from pathlib import Path
import os


class OnnxEmbedder:
    def __init__(self):
        from liteharness.embed_service import EmbedModel, download_model
        directory = Path(os.environ.get('LITEHARNESS_CONVO_HOME', Path.home() / '.liteharness' / 'conversations')) / 'models' / 'all-MiniLM-L6-v2'
        if not (directory / 'tokenizer.json').exists() or not (directory / 'onnx/model.onnx').exists():
            download_model(directory)  # Downloads model artifacts only, never conversation text.
        self.model = EmbedModel(directory)

    def get_sentence_embedding_dimension(self):
        return 384

    def encode(self, texts, convert_to_numpy=True, batch_size=64):
        import numpy as np
        scalar = isinstance(texts, str)
        texts = [texts] if scalar else texts
        vectors = []
        for offset in range(0, len(texts), batch_size):
            vectors.extend(self.model.encode(texts[offset:offset + batch_size]))
        array = np.asarray(vectors, dtype=np.float32)
        return array[0] if scalar else array
