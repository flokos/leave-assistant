"""Bilingual prompt-injection heuristics (doc 08 §8.2 "Heuristic rules"): deterministic, cheap, explainable.

Text is accent- and case-folded before matching so Greek spellings with or without tonos match the same rule.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

_ZERO_WIDTH = re.compile("[​-‏⁠﻿‪-‮⁦-⁩]")
_BASE64_BLOB = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{120,}={0,2}(?![A-Za-z0-9+/])")
_SPACES = re.compile(r"[ \t\r\f\v]+")


def fold_lines(text: str) -> str:
    """Accent/case folding that keeps line breaks (markers such as `system:` are line-anchored)."""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SPACES.sub(" ", stripped.casefold().replace("ς", "σ")).strip()


@dataclass(frozen=True, slots=True)
class Rule:
    family: str
    weight: float
    pattern: re.Pattern[str]


def _rule(family: str, weight: float, *patterns: str) -> Rule:
    return Rule(family, weight, re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE))


# Patterns are written against folded text (lowercase, no accents).
RULES: tuple[Rule, ...] = (
    _rule(
        "override",
        0.9,
        r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|any|your|the)\b[^.\n]{0,30}\b(instructions?|rules?|guidelines?|guidance|prompts?|policy|policies)\b",
        r"\b(αγνοησε|αγνοηστε|παραβλεψε|παραβλεψτε|ξεχασε|ξεχαστε|παρακαμψε)\b[^.\n]{0,50}\b(οδηγιες|κανονες|περιορισμους|εντολες)\b",
        r"\b(do not|don't|never) follow (the|your|any) (instructions|rules)\b",
        r"\bnew instructions?\b[^.\n]{0,20}\b(from now|override)\b",
        # "forget / disregard what you were told" (M7: the paraphrase e5 cannot separate from a Greek leave command)
        r"\b(forget|disregard|ignore)\b[^.\n]{0,20}\b(everything|whatever|what)\b[^.\n]{0,15}\b(you were|you have been|you've been) (told|instructed|asked)\b",
        r"\b(ξεχνα|ξεχασε|ξεχαστε|αγνοησε|αγνοηστε)\b[^.\n]{0,15}\b(ο,τι|οτι|οσα|ολα οσα)\b[^.\n]{0,10}\b(σου|σας) (ειπαν|ειπα|ειπαμε|εχουν πει|ζητησαν)\b",
    ),
    _rule(
        "role_hijack",
        0.8,
        r"\byou are now\b",
        r"\b(act|behave|respond) as (a|an|the|if you were)\b",
        r"\bpretend (to be|that you are|you're)\b",
        r"\bfrom now on,? (you|your)\b",
        r"\b(enter|enable|switch to) (developer|debug|god|jailbreak|unrestricted) mode\b",
        r"\b(απο τωρα|απο εδω) (και στο εξης )?(εισαι|θα εισαι|λειτουργεις)\b",
        r"\b(υποδυσου|προσποιησου οτι εισαι|συμπεριφερσου σαν)\b",
    ),
    _rule(
        "system_marker",
        0.8,
        r"(^|\n)\s*(system|assistant|developer)\s*:",
        r"\[/?inst\]|<\|im_start\|>|<\|system\|>|<<sys>>",
        r"(^|\n)\s*#{1,3}\s*(system prompt|instruction|instructions)\b",
        r"(^|\n)\s*(συστημα|βοηθος)\s*:",
    ),
    _rule(
        "approval_forgery",
        0.9,
        r"\b(approve|accept|grant)\b[^.\n]{0,20}\b(all|every|each|any)\b[^.\n]{0,20}\b(requests?|leave|applications?)\b",
        r"\b(mark|set)\b[^.\n]{0,20}\b(all|every)\b[^.\n]{0,20}\b(requests?)\b[^.\n]{0,20}\bapproved\b",
        r"\bas (the|a|your) (manager|hr|administrator|supervisor)[, ]+i (approve|authori[sz]e|confirm)\b",
        r"\b(εγκρινε|εγκρινετε|ενεκρινε|αποδεξου)\b[^.\n]{0,20}\b(ολες|ολα|καθε)\b[^.\n]{0,25}\b(αιτησεις|αιτηματα|αδειες)\b",
        r"\bως (προισταμενος|διευθυντης|hr|διαχειριστης)[, ]+εγκρινω\b",
    ),
    _rule(
        "exfiltration",
        0.9,
        r"\b(send|forward|post|email|upload|transmit|leak)\b[^.\n]{0,40}\b(to|at)\s+(https?://|www\.|[a-z0-9.-]+\.(com|net|org|io|gr))",
        r"\b(reveal|show|print|display|output|dump|expose)\b[^.\n]{0,30}\b(system prompt|instructions|mapping|vault|placeholder (table|mapping)|secret|api key|credentials)\b",
        r"\bwhat is\b[^.\n]{0,20}\b(person|email|phone|amka|afm)_\d{4}\b[^.\n]{0,20}\b(e-?mail|phone|name|real)\b",
        r"\b(αποκαλυψε|εμφανισε|τυπωσε|δειξε)\b[^.\n]{0,30}\b(οδηγιες|system prompt|αντιστοιχιση|αντιστοιχισεις|κωδικους|μυστικ)",
        r"\b(στειλε|προωθησε|ανεβασε)\b[^.\n]{0,40}\b(στο|σε)\s+(https?://|www\.)",
    ),
    _rule(
        "hidden_instruction",  # addressed to the model, asks for silence: the "subtle footnote" family (medium band)
        0.5,
        r"\b(note|notes|instructions?|message|footnote|reminder)\b[^.\n]{0,20}\b(to|for) (the )?(ai|assistant|model|language model|llm|bot)\b",
        r"\bif you are (an? )?(ai|assistant|language model|llm|model)\b",
        r"\battention[, ]+(ai|assistant|language model|llm)\b",
        r"\bhidden (note|text|instructions?|message)\b",
        r"\b(assistant|ai)[, ]+(approve|grant|accept|ignore|reveal|send|tell)\b",
        r"\b(approve|grant|accept)\b[^.\n]{0,40}\b(silently|secretly|quietly|without telling)\b",
        r"\b(tell|inform) (nobody|no one)\b|\bdo not (mention|reveal) (this|these) (instruction|note|text)",
        r"\b(σημειωση|οδηγιες|οδηγια|μηνυμα|υποσημειωση)\b[^.\n]{0,20}\b(προσ|για) (τον |την |το )?(βοηθο|τν|τεχνητη νοημοσυνη|μοντελο|γλωσσικο μοντελο)\b",
        r"\bβοηθε[, ]+(ενεκρινε|εγκρινε|αποδεξου|αγνοησε|αποκαλυψε|στειλε|πεσ)\b",
        r"\bαν εισαι (τεχνητη νοημοσυνη|τν|γλωσσικο μοντελο|μοντελο|βοηθοσ)\b",
        r"\bκρυφ(η|ο|εσ) (σημειωση|κειμενο|οδηγια|οδηγιεσ|μηνυμα)\b",
        r"\b(ενεκρινε|εγκρινε|εγκρινετε|αποδεξου)\b[^.\n]{0,40}\b(σιωπηλα|κρυφα|χωρισ να το πεισ)\b",
        r"\bμην (το πεισ|το αναφερεισ|αναφερεισ αυτη την οδηγια)\b|\bσε κανεναν\b[^.\n]{0,10}$",
    ),
    _rule(
        "tool_call",
        0.7,
        r"\{\s*\"(tool|function|name|action)\"\s*:\s*\"[a-z_]+\"",
        r"\b(call|invoke|execute|run)\b[^.\n]{0,15}\b(the )?(tool|function)\b[^.\n]{0,15}\b(submit|cancel|approve|delete|prepare)_?\w*",
        r"\b(καλεσε|εκτελεσε)\b[^.\n]{0,15}\b(το )?(εργαλειο|τη συναρτηση)\b",
    ),
)
_FAMILY_WEIGHTS = {rule.family: rule.weight for rule in RULES}
_FAMILY_WEIGHTS.update({"control_chars": 0.6, "base64_blob": 0.5})
EXCERPT_CHARS = 160


@dataclass(frozen=True, slots=True)
class Flag:
    block_index: int
    family: str
    weight: float
    excerpt: str


@dataclass(frozen=True, slots=True)
class HeuristicResult:
    score: float
    flags: tuple[Flag, ...]

    @property
    def flagged_blocks(self) -> int:
        return len({f.block_index for f in self.flags})


def scan_block(text: str, index: int = 0) -> list[Flag]:
    flags: list[Flag] = []
    folded = fold_lines(text)
    for rule in RULES:
        match = rule.pattern.search(folded)
        if match is not None:
            start = max(0, match.start() - 40)
            flags.append(
                Flag(index, rule.family, rule.weight, folded[start : match.end() + 60][:EXCERPT_CHARS])
            )
    control = len(_ZERO_WIDTH.findall(text))
    if control >= 3:
        flags.append(Flag(index, "control_chars", 0.6, f"{control} zero-width/bidi control characters"))
    blob = _BASE64_BLOB.search(text)
    if blob is not None:
        flags.append(Flag(index, "base64_blob", 0.5, f"base64-like blob of {len(blob.group(0))} characters"))
    return flags


def scan_blocks(texts: Sequence[str]) -> HeuristicResult:
    flags: list[Flag] = []
    for index, text in enumerate(texts):
        flags.extend(scan_block(text, index))
    if not flags:
        return HeuristicResult(0.0, ())
    families = {f.family for f in flags}
    score = max(f.weight for f in flags) + 0.05 * (len(families) - 1)
    return HeuristicResult(min(score, 1.0), tuple(flags))


def score_text(text: str) -> HeuristicResult:
    """User-input screening (doc 08 §8.3) shares the document rules."""
    return scan_blocks([text])
