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

# The live service (precertly-live): the API image with live mode on, behind the gateway. The
# ECS service name is also the task definition family.
API_LIVE_SERVICE = "precertly-api-live"
LIVE_CONTAINER = "api"
LIVE_TASK_ROLE = "precertly-live-task"
LIVE_EXECUTION_ROLE = "precertly-live-execution"
LIVE_LOG_GROUP = "/precertly/live"
# The Cloud Map namespace is private DNS inside the VPC; the service in it is registered as
# api.<namespace>.
LIVE_NAMESPACE = "precertly-live.local"
LIVE_CLOUD_MAP_SERVICE = "api"
API_NAME = "precertly-live"

# Sign-in (precertly-auth).
USER_POOL = "precertly-live"
USER_POOL_CLIENT = "precertly-web"
