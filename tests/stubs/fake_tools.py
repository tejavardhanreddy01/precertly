"""Stand-in for aws, curl, npx, docker and session-manager-plugin, used by the deploy tests.

Installed on PATH under each tool's name. Every call is appended to calls.log next to the bin
directory. Without a stub.json next to it the tool fails with status 97, so a dry-run test proves
nothing was called. With one, the tool answers from canned text; the `aws ecs` calls keep a
tiny service model (desired and running counts) in state.json.

stub.json keys (all optional):
  stacks            {stack name: status}; a stack that is not listed does not exist
  service_missing   describe-services finds no service
  cluster_missing   describe-services fails with ClusterNotFoundException
  desired, running  starting counts of the service
  stuck_running     running never follows desired (a task that never starts or stops)
  stick_on_zero     running follows desired up, but does not drop when desired becomes 0
  health_check      the task definition has a container health check
  ecr_missing       the repository does not exist
  fail_all          every aws call fails with this stderr (status 255)
  exec              {"/health" | "/policies" | "smoke_llm": text}: output of execute-command
  exec_sleep        seconds execute-command takes
  exec_errors       {"/health" | "/policies" | "smoke_llm": N}: the first N calls fail with
                    "agent isn't running" (InvalidParameterException)
  agent_polls       the ExecuteCommandAgent reports PENDING for the first N status queries
  agent_never_running  it never reports RUNNING
  health            healthStatus of the task (default HEALTHY)
  health_after_polls   healthStatus is UNKNOWN for the first N describe-tasks health queries,
                    then HEALTHY
  diff_text         what `cdk diff` prints; {email}, {arn}, {prefix}, {api} and {auth} are
                    replaced by the budgetEmail, webCertificateArn, authDomainPrefix,
                    liveApiOrigin and liveAuthOrigin context values (empty when not given)
  diff_fail         `cdk diff` prints this to stderr and fails
  outputs           {output key: value} for the data stack's outputs (defaults below)
  no_outputs        the data stack exists but has no outputs
  aurora_missing    describe-db-clusters fails with DBClusterNotFoundFault
  tools_running     a precertly-tools task is already running (its ARN is listed)
  tools_run_fail    run-task starts nothing and reports this failure reason
  tools_exit        container exit code of a tools task (default 0)
  tools_polls       describe-tasks reports RUNNING this many times before STOPPED (default 0)
  tools_never_stops the task stays RUNNING until stop-task is called (then exit code 137)
  tools_describe_fail  describe-tasks for the tools task fails
  tools_stop_fail   stop-task fails
  tools_log         what the log group holds (default "tools output")
  build_fail        {"uv sync" | "npm ci" | "npm run build" | ...: stderr}: that uv, npm or node
                    command (matched as a prefix of "tool args") fails with status 1
  build_touches     path (relative to web/, where npm runs) that every npm command appends to,
                    to model a build that changes a tracked file
  s3_stale          keys already in the bucket that the build does not have; `sync --delete`
                    reports them as deletions unless an --exclude pattern covers them
  s3_fail           "assets" | "rest" | "index": that upload fails; "dryrun": the preview fails
  content_types     {key: type} overriding what head-object reports (default: by extension)
  invalidate_fail   create-invalidation fails with this stderr
  inval_polls       get-invalidation reports InProgress for the first N queries, then Completed
  inval_never       get-invalidation never reports Completed
  site              {key: override} for curl, which by default serves a healthy site. The key is
                    the URL path ("/", "/nope", "/assets/index-abc123.js") or, for plain HTTP,
                    "http:" plus the path. An override may hold status, headers ({name: value};
                    null removes the header), body, exit (curl exits with this code, stderr says
                    why and the write-out is "000 0"). The key "*" applies to every request.
  site_lowercase_headers  send every header name in lower case (as over HTTP/2)
  tls_verify        ssl_verify_result for https requests (default 0)
  live_desired, live_running, live_service_missing, live_stuck_running, live_stick_on_zero
                    the same, for the live service precertly-api-live (its own counts)
  publish_outputs   how many PublishOutput... outputs a dependency stack has (default 2)
  gateway           {"METHOD /path": override} for the live API's gateway, which curl reaches at
                    any *.execute-api.* host; an override is like one in "site" (status, headers,
                    body, exit). The key "*" applies to every request. By default the gateway
                    answers GET /health with 200 while the live service has a running task and
                    500 otherwise, 401 for the two authorized routes, 404 for any other path,
                    and a preflight with the CORS headers only for the origin below.
  gateway_polls     GET /health answers 500 this many times after the task is running (cold start)
  gateway_origin    the one origin the gateway allows (default https://precertly.example.org)
  live_run_line     what the live run script prints over ECS Exec (default: a finished run)
"""

