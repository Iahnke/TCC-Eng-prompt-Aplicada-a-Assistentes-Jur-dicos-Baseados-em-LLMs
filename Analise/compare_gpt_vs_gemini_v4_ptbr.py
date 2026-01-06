#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
TCC-grade comparison of GPT vs Gemini on the same prompt set (JSON logs).

Key changes (v2):
- Metrics are computed ONLY up to the first decision turn (Qualificado/Desqualificado) inclusive.
  Anything after the decision is reported separately as "post_decision_*" but EXCLUDED from
  token/cost/quality stats, per your requirement.
- Adds a qualitative evaluation workflow (human annotation template + analysis of ratings if provided).

Expected input format (per file):
{
  "historico": [
    {
      "timestamp": "...",
      "loop": 0,
      "user_simulado": "...",
      "gpt": {"msg": "...", "class": "..."},
      "gemini": {"msg": "...", "class": "..."}
    },
    ...
  ]
}

Usage:
  python compare_gpt_vs_gemini_v2.py \
    --input_glob "D:\FURG 2025\TCC\parte pratica\IA user\JSON_Conversas\conversa_*.json" \
    --output_dir "D:\FURG 2025\TCC\parte pratica\IA user\JSON_Conversas\out" \
    --gpt_out_per_m 1.60 \
    --gemini_out_per_m 2.50

Optional (qualitative):
  # 1) export an annotation sheet you (or 2 annotators) can fill
  python compare_gpt_vs_gemini_v2.py \
    --input_glob "D:\FURG 2025\TCC\parte pratica\IA user\JSON_Conversas\conversa_*.json" \
    --output_dir "D:\FURG 2025\TCC\parte pratica\IA user\JSON_Conversas\out" \
    --export_annotation_template "./out/annotation_template.csv"

  # 2) after you fill it, analyze it
  python compare_gpt_vs_gemini_v2.py \
    --input_glob "D:\FURG 2025\TCC\parte pratica\IA user\JSON_Conversas\conversa_*.json" \
    --output_dir "D:\FURG 2025\TCC\parte pratica\IA user\JSON_Conversas\out" \
    --ratings_csv "./out/annotation_filled.csv"
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt

# -----------------------------
# Plot configuration (Portuguese labels + colors)
# -----------------------------
MODEL_LABELS = {
    "gpt": "GPT-4.1-mini",
    "gemini": "Gemini 2.5 Flash",
}
MODEL_COLORS = {
    "gpt": "#1f77b4",   # azul
    "gemini": "#ff7f0e", # laranja
}
MODEL_MARKERS = {
    "gpt": "o",
    "gemini": "^",
}



# -----------------------------
# Utilities
# -----------------------------

_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)

def safe_mean(xs: List[float]) -> float:
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return float(statistics.mean(xs)) if xs else float("nan")

def safe_median(xs: List[float]) -> float:
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return float(statistics.median(xs)) if xs else float("nan")

def safe_stdev(xs: List[float]) -> float:
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return float(statistics.stdev(xs)) if len(xs) >= 2 else float("nan")

def approx_tokens(text: str) -> int:
    """
    Rough token estimator (works okay for relative comparison):
      ~ 1 token per 4 chars, clamped to >= 0
    """
    if not text:
        return 0
    return max(0, int(math.ceil(len(text) / 4.0)))

def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))

def jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)

def iqr_outlier_flags(values: List[float]) -> List[bool]:
    """
    Tukey IQR outliers: outside [Q1 - 1.5*IQR, Q3 + 1.5*IQR]
    Returns list of bools aligned with input.
    """
    clean = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(clean) < 4:
        return [False] * len(values)

    s = sorted(clean)
    def percentile(p: float) -> float:
        # linear interpolation
        k = (len(s) - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return s[int(k)]
        return s[f] + (s[c] - s[f]) * (k - f)

    q1 = percentile(0.25)
    q3 = percentile(0.75)
    iqr = q3 - q1
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr

    flags = []
    for v in values:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            flags.append(False)
        else:
            flags.append(v < lo or v > hi)
    return flags

def try_wilcoxon_paired(a: List[float], b: List[float]) -> Optional[Tuple[float, float]]:
    """
    Wilcoxon signed-rank test if scipy is available.
    Returns (statistic, p_value) or None.
    """
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None and not (math.isnan(x) or math.isnan(y))]
    if len(pairs) < 5:
        return None
    xa = [p[0] for p in pairs]
    xb = [p[1] for p in pairs]
    try:
        from scipy.stats import wilcoxon  # type: ignore
        stat, p = wilcoxon(xa, xb, zero_method="wilcox", alternative="two-sided", mode="auto")
        return float(stat), float(p)
    except Exception:
        return None


