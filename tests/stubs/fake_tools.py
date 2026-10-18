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
  diff_text         what `cdk diff` prints; {email} is replaced by the budgetEmail context value
  diff_fail         `cdk diff` prints this to stderr and fails
"""

import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ACCOUNT = "123456789012"
TASK_ARN = f"arn:aws:ecs:us-east-2:{ACCOUNT}:task/precertly/0123456789abcdef"
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
        print(stacks[name])
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
        print("RUNNING\tUNKNOWN")
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


def execute_command(args: list[str], config: dict) -> int:
    command = args[args.index("--command") + 1]
    time.sleep(config.get("exec_sleep", 0))
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