import fnmatch
import json
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ACCOUNT = "123456789012"
TASK_ARN = f"arn:aws:ecs:us-east-2:{ACCOUNT}:task/precertly/0123456789abcdef"
LIVE_TASK_ARN = f"arn:aws:ecs:us-east-2:{ACCOUNT}:task/precertly/1111222233334444"
TOOLS_ARN = f"arn:aws:ecs:us-east-2:{ACCOUNT}:task/precertly/aaaabbbbccccdddd"
OTHER_TOOLS_ARN = f"arn:aws:ecs:us-east-2:{ACCOUNT}:task/precertly/9999888877776666"
DATA_OUTPUTS = {
    "ClusterIdentifier": "precertly-data-database-abc123",
    "SecretArn": f"arn:aws:secretsmanager:us-east-2:{ACCOUNT}:secret:DatabaseSecret-AbCdEf",
    "ToolsSubnetIds": "subnet-0a1b2c,subnet-3d4e5f",
    "ToolsSecurityGroupId": "sg-0123abcd",
    "ToolsLogGroupName": "/precertly/tools",
    # precertly-web's outputs
    "DistributionId": "E1A2B3C4D5E6F7",
    "BucketName": "precertly-web-sitebucket397a1860-abc123def456",
    "SiteUrl": "https://precertly.example.org",
    # precertly-live's outputs
    "ApiUrl": "https://abc123def4.execute-api.us-east-2.amazonaws.com/",
}
LIVE_RUN_LINE = (
    "LIVE_RUN status=done reason=- overall=met tokens=1000/234 "
    "timings=waking_database:12.3,checking_limits:0.1,ingesting:5.0,judging:40.0"
)
IMAGE = f"{ACCOUNT}.dkr.ecr.us-east-2.amazonaws.com/precertly-api:sha-abc123def456"


def main() -> int:
    tool = Path(sys.argv[0]).name
    args = sys.argv[1:]
    with (BASE / "calls.log").open("a") as log:
        log.write(f"{tool} {' '.join(args)}\n")
    config_path = BASE / "stub.json"
    if not config_path.exists():
        return 97
    config = json.loads(config_path.read_text())
    if tool == "aws":
        return aws(args, config)
    if tool == "npx":
        return npx(args, config)
    if tool in ("uv", "npm", "node"):
        # The Vite variables each build command sees, for the tests that check what is passed.
        with (BASE / "build_env.log").open("a") as env_log:
            seen = " ".join(
                f"{k}={os.environ[k]}"
                for k in ("VITE_LIVE_API_URL", "VITE_LIVE_HOSTED_UI_URL", "VITE_LIVE_CLIENT_ID")
                if k in os.environ
            )
            env_log.write(f"{tool} {' '.join(args)} | {seen}\n")
        return build_tool(tool, args, config)
    if tool == "curl":
        return curl(args, config)
    return 0


def fail(message: str, code: int = 254) -> int:
    print(message, file=sys.stderr)
    return code


