import json
import os
import smtplib
import ssl
import sys
import zipfile
from io import BytesIO
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


PHILIPPINES_TZ = timezone(timedelta(hours=8), name="PHT")
CLOUDFLARE_ANALYTICS_URL = "https://dash.cloudflare.com/?to=/:account/web-analytics"
PUBLISH_STATUS_ARTIFACT_PREFIX = "trendwatch-publish-status-"


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable {name} is not configured")
    return value


def fetch_workflow_runs(repository, token, start_utc, end_utc):
    workflow = quote("deploy.yml", safe="")
    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/"
        f"{workflow}/runs?per_page=100"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Could not retrieve automation status from GitHub: {error}") from error

    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise RuntimeError("GitHub returned an unexpected workflow-runs response")

    matching_runs = []
    for run in runs:
        created_at = run.get("created_at")
        if not isinstance(created_at, str):
            continue
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if start_utc <= created <= end_utc:
            matching_runs.append(run)
    return matching_runs


def github_api_request(url, token, accept="application/vnd.github+json"):
    request = Request(
        url,
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Could not retrieve publishing metrics from GitHub: {error}") from error


def fetch_publish_status(repository, token, run_id):
    """Fetches and validates the publishing-metrics artifact for one deployment run."""
    artifacts_url = (
        f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/artifacts"
        "?per_page=100"
    )
    try:
        artifacts = json.loads(github_api_request(artifacts_url, token))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("GitHub returned invalid artifact metadata") from error
    if not isinstance(artifacts, dict):
        raise RuntimeError("GitHub returned an unexpected workflow-artifacts response")
    artifact_list = artifacts.get("artifacts")
    if not isinstance(artifact_list, list):
        raise RuntimeError("GitHub returned an unexpected workflow-artifacts response")

    expected_name = f"{PUBLISH_STATUS_ARTIFACT_PREFIX}{run_id}"
    artifact = next(
        (
            item for item in artifact_list
            if isinstance(item, dict)
            and item.get("name") == expected_name
            and not item.get("expired", False)
            and isinstance(item.get("id"), int)
        ),
        None,
    )
    if artifact is None:
        return None

    archive_url = (
        f"https://api.github.com/repos/{repository}/actions/artifacts/"
        f"{artifact['id']}/zip"
    )
    archive_bytes = github_api_request(
        archive_url,
        token,
        accept="application/vnd.github+json",
    )
    try:
        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            status_name = next(
                (
                    name for name in archive.namelist()
                    if name.rsplit("/", 1)[-1] == "publish_status.json"
                ),
                None,
            )
            if status_name is None:
                raise RuntimeError("Publishing-metrics artifact has no status JSON file")
            status = json.loads(archive.read(status_name))
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("Publishing-metrics artifact is invalid") from error

    required_counts = (
        "articles_created",
        "articles_updated",
        "trends_skipped",
        "trends_deferred",
        "retry_queue_remaining",
    )
    if (
        not isinstance(status, dict)
        or status.get("schema_version") != 1
        or any(type(status.get(key)) is not int or status[key] < 0 for key in required_counts)
    ):
        raise RuntimeError("Publishing-metrics artifact has an invalid data shape")
    return status


def build_report(repository, token, now=None):
    now_utc = now or datetime.now(timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    start_utc = now_utc - timedelta(days=1)
    now_pht = now_utc.astimezone(PHILIPPINES_TZ)
    start_pht = start_utc.astimezone(PHILIPPINES_TZ)
    report_errors = []

    try:
        runs = fetch_workflow_runs(repository, token, start_utc, now_utc)
    except RuntimeError as error:
        runs = []
        report_errors.append(str(error))

    counts = {"success": 0, "failure": 0, "cancelled": 0, "in_progress": 0, "other": 0}
    publish_metrics = {}
    for run in runs:
        if run.get("status") != "completed":
            counts["in_progress"] += 1
            continue
        conclusion = run.get("conclusion")
        if conclusion in counts:
            counts[conclusion] += 1
        else:
            counts["other"] += 1
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        try:
            publish_metrics[run_id] = fetch_publish_status(repository, token, run_id)
        except RuntimeError as error:
            publish_metrics[run_id] = None
            report_errors.append(f"Run #{run.get('run_number', '?')} publishing metrics: {error}")

    if counts["failure"] or report_errors:
        status = "ATTENTION"
    elif runs:
        status = "Daily status"
    else:
        status = "No runs"
    date_label = now_pht.strftime("%Y-%m-%d")
    subject = f"{status}: TrendWatch automation report for {date_label} PHT"
    lines = [
        "TrendWatch Philippines — Daily automation report",
        f"Report date: {date_label} (Philippines Time)",
        f"Reporting window: {start_pht:%Y-%m-%d %H:%M} to {now_pht:%Y-%m-%d %H:%M} PHT",
        "",
        "Automation workflow runs in the reporting window:",
        f"- Successful: {counts['success']}",
        f"- Failed: {counts['failure']}",
        f"- Cancelled: {counts['cancelled']}",
        f"- Still running or queued: {counts['in_progress']}",
        f"- Other conclusions: {counts['other']}",
    ]

    metrics_available = [
        run for run in runs
        if isinstance(run.get("id"), int)
        and publish_metrics.get(run["id"]) is not None
    ]
    metrics_missing = [
        run for run in runs
        if isinstance(run.get("id"), int)
        and run.get("status") == "completed"
        and publish_metrics.get(run["id"]) is None
    ]
    successful_metrics = [
        run for run in metrics_available
        if run.get("conclusion") == "success"
    ]
    published_created = sum(
        publish_metrics[run["id"]]["articles_created"]
        for run in successful_metrics
    )
    published_updated = sum(
        publish_metrics[run["id"]]["articles_updated"]
        for run in successful_metrics
    )
    lines.extend([
        "",
        "Article publishing in the reporting window:",
        (
            f"- Successfully posted: {published_created} new article(s), "
            f"{published_updated} refreshed article(s) across "
            f"{len(successful_metrics)} deployment(s) with metrics."
        ),
        (
            f"- Skipped: {sum(publish_metrics[run['id']]['trends_skipped'] for run in metrics_available)} "
            "trend(s)."
        ),
        (
            f"- Deferred: {sum(publish_metrics[run['id']]['trends_deferred'] for run in metrics_available)} "
            "trend(s); retry-queue counts are listed per run."
        ),
        f"- Publishing metrics unavailable: {len(metrics_missing)} completed run(s).",
        "- Publish totals count successful deployment operations, not unique article URLs.",
    ])

    if not runs and not report_errors:
        lines.append("- No matching workflow runs were found.")

    for run in sorted(runs, key=lambda item: item.get("created_at", ""), reverse=True):
        created = run.get("created_at", "time unavailable")
        try:
            created = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(
                PHILIPPINES_TZ
            ).strftime("%Y-%m-%d %H:%M PHT")
        except (AttributeError, ValueError):
            pass
        conclusion = run.get("conclusion") or run.get("status") or "unknown"
        lines.append(
            f"- Run #{run.get('run_number', '?')}: {conclusion}, "
            f"{created} — {run.get('html_url', 'link unavailable')}"
        )
        if run.get("status") == "completed" and isinstance(run.get("id"), int):
            metrics = publish_metrics.get(run["id"])
            if metrics is None:
                lines.append(
                    "  Article status: unavailable (metrics artifact missing or could not be read)."
                )
            elif conclusion == "success":
                lines.append(
                    "  Article status: "
                    f"{metrics['articles_created']} new posted, "
                    f"{metrics['articles_updated']} refreshed, "
                    f"{metrics['trends_skipped']} skipped, "
                    f"{metrics['trends_deferred']} deferred, "
                    f"{metrics['retry_queue_remaining']} remaining in retry queue."
                )
                if not metrics["articles_created"] and not metrics["articles_updated"]:
                    lines.append("  No new or refreshed article was posted in this run.")
            else:
                lines.append(
                    "  Article status: deployment did not succeed; generated articles "
                    "are not counted as posted."
                )

    for error in report_errors:
        lines.extend(["", f"Status retrieval warning: {error}"])

    lines.extend([
        "",
        "Site analytics",
        "Cloudflare Web Analytics visitor counts are not included because Cloudflare does not "
        "document a supported API for retrieving these Web Analytics dashboard metrics for this report.",
        f"View the Cloudflare Web Analytics dashboard: {CLOUDFLARE_ANALYTICS_URL}",
        "",
        f"GitHub Actions workflow: https://github.com/{repository}/actions/workflows/deploy.yml",
        "This report summarizes workflow run status; it does not independently verify generated content.",
    ])
    return subject, "\n".join(lines), bool(report_errors or counts["failure"])


def send_email(subject, body):
    smtp_host = required_env("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = required_env("SMTP_USERNAME")
    smtp_password = required_env("SMTP_PASSWORD")
    recipient = required_env("REPORT_EMAIL_TO")
    sender = os.environ.get("REPORT_EMAIL_FROM", smtp_username).strip()

    if smtp_port not in {465, 587}:
        raise ValueError("SMTP_PORT must be 465 (SSL) or 587 (STARTTLS)")
    if "@" not in parseaddr(recipient)[1] or "@" not in parseaddr(sender)[1]:
        raise ValueError("REPORT_EMAIL_TO and REPORT_EMAIL_FROM must be valid email addresses")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)

    if smtp_port == 465:
        with smtplib.SMTP_SSL(
            smtp_host, smtp_port, timeout=30, context=ssl.create_default_context()
        ) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(smtp_username, smtp_password)
            server.send_message(message)


def main():
    repository = required_env("GITHUB_REPOSITORY")
    token = required_env("GITHUB_TOKEN")
    subject, body, report_has_errors = build_report(repository, token)
    send_email(subject, body)
    print("Daily automation report email sent.")
    if report_has_errors:
        sys.exit("The report was sent with workflow-status warnings or failures.")


if __name__ == "__main__":
    main()
