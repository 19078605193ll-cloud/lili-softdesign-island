from collections import Counter

from app.knowledge.catalog import REQUIRED_CHAPTER_CODES, flatten_catalog, load_catalog


def test_catalog_is_complete_and_valid() -> None:
    catalog, checksum = load_catalog()
    nodes = flatten_catalog(catalog)
    counts = Counter(node.node_type for node in nodes)

    assert catalog.subject.code == "software-designer.foundation"
    assert {chapter.code for chapter in catalog.chapters} == REQUIRED_CHAPTER_CODES
    assert counts == {"chapter": 14, "module": 52, "topic": 241}
    assert len(checksum) == 64
    assert all(node.description for node in nodes)


def test_every_topic_contains_classification_metadata() -> None:
    catalog, _ = load_catalog()
    topics = [topic for chapter in catalog.chapters for module in chapter.modules for topic in module.topics]

    assert topics
    assert all(topic.aliases for topic in topics)
    assert all(topic.keywords for topic in topics)
    assert all(topic.classification_guidance for topic in topics)
    assert all(topic.syllabus_refs for topic in topics)