def npx(args: list[str], config: dict) -> int:
    email = next((a.split("=", 1)[1] for a in args if a.startswith("budgetEmail=")), "")
    arn = next((a.split("=", 1)[1] for a in args if a.startswith("webCertificateArn=")), "")
    prefix = next((a.split("=", 1)[1] for a in args if a.startswith("authDomainPrefix=")), "")
    api = next((a.split("=", 1)[1] for a in args if a.startswith("liveApiOrigin=")), "")
    auth = next((a.split("=", 1)[1] for a in args if a.startswith("liveAuthOrigin=")), "")

    def fill(text: str) -> str:
        return (
            text.replace("{email}", email)
            .replace("{arn}", arn)
            .replace("{prefix}", prefix)
            .replace("{api}", api)
            .replace("{auth}", auth)
        )

    if "diff" in args and "diff_fail" in config:
        print(fill(config["diff_fail"]), file=sys.stderr)
        return 1
    if "diff" in args:
        print(
            fill(
                config.get(
                    "diff_text", "Resources\n[~] AWS::Budgets::Budget Budget\n subscriber {email}"
                )
            )
        )
    return 0


def build_tool(tool: str, args: list[str], config: dict) -> int:
    command = " ".join([tool, *args])
    for prefix, message in config.get("build_fail", {}).items():
        if command.startswith(prefix):
            return fail(message, 1)
    touched = config.get("build_touches")
    if touched and tool == "npm":
        with Path(touched).open("a") as handle:
            handle.write("changed by the build\n")
    return 0


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "font-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'none'; "
        "frame-ancestors 'none'; object-src 'none'"
    ),
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
INDEX_BODY = (
    "<!doctype html><html><head><title>Precertly</title>"
    '<script type="module" crossorigin src="./assets/index-abc123.js"></script>'
    '<link rel="stylesheet" crossorigin href="./assets/index-abc123.css"></head>'
    '<body><div id="root"></div></body></html>'
)
NOT_FOUND_BODY = "<!doctype html><title>Not found</title><h1>This page does not exist</h1>"


def site_response(path: str, scheme: str, host: str) -> dict:
    """What the healthy site answers; tests override single parts through stub.json "site"."""
    if scheme == "http":
        return {"status": 301, "headers": {"Location": f"https://{host}{path}"}, "body": ""}
    secure = {**SECURITY_HEADERS}
    if path == "/":
        return {
            "status": 200,
            "headers": {
                "Content-Type": "text/html",
                "Cache-Control": "public,max-age=60",
                **secure,
            },
            "body": INDEX_BODY,
        }
    if path == "/assets/index-abc123.js":
        kind, cache = "text/javascript", "public,max-age=31536000,immutable"
    elif path == "/assets/index-abc123.css":
        kind, cache = "text/css", "public,max-age=31536000,immutable"
    elif path == "/favicon.svg":
        kind, cache = "image/svg+xml", "public,max-age=300"
    elif path.startswith("/data/") and path.endswith(".json"):
        kind, cache = "application/json", "public,max-age=300"
    else:
        return {
            "status": 404,
            "headers": {
                "Content-Type": "text/html",
                "Cache-Control": "public,max-age=10",
                **secure,
            },
            "body": NOT_FOUND_BODY,
        }
    return {
        "status": 200,
        "headers": {"Content-Type": kind, "Cache-Control": cache, **secure},
        "body": f"contents of {path}",
    }


def gateway_response(method: str, path: str, request_headers: dict, config: dict) -> dict:
    """What the live API's gateway answers (see the module docstring)."""
    allowed = config.get("gateway_origin", "https://precertly.example.org")
    health = path == "/health"
    start = path == "/live/runs"
    poll = path.startswith("/live/runs/") and len(path) > len("/live/runs/")
    if method == "OPTIONS":
        if not (health or start or poll):
            return {"status": 404, "headers": {}, "body": '{"message":"Not Found"}'}
        headers = {}
        if request_headers.get("origin") == allowed:
            headers = {
                "Access-Control-Allow-Origin": allowed,
                "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
                "Access-Control-Allow-Headers": "authorization,content-type",
                "Access-Control-Max-Age": "3600",
            }
        return {"status": 204, "headers": headers, "body": ""}
    if method == "GET" and health:
        running = read_state(config).get("live_running", 0) >= 1
        if running and bump("gateway_health") > config.get("gateway_polls", 0):
            return {"status": 200, "headers": {}, "body": '{"status":"ok"}'}
        return {"status": 500, "headers": {}, "body": '{"message":"Internal Server Error"}'}
    if (method == "POST" and start) or (method == "GET" and poll):
        return {"status": 401, "headers": {}, "body": '{"message":"Unauthorized"}'}
    return {"status": 404, "headers": {}, "body": '{"message":"Not Found"}'}


