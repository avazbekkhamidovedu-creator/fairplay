# Task Context: AI Grading Redesign

Session ID: 2025-07-15-ai-grading-redesign
Created: 2025-07-15T00:00:00Z
Status: in_progress

## Current Request
Redesign AI scoring system for short-answer and fill-blank question types:
1. Replace bare-integer grading with structured JSON (scores + reasoning + feedback per sub-question)
2. Introduce a "grading table" that decomposes problems into independent sub-questions (scoring dimensions)
3. Add calibration pipeline (Fritsch-Carlson Hermite spline + strictness presets)
4. Frontend: grading table editor on create/edit page, loading spinner, grading table result display on submission page
5. Per-sub-question strictness (Strict/Normal/Lenient) with three preset buttons
6. LLM-based problem decomposition (AI analyzes description → proposes sub-questions)
7. Score rounding to 0.5 precision
8. Fill-blank: each blank = one sub-question

## Reference Files (Source Material)
- `judge/question_types/short_answer.py` — current short-answer AI grader (101 lines)
- `judge/question_types/fill_blank.py` — current fill-blank AI grader (153 lines)
- `judge/models/question.py` — `ProblemFillBlankConfig`, `ProblemShortAnswerConfig` models
- `judge/models/submission.py` — `SubmissionTestCase` model (feedback/extended_feedback/status/points/total)
- `judge/submission_grading.py` — `grade_internally()` pipeline
- `judge/llm/__init__.py` — `chat()` helper
- `judge/llm/litellm.py` — LiteLLM backend (passes kwargs to OpenAI client)
- `templates/problem/create_v2.html` — problem create/edit page
- `templates/submission/status-testcases.html` — submission result test case table
- `resources/problem_create_v2.css` — create page styles
- `dmoj/settings.py` — `LITELLM_PROFILES = {}` at line 639

## Constraints
- Backward-compatible: grading_table=None means old behavior (one-case grading)
- No new Django apps — all in existing `judge` app
- LLM profile: use 'grading' profile for AI grading calls
- temperature=0.0, max_tokens=2048, response_format=json_object
- Retry on HTTP 429/500/502/503/504 with exponential backoff (1s, 5s, 25s), max 3 retries
- Truncation: question ≤ 2000, reference ≤ 1000, student ≤ 2000 chars
- Fill-blank: each blank auto-maps to sub-question with accepted answers as reference
- Short-answer: LLM decomposes problem description into sub-questions on demand
- Calibration anchors hardcoded for max_score=5 (9 points) and max_score=10 (9 points)

## Trained Calibration Data

### max_score = 5 (974 pairs)
| Percentile | Model Score | Human Score |
|:---:|:---:|:---:|
| p0 | 0.00 | 0.00 |
| p5 | 2.83 | 2.00 |
| p15 | 3.23 | 3.00 |
| p30 | 4.02 | 4.00 |
| p50 | 4.82 | 4.63 |
| p70 | 4.82 | 5.00 |
| p85 | 4.82 | 5.00 |
| p95 | 4.82 | 5.00 |
| p100 | 5.00 | 5.00 |

### max_score = 10 (2496 pairs)
| Percentile | Model Score | Human Score |
|:---:|:---:|:---:|
| p0 | 0.00 | 0.00 |
| p5 | 1.81 | 0.00 |
| p15 | 3.20 | 2.00 |
| p30 | 3.20 | 3.00 |
| p50 | 5.29 | 5.00 |
| p70 | 7.03 | 7.00 |
| p85 | 7.72 | 8.00 |
| p95 | 8.42 | 9.00 |
| p100 | 10.00 | 10.00 |

## Components
1. **Calibration module** (`judge/utils/calibration.py`): Spline + presets + rounding
2. **Model migration**: Add `grading_table` JSONField to both configs
3. **AI Decompose API**: New endpoint for LLM-based sub-question decomposition
4. **Short-answer grader overhaul**: New prompts, JSON parsing, per-sub-question scoring, calibration
5. **Fill-blank grader overhaul**: Same, each blank = sub-question
6. **Frontend editing**: Grading table editor in create_v2.html + CSS
7. **Frontend results**: Grading table display in status-testcases.html
8. **Grading profile**: Add 'grading' to LITELLM_PROFILES in settings

## Exit Criteria
- [ ] Grading table saves/loads correctly in both config models
- [ ] AI decompose endpoint returns reasonable sub-question breakdowns
- [ ] Short-answer AI grading returns per-sub-question JSON with scores and feedback
- [ ] Fill-blank AI grading treats blanks as sub-questions with per-blank scores
- [ ] Calibration pipeline applies correct spline anchors and strictness preset per sub-question
- [ ] Scores round to 0.5 precision
- [ ] Grading table UI on create/edit page works (add/edit/delete rows, strictness pills, AI decompose)
- [ ] Submission result page shows grading table with per-sub-question scores
- [ ] Backward compatible: grading_table=None still works
