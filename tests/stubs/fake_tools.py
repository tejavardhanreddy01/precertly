"""Stand-in for aws, npx, docker and session-manager-plugin, used by tests/test_deploy_script.py.

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
  diff_text         what `cdk diff` prints; {email} is replaced by the budgetEmail context value
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
"""

import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ACCOUNT = "123456789012"
TASK_ARN = f"arn:aws:ecs:us-east-2:{ACCOUNT}:task/precertly/0123456789abcdef"
TOOLS_ARN = f"arn:aws:ecs:us-east-2:{ACCOUNT}:task/precertly/aaaabbbbccccdddd"
OTHER_TOOLS_ARN = f"arn:aws:ecs:us-east-2:{ACCOUNT}:task/precertly/9999888877776666"
DATA_OUTPUTS = {
    "ClusterIdentifier": "precertly-data-database-abc123",
    "SecretArn": f"arn:aws:secretsmanager:us-east-2:{ACCOUNT}:secret:DatabaseSecret-AbCdEf",
    "ToolsSubnetIds": "subnet-0a1b2c,subnet-3d4e5f",
    "ToolsSecurityGroupId": "sg-0123abcd",
    "ToolsLogGroupName": "/precertly/tools",
}
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
    return 0


def fail(message: str, code: int = 254) -> int:
    print(message, file=sys.stderr)
    return code


def npx(args: list[str], config: dict) -> int:
    email = next((a.split("=", 1)[1] for a in args if a.startswith("budgetEmail=")), "")
    if "diff" in args and "diff_fail" in config:
        print(config["diff_fail"].replace("{email}", email), file=sys.stderr)
        return 1
    if "diff" in args:
        text = config.get(
            "diff_text", "Resources\n[~] AWS::Budgets::Budget Budget\n subscriber {email}"
        )
        print(text.replace("{email}", email))
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
    if path.exists():
        return json.loads(path.read_text())
    return {"desired": config.get("desired", 0), "running": config.get("running", 0)}


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
        if "Outputs" in query:
            key = query.split("OutputKey==")[1].split("'")[1]
            outputs = {**DATA_OUTPUTS, **config.get("outputs", {})}
            print("None" if config.get("no_outputs") else outputs.get(key, "None"))
        else:
            print(stacks[name])
    elif tools_call(args):
        return tools(args, config, state)
    elif operation == "describe-db-clusters":
        if config.get("aurora_missing"):
            return fail("An error occurred (DBClusterNotFoundFault): DBCluster x not found.")
        print("available\t16.13\t0.0\t2.0")
    elif operation == "describe-services":
        if config.get("cluster_missing"):
            return fail("An error occurred (ClusterNotFoundException): Cluster not found.")
        if config.get("service_missing"):
            print("None")
        else:
            print(
                f"ACTIVE\t{state['desired']}\t{state['running']}\t0\tCOMPLETED\t"
                f"arn:aws:ecs:us-east-2:{ACCOUNT}:task-definition/precertly-api:3"
            )
    elif operation == "update-service":
        state["desired"] = int(args[args.index("--desired-count") + 1])
        stuck = config.get("stuck_running") or (
            config.get("stick_on_zero") and state["desired"] == 0
        )
        if not stuck:
            state["running"] = state["desired"]
        (BASE / "state.json").write_text(json.dumps(state))
        print(state["desired"])
    elif operation == "describe-task-definition":
        print(f"3\t{IMAGE}\t{'CMD-SHELL' if config.get('health_check') else 'None'}")
    elif operation == "list-tasks":
        print(TASK_ARN if state["running"] else "None")
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