def curl(args: list[str], config: dict) -> int:
    """A read-only HTTP client: writes the headers and body files the script asks for and prints
    the --write-out text. Nothing leaves the machine."""
    url = args[-1]
    scheme, _, rest = url.partition("://")
    host, _, tail = rest.partition("/")
    path = "/" + tail
    option = {a: args[i + 1] for i, a in enumerate(args[:-1]) if a.startswith("--")}
    if ".execute-api." in host:
        request_headers = {
            name.strip().lower(): value.strip()
            for i, a in enumerate(args[:-1])
            if a == "--header"
            for name, _, value in [args[i + 1].partition(":")]
        }
        method = option.get("--request", "GET")
        sites = config.get("gateway", {})
        key = f"{method} {path}"
        response = gateway_response(method, path, request_headers, config)
    else:
        sites = config.get("site", {})
        key = path if scheme == "https" else f"http:{path}"
        response = site_response(path, scheme, host)
    for override in (sites.get("*", {}), sites.get(key, {})):
        response["status"] = override.get("status", response["status"])
        response["body"] = override.get("body", response["body"])
        response["headers"] = {**response["headers"], **override.get("headers", {})}
        if "exit" in override:
            response = {**response, "exit": override["exit"], "stderr": override.get("stderr", "")}
    if "exit" in response:
        print(response["stderr"] or f"curl: ({response['exit']}) failed", file=sys.stderr)
        print("000 0", end="")
        return int(response["exit"])
    lines = [f"HTTP/2 {response['status']}"]
    for name, value in response["headers"].items():
        if value is not None:
            lines.append(
                f"{name.lower() if config.get('site_lowercase_headers') else name}: {value}"
            )
    Path(option["--dump-header"]).write_text("\r\n".join(lines) + "\r\n\r\n")
    Path(option["--output"]).write_text(response["body"])
    verify = config.get("tls_verify", 0) if scheme == "https" else 0
    print(
        option["--write-out"]
        .replace("%{http_code}", str(response["status"]))
        .replace("%{ssl_verify_result}", str(verify)),
        end="",
    )
    return 0


CONTENT_TYPES = {
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".js": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
}


def s3(args: list[str], config: dict) -> int:
    """`aws s3 sync|cp`: lists what a sync would upload or delete (from the local source tree and
    the stub's s3_stale keys) and fails when asked to. Nothing is stored."""
    operation = args[1]
    kind = "index" if operation == "cp" else ("assets" if args[2].endswith("/assets") else "rest")
    dryrun = "--dryrun" in args
    if config.get("s3_fail") == ("dryrun" if dryrun else kind):
        return fail("An error occurred (AccessDenied) when calling the PutObject operation")
    if operation == "cp":
        print(f"upload: {args[2]} to {args[3]}")
        return 0
    source, target = Path(args[2]), args[3]
    excludes = [args[i + 1] for i, a in enumerate(args) if a == "--exclude"]
    prefix = "(dryrun) " if dryrun else ""
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source).as_posix()
        if path.is_file() and not any(fnmatch.fnmatch(relative, e) for e in excludes):
            print(f"{prefix}upload: {path} to {target}/{relative}")
    if "--delete" in args:
        base = target.removeprefix("s3://").split("/", 1)
        folder = base[1] + "/" if len(base) > 1 else ""
        for key in config.get("s3_stale", []):
            relative = key.removeprefix(folder)
            if key.startswith(folder) and not any(fnmatch.fnmatch(relative, e) for e in excludes):
                print(f"{prefix}delete: s3://{base[0]}/{key}")
    return 0


