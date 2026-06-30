"""
Lead Scoring Service — Phase 2 AI Brain
=========================================
Uses Claude Sonnet 4.5 (via Emergent LLM Key) to evaluate each tenant lead
on a 0-100 scale based on income, employment, profile completeness and
match quality.

Stored on each `tenant_leads` document:
  - score:               int 0-100
  - score_label:         "hot" | "warm" | "cold"
  - score_breakdown:     dict of category -> int (sum = score)
  - score_reasoning:     short explanation (1-2 sentences ES)
  - scored_at:           ISO datetime
  - scored_model:        "claude-sonnet-4-5-20250929"
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from uuid import uuid4

logger = logging.getLogger(__name__)

MODEL_PROVIDER = "anthropic"
MODEL_NAME = "claude-sonnet-4-5-20250929"

SCORING_SYSTEM_PROMPT = """Eres un analista experto de leads de bienes raíces residenciales para Ross House Rentals (Dumas, TX).

Tu tarea: evaluar a un prospecto inquilino y darle una puntuación de 0 a 100 que represente qué tan probable es que se convierta en un inquilino real, confiable y rentable.

# Criterios de puntuación (suma exactamente 100):

1. **income_ratio** (30 pts):
   - Ratio ingreso/renta ≥ 3.0x → 30
   - Ratio 2.5-3.0x → 22
   - Ratio 2.0-2.5x → 14
   - Ratio < 2.0x → 6
   - Sin datos de ingreso → 8 (neutral, posible incompleto)

2. **employment** (20 pts):
   - "employed" o "self_employed" → 20
   - "retired" → 16
   - "student" → 10
   - "unemployed" → 4
   - sin dato → 8

3. **profile_completeness** (15 pts):
   - Calcula cuántos de estos campos están llenos: phone, move_in_date, household_size, employment_status, monthly_income, current_situation, notes
   - 7/7 → 15, 6/7 → 13, 5/7 → 11, 4/7 → 9, 3/7 → 6, 2/7 → 3, ≤1 → 0

4. **urgency** (15 pts):
   - move_in_date dentro de 30 días → 15
   - 30-60 días → 12
   - 60-90 días → 8
   - >90 días o "Flexible"/null → 6

5. **budget_fit** (10 pts):
   - El rango de renta típico de Ross House en Dumas TX es $900-$1,800/mes (3-4 hab).
   - max_budget entre $1,000-$2,000 → 10
   - max_budget $800-$1,000 o $2,000-$2,500 → 6
   - fuera de rango (<$800 o >$2,500) → 3

6. **household_match** (10 pts):
   - household_size razonable para bedrooms_wanted (≤ bedrooms × 2) → 10
   - household_size = bedrooms × 2+1 → 7
   - claramente sobrepoblado → 3
   - sin pets o pets con detalles claros → bonus implícito en este score

