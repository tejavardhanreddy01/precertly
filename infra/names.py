"""Names shared by every stack and by the tests, so a rename happens in one place."""

ECR_REPO = "precertly-api"
CLUSTER = "precertly"
SERVICE = "precertly-api"
TOOLS_FAMILY = "precertly-tools"
TASK_ROLE = "precertly-ecs-task"
EXECUTION_ROLE = "precertly-ecs-execution"
LOG_GROUP = "/precertly/api"

# The tools task (migrations, ingest, check) lives in the data stack.
TOOLS_CONTAINER = "tools"
TOOLS_TASK_ROLE = "precertly-tools-task"
TOOLS_EXECUTION_ROLE = "precertly-tools-execution"
TOOLS_LOG_GROUP = "/precertly/tools"
# Reserved for the later DB-enabled API service in the data stack. Nothing defines it yet.
API_LIVE_SERVICE = "precertly-api-live"