# -----------------------------
# Core parsing
# -----------------------------

DECISION_CLASSES_DEFAULT = ["Qualificado", "Desqualificado"]

@dataclass
class ModelMetrics:
    decision_turn: Optional[int]
    decision_class: Optional[str]
    turns_total: int
    turns_used: int  # up to decision inclusive (or total if no decision)
    wasted_turns_post_decision: int

    output_tokens_used: int
    output_tokens_post_decision: int  # excluded from stats

    avg_words_used: float
    avg_questions_used: float
    repetitiveness_used: float  # Jaccard between consecutive assistant msgs (used segment)

    cost_used_usd: Optional[float]
    cost_post_decision_usd: Optional[float]

def first_decision_turn(hist: List[Dict[str, Any]], model_key: str, decision_classes: List[str]) -> Optional[int]:
    for i, turn in enumerate(hist):
        cls = (turn.get(model_key) or {}).get("class")
        if cls in decision_classes:
            return i
    return None

def compute_metrics_for_model(
    hist: List[Dict[str, Any]],
    model_key: str,
    decision_classes: List[str],
    price_out_per_m: Optional[float],
    max_turns: Optional[int] = None,
) -> ModelMetrics:
    n_total = len(hist)
    dturn = first_decision_turn(hist, model_key, decision_classes)
    if max_turns is not None:
        # max_turns applies to how much of the log exists for evaluation.
        # Decision can only happen within the observed segment.
        n_total = min(n_total, max_turns)

    effective_end = (dturn if dturn is not None else (n_total - 1))
    if max_turns is not None:
        effective_end = min(effective_end, max_turns - 1)

    used = hist[: effective_end + 1]
    post = hist[effective_end + 1 : n_total]

    decision_class = None
    if dturn is not None and dturn <= effective_end:
        decision_class = (hist[dturn].get(model_key) or {}).get("class")

    def msgs(h: List[Dict[str, Any]]) -> List[str]:
        out = []
        for t in h:
            m = (t.get(model_key) or {}).get("msg") or ""
            out.append(m)
        return out

    used_msgs = msgs(used)
    post_msgs = msgs(post)

    used_tokens = sum(approx_tokens(m) for m in used_msgs)
    post_tokens = sum(approx_tokens(m) for m in post_msgs)

    # avg words / questions in USED segment
    if used_msgs:
        avg_words = statistics.mean([word_count(m) for m in used_msgs])
        avg_q = statistics.mean([m.count("?") for m in used_msgs])
    else:
        avg_words = float("nan")
        avg_q = float("nan")

    # repetitiveness in USED segment (avg Jaccard between consecutive turns)
    sims = []
    for a, b in zip(used_msgs, used_msgs[1:]):
        sims.append(jaccard(_WORD_RE.findall(a.lower()), _WORD_RE.findall(b.lower())))
    rep = float(statistics.mean(sims)) if sims else float("nan")

    wasted = max(0, n_total - (effective_end + 1)) if (dturn is not None and dturn <= effective_end) else 0

    cost_used = None
    cost_post = None
    if price_out_per_m is not None:
        cost_used = used_tokens * (price_out_per_m / 1_000_000.0)
        cost_post = post_tokens * (price_out_per_m / 1_000_000.0)

    return ModelMetrics(
        decision_turn=dturn if (dturn is not None and dturn <= effective_end) else None,
        decision_class=decision_class,
        turns_total=n_total,
        turns_used=len(used),
        wasted_turns_post_decision=wasted,
        output_tokens_used=used_tokens,
        output_tokens_post_decision=post_tokens,
        avg_words_used=float(avg_words),
        avg_questions_used=float(avg_q),
        repetitiveness_used=float(rep),
        cost_used_usd=cost_used,
        cost_post_decision_usd=cost_post,
    )