def s3api(args: list[str], config: dict) -> int:
    key = args[args.index("--key") + 1]
    overrides = config.get("content_types", {})
    print(overrides.get(key, CONTENT_TYPES.get(Path(key).suffix, "binary/octet-stream")))
    return 0


def cloudfront(args: list[str], config: dict) -> int:
    if args[1] == "create-invalidation":
        if "invalidate_fail" in config:
            return fail(config["invalidate_fail"])
        print("I2J3K4L5M6N7")
    else:
        polls = bump("invalidation")
        done = not config.get("inval_never") and polls > config.get("inval_polls", 0)
        print("Completed" if done else "InProgress")
    return 0


def bump(name: str) -> int:
    """Count a call by name across stub invocations; returns the new count."""
    path = BASE / "counters.json"
    counters = json.loads(path.read_text()) if path.exists() else {}
    counters[name] = counters.get(name, 0) + 1
    path.write_text(json.dumps(counters))
    return counters[name]


def read_state(config: dict) -> dict:
    path = BASE / "state.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    for key in ("desired", "running", "live_desired", "live_running"):
        state.setdefault(key, config.get(key, 0))
    return state


def service_prefix(args: list[str]) -> str:
    """The state and config prefix of the service a call is about: "live_" or nothing."""
    return "live_" if "precertly-api-live" in args else ""


def aws(args: list[str], config: dict) -> int:
    if "fail_all" in config:
        return fail(config["fail_all"], 255)
    service, operation = args[0], args[1]
    state = read_state(config)

    if service == "sts":
        print(ACCOUNT)
    elif service == "cloudformation":
        name = args[args.index("--stack-name") + 1]
        stacks = config.get("stacks", {})
        if name not in stacks:
            return fail(f"An error occurred (ValidationError): Stack with id {name} does not exist")
        query = " ".join(args)
        if "starts_with" in query:
            print(config.get("publish_outputs", 2))
        elif "Outputs" in query:
            key = query.split("OutputKey==")[1].split("'")[1]
            outputs = {**DATA_OUTPUTS, **config.get("outputs", {})}
            print("None" if config.get("no_outputs") else outputs.get(key, "None"))
        else:
            print(stacks[name])
    elif service == "s3":
        return s3(args, config)
    elif service == "s3api":
        return s3api(args, config)
    elif service == "cloudfront":
        return cloudfront(args, config)
    elif tools_call(args):
        return tools(args, config, state)
    elif operation == "describe-db-clusters":
        if config.get("aurora_missing"):
            return fail("An error occurred (DBClusterNotFoundFault): DBCluster x not found.")
        print("available\t16.13\t0.0\t2.0")
    elif operation == "describe-services":
        p = service_prefix(args)
        if config.get("cluster_missing"):
            return fail("An error occurred (ClusterNotFoundException): Cluster not found.")
        if config.get(f"{p}service_missing"):
            print("None")
        else:
            family = "precertly-api-live" if p else "precertly-api"
            print(
                f"ACTIVE\t{state[p + 'desired']}\t{state[p + 'running']}\t0\tCOMPLETED\t"
                f"arn:aws:ecs:us-east-2:{ACCOUNT}:task-definition/{family}:3"
            )
    elif operation == "update-service":
        p = service_prefix(args)
        state[p + "desired"] = int(args[args.index("--desired-count") + 1])
        stuck = config.get(f"{p}stuck_running") or (
            config.get(f"{p}stick_on_zero") and state[p + "desired"] == 0
        )
        if not stuck:
            state[p + "running"] = state[p + "desired"]
        (BASE / "state.json").write_text(json.dumps(state))
        print(state[p + "desired"])
    elif operation == "describe-task-definition":
        print(f"3\t{IMAGE}\t{'CMD-SHELL' if config.get('health_check') else 'None'}")
    elif operation == "list-tasks":
        p = service_prefix(args)
        print((LIVE_TASK_ARN if p else TASK_ARN) if state[p + "running"] else "None")
    elif operation == "describe-tasks":
        if "managedAgents" in " ".join(args):
            polls = bump("agent")
            pending = config.get("agent_never_running") or polls <= config.get("agent_polls", 0)
            print("PENDING" if pending else "RUNNING")
        else:
            polls = bump("health")
            health = config.get("health", "HEALTHY")
            if polls <= config.get("health_after_polls", 0):
                health = "UNKNOWN"
            print(f"RUNNING\t{health}")
    elif operation == "execute-command":
        return execute_command(args, config)
    elif service in ("ec2", "rds"):
        print(0)
    elif service == "ecr":
        if config.get("ecr_missing"):
            return fail("An error occurred (RepositoryNotFoundException): repository not found")
        print("sha-abc123def456" if "imageDetails[].imageTags[]" in " ".join(args) else 1)
    else:
        return fail(f"stub: unexpected call {args}", 98)
    return 0