# Regla de salida (estricta):
Responde EXCLUSIVAMENTE con JSON válido (sin markdown, sin ```, sin texto extra):
{
  "income_ratio": <int 0-30>,
  "employment": <int 0-20>,
  "profile_completeness": <int 0-15>,
  "urgency": <int 0-15>,
  "budget_fit": <int 0-10>,
  "household_match": <int 0-10>,
  "score": <int 0-100>,
  "label": "hot" | "warm" | "cold",
  "reasoning": "1-2 oraciones en español explicando los puntos clave"
}

Reglas:
- `score` debe ser la suma exacta de las 6 categorías.
- "hot" si score ≥ 75, "warm" si 50-74, "cold" si < 50.
- `reasoning` máximo 200 caracteres, tono ejecutivo, sin saludos."""


def _label_from_score(score: int) -> str:
    if score >= 75:
        return "hot"
    if score >= 50:
        return "warm"
    return "cold"


def _heuristic_score(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic fallback scoring if the LLM is unavailable.

    Mirrors the rubric in SCORING_SYSTEM_PROMPT.
    """
    income = float(lead.get("monthly_income") or 0)
    budget = float(lead.get("max_budget") or 0)
    employment = (lead.get("employment_status") or "").lower()
    move_in = lead.get("move_in_date") or ""
    household = int(lead.get("household_size") or 1)
    beds = int(lead.get("bedrooms_wanted") or 1)

    # income_ratio
    if income and budget:
        ratio = income / budget
        if ratio >= 3.0:
            income_pts = 30
        elif ratio >= 2.5:
            income_pts = 22
        elif ratio >= 2.0:
            income_pts = 14
        else:
            income_pts = 6
    else:
        income_pts = 8

    # employment
    emp_map = {"employed": 20, "self_employed": 20, "retired": 16, "student": 10, "unemployed": 4}
    employment_pts = emp_map.get(employment, 8)

    # profile_completeness
    filled = sum(1 for k in ("phone", "move_in_date", "household_size",
                              "employment_status", "monthly_income",
                              "current_situation", "notes") if lead.get(k))
    profile_map = {7: 15, 6: 13, 5: 11, 4: 9, 3: 6, 2: 3, 1: 0, 0: 0}
    profile_pts = profile_map.get(filled, 0)

    # urgency
    urgency_pts = 6  # default Flexible
    if move_in:
        try:
            d = datetime.fromisoformat(move_in.replace("Z", "")).replace(tzinfo=None)
            days = (d - datetime.utcnow()).days
            if days <= 30:
                urgency_pts = 15
            elif days <= 60:
                urgency_pts = 12
            elif days <= 90:
                urgency_pts = 8
        except Exception:
            pass

    # budget_fit
    if 1000 <= budget <= 2000:
        budget_pts = 10
    elif 800 <= budget < 1000 or 2000 < budget <= 2500:
        budget_pts = 6
    else:
        budget_pts = 3

    # household_match
    if household <= beds * 2:
        household_pts = 10
    elif household <= beds * 2 + 1:
        household_pts = 7
    else:
        household_pts = 3

    total = income_pts + employment_pts + profile_pts + urgency_pts + budget_pts + household_pts
    total = max(0, min(100, total))

    return {
        "income_ratio": income_pts,
        "employment": employment_pts,
        "profile_completeness": profile_pts,
        "urgency": urgency_pts,
        "budget_fit": budget_pts,
        "household_match": household_pts,
        "score": total,
        "label": _label_from_score(total),
        "reasoning": "Cálculo heurístico (fallback sin LLM). Revisa ratio ingreso/renta y completitud del perfil.",
    }


async def score_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Score a single lead using Claude Sonnet 4.5. Falls back to heuristic on error.

    Returns a dict with the scoring breakdown + metadata fields:
      score, score_label, score_breakdown, score_reasoning, scored_at, scored_model
    """
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    summary = {
        "name": lead.get("name"),
        "email": lead.get("email"),
        "phone": lead.get("phone"),
        "bedrooms_wanted": lead.get("bedrooms_wanted"),
        "max_budget": lead.get("max_budget"),
        "monthly_income": lead.get("monthly_income"),
        "employment_status": lead.get("employment_status"),
        "move_in_date": lead.get("move_in_date"),
        "household_size": lead.get("household_size"),
        "current_situation": lead.get("current_situation"),
        "has_pets": lead.get("has_pets"),
        "pet_details": lead.get("pet_details"),
        "language_pref": lead.get("language_pref"),
        "notes": (lead.get("notes") or "")[:300],
    }

    breakdown: Optional[Dict[str, Any]] = None

    if api_key:
        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage
            chat = LlmChat(
                api_key=api_key,
                session_id=f"lead_score_{uuid4()}",
                system_message=SCORING_SYSTEM_PROMPT,
            ).with_model(MODEL_PROVIDER, MODEL_NAME)
            user_msg = "Evalúa este lead y devuelve el JSON:\n\n```json\n" + \
                       json.dumps(summary, indent=2, default=str, ensure_ascii=False) + "\n```"
            resp = await chat.send_message(UserMessage(text=user_msg))
            text = (resp or "").strip()
            # Strip code fences if Claude added them anyway
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text.rsplit("```", 1)[0]
            text = text.strip()
            parsed = json.loads(text)

            # Validate + normalize
            score = int(parsed.get("score", 0))
            score = max(0, min(100, score))
            label = parsed.get("label") or _label_from_score(score)
            if label not in ("hot", "warm", "cold"):
                label = _label_from_score(score)
            breakdown = {
                "income_ratio": int(parsed.get("income_ratio", 0)),
                "employment": int(parsed.get("employment", 0)),
                "profile_completeness": int(parsed.get("profile_completeness", 0)),
                "urgency": int(parsed.get("urgency", 0)),
                "budget_fit": int(parsed.get("budget_fit", 0)),
                "household_match": int(parsed.get("household_match", 0)),
                "score": score,
                "label": label,
                "reasoning": (parsed.get("reasoning") or "")[:300],
            }
        except Exception as e:
            logger.warning(f"[lead_scoring] LLM scoring failed, using heuristic: {e}")
            breakdown = None

    if breakdown is None:
        breakdown = _heuristic_score(lead)

    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "score": breakdown["score"],
        "score_label": breakdown["label"],
        "score_breakdown": {
            "income_ratio": breakdown["income_ratio"],
            "employment": breakdown["employment"],
            "profile_completeness": breakdown["profile_completeness"],
            "urgency": breakdown["urgency"],
            "budget_fit": breakdown["budget_fit"],
            "household_match": breakdown["household_match"],
        },
        "score_reasoning": breakdown["reasoning"],
        "scored_at": now_iso,
        "scored_model": MODEL_NAME if api_key else "heuristic-v1",
    }


async def score_and_persist(db, lead_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a lead by id, compute its score, and persist on the same doc."""
    lead = await db.tenant_leads.find_one({"_id": lead_id})
    if not lead:
        return None
    result = await score_lead(lead)
    await db.tenant_leads.update_one(
        {"_id": lead_id},
        {"$set": {**result, "updated_at": datetime.utcnow()}},
    )
    return result
