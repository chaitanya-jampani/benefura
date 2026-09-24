"""Spike 1: strict function tools with store=False replay, tool_choice override, nested agent-as-tool."""

from __future__ import annotations

import argparse
import json
from typing import Any

from _common import check, openai_client, project_client, settings, timer, write_result

PLAN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_usage",
        "description": "Amount used and remaining for a benefit in the current benefit period (runs in the browser).",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "benefitId": {"type": "string", "description": "Benefit id, e.g. massage"},
                "memberId": {"type": ["string", "null"], "description": "Member alias id or null for all"},
            },
            "required": ["benefitId", "memberId"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "ask_knowledge_agent",
        "description": "Ask the public-knowledge agent a general CA/AU health benefits or tax question.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "region": {"type": "string", "enum": ["CA", "AU"]},
            },
            "required": ["question", "region"],
            "additionalProperties": False,
        },
    },
]

INSTRUCTIONS = (
    "You are Benefura's plan and claims assistant for a fictional demo plan. Use get_usage for any question about "
    "amounts used or remaining. Use ask_knowledge_agent for general public rules (tax credits, government plans). "
    "Answer in two sentences at most."
)

TURNS = [
    "How much massage therapy do I have left this year?",
    "And for [MEMBER_B]? Also, can massage count as a medical expense on my Canadian taxes?",
    "Thanks. Summarize both answers in one line.",
]


def fake_get_usage(args: dict[str, Any]) -> dict[str, Any]:
    member = args.get("memberId") or "all"
    return {"benefitId": args["benefitId"], "memberId": member, "usedCents": 24000, "limitCents": 50000,
            "remainingCents": 26000, "period": "2026-01-01..2026-12-31"}


def to_input_item(item: dict[str, Any], keep_reasoning: bool) -> dict[str, Any] | None:
    """Replay rules: strip ids/status, drop reasoning unless it carries encrypted content and we keep it."""
    kind = item.get("type")
    if kind == "reasoning":
        if keep_reasoning and item.get("encrypted_content"):
            return {"type": "reasoning", "summary": item.get("summary") or [], "encrypted_content": item["encrypted_content"]}
        return None
    if kind == "function_call":
        return {"type": "function_call", "call_id": item["call_id"], "name": item["name"], "arguments": item["arguments"]}
    if kind == "message":
        text = "".join(part.get("text", "") for part in item.get("content", []) if part.get("type") == "output_text")
        return {"role": "assistant", "content": text}
    return None


def run_loop(client: Any, *, model: str | None, agent: dict[str, str] | None, keep_reasoning: bool) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    total_steps = 0
    nested_calls = 0
    for text in TURNS:
        history.append({"role": "user", "content": text})
        rounds = 0
        answer = ""
        for _ in range(3):
            kwargs: dict[str, Any] = {"input": history, "store": False}
            if agent:
                kwargs["extra_body"] = {"agent_reference": agent}  # same shape agent-framework-foundry 1.13 sends
            else:
                kwargs |= {"model": model, "instructions": INSTRUCTIONS, "tools": PLAN_TOOLS,
                           "parallel_tool_calls": False, "reasoning": {"effort": "low"}, "max_output_tokens": 800}
            if keep_reasoning:
                kwargs["include"] = ["reasoning.encrypted_content"]
            with timer() as t:
                resp = client.responses.create(**kwargs)
            items = [o.model_dump(exclude_none=True) for o in resp.output]
            calls = [i for i in items if i.get("type") == "function_call"]
            history.extend(x for x in (to_input_item(i, keep_reasoning) for i in items) if x)
            answer = resp.output_text or answer
            print(f"  turn {len(turns) + 1} step: {len(calls)} call(s), {t['ms']} ms, usage={resp.usage and resp.usage.total_tokens}")
            if not calls:
                break
            rounds += 1
            for call in calls:
                total_steps += 1
                args = json.loads(call["arguments"])
                if call["name"] == "get_usage":
                    output: Any = fake_get_usage(args)
                elif call["name"] == "ask_knowledge_agent":
                    nested_calls += 1
                    nested = client.responses.create(
                        model=settings().chat_model,
                        instructions="You answer general public health-benefit questions for CA/AU in one sentence.",
                        input=args["question"],
                        store=False,
                        reasoning={"effort": "low"},
                        max_output_tokens=400,
                    )
                    output = {"answer": nested.output_text, "citations": []}
                else:
                    output = {"error": f"tool {call['name']} not allowed"}
                history.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(output)})
        turns.append({"text": text, "toolRounds": rounds, "answer": answer[:300]})
    return {"turns": turns, "toolSteps": total_steps, "nestedCalls": nested_calls, "historyItems": len(history)}


def tool_choice_none(client: Any, model: str) -> bool:
    resp = client.responses.create(
        model=model, instructions=INSTRUCTIONS, tools=PLAN_TOOLS, tool_choice="none", store=False,
        input=[{"role": "user", "content": "How much massage do I have left?"}], max_output_tokens=300,
        reasoning={"effort": "low"},
    )
    return not any(o.type == "function_call" for o in resp.output)


def prompt_agent_loop(keep_reasoning: bool) -> dict[str, Any]:
    from azure.ai.projects.models import FunctionTool, PromptAgentDefinition

    project = project_client()
    name = "benefura-spike-s01"
    tools = [FunctionTool(name=t["name"], description=t["description"], parameters=t["parameters"], strict=True)
             for t in PLAN_TOOLS]
    version = project.agents.create_version(
        agent_name=name,
        definition=PromptAgentDefinition(model=settings().chat_model, instructions=INSTRUCTIONS, tools=tools),
    )
    try:
        client = project.get_openai_client()
        out = run_loop(client, model=None, agent={"name": name, "version": str(version.version), "type": "agent_reference"},
                       keep_reasoning=keep_reasoning)
        out["agentVersion"] = version.version
        return out
    finally:
        project.agents.delete_version(agent_name=name, agent_version=str(version.version))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-reasoning", action="store_true")
    parser.add_argument("--prompt-agent", action="store_true")
    args = parser.parse_args()

    model = settings().chat_model
    client = openai_client()
    results: dict[str, bool] = {}
    data: dict[str, Any] = {"model": model, "keepReasoning": args.keep_reasoning}

    print("a/b. replay loop (Responses API, store=False)")
    loop = run_loop(client, model=model, agent=None, keep_reasoning=args.keep_reasoning)
    data["loop"] = loop
    check(results, "three turns completed", len(loop["turns"]) == 3)
    check(results, "a turn used >= 1 tool round", max(t["toolRounds"] for t in loop["turns"]) >= 1)
    check(results, "nested agent-as-tool call ran", loop["nestedCalls"] >= 1)

    print("c. tool_choice none")
    check(results, "tool_choice=none suppresses calls", tool_choice_none(client, model))

    if args.prompt_agent:
        print("e. prompt agent (agent_reference) with store=False")
        try:
            data["promptAgent"] = prompt_agent_loop(args.keep_reasoning)
            check(results, "prompt agent loop completed", len(data["promptAgent"]["turns"]) == 3)
        except Exception as exc:  # noqa: BLE001
            data["promptAgentError"] = repr(exc)
            check(results, "prompt agent loop completed", False, repr(exc)[:300])

    data["checks"] = results
    write_result("s01", data, all(results.values()))


if __name__ == "__main__":
    main()
