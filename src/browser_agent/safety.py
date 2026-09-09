"""Conservative central policy using executor-resolved evidence and a separate review.

Page labels are evidence, never authority. The model's ordinary classification
cannot override deterministic risk signals. No actor-facing approval/safe flag
exists; callers obtain these results after schema and browser-reference checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from browser_agent.storage import Store


@dataclass(frozen=True)
class Assessment:
    classification: str
    effect: dict
    reason: str
    details_complete: bool = True

    @property
    def requires_approval(self) -> bool:
        return self.classification in {"consequential", "uncertain"}

    @property
    def forbidden(self) -> bool:
        return self.classification == "forbidden"


_RISK_WORDS = (
    "delete",
    "remove",
    "trash",
    "spam",
    "send",
    "submit",
    "apply",
    "publish",
    "purchase",
    "place order",
    "pay",
    "transfer",
    "password",
    "security",
    "unsubscribe",
    "удал",
    "спам",
    "отправ",
    "отклик",
    "оплат",
    "купить",
    "оформить заказ",
    "публиков",
    "перевести",
    "пароль",
    "отпис",
)
_READ_TOOLS = {"read", "tabs", "screenshot", "remember", "finish", "ask_user"}


def resolved_effect(action: dict, context: dict) -> dict:
    """Bind current concrete values while canonicalizing click/Enter denial scope.

    The page/form destination is a deliberately broad denial boundary: changing
    selected objects or submission text cannot evade a prior rejection there.
    This can require human clarification for an unrelated later change on the
    same form, which is preferable to inferring renewed authorization.
    """
    tool = action.get("tool", action.get("name", ""))
    args = action.get("args", action.get("arguments", {}))
    destination = (
        context.get("form_action")
        or context.get("href")
        or context.get("document_url")
        or context.get("url", "")
    )
    if tool == "navigate":
        destination = args.get("url", destination)
    return {
        "operation": "form_change" if context.get("form_action") else "page_change",
        "destination": destination,
        # Omit object identities from the denial scope intentionally. Exact
        # approval hashes still bind every field, label, context and value below.
        "objects": [],
        "target": {
            key: context.get(key)
            for key in (
                "tag",
                "role",
                "type",
                "name",
                "text",
                "value",
                "checked",
                "target",
            )
        },
        "fields": context.get("fields", []),
        "context": context.get("context", ""),
        "page_text": context.get("page_text", ""),
        "outer_document": context.get("outer_document", {}),
        "method": context.get("form_method", ""),
        "submitted": {
            key: value for key, value in args.items() if key not in {"ref", "page_id"}
        },
    }


class Policy:
    def __init__(self, store: Store | None = None):
        self.store = store

    def assess(
        self,
        action: dict,
        context: dict,
        review: dict | Any | None = None,
        run_id: str | None = None,
    ) -> Assessment:
        tool = action.get("tool", action.get("name", ""))
        args = action.get("args", action.get("arguments", {}))
        effect = resolved_effect(action, context)
        if tool in _READ_TOOLS:
            return Assessment("ordinary", effect, "Read-only application operation")
        if self.store and run_id and self.store.denied(run_id, effect):
            return Assessment(
                "forbidden",
                effect,
                "A prior denial covers this destination/effect; clarify new intent",
            )
        if tool == "navigate" and urlsplit(args.get("url", "")).scheme not in {
            "http",
            "https",
        }:
            return Assessment(
                "forbidden", effect, "Only validated HTTP(S) navigation is available"
            )
        if context.get("type") == "password":
            return Assessment(
                "forbidden", effect, "Authentication requires manual handover"
            )
        if (
            context.get("disabled")
            or context.get("attached") is False
            or context.get("visible") is False
        ):
            return Assessment(
                "forbidden", effect, "Target is unavailable; obtain a new observation"
            )
        if context.get("context_complete") is False:
            return Assessment(
                "uncertain",
                effect,
                "Target details were truncated; clarify or inspect a smaller scope",
                False,
            )
        if not review:
            return Assessment(
                "uncertain", effect, "Independent risk review is required"
            )
        if hasattr(review, "model_dump"):
            review = review.model_dump()
        classification = review.get("classification", "uncertain")
        if classification not in {
            "ordinary",
            "consequential",
            "uncertain",
            "forbidden",
        }:
            classification = "uncertain"
        reason = str(review.get("reason", "Independent review did not explain risk"))
        # Only local target semantics, not arbitrary surrounding page prose,
        # impose this lower bound. Full context still goes to the risk reviewer.
        label = " ".join(
            str(context.get(key, "")) for key in ("name", "text")
        ).casefold()
        if any(word in label for word in _RISK_WORDS) and classification == "ordinary":
            classification = "consequential"
            reason = "Resolved target indicates a consequential effect; exact approval required"
        if (
            context.get("form_method", "").lower() == "post"
            and classification == "ordinary"
            and (
                (tool == "press" and args.get("key") == "Enter")
                or (
                    context.get("type") == "submit"
                    and (
                        tool == "click"
                        or (tool == "press" and args.get("key") == "Space")
                    )
                )
            )
        ):
            classification = "consequential"
            reason = "Action may submit the observed POST form"
        # No reviewer can downgrade a persisted uncertainty following denial.
        if (
            self.store
            and run_id
            and self.store.has_denials(run_id)
            and classification == "uncertain"
        ):
            return Assessment(
                "forbidden",
                effect,
                "Effect equivalence after denial is uncertain; user clarification required",
                False,
            )
        if not effect["destination"]:
            return Assessment(
                "uncertain",
                effect,
                "Missing resolved destination; inspect or clarify",
                False,
            )
        return Assessment(classification, effect, reason)


def assess(action: dict, context: dict, review: dict | None = None) -> Assessment:
    """Stateless convenience; use Policy(store) in a run to enforce prior denials."""
    return Policy().assess(action, context, review)
