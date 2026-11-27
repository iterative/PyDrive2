"""Reuse a completed PR matrix when GitHub would check out the same files."""

import json
import os
import subprocess
import time
from pathlib import Path


def api(path, *, pages=False):
    args = ["gh", "api"]
    if pages:
        args += ["--paginate", "--slurp"]
    url = f"repos/{os.environ['GITHUB_REPOSITORY']}/{path}"
    return json.loads(subprocess.check_output([*args, url], text=True))


def merge_commit(event):
    pr = event["pull_request"]
    if event["action"] != "edited":
        return api(f"git/commits/{os.environ['GITHUB_SHA']}")

    # On an automatic retarget the event can still point at the old merge ref.
    for attempt in range(6):
        if attempt:
            time.sleep(2)
        current = api(f"pulls/{pr['number']}")
        if (
            current["head"]["sha"] != pr["head"]["sha"]
            or current["base"]["ref"] != pr["base"]["ref"]
            or current["state"] != "open"
        ):
            break
        if current["mergeable"] is False:
            raise SystemExit(
                "Resolve the merge conflict before rerunning the tests."
            )
        if current["mergeable"] and current["merge_commit_sha"]:
            commit = api(f"git/commits/{current['merge_commit_sha']}")
            if [p["sha"] for p in commit["parents"]] == [
                current["base"]["sha"],
                pr["head"]["sha"],
            ]:
                return commit
    raise SystemExit(
        "GitHub has no current test merge commit for this PR revision."
    )


def previous_matrix(pr, checkout, live):
    head = pr["head"]
    query = (
        f"actions/runs?event=pull_request&head_sha={head['sha']}&per_page=100"
    )
    for page in api(query, pages=True):
        for run in page["workflow_runs"]:
            if (
                run["path"]
                not in {
                    ".github/workflows/test.yml",
                    ".github/workflows/retarget.yml",
                }
                or run["conclusion"] not in {"success", "failure"}
                or (run["head_repository"] or {}).get("id")
                != head["repo"]["id"]
                or run["head_branch"] != head["ref"]
            ):
                continue
            jobs = api(
                f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100",
                pages=True,
            )
            matrix = [
                {
                    step["name"]: step["conclusion"]
                    for step in job.get("steps") or []
                }
                for batch in jobs
                for job in batch["jobs"]
                if job["name"].split(" / ")[-1].startswith("test (")
            ]
            if not matrix or not all(
                steps.get(checkout) == "success" for steps in matrix
            ):
                continue
            required = ["Run offline tests"]
            if live:
                required.append("Run live Drive tests")
            if run["conclusion"] == "success" and not all(
                steps.get(name) == "success"
                for steps in matrix
                for name in required
            ):
                continue
            return run
    return None


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    pr = event["pull_request"]
    commit = merge_commit(event)
    checkout = f"Check out PR #{pr['number']} (tree {commit['tree']['sha']})"
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        print(f"sha={commit['sha']}\ncheckout={checkout}", file=output)
    if os.environ["GITHUB_RUN_ATTEMPT"] != "1":
        return  # A maintainer explicitly requested another attempt.
    live = (
        pr["head"]["repo"]["full_name"] == os.environ["GITHUB_REPOSITORY"]
        and "dependabot[bot]"
        not in {os.environ["GITHUB_ACTOR"], pr["user"]["login"]}
        and not pr["head"]["ref"].startswith("dependabot/")
    )
    previous = previous_matrix(pr, checkout, live)
    if previous:
        url = previous["html_url"] + f"/attempts/{previous['run_attempt']}"
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
            print(
                f"The [matrix for this merge tree]({url}) "
                f"finished with {previous['conclusion']}.",
                file=summary,
            )
        if previous["conclusion"] != "success":
            raise SystemExit(f"The matrix for this merge tree failed: {url}")
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            print("passed=true", file=output)


if __name__ == "__main__":
    main()
