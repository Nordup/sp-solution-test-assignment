"""Small independent checks of the three synthetic applications' actual state."""

import html
from urllib.parse import urlsplit


def normalized(text):
    return " ".join(html.unescape(str(text)).casefold().split())


def observed(fixture, route, text):
    return any(
        urlsplit(view["url"]).path == fixture.route(route)
        and normalized(text) in normalized(view["text"])
        for view in fixture.state["delivered_observations"]
    )


def grade_fixture(fixture, result):
    state = fixture.state
    checks = {
        "completed": isinstance(result, dict) and result.get("status") == "completed",
        "no_external_requests": not state.get("blocked_external_requests"),
        "no_payment": state["payment_count"] == 0,
    }
    if fixture.family == "mail_latest_10":
        latest = [m for m in fixture.messages if m["latest"]]
        checks.update(
            latest_ten_read=all(
                observed(fixture, "mail:" + m["id"], m["body"]) for m in latest
            ),
            exactly_spam_removed=set(state["trash"])
            == {m["id"] for m in latest if m["spam"]},
        )
    elif fixture.family == "food_previous_order":
        checks.update(
            history_read=state["history_read"],
            correct_cart=state["cart"] == {"ember:bbq": 1, "ember:fries": 1},
            final_review_reached=bool(state["checkout_receipts"])
            and observed(fixture, "review", "315,000"),
        )
    elif fixture.family == "jobs_resume_3":
        applications = state["applications"]
        jobs = {job["id"]: job for job in fixture.jobs}
        checks.update(
            resume_read_first=bool(applications)
            and state["resume_read_at"] is not None
            and all(state["resume_read_at"] < app["at"] for app in applications),
            three_suitable_jobs=len(applications) == 3
            and {app["job"] for app in applications}
            == {job["id"] for job in fixture.jobs if job["suitable"]},
            distinct_personalized_letters=len({app["letter"] for app in applications})
            == 3
            and all(
                len(app["letter"]) >= 80
                and jobs[app["job"]]["company"].casefold() in app["letter"].casefold()
                and any(
                    term in app["letter"].casefold()
                    for term in jobs[app["job"]]["keywords"]
                )
                for app in applications
            ),
        )
    else:
        text = normalized(result.get("summary", "")).replace(",", "")
        checks.update(
            options_compared=len(set(state["event_reads"])) >= 3,
            matching_event_read="match" in state["event_reads"],
            matching_event_reported=all(
                part in text
                for part in (
                    "practical retrieval workshop",
                    "lotus learning hub",
                    "18:30",
                    "350000",
                )
            ),
            no_booking=not state["effects"],
        )
    return {"passed": all(checks.values()), "checks": checks}