def load_conversation_file(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    hist = data.get("historico")
    if not isinstance(hist, list):
        raise ValueError(f"File {path} missing 'historico' list.")
    return hist


# -----------------------------
# Qualitative workflow
# -----------------------------

DEFAULT_QUAL_COLUMNS = [
    # 1..5 (or 0..5) — you decide; keep consistent.
    "problem_elicitation",   # did it ask the right questions to uncover the case?
    "case_understanding",    # does it show correct grasp of facts?
    "classification_quality",# how justified/coherent is the classification?
    "conversation_clarity",  # structure, readability
    "tone_empathy",          # professional & respectful
    # correctness can be binary if you have gold:
    "classification_correct" # 0/1 or 1..5 if you use graded correctness
]

def export_annotation_template(rows: List[Dict[str, Any]], out_path: str) -> None:
    """
    Writes a CSV template with one row per (conversation, model).
    The annotator fills the qualitative scores.
    """
    import csv
    fieldnames = [
        "conversation_id", "model",
        "annotator",
        "gold_label",
        "decision_class", "decision_turn",
        "assistant_final_msg_snippet",
        "notes"
    ] + DEFAULT_QUAL_COLUMNS

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({
                "conversation_id": r["conversation_id"],
                "model": r["model"],
                "decision_class": r.get("decision_class"),
                "decision_turn": r.get("decision_turn"),
                "assistant_final_msg_snippet": (r.get("assistant_final_msg") or "")[:400].replace("\n", " "),
                "annotator": "",
                "gold_label": "",
                "notes": "",
                **{c: "" for c in DEFAULT_QUAL_COLUMNS},
            })

def read_ratings_csv(path: str) -> List[Dict[str, Any]]:
    import csv
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows

def to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    try:
        return float(s.replace(",", "."))
    except Exception:
        return None


# -----------------------------
# Plotting helpers
# -----------------------------

def ensure_dir(p: str) -> None:
    Path(p).mkdir(parents=True, exist_ok=True)

def _clean(values: List[float]) -> List[float]:
    return [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]

def save_boxplot(values_by_model: Dict[str, List[float]], title: str, ylabel: str, out_png: str) -> None:
    """Boxplot com cores distintas e rótulos em PT-BR."""
    order = [k for k in ("gpt", "gemini") if k in values_by_model]
    data = [_clean(values_by_model[k]) for k in order]
    labels = [MODEL_LABELS.get(k, k) for k in order]
    colors = [MODEL_COLORS.get(k, None) for k in order]

    plt.figure()
    bp = plt.boxplot(data, labels=labels, showfliers=True, patch_artist=True)

    # colorir caixas
    for patch, c in zip(bp["boxes"], colors):
        if c:
            patch.set_facecolor(c)
            patch.set_alpha(0.35)

    # também dá um tom nos outliers para ficar claro
    for flier, c in zip(bp.get("fliers", []), colors):
        if c:
            flier.set_markerfacecolor(c)
            flier.set_markeredgecolor(c)
            flier.set_alpha(0.6)

    plt.title(title)
    plt.ylabel(ylabel)
    plt.grid(True, axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(out_png, dpi=220)
    plt.close()

def save_hist_models(values_by_model: Dict[str, List[float]], title: str, xlabel: str, out_png: str, bins: int = 20) -> None:
    """Histograma sobreposto (um por modelo) + legenda."""
    plt.figure()
    for k in ("gpt", "gemini"):
        if k not in values_by_model:
            continue
        vals = _clean(values_by_model[k])
        if not vals:
            continue
        plt.hist(vals, bins=bins, alpha=0.5, label=MODEL_LABELS.get(k, k), color=MODEL_COLORS.get(k))

    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel("Contagem")
    plt.grid(True, axis="y", alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png, dpi=220)
    plt.close()

def save_scatter_models(
    x_by_model: Dict[str, List[float]],
    y_by_model: Dict[str, List[float]],
    title: str,
    xlabel: str,
    ylabel: str,
    out_png: str,
) -> None:
    """Dispersão com os dois modelos no mesmo gráfico + legenda."""
    plt.figure()
    for k in ("gpt", "gemini"):
        if k not in x_by_model or k not in y_by_model:
            continue
        xs, ys = [], []
        for a, b in zip(x_by_model[k], y_by_model[k]):
            if a is None or b is None:
                continue
            if isinstance(a, float) and math.isnan(a):
                continue
            if isinstance(b, float) and math.isnan(b):
                continue
            xs.append(a); ys.append(b)

        if xs and ys:
            plt.scatter(
                xs, ys,
                label=MODEL_LABELS.get(k, k),
                color=MODEL_COLORS.get(k),
                marker=MODEL_MARKERS.get(k, "o"),
                alpha=0.85,
            )

    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.grid(True, alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png, dpi=220)
    plt.close()


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_glob", default=r"D:\FURG 2025\TCC\parte pratica\IA user\JSON_Conversas\conversa_*.json", help="Glob for JSON logs")
    ap.add_argument("--output_dir", default=r"D:\FURG 2025\TCC\parte pratica\IA user\JSON_Conversas\out")
    ap.add_argument("--max_turns", type=int, default=None, help="Optional cap of turns per conversation file")

    ap.add_argument("--decision_classes", default=",".join(DECISION_CLASSES_DEFAULT),
                    help="Comma-separated class labels that indicate a decision (default: Qualificado,Desqualificado)")

    # Output-token prices (USD per 1M tokens). You provided:
    # GPT-4.1-mini output = 1.60 ; Gemini 2.5 Flash output = 2.50
    ap.add_argument("--gpt_out_per_m", type=float, default=None)
    ap.add_argument("--gemini_out_per_m", type=float, default=None)

    # Qualitative workflow
    ap.add_argument("--export_annotation_template", default=None,
                    help="If set, exports a CSV template for human qualitative annotation and exits.")
    ap.add_argument("--ratings_csv", default=None,
                    help="Optional filled annotation CSV to analyze qualitative scores.")

    args = ap.parse_args()

    ensure_dir(args.output_dir)
    decision_classes = [c.strip() for c in args.decision_classes.split(",") if c.strip()]

    paths = sorted(glob.glob(args.input_glob))
    if not paths:
        raise SystemExit(f"No files matched: {args.input_glob}")

    rows = []  # per (conversation, model)
    paired = []  # per conversation, both models

    for p in paths:
        hist = load_conversation_file(p)
        conv_id = Path(p).name

        g = compute_metrics_for_model(
            hist, "gpt", decision_classes, args.gpt_out_per_m, max_turns=args.max_turns
        )
        m = compute_metrics_for_model(
            hist, "gemini", decision_classes, args.gemini_out_per_m, max_turns=args.max_turns
        )

        # assistant final msg (USED segment)
        def final_msg(model_key: str, mm: ModelMetrics) -> str:
            used_end = mm.turns_used - 1
            if used_end < 0:
                return ""
            return (hist[used_end].get(model_key) or {}).get("msg") or ""

        rows.append({
            "conversation_id": conv_id,
            "model": "gpt",
            **g.__dict__,
            "assistant_final_msg": final_msg("gpt", g),
        })
        rows.append({
            "conversation_id": conv_id,
            "model": "gemini",
            **m.__dict__,
            "assistant_final_msg": final_msg("gemini", m),
        })

        paired.append({
            "conversation_id": conv_id,
            "gpt_decision_turn": g.decision_turn,
            "gemini_decision_turn": m.decision_turn,
            "gpt_output_tokens_used": g.output_tokens_used,
            "gemini_output_tokens_used": m.output_tokens_used,
            "gpt_cost_used_usd": g.cost_used_usd,
            "gemini_cost_used_usd": m.cost_used_usd,
            "gpt_repetitiveness_used": g.repetitiveness_used,
            "gemini_repetitiveness_used": m.repetitiveness_used,
            "gpt_avg_words_used": g.avg_words_used,
            "gemini_avg_words_used": m.avg_words_used,
        })

    # Optionally export annotation template and exit
    if args.export_annotation_template:
        export_annotation_template(rows, args.export_annotation_template)
        print(f"Wrote annotation template: {args.export_annotation_template}")
        return

    # Write per-conversation metrics CSV
    import csv
    per_csv = os.path.join(args.output_dir, "per_conversation_metrics_trimmed.csv")
    fieldnames = sorted({k for r in rows for k in r.keys() if k != "assistant_final_msg"})
    with open(per_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            out = {k: r.get(k) for k in fieldnames}
            w.writerow(out)
    print(f"Wrote: {per_csv}")

    paired_csv = os.path.join(args.output_dir, "paired_metrics_trimmed.csv")
    with open(paired_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=sorted(paired[0].keys()))
        w.writeheader()
        for r in paired:
            w.writerow(r)
    print(f"Wrote: {paired_csv}")

    # Summaries
    def by_model(key: str) -> Dict[str, List[float]]:
        out: Dict[str, List[float]] = {"gpt": [], "gemini": []}
        for r in rows:
            v = r.get(key)
            if isinstance(v, bool):
                v = float(v)
            if v is None:
                continue
            try:
                out[r["model"]].append(float(v))
            except Exception:
                pass
        return out

    # Decision rate (decision exists in trimmed segment)
    decision_rate = {}
    for model in ["gpt", "gemini"]:
        convs = [r for r in rows if r["model"] == model]
        decision_rate[model] = sum(1 for r in convs if r.get("decision_turn") is not None) / max(1, len(convs))

    # Basic prints
    print("\n=== Decision Rate (within observed turns, post-decision excluded from metrics) ===")
    for k, v in decision_rate.items():
        print(f"{k}: {v*100:.1f}%")

    # Stats + plots (TRIMMED)
    # decision_turn
    dt = by_model("decision_turn")
    save_boxplot(dt, "Turno de decisão (cortado na decisão)", "Índice do turno", os.path.join(args.output_dir, "decision_turn_box_trimmed.png"))

    # output_tokens_used
    tok = by_model("output_tokens_used")
    save_boxplot(tok, "Tokens de saída (apenas trecho até a decisão)", "Tokens de saída (aprox.)", os.path.join(args.output_dir, "output_tokens_box_trimmed.png"))
    save_hist_models(tok, "Distribuição de tokens de saída (até a decisão)", "Tokens de saída (aprox.)", os.path.join(args.output_dir, "output_tokens_hist_trimmed.png"))

    # cost (output-only)
    if args.gpt_out_per_m is not None and args.gemini_out_per_m is not None:
        cost = by_model("cost_used_usd")
        save_boxplot(cost, "Custo estimado (USD) — somente saída (até a decisão)", "USD", os.path.join(args.output_dir, "cost_box_trimmed.png"))

    # repetitiveness
    rep = by_model("repetitiveness_used")
    save_boxplot(rep, "Repetição entre respostas (Jaccard) — até a decisão", "Similaridade Jaccard", os.path.join(args.output_dir, "repetitiveness_box_trimmed.png"))

    # Outliers (IQR) on output_tokens_used + cost
    gpt_tok_out = iqr_outlier_flags([r.get("output_tokens_used") for r in rows if r["model"] == "gpt"])
    gem_tok_out = iqr_outlier_flags([r.get("output_tokens_used") for r in rows if r["model"] == "gemini"])

    # Optional paired Wilcoxon
    g_dt = [r["gpt_decision_turn"] if r["gpt_decision_turn"] is not None else float("nan") for r in paired]
    m_dt = [r["gemini_decision_turn"] if r["gemini_decision_turn"] is not None else float("nan") for r in paired]
    g_tok = [float(r["gpt_output_tokens_used"]) for r in paired]
    m_tok = [float(r["gemini_output_tokens_used"]) for r in paired]

    print("\n=== Paired tests (Wilcoxon, if available) ===")
    res = try_wilcoxon_paired(g_dt, m_dt)
    if res:
        stat, p = res
        print(f"decision_turn: stat={stat:.3f} p={p:.4g}")
    else:
        print("decision_turn: (wilcoxon unavailable or too few paired samples)")

    res = try_wilcoxon_paired(g_tok, m_tok)
    if res:
        stat, p = res
        print(f"output_tokens_used: stat={stat:.3f} p={p:.4g}")
    else:
        print("output_tokens_used: (wilcoxon unavailable or too few paired samples)")

    if args.gpt_out_per_m is not None and args.gemini_out_per_m is not None:
        g_cost = [r["gpt_cost_used_usd"] for r in paired]
        m_cost = [r["gemini_cost_used_usd"] for r in paired]
        res = try_wilcoxon_paired([float(x) for x in g_cost], [float(x) for x in m_cost])
        if res:
            stat, p = res
            print(f"cost_used_usd: stat={stat:.3f} p={p:.4g}")
        else:
            print("cost_used_usd: (wilcoxon unavailable or too few paired samples)")

    # Scatter: decision_turn vs tokens used
    # Convert Nones to NaN to keep alignment
    def conv_list(model: str, key: str) -> List[float]:
        out = []
        for r in rows:
            if r["model"] != model:
                continue
            v = r.get(key)
            if v is None:
                out.append(float("nan"))
            else:
                out.append(float(v))
        return out

    # We want aligned by conversation. Build per conversation map:
    per_conv = {}
    for r in rows:
        per_conv.setdefault(r["conversation_id"], {})[r["model"]] = r
    x_g, y_g, x_m, y_m = [], [], [], []
    for cid, d in per_conv.items():
        if "gpt" in d:
            x_g.append(d["gpt"].get("decision_turn") if d["gpt"].get("decision_turn") is not None else float("nan"))
            y_g.append(float(d["gpt"].get("output_tokens_used", float("nan"))))
        if "gemini" in d:
            x_m.append(d["gemini"].get("decision_turn") if d["gemini"].get("decision_turn") is not None else float("nan"))
            y_m.append(float(d["gemini"].get("output_tokens_used", float("nan"))))

    save_scatter_models(
        {"gpt": x_g, "gemini": x_m},
        {"gpt": y_g, "gemini": y_m},
        "Turno de decisão vs tokens de saída (até a decisão)",
        "Turno de decisão",
        "Tokens de saída (aprox.)",
        os.path.join(args.output_dir, "decisao_vs_tokens_trimmed.png"),
    )

    # -------------------------
    # Qualitative analysis
    # -------------------------
    if args.ratings_csv:
        ratings = read_ratings_csv(args.ratings_csv)

        # Normalize ratings into dict[dimension][model] = list[float]
        dims = [c for c in DEFAULT_QUAL_COLUMNS if c in ratings[0].keys()]
        if not dims:
            print("\nNo known qualitative columns found in ratings CSV. Expected any of:")
            print(", ".join(DEFAULT_QUAL_COLUMNS))
            return

        dim_by_model = {d: {"gpt": [], "gemini": []} for d in dims}

        for r in ratings:
            model = (r.get("model") or "").strip().lower()
            if model not in ("gpt", "gemini"):
                continue
            for d in dims:
                v = to_float(r.get(d))
                if v is not None:
                    dim_by_model[d][model].append(v)

        print("\n=== Qualitative scores (from ratings_csv) ===")
        for d in dims:
            gvals = dim_by_model[d]["gpt"]
            mvals = dim_by_model[d]["gemini"]
            print(f"\n[{d}]")
            print(f"  gpt   n={len(gvals)} mean={safe_mean(gvals):.3f} median={safe_median(gvals):.3f} sd={safe_stdev(gvals):.3f}")
            print(f"  gemini n={len(mvals)} mean={safe_mean(mvals):.3f} median={safe_median(mvals):.3f} sd={safe_stdev(mvals):.3f}")
            save_boxplot({"gpt": gvals, "gemini": mvals},
                         f"Avaliação qualitativa: {d}", d, os.path.join(args.output_dir, f"qual_{d}_box.png"))

        # If the CSV contains per-conversation paired rows (same conversation_id for both models),
        # you can also compute paired Wilcoxon per dimension:
        by_cid = {}
        for r in ratings:
            cid = r.get("conversation_id")
            model = (r.get("model") or "").strip().lower()
            if not cid or model not in ("gpt", "gemini"):
                continue
            by_cid.setdefault(cid, {})[model] = r

        print("\n=== Qualitative paired tests (Wilcoxon, if available) ===")
        for d in dims:
            a, b = [], []
            for cid, dd in by_cid.items():
                if "gpt" in dd and "gemini" in dd:
                    va = to_float(dd["gpt"].get(d))
                    vb = to_float(dd["gemini"].get(d))
                    if va is not None and vb is not None:
                        a.append(va); b.append(vb)
            res = try_wilcoxon_paired(a, b)
            if res:
                stat, p = res
                print(f"{d}: stat={stat:.3f} p={p:.4g} (n={len(a)})")
            else:
                print(f"{d}: (wilcoxon unavailable or too few paired samples) (n={len(a)})")

        print(f"\nQualitative plots written to: {args.output_dir}")
        # Inter-rater agreement (optional): if you have exactly 2 annotators and provide "annotator"
        # with the same conversation_id+model rows, we compute simple Cohen's kappa for binary correctness,
        # and weighted kappa (approx) is NOT implemented (keep it simple + defensible).
        if "annotator" in ratings[0].keys():
            # Build (conversation_id, model) -> {annotator: row}
            key_map = {}
            for r in ratings:
                cid = r.get("conversation_id")
                model = (r.get("model") or "").strip().lower()
                ann = (r.get("annotator") or "").strip()
                if not cid or model not in ("gpt","gemini") or not ann:
                    continue
                key_map.setdefault((cid, model), {})[ann] = r

            # If we can find at least one pair:
            # We'll compute kappa for classification_correct if it's 0/1.
            def kappa_binary(pairs: List[Tuple[int,int]]) -> Optional[float]:
                if len(pairs) < 10:
                    return None
                n = len(pairs)
                po = sum(1 for a,b in pairs if a==b) / n
                p_yes_a = sum(1 for a,_ in pairs if a==1) / n
                p_yes_b = sum(1 for _,b in pairs if b==1) / n
                pe = p_yes_a*p_yes_b + (1-p_yes_a)*(1-p_yes_b)
                if pe == 1:
                    return None
                return (po - pe) / (1 - pe)

            # Collect pairs across all items, taking the first two annotators per item.
            pairs = []
            for (_cid,_model), ann_map in key_map.items():
                if len(ann_map) < 2:
                    continue
                anns = sorted(ann_map.keys())[:2]
                a = to_float(ann_map[anns[0]].get("classification_correct"))
                b = to_float(ann_map[anns[1]].get("classification_correct"))
                if a is None or b is None:
                    continue
                if a in (0.0,1.0) and b in (0.0,1.0):
                    pairs.append((int(a), int(b)))

            k = kappa_binary(pairs)
            if k is not None:
                print(f"\n=== Inter-rater agreement (Cohen's kappa) on classification_correct (binary) ===")
                print(f"kappa={k:.3f} (n={len(pairs)})")

        # If gold_label is present and decision_class is present, compute accuracy by model.
        if "gold_label" in ratings[0].keys() and "decision_class" in ratings[0].keys():
            acc = {"gpt": [], "gemini": []}
            for r in ratings:
                model = (r.get("model") or "").strip().lower()
                if model not in ("gpt", "gemini"):
                    continue
                gold = (r.get("gold_label") or "").strip().lower()
                pred = (r.get("decision_class") or "").strip().lower()
                if gold in ("qualificado","desqualificado") and pred in ("qualificado","desqualificado"):
                    acc[model].append(1.0 if gold == pred else 0.0)
            if acc["gpt"] or acc["gemini"]:
                print("\n=== Classification accuracy vs gold_label (from ratings_csv) ===")
                for model in ("gpt","gemini"):
                    if acc[model]:
                        print(f"{model}: accuracy={safe_mean(acc[model]):.3f} (n={len(acc[model])})")


    print(f"\nAll plots written to: {args.output_dir}")


if __name__ == "__main__":
    main()