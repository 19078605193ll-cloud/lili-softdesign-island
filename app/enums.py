from enum import StrEnum


class NodeType(StrEnum):
    CHAPTER = "chapter"
    MODULE = "module"
    TOPIC = "topic"


class RecordStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class PaperPeriod(StrEnum):
    FIRST_HALF = "first_half"
    SECOND_HALF = "second_half"
    OTHER = "other"


class PaperStatus(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"
    RETIRED = "retired"


class QuestionType(StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"


class QuestionSourceType(StrEnum):
    RECALLED = "recalled"
    OFFICIAL = "official"
    PRACTICE = "practice"
    AI_GENERATED = "ai_generated"


class QuestionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class AssignmentRole(StrEnum):
    PRIMARY = "primary"
    RELATED = "related"


class AssignmentStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class AssignmentSource(StrEnum):
    MANUAL = "manual"
    AI = "ai"
    IMPORT = "import"


class ImportBatchStatus(StrEnum):
    UPLOADED = "uploaded"
    SECTIONED = "sectioned"
    PARSED = "parsed"
    IN_REVIEW = "in_review"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHED = "published"
    FAILED = "failed"


class ImportItemStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    PUBLISHED = "published"


class SourceDocumentRole(StrEnum):
    COMBINED = "combined"
    QUESTIONS = "questions"
    ANSWERS = "answers"


class AssetKind(StrEnum):
    FIGURE = "figure"
    TABLE = "table"
    CODE = "code"
    SOURCE_SNAPSHOT = "source_snapshot"
