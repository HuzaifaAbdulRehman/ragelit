import math
import re

from app.retrieval.embeddings import Embedding

_TOPIC = re.compile(r"\baudittopic(\d+)\b")


class FixtureEmbeddings:
    dimension = 64
    identifier = "fixture-topic-v1"

    def documents(self, texts: list[str]) -> list[Embedding]:
        return [self.query(text) for text in texts]

    def query(self, text: str) -> Embedding:
        topics = {int(value) for value in _TOPIC.findall(text)}
        if len(topics) != 1 or not 0 <= (topic := next(iter(topics))) < 27:
            raise ValueError("invalid fixture topic")
        dense = [0.0] * self.dimension
        dense[0], dense[topic + 1] = 1 / math.sqrt(17), 4 / math.sqrt(17)
        return Embedding(tuple(dense), (0, topic + 1), (1.0, 4.0))