def tools_call(args: list[str]) -> bool:
    """Calls about the precertly-tools task rather than the API service."""
    joined = " ".join(args)
    return (
        "--family" in args
        or args[1] in ("run-task", "stop-task")
        or (args[1] == "describe-tasks" and "exitCode" in joined)
        or args[0] == "logs"
    )


def tools(args: list[str], config: dict, state: dict) -> int:
    operation = args[1]
    task = state.get("tools") or (
        {"arn": OTHER_TOOLS_ARN, "status": "RUNNING", "polls": 0}
        if config.get("tools_running")
        else None
    )
    path = BASE / "state.json"

    def save(new: dict) -> None:
        state["tools"] = new
        path.write_text(json.dumps(state))

    if operation == "list-tasks":
        print(task["arn"] if task and task["status"] == "RUNNING" else "")
    elif operation == "run-task":
        if config.get("tools_run_fail"):
            print(f"None\t{config['tools_run_fail']}")
        else:
            save({"arn": TOOLS_ARN, "status": "RUNNING", "polls": 0})
            print(f"{TOOLS_ARN}\tNone")
    elif operation == "describe-tasks":
        if config.get("tools_describe_fail"):
            return fail("An error occurred (ThrottlingException): Rate exceeded")
        assert task is not None
        if task["status"] == "RUNNING" and not config.get("tools_never_stops"):
            task["polls"] += 1
            if task["polls"] > config.get("tools_polls", 0):
                task["status"] = "STOPPED"
                task["exit"] = config.get("tools_exit", 0)
            save(task)
        if task["status"] == "STOPPED":
            print(f"STOPPED\t{task.get('exit', 137)}\tEssential container in task exited")
        else:
            print("RUNNING\tNone\tNone")
    elif operation == "stop-task":
        if config.get("tools_stop_fail"):
            return fail("An error occurred (ThrottlingException): Rate exceeded")
        assert task is not None
        if task["status"] == "RUNNING":
            task["status"] = "STOPPED"
            task["exit"] = 137
            save(task)
        print("STOPPED")
    elif args[0] == "logs":
        print(config.get("tools_log", "tools output"))
    else:
        return fail(f"stub: unexpected tools call {args}", 98)
    return 0


def execute_command(args: list[str], config: dict) -> int:
    command = args[args.index("--command") + 1]
    time.sleep(config.get("exec_sleep", 0))
    for key, count in config.get("exec_errors", {}).items():
        if key in command and bump(f"exec:{key}") <= count:
            return fail(
                "An error occurred (InvalidParameterException) when calling the ExecuteCommand "
                "operation: The execute command failed because execute command agent isn't "
                "running. Wait and try again or run describe-tasks to check the status."
            )
    print(
        "The Session Manager plugin was installed successfully. Use the AWS CLI to start a session."
    )
    print("Starting session with SessionId: ecs-execute-command-0abc")
    outputs = config.get("exec", {})
    if "live_smoke_run" in command:
        print(config.get("live_run_line", LIVE_RUN_LINE))
    for key in ("/policies", "/health", "smoke_llm"):
        if key in command:
            default = (
                "tokens in/out: 10/5  latency: 800 ms"
                if key == "smoke_llm"
                else f"SMOKE_HTTP {key} 200"
            )
            print(outputs.get(key, default))
            break
    print("Exiting session with sessionId: ecs-execute-command-0abc.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
