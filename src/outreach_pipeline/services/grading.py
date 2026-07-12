"""Content grading domain service — separate from messaging.

Grades text content against quality checks using the validation layer
in audit mode. This is NOT a messaging subcase.
"""
from __future__ import annotations

from outreach_pipeline.types import ContentGrade, ContentPage, GradingReport
from outreach_pipeline.validation.tone import validate


def grade_chunk(text: str, source: str = "", profile: str = "content_grading") -> ContentGrade:
    """Grade a single text chunk against a validation profile (audit mode)."""
    result = validate(text, profile=profile)

    violation_count = len(result.violations)
    quality_score = max(0.0, 100.0 - (violation_count * 20.0))

    category_scores = {}
    for v in result.violations:
        label = v.get("label", "unknown")
        category_scores[label] = category_scores.get(label, 0) + 1

    page = ContentPage(url="", title=source, text=text, source_file=source)
    return ContentGrade(
        page=page,
        violations=result.violations,
        quality_score=quality_score,
        category_scores=category_scores,
    )


def build_report(grades: list[ContentGrade]) -> GradingReport:
    """Aggregate grades into a grading report."""
    if not grades:
        return GradingReport()

    violations_by_type: dict[str, int] = {}
    total_score = 0.0

    for grade in grades:
        total_score += grade.quality_score
        for v in grade.violations:
            label = v.get("label", "unknown")
            violations_by_type[label] = violations_by_type.get(label, 0) + 1

    sorted_grades = sorted(grades, key=lambda g: g.quality_score)
    worst = sorted_grades[:10]

    return GradingReport(
        total_chunks=len(grades),
        violations_by_type=violations_by_type,
        worst_chunks=worst,
        overall_score=round(total_score / len(grades), 1),
    )


def grade_collection(
    collection_name: str,
    db_path: str = "rag/materials_db",
    profile: str = "content_grading",
) -> GradingReport:
    """Grade all chunks in a RAG collection."""
    from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter

    adapter = ChromaDBAdapter(db_path=db_path)
    chunks = adapter.get_all_chunks(collection_name=collection_name)

    grades = [grade_chunk(chunk.text, source=chunk.source, profile=profile) for chunk in chunks]
    return build_report(grades)
