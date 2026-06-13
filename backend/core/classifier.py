"""
Task Classifier
Analyzes user input and determines the task type so the router
can pick the best model for the job.
"""

import re
from dataclasses import dataclass


@dataclass
class ClassifiedTask:
    raw_input: str
    task_type: str
    confidence: float
    keywords_matched: list[str]
    suggested_models: list[str]


# Keyword signals for each task type
TASK_SIGNALS: dict[str, list[str]] = {
    "deep_reasoning": [
        "why", "explain", "reason", "analyze", "think through", "logic",
        "proof", "argument", "philosophy", "compare deeply", "evaluate",
        "weigh", "pros and cons", "hypothesis", "theorem", "deduce"
    ],
    "web_research": [
        "search", "find online", "look up", "browse", "latest", "news",
        "current", "today", "recent", "what is happening", "google",
        "website", "url", "link", "surf", "research online", "check online"
    ],
    "coding": [
        "code", "write a script", "python", "javascript", "function",
        "class", "debug", "fix bug", "program", "algorithm", "api",
        "build", "develop", "compile", "syntax", "error in code", "refactor"
    ],
    "document_work": [
        "create", "write", "draft", "edit", "file", "project", "document",
        "essay", "report", "summarize", "letter", "email", "pdf", "word",
        "docx", "edit text", "proofread", "rewrite", "format", "paragraph",
        "article", "blog", "save", "download", "store locally", "app",
        "calculator", "build app", "github", "repository"
    ],
    "pc_diagnosis": [
        "crash", "error", "not working", "broken", "fix my pc", "blue screen",
        "bsod", "slow", "freeze", "unresponsive", "diagnose", "problem with",
        "fault", "issue", "corrupt", "registry", "driver", "memory leak",
        "cpu", "ram", "disk", "process", "task manager"
    ],
    "shell_automation": [
        "run command", "terminal", "shell", "automate", "script", "batch",
        "powershell", "bash", "cmd", "execute", "schedule", "cron",
        "install", "uninstall", "move files", "rename", "delete folder",
        "mkdir", "directory", "folder"
    ],
    "fast_chat": [
        "what is", "who is", "how do i", "quick", "simple", "tell me",
        "give me", "list", "yes or no", "define", "meaning of", "example of"
    ],
    "multimodal": [
        "image", "picture", "photo", "screenshot", "look at this",
        "describe this", "what's in this", "analyze this image", "video"
    ],
    "analysis": [
        "analyze", "data", "chart", "graph", "statistics", "pattern",
        "trend", "insight", "breakdown", "metrics", "numbers", "dataset"
    ],
    "summarization": [
        "summarize", "summary", "tldr", "key points", "main points",
        "brief", "overview", "condense", "shorten", "in short"
    ],
}


def classify_task(user_input: str) -> ClassifiedTask:
    """
    Classify a user task based on keyword signals.
    Returns the most likely task type and confidence score.
    """
    text = user_input.lower().strip()
    scores: dict[str, int] = {}
    matched_keywords: dict[str, list[str]] = {}

    for task_type, keywords in TASK_SIGNALS.items():
        hits = [kw for kw in keywords if kw in text]
        scores[task_type] = len(hits)
        matched_keywords[task_type] = hits

    # Pick best match
    best_type = max(scores, key=lambda t: scores[t])
    best_score = scores[best_type]

    # If no signals matched, fall back to general
    if best_score == 0:
        best_type = "general"
        confidence = 0.5
        keywords_matched = []
    else:
        total_possible = len(TASK_SIGNALS[best_type])
        confidence = min(1.0, best_score / max(total_possible * 0.3, 1))
        keywords_matched = matched_keywords[best_type]

    return ClassifiedTask(
        raw_input=user_input,
        task_type=best_type,
        confidence=round(confidence, 2),
        keywords_matched=keywords_matched,
        suggested_models=[]  # filled in by router
    )


if __name__ == "__main__":
    # Quick test
    test_inputs = [
        "My PC keeps crashing with a blue screen, can you fix it?",
        "Search online for the latest news about AI in 2025",
        "Write me a Python script to rename all files in a folder",
        "Summarize this document for me",
        "Why does quantum entanglement not allow faster than light communication?",
        "What is the capital of France?",
    ]

    for inp in test_inputs:
        result = classify_task(inp)
        print(f"\nInput: {inp[:60]}...")
        print(f"  → Task Type : {result.task_type}")
        print(f"  → Confidence: {result.confidence}")
        print(f"  → Keywords  : {result.keywords_matched}")
