"""The auth and live stacks must not change the stacks that are already deployed.

Each test builds a smaller app the way app.py did before the two stacks existed and compares
its templates with the ones the full app produces. (The same comparison was also made once by
hand against `cdk synth` of main, byte for byte; see docs/deployment.md.)
"""

import ast
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aws_cdk as cdk
from aws_cdk.assertions import Template
from support import context

from app import STACK_NAMES, build_app
from app_stack import AppStack
from base_stack import BaseStack
from data_stack import DataStack
from web_stack import WebStack

INFRA = Path(__file__).resolve().parents[1]
CTX = context()
# The outputs the data stack had before the live stack referred to it.
DATA_OUTPUTS_BEFORE = {
    "ClusterIdentifier",
    "SecretArn",
    "ToolsSubnetIds",
    "ToolsSecurityGroupId",
    "ToolsLogGroupName",
}


def _full(name: str, ctx: dict[str, Any] | None = None) -> Mapping[str, Any]:
    app = build_app(ctx or CTX)
    stack = next(c for c in app.node.children if c.node.id == name)
    assert isinstance(stack, cdk.Stack)
    return Template.from_stack(stack).to_json()


def _before() -> dict[str, Mapping[str, Any]]:
    """The four original stacks, built alone with the same context."""
    app = cdk.App(analytics_reporting=False, context=CTX)
    base = BaseStack(app, "precertly-base", budget_email=CTX["budgetEmail"])
    app_stack = AppStack(app, "precertly-app", image_tag=CTX["imageTag"])
    data = DataStack(
        app,
        "precertly-data",
        image_tag=CTX["imageTag"],
        vpc=app_stack.vpc,
        database_subnets=app_stack.database_subnets,
        tools_subnets=app_stack.public_subnets,
    )
    web = WebStack(
        app,
        "precertly-web",
        domain=CTX["webDomain"],
        certificate_arn=CTX["webCertificateArn"],
    )
    return {
        stack.node.id: Template.from_stack(stack).to_json()
        for stack in (base, app_stack, data, web)
    }


def test_base_app_and_web_templates_are_unchanged() -> None:
    before = _before()
    for name in ("precertly-base", "precertly-app", "precertly-web"):
        assert json.dumps(_full(name), sort_keys=True) == json.dumps(before[name], sort_keys=True)


def test_data_template_only_gains_the_two_outputs_the_live_stack_reads() -> None:
    before = _before()["precertly-data"]
    after = _full("precertly-data")
    for section in set(before) | set(after):
        if section != "Outputs":
            assert after.get(section) == before.get(section), section
    # Every existing output is unchanged, and nothing else was added.
    for key, value in before["Outputs"].items():
        assert after["Outputs"][key] == value
    added = {k: v for k, v in after["Outputs"].items() if k not in before["Outputs"]}
    assert set(before["Outputs"]) == DATA_OUTPUTS_BEFORE
    assert len(added) == 2
    for key, value in added.items():
        assert key.startswith("PublishOutput")
        assert set(value) == {"Value"}  # an output only: no Export, so nothing to import
    text = json.dumps(added)
    assert "DatabaseSecurityGroup" in text
    assert "DatabaseSecretAttachment" in text


def test_data_template_gets_no_ingress_resource_from_the_live_stack() -> None:
    template = _full("precertly-data")
    ingress = [
        r for r in template["Resources"].values() if r["Type"] == "AWS::EC2::SecurityGroupIngress"
    ]
    assert len(ingress) == 1  # the tools task, as before
    assert "Live" not in json.dumps(ingress)
    assert all(
        "SecurityGroupIngress" not in r["Properties"]
        for r in template["Resources"].values()
        if r["Type"] == "AWS::EC2::SecurityGroup"
    )


def test_the_four_original_templates_do_not_name_the_new_stacks() -> None:
    for name in ("precertly-base", "precertly-app", "precertly-data", "precertly-web"):
        text = json.dumps(_full(name))
        assert "precertly-live" not in text
        assert "precertly-auth" not in text
        assert "precertly-api-live" not in text


def test_the_four_original_templates_do_not_depend_on_the_new_context() -> None:
    other = context(authDomainPrefix="some-other-prefix")
    for name in ("precertly-base", "precertly-app", "precertly-data", "precertly-web"):
        assert json.dumps(_full(name, other), sort_keys=True) == json.dumps(
            _full(name), sort_keys=True
        )


def test_dependencies_run_one_way() -> None:
    assembly = build_app(CTX).synth()
    dependencies = {
        stack.stack_name: {d.id for d in stack.dependencies if d.id in STACK_NAMES}
        for stack in assembly.stacks
    }
    assert dependencies == {
        "precertly-base": set(),
        "precertly-app": set(),
        "precertly-data": {"precertly-app"},
        "precertly-web": set(),
        "precertly-auth": set(),
        "precertly-live": {"precertly-app", "precertly-data", "precertly-auth"},
    }


def test_older_stacks_do_not_import_the_new_modules() -> None:
    forbidden = ("live_stack", "auth_stack", "LiveStack", "AuthStack")
    for module in ("base_stack.py", "app_stack.py", "data_stack.py", "web_stack.py"):
        tree = ast.parse((INFRA / module).read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(a.name for a in node.names)
        assert not {i for i in imported if any(f in i for f in forbidden)}, module


def test_auth_stack_imports_nothing_from_the_stacks_it_stands_apart_from() -> None:
    tree = ast.parse((INFRA / "auth_stack.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not imported & {"app_stack", "data_stack", "live_stack", "base_stack"}
