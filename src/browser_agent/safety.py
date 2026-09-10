"""Small, conservative approval gate based on the actual browser target.

The actor cannot mark its own action safe. Unknown buttons and form submissions
require confirmation; ordinary links and local text editing do not. A denied or
uncertain consequential action ends the run instead of being replayed.
"""

from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit


@dataclass(frozen=True)
class Assessment:
    classification: str
    effect: dict
    reason: str
    details_complete: bool = True

    @property
    def requires_approval(self):
        return self.classification == "consequential"

    @property
    def forbidden(self):
        return self.classification == "forbidden"


# Generic action language, never site-specific selectors or navigation recipes.
_CRITICAL = (
    "delete",
    "remove",
    "empty",
    "send",
    "submit",
    "apply",
    "publish",
    "purchase",
    "place order",
    "pay",
    "transfer",
    "unsubscribe",
    "upload",
    "reset",
    "удал",
    "очист",
    "отправ",
    "отклик",
    "оплат",
    "купить",
    "оформить",
    "публиков",
    "перевести",
    "отпис",
    "загруз",
    "сброс",
)
_READ_ONLY = {"read", "tabs", "screenshot", "remember", "finish", "ask_user"}
_VIEWING = {"back", "scroll", "switch_tab"}


def _action_link(context):
    label = " ".join(str(context.get(k, "")) for k in ("name", "text")).casefold()
    href = urlsplit(str(context.get("href", "")))
    # Folder/category nouns such as "Trash" or "Spam" alone are navigation.
    if any(word in label for word in _CRITICAL):
        return True
    if any(
        phrase in label for phrase in ("trash this", "trash selected", "mark as spam")
    ):
        return True
    segments = (
        unquote(href.path).casefold().replace("-", "/").replace("_", "/").split("/")
    )
    if any(segment.startswith(word) for segment in segments for word in _CRITICAL):
        return True
    return any(
        key.casefold() in {"action", "operation", "op", "do", "command"}
        and any(word in value.casefold() for word in _CRITICAL)
        for key, value in parse_qsl(href.query)
    )


def resolved_effect(action, context):
    """Display exact observed form values and proposed tool arguments separately."""
    args = action.get("args", {})
    destination = (
        context.get("form_action")
        or context.get("href")
        or context.get("document_url")
        or context.get("url", "")
    )
    if action.get("tool") == "navigate":
        destination = args.get("url", destination)
    return {
        "operation": "form_change" if context.get("form_action") else "page_change",
        "destination": destination,
        "target": {
            k: context.get(k)
            for k in ("tag", "role", "type", "name", "text", "value", "checked")
        },
        "fields": context.get("fields", []),
        "context": context.get("context", ""),
        "method": context.get("form_method", ""),
        "action_arguments": {
            k: v for k, v in args.items() if k not in {"ref", "page_id"}
        },
    }


def assess(action, context):
    tool, args = action["tool"], action.get("args", {})
    effect = resolved_effect(action, context)

    def result(kind, reason, complete=True):
        return Assessment(kind, effect, reason, complete)

    if tool in _READ_ONLY:
        return result("ordinary", "Read-only tool")
    if context.get("type") in {"password", "file"}:
        return result(
            "forbidden", "Enter credentials or choose files manually in the browser"
        )
    if (
        context.get("disabled")
        or context.get("attached") is False
        or context.get("visible") is False
    ):
        return result("forbidden", "Target unavailable; take a fresh observation")
    if tool in _VIEWING:
        return result(
            "ordinary", "View/navigation action; page prose is not its target label"
        )
    if tool == "navigate":
        if urlsplit(args.get("url", "")).scheme not in {"http", "https"}:
            return result("forbidden", "Only HTTP(S) navigation is supported")
        critical = _action_link({"href": args["url"]})
    elif tool in {"fill", "select"}:
        # Preparation stays local. The ensuing submission has its own approval.
        critical = False
    elif (
        tool == "press"
        and args.get("key")
        not in {
            "Enter",
            "Space",
            "Delete",
            "Backspace",
        }
        or (
            tool == "press"
            and args.get("key") in {"Delete", "Backspace"}
            and context.get("tag") in {"input", "textarea"}
        )
    ):
        critical = False
    elif tool in {"click", "press"}:
        link = (
            context.get("tag") == "a"
            and bool(context.get("href"))
            and not context.get("form_action")
        )
        if link:
            critical = _action_link(context)
        elif tool == "click" and (
            context.get("tag") in {"textarea", "select"}
            or context.get("type") in {"text", "search", "email", "number"}
        ):
            critical = False
        else:
            critical = (
                True  # A JS button or Enter/Space can submit, even with a bland label.
            )
    else:
        critical = True
    if critical and context.get("context_complete") is False:
        return result(
            "forbidden",
            "Cannot show complete action/form details; inspect manually",
            False,
        )
    if critical:
        return result(
            "consequential", "Confirm this exact control, destination and form values"
        )
    return result("ordinary", "Ordinary navigation or local form preparation")
